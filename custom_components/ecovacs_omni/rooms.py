"""Room selection and ordered (sequential) room cleaning."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from typing import Any

from deebot_client.capabilities import CapabilityNumber, CapabilitySetTypes
from deebot_client.commands.json.clean import CleanAreaV2
from deebot_client.device import Device
from deebot_client.events import (
    CleanCountEvent,
    CleanJobStatus,
    ReportStatsEvent,
    RoomsEvent,
    StateEvent,
)
from deebot_client.models import CleanMode, State
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store

from .commands import (
    FREE_CLEAN,
    SPOT_AREA,
    UNKNOWN_TYPE_CODE,
    CleanRoomsV2,
    response_code,
)
from .const import (
    DOMAIN,
    SEQUENTIAL_ROOM_TIMEOUT,
    SEQUENTIAL_START_TIMEOUT,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# After the robot docked without sending a "job finished" report we wait this
# long before we assume the room is done (the report normally arrives first).
_DOCKED_GRACE_SECONDS = 90


@dataclass(frozen=True)
class RoomInfo:
    """A room of the active map."""

    id: int
    name: str


class RoomQueueAbortedError(HomeAssistantError):
    """Raised internally when the queue has to stop."""


class RoomManager:
    """Keeps the rooms, the user's ordered room selection and the room queue."""

    def __init__(self, hass: HomeAssistant, device: Device) -> None:
        """Initialize the manager."""
        self._hass = hass
        self._device = device
        self._did: str = device.device_info["did"]
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.rooms.{self._did}"
        )
        self.map_id: str | None = None
        self.rooms: dict[int, RoomInfo] = {}
        self._selections: dict[str, list[int]] = {}
        self.sequential: bool = True
        # "freeClean" (newer robots, e.g. T50) or "spotArea" (older robots).
        # Detected automatically on the first room cleaning.
        self.clean_type: str = FREE_CLEAN

        # Queue state
        self._queue_task: asyncio.Task[None] | None = None
        self.queue: list[int] = []
        self.current_room: int | None = None
        self.queue_total: int = 0

        self._listeners: list[Callable[[], None]] = []
        self._rooms_listeners: list[Callable[[], None]] = []
        self._unsub_rooms: Callable[[], None] | None = None

    # ------------------------------------------------------------------ setup

    async def async_load(self) -> None:
        """Load persisted data and start listening for room updates."""
        if data := await self._store.async_load():
            self._selections = {
                str(map_id): [int(i) for i in ids]
                for map_id, ids in data.get("selections", {}).items()
            }
            self.sequential = bool(data.get("sequential", True))
            if data.get("clean_type") in (FREE_CLEAN, SPOT_AREA):
                self.clean_type = data["clean_type"]

        if self._device.capabilities.map is not None:
            self._unsub_rooms = self._device.events.subscribe(
                self._device.capabilities.map.rooms.event, self._on_rooms
            )

    async def async_shutdown(self) -> None:
        """Stop the queue and unsubscribe."""
        if self._unsub_rooms:
            self._unsub_rooms()
            self._unsub_rooms = None
        await self.async_cancel_queue()
        await self._store.async_save(self._data_to_store())

    async def _on_rooms(self, event: RoomsEvent) -> None:
        self.map_id = event.map_id
        rooms: dict[int, RoomInfo] = {}
        for room in event.rooms:
            name = room.name.strip() if room.name else ""
            rooms[room.id] = RoomInfo(room.id, name or f"Room {room.id}")
        self.rooms = rooms
        # Drop selected rooms which don't exist anymore
        if (selection := self._selections.get(event.map_id)) is not None:
            self._selections[event.map_id] = [i for i in selection if i in rooms]
        for listener in list(self._rooms_listeners):
            listener()
        self._async_changed()

    # -------------------------------------------------------------- listeners

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Listen for any change (selection, queue, rooms)."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_add_rooms_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Listen for a new room list."""
        self._rooms_listeners.append(listener)
        return lambda: self._rooms_listeners.remove(listener)

    @callback
    def _async_changed(self, *, save: bool = False) -> None:
        if save:
            self._store.async_delay_save(self._data_to_store, 1)
        for listener in list(self._listeners):
            listener()

    def _data_to_store(self) -> dict[str, Any]:
        return {
            "selections": self._selections,
            "sequential": self.sequential,
            "clean_type": self.clean_type,
        }

    # -------------------------------------------------------------- selection

    @property
    def selection(self) -> list[int]:
        """Return the ordered room selection of the active map."""
        if self.map_id is None:
            return []
        return list(self._selections.get(self.map_id, []))

    def room_name(self, room_id: int) -> str:
        """Return the name of a room."""
        if room := self.rooms.get(room_id):
            return room.name
        return f"Room {room_id}"

    @callback
    def async_set_selected(self, room_id: int, selected: bool) -> None:
        """Select (append at the end) or deselect a room."""
        if self.map_id is None:
            return
        selection = self._selections.setdefault(self.map_id, [])
        if selected and room_id not in selection:
            selection.append(room_id)
        elif not selected and room_id in selection:
            selection.remove(room_id)
        else:
            return
        self._async_changed(save=True)

    @callback
    def async_set_selection(self, room_ids: Sequence[int]) -> None:
        """Replace the whole selection (keeps the given order)."""
        if self.map_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_rooms"
            )
        self._selections[self.map_id] = list(dict.fromkeys(room_ids))
        self._async_changed(save=True)

    @callback
    def async_set_sequential(self, sequential: bool) -> None:
        """Set if rooms should be cleaned one after another."""
        self.sequential = sequential
        self._async_changed(save=True)

    def resolve_rooms(self, rooms: Sequence[str | int]) -> list[int]:
        """Resolve room names or ids to room ids (order is kept)."""
        if not self.rooms:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_rooms"
            )
        by_name = {room.name.casefold(): room.id for room in self.rooms.values()}
        result: list[int] = []
        for value in rooms:
            text = str(value).strip()
            if text.casefold() in by_name:
                room_id = by_name[text.casefold()]
            elif text.isdigit() and int(text) in self.rooms:
                room_id = int(text)
            else:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_room",
                    translation_placeholders={
                        "room": text,
                        "rooms": ", ".join(r.name for r in self.rooms.values()),
                    },
                )
            if room_id not in result:
                result.append(room_id)
        return result

    # ------------------------------------------------------------------ queue

    @property
    def queue_running(self) -> bool:
        """Return True if a sequential room queue is active."""
        return self._queue_task is not None and not self._queue_task.done()

    async def async_cancel_queue(self) -> None:
        """Cancel a running room queue (does not stop the robot)."""
        task = self._queue_task
        self._queue_task = None
        if task is not None and not task.done():
            task.cancel()
            try:  # noqa: SIM105
                await task
            except asyncio.CancelledError:
                pass
        self._reset_queue_state()

    def _reset_queue_state(self) -> None:
        self.queue = []
        self.current_room = None
        self.queue_total = 0
        self._async_changed()

    async def async_apply_settings(
        self,
        *,
        work_mode: str | None = None,
        fan_speed: str | None = None,
        water_amount: str | int | None = None,
    ) -> None:
        """Apply optional cleaning settings before a cleaning starts."""
        caps = self._device.capabilities
        if work_mode is not None:
            if caps.clean.work_mode is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_supported",
                    translation_placeholders={"option": "work_mode"},
                )
            await self._device.execute_command(caps.clean.work_mode.set(work_mode))
        if fan_speed is not None:
            if caps.fan_speed is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_supported",
                    translation_placeholders={"option": "fan_speed"},
                )
            await self._device.execute_command(caps.fan_speed.set(fan_speed))
        if water_amount is not None:
            amount = caps.water.amount if caps.water else None
            if isinstance(amount, CapabilityNumber):
                value = int(water_amount)
                if not amount.min <= value <= amount.max:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="water_amount_range",
                        translation_placeholders={
                            "min": str(amount.min),
                            "max": str(amount.max),
                        },
                    )
                await self._device.execute_command(amount.set(value))
            elif isinstance(amount, CapabilitySetTypes):
                await self._device.execute_command(amount.set(str(water_amount)))
            else:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_supported",
                    translation_placeholders={"option": "water_amount"},
                )

    def _default_cleanings(self) -> int:
        if event := self._device.events.get_last_event(CleanCountEvent):
            return event.count
        return 1

    async def async_clean(
        self,
        room_ids: Sequence[int],
        *,
        sequential: bool | None = None,
        cleanings: int | None = None,
    ) -> None:
        """Clean the given rooms in the given order."""
        if not room_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_rooms_selected"
            )
        if self._device.capabilities.clean.action.area is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_supported",
                translation_placeholders={"option": "rooms"},
            )
        await self.async_cancel_queue()
        if sequential is None:
            sequential = self.sequential
        if cleanings is None:
            cleanings = self._default_cleanings()
        elif (count := self._device.capabilities.clean.count) is not None and (
            cleanings != self._default_cleanings()
        ):
            # Like the app: set the number of passes before starting
            await self._device.execute_command(count.set(cleanings))

        room_ids = list(room_ids)
        if not sequential or len(room_ids) == 1:
            await self._send_area_clean(room_ids, cleanings)
            return

        self.queue = room_ids
        self.queue_total = len(room_ids)
        self._queue_task = self._hass.async_create_background_task(
            self._run_queue(cleanings), name=f"{DOMAIN}_room_queue_{self._did}"
        )
        self._async_changed()

    async def _send_area_clean(self, room_ids: list[int], cleanings: int) -> None:
        area = self._device.capabilities.clean.action.area
        assert area is not None
        if not (isinstance(area, type) and issubclass(area, CleanAreaV2)):
            # Older robots: use the command of deebot-client
            _LOGGER.debug("Cleaning rooms %s (%s cleanings)", room_ids, cleanings)
            await self._device.execute_command(
                area(CleanMode.SPOT_AREA, list(room_ids), cleanings)
            )
            return

        for attempt in range(2):
            _LOGGER.debug(
                "Cleaning rooms %s with type %s (%s cleanings)",
                room_ids,
                self.clean_type,
                cleanings,
            )
            response = await self._device.execute_command(
                CleanRoomsV2(room_ids, self.clean_type)
            )
            code = response_code(response)
            if code == UNKNOWN_TYPE_CODE and attempt == 0:
                other = SPOT_AREA if self.clean_type == FREE_CLEAN else FREE_CLEAN
                _LOGGER.info(
                    'Robot does not know room cleaning type "%s", using "%s"',
                    self.clean_type,
                    other,
                )
                self.clean_type = other
                self._async_changed(save=True)
                continue
            if code not in (None, 0):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="clean_rejected",
                    translation_placeholders={
                        "code": str(code),
                        "response": str(response),
                    },
                )
            return

    async def _run_queue(self, cleanings: int) -> None:
        try:
            while self.queue:
                room_id = self.queue.pop(0)
                self.current_room = room_id
                self._async_changed()
                _LOGGER.debug(
                    "Room queue: starting room %s (%s)",
                    room_id,
                    self.room_name(room_id),
                )
                await self._clean_single_room(room_id, cleanings)
            _LOGGER.debug("Room queue finished")
        except RoomQueueAbortedError as err:
            _LOGGER.info("Room queue stopped: %s", err)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Room queue failed")
        finally:
            if asyncio.current_task() is self._queue_task:
                self._queue_task = None
                self._reset_queue_state()

    async def _clean_single_room(self, room_id: int, cleanings: int) -> None:
        """Start one room and wait until the robot reports the job as done."""
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        done: asyncio.Future[bool] = loop.create_future()
        docked_handle: asyncio.TimerHandle | None = None
        job_id: str | None = None

        state_cap = self._device.capabilities.state
        old_state = self._device.events.get_last_event(state_cap.event)
        old_report = self._device.events.get_last_event(ReportStatsEvent)

        def finish(result: bool) -> None:
            if not done.done():
                done.set_result(result)

        def cancel_docked_timer() -> None:
            nonlocal docked_handle
            if docked_handle is not None:
                docked_handle.cancel()
                docked_handle = None

        async def on_state(event: StateEvent) -> None:
            nonlocal docked_handle
            if event is old_state:
                return
            if event.state is State.CLEANING:
                started.set()
                cancel_docked_timer()
            elif not started.is_set():
                return
            elif event.state is State.DOCKED and docked_handle is None:
                docked_handle = loop.call_later(_DOCKED_GRACE_SECONDS, finish, True)
            elif event.state is State.IDLE:
                # Stopped from the app or the robot itself
                finish(False)

        async def on_report(event: ReportStatsEvent) -> None:
            nonlocal job_id
            if event is old_report:
                return
            if event.status is CleanJobStatus.CLEANING:
                started.set()
                job_id = event.cleaning_id
                return
            if not started.is_set():
                return
            if job_id is not None and event.cleaning_id != job_id:
                return
            if event.status in (
                CleanJobStatus.FINISHED,
                CleanJobStatus.FINISHED_WITH_WARNINGS,
            ):
                finish(True)
            elif event.status is CleanJobStatus.MANUALLY_STOPPED:
                finish(False)

        unsubscribers = [
            self._device.events.subscribe(state_cap.event, on_state),
            self._device.events.subscribe(ReportStatsEvent, on_report),
        ]
        try:
            await self._send_area_clean([room_id], cleanings)
            try:
                async with asyncio.timeout(SEQUENTIAL_START_TIMEOUT):
                    await started.wait()
            except TimeoutError as err:
                raise RoomQueueAbortedError(
                    f"robot did not start cleaning room {room_id}"
                ) from err
            try:
                async with asyncio.timeout(SEQUENTIAL_ROOM_TIMEOUT):
                    completed = await done
            except TimeoutError as err:
                raise RoomQueueAbortedError(
                    f"room {room_id} did not finish in time"
                ) from err
            if not completed:
                raise RoomQueueAbortedError("cleaning was stopped")
        finally:
            cancel_docked_timer()
            for unsubscribe in unsubscribers:
                unsubscribe()

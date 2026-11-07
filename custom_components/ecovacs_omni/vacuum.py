"""Vacuum entity for Ecovacs Omni."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from deebot_client.capabilities import Capabilities, DeviceType
from deebot_client.commands import StationAction
from deebot_client.commands.json.station_action import (
    StationAction as StationActionCommand,
)
from deebot_client.device import Device
from deebot_client.events import CachedMapInfoEvent, FanSpeedEvent, StateEvent
from deebot_client.events.map import Map
from deebot_client.models import CleanAction, State
from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    StateVacuumEntityDescription,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, ServiceResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .commands import response_code
from .const import DOMAIN
from .controller import EcovacsOmniConfigEntry
from .entity import EcovacsOmniEntity
from .rooms import RoomManager

_LOGGER = logging.getLogger(__name__)

_SEGMENT_SEPARATOR = "_"

_STATE_TO_ACTIVITY = {
    State.IDLE: VacuumActivity.IDLE,
    State.CLEANING: VacuumActivity.CLEANING,
    State.RETURNING: VacuumActivity.RETURNING,
    State.DOCKED: VacuumActivity.DOCKED,
    State.ERROR: VacuumActivity.ERROR,
    State.PAUSED: VacuumActivity.PAUSED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcovacsOmniConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the vacuum entities."""
    controller = entry.runtime_data
    async_add_entities(
        EcovacsOmniVacuum(device, controller.room_manager(device))
        for device in controller.devices
        if device.capabilities.device_type is DeviceType.VACUUM
    )


class EcovacsOmniVacuum(EcovacsOmniEntity[Capabilities], StateVacuumEntity):
    """The robot itself."""

    _unrecorded_attributes = frozenset({"rooms", "job_rooms", "room_queue"})
    _attr_supported_features = (
        VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.SEND_COMMAND
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.START
    )
    entity_description = StateVacuumEntityDescription(
        key="vacuum", translation_key="vacuum", name=None
    )

    def __init__(self, device: Device, rooms: RoomManager) -> None:
        """Initialize the vacuum."""
        super().__init__(device, device.capabilities)
        self._rooms = rooms
        self._maps: dict[str, Map] = {}
        if fan_speed := self._capability.fan_speed:
            self._attr_supported_features |= VacuumEntityFeature.FAN_SPEED
            self._attr_fan_speed_list = [
                level.name.lower() for level in fan_speed.types
            ]
        if self._capability.map and self._capability.clean.action.area:
            self._attr_supported_features |= VacuumEntityFeature.CLEAN_AREA

    async def async_added_to_hass(self) -> None:
        """Subscribe to the robot events."""
        await super().async_added_to_hass()

        async def on_state(event: StateEvent) -> None:
            self._attr_activity = _STATE_TO_ACTIVITY[event.state]
            self.async_write_ha_state()

        self._subscribe(self._capability.state.event, on_state)

        if self._capability.fan_speed:

            async def on_fan_speed(event: FanSpeedEvent) -> None:
                self._attr_fan_speed = event.speed.name.lower()
                self.async_write_ha_state()

            self._subscribe(self._capability.fan_speed.event, on_fan_speed)

        if map_caps := self._capability.map:

            async def on_map_info(event: CachedMapInfoEvent) -> None:
                self._maps = {map_obj.id: map_obj for map_obj in event.maps}
                self._check_segments_changed()

            self._subscribe(map_caps.cached_info.event, on_map_info)

        @callback
        def on_rooms_changed() -> None:
            self._check_segments_changed()
            self.async_write_ha_state()

        self.async_on_remove(self._rooms.async_add_listener(on_rooms_changed))

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Expose rooms, rooms of the job and queue for dashboards and templates."""
        rooms = self._rooms
        return {
            "rooms": {room.name: room.id for room in rooms.rooms.values()},
            "job_rooms": [rooms.room_name(i) for i in rooms.order_rooms],
            "room_queue": {
                "running": rooms.queue_running,
                "current": (
                    rooms.room_name(rooms.current_room)
                    if rooms.current_room is not None
                    else None
                ),
                "remaining": [rooms.room_name(i) for i in rooms.queue],
                "total": rooms.queue_total,
            },
        }

    # ---------------------------------------------------------- basic actions

    async def async_start(self) -> None:
        """Start (or resume) cleaning."""
        if self._device.events.get_last_event(
            self._capability.state.event
        ) == StateEvent(State.PAUSED):
            await self._clean_command(CleanAction.RESUME)
            return
        await self._rooms.async_cancel_queue()
        self._rooms.stop_requested = False
        await self._clean_command(CleanAction.START)

    async def async_pause(self) -> None:
        """Pause cleaning (a room queue keeps waiting)."""
        await self._clean_command(CleanAction.PAUSE)

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning and the room queue."""
        await self._rooms.async_cancel_queue()
        self._rooms.stop_requested = True
        await self._clean_command(CleanAction.STOP)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return to the station and cancel the room queue."""
        await self._rooms.async_cancel_queue()
        if self.activity in (VacuumActivity.CLEANING, VacuumActivity.PAUSED):
            self._rooms.stop_requested = True
        await self._device.execute_command(self._capability.charge.execute())

    async def async_locate(self, **kwargs: Any) -> None:
        """Play a sound."""
        await self._device.execute_command(self._capability.play_sound.execute())

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the suction power."""
        assert self._capability.fan_speed is not None
        await self._device.execute_command(self._capability.fan_speed.set(fan_speed))

    async def _clean_command(self, action: CleanAction) -> None:
        response = await self._device.execute_command(
            self._capability.clean.action.command(action)
        )
        if (code := response_code(response)) not in (None, 0):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_rejected",
                translation_placeholders={
                    "command": action.value,
                    "code": str(code),
                    "response": str(response),
                },
            )

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Send a raw command (for experiments)."""
        if isinstance(params, list):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="params_dict"
            )
        await self._device.execute_command(
            self._capability.custom.set(command, params or {})
        )

    # ----------------------------------------------------- native area cleaning

    @callback
    def _check_segments_changed(self) -> None:
        if (last_seen := self.last_seen_segments) is None:
            return
        if {s.id for s in last_seen} != {s.id for s in self._get_segments()}:
            self.async_create_segments_issue()

    def _get_segments(self) -> list[Segment]:
        last_seen = self.last_seen_segments or []
        map_id = self._rooms.map_id
        if map_id is None or not self._maps:
            return last_seen
        map_obj = self._maps.get(map_id)
        group = map_obj.name if map_obj else None
        other_maps = {m for m in self._maps if m != map_id}
        segments = [
            s for s in last_seen if s.id.partition(_SEGMENT_SEPARATOR)[0] in other_maps
        ]
        segments.extend(
            Segment(
                id=f"{map_id}{_SEGMENT_SEPARATOR}{room.id}", name=room.name, group=group
            )
            for room in self._rooms.rooms.values()
        )
        return segments

    async def async_get_segments(self) -> list[Segment]:
        """Return the rooms that can be mapped to Home Assistant areas."""
        return self._get_segments()

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        """Clean rooms selected through vacuum.clean_area (order is kept)."""
        room_ids: list[int] = []
        for composite in segment_ids:
            map_id, _, room_id = composite.partition(_SEGMENT_SEPARATOR)
            if map_id != self._rooms.map_id:
                _LOGGER.warning(
                    "Room %s belongs to map %s which is not active, skipping",
                    room_id,
                    map_id,
                )
                continue
            room_ids.append(int(room_id))
        if room_ids:
            await self._rooms.async_clean(room_ids)

    # ------------------------------------------------------ integration services

    async def async_clean_rooms(
        self,
        rooms: list[str],
        sequential: bool | None = None,
        cleanings: int | None = None,
        work_mode: str | None = None,
        fan_speed: str | None = None,
        water_amount: str | int | None = None,
    ) -> None:
        """Clean the given rooms in the given order."""
        room_ids = self._rooms.resolve_rooms(rooms)
        await self._rooms.async_apply_settings(
            work_mode=work_mode, fan_speed=fan_speed, water_amount=water_amount
        )
        await self._rooms.async_clean(
            room_ids, sequential=sequential, cleanings=cleanings
        )

    async def async_cancel_room_queue(self) -> None:
        """Cancel the queue, the robot finishes the current room."""
        await self._rooms.async_cancel_queue()

    async def async_station_action(self, action: str) -> None:
        """Run a station action (empty dustbin, wash mop, dry mop, clean base)."""
        await self._device.execute_command(
            StationActionCommand(StationAction[action.upper()])
        )

    async def async_get_rooms(self) -> ServiceResponse:
        """Return the rooms of the active map."""
        return {
            "map_id": self._rooms.map_id,
            "rooms": [
                {
                    "id": room.id,
                    "name": room.name,
                }
                for room in self._rooms.rooms.values()
            ],
            "job_rooms": [self._rooms.room_name(i) for i in self._rooms.order_rooms],
        }

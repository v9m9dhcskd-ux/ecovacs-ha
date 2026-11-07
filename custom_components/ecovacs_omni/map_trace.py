"""Keeps the cleaning trace (the line the robot has driven) on the map current.

deebot-client only asks for the trace once, when the map is enabled, and then
relies on the robot pushing ``onMapTrace`` messages. Newer robots (e.g. the T50
MAX PRO OMNI) send ``onMapTrace_V2`` instead, or nothing usable, so the line on
the map stayed empty during a cleaning. While the robot is cleaning we
therefore fetch the new trace points ourselves.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging

from deebot_client.capabilities import CapabilityMap
from deebot_client.commands.json.map import GetMapTrace
from deebot_client.device import Device
from deebot_client.events import StateEvent
from deebot_client.events.map import MapTraceEvent
from deebot_client.models import State
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

TRACE_POLL_INTERVAL = timedelta(seconds=15)
# Points per getMapTrace answer (see GetMapTrace in deebot-client)
_POINTS_PER_REQUEST = 200
# States in which the robot drives and the trace grows
_ACTIVE_STATES = frozenset({State.CLEANING, State.RETURNING})


class MapTraceUpdater:
    """Fetches new trace points while the robot is cleaning."""

    def __init__(
        self, hass: HomeAssistant, device: Device, capability: CapabilityMap
    ) -> None:
        """Initialize the updater."""
        self._hass = hass
        self._device = device
        self._capability = capability
        # Trace points up to this index are already on the map
        self._next_start = 0
        self._active = False
        self._polling = False
        self._unsub_timer: Callable[[], None] | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        # Only json robots can be asked for a part of the trace
        self._incremental = any(
            isinstance(command, GetMapTrace) for command in capability.trace.get
        )

    @callback
    def async_start(self) -> None:
        """Start listening (after the map itself subscribed to the trace)."""
        # Deferred: deebot-client subscribes the map to the trace in a task
        # started by the first map subscriber. It has to stay the first trace
        # subscriber, otherwise it would only get the last part of the trace.
        self._hass.async_create_task(self._async_subscribe(), eager_start=False)

    async def _async_subscribe(self) -> None:
        events = self._device.events
        self._unsubscribers = [
            events.subscribe(self._capability.trace.event, self._on_trace),
            events.subscribe(StateEvent, self._on_state),
        ]

    @callback
    def async_stop(self) -> None:
        """Stop everything."""
        self._stop_timer()
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    async def _on_trace(self, event: MapTraceEvent) -> None:
        end = min(event.start + _POINTS_PER_REQUEST, event.total)
        if event.start == 0 or event.total < self._next_start:
            # New trace (new job)
            self._next_start = end
        else:
            self._next_start = max(self._next_start, end)

    async def _on_state(self, event: StateEvent) -> None:
        active = event.state in _ACTIVE_STATES
        if active == self._active:
            return
        self._active = active
        if active:
            # (Re)started: load the whole trace once, then only new points
            self._next_start = 0
            self._unsub_timer = async_track_time_interval(
                self._hass, self._async_poll, TRACE_POLL_INTERVAL
            )
        else:
            self._stop_timer()
        # Also fetch the last points when the robot stops driving
        await self._async_poll()

    def _stop_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _async_poll(self, _now: datetime | None = None) -> None:
        if self._polling:
            return
        self._polling = True
        try:
            events = self._device.events
            events.request_refresh(self._capability.position.event)
            if self._incremental:
                # The answer requests the following parts itself
                await self._device.execute_command(GetMapTrace(self._next_start))
            else:
                events.request_refresh(self._capability.trace.event)
        except Exception:
            _LOGGER.debug("Fetching the map trace failed", exc_info=True)
        finally:
            self._polling = False

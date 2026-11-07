"""Cleaning trace on the map."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

from deebot_client.command import DeviceCommandResult
from deebot_client.commands.json.work_state import GetWorkState
from deebot_client.events import StateEvent
from deebot_client.events.map import MapTraceEvent
from deebot_client.models import State
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.ecovacs_omni.map_trace import TRACE_POLL_INTERVAL

from .test_rooms import VACUUM, _device, _notify, _sent
from .test_t50_messages import _message, _raw

# 10 points, LZMA compressed like the robot sends them
TRACE_VALUE = "XQAAgAAyAAAAAABo/r0Ce/RBj1cBdEvw3ob2yybaG+9Jzh1/XQasztRRS//23LAA"


def _trace_starts(mock_execute: AsyncMock) -> list[int]:
    return [args["traceStart"] for args in _sent(mock_execute, "getMapTrace")]


async def _tick(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    async_fire_time_changed(
        hass, dt_util.utcnow() + TRACE_POLL_INTERVAL + timedelta(seconds=1)
    )
    await _notify(hass, entry, StateEvent(State.CLEANING))


async def test_trace_is_fetched_while_cleaning(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """While cleaning only the new trace points are requested."""
    entry = init_integration
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_execute.reset_mock()

    await _notify(hass, entry, StateEvent(State.CLEANING))
    # Start of the job: the whole trace
    assert _trace_starts(mock_execute) == [0]

    # The robot has 150 points so far
    await _notify(hass, entry, MapTraceEvent(start=0, total=150, data=""))
    await _tick(hass, entry)
    assert _trace_starts(mock_execute) == [0, 150]

    # Docked: last points once, then no more requests
    await _notify(hass, entry, MapTraceEvent(start=150, total=180, data=""))
    await _notify(hass, entry, StateEvent(State.DOCKED))
    assert _trace_starts(mock_execute) == [0, 150, 180]
    async_fire_time_changed(hass, dt_util.utcnow() + 3 * TRACE_POLL_INTERVAL)
    await hass.async_block_till_done()
    assert _trace_starts(mock_execute) == [0, 150, 180]

    # Next job starts with the whole trace again
    await _notify(hass, entry, StateEvent(State.CLEANING))
    assert _trace_starts(mock_execute) == [0, 150, 180, 0]


async def test_map_trace_v2_without_total(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """onMapTrace_V2 is accepted without totalCount."""
    await _message(
        hass,
        init_integration,
        "onMapTrace_V2",
        _raw({"traceStart": 20, "pointCount": 10, "traceValue": TRACE_VALUE}),
    )
    event = _device(init_integration).events.get_last_event(MapTraceEvent)
    assert event == MapTraceEvent(start=20, total=30, data=TRACE_VALUE)


async def test_work_state_answer_keeps_cleaning(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """The answer of getWorkState is corrected like the message."""
    entry = init_integration
    data: dict[str, Any] = {
        "paused": 0,
        "robotState": {"state": "cleaning"},
        "stationState": {"state": "washing"},
    }
    mock_execute.return_value = DeviceCommandResult(
        device_reached=True, raw_response={"resp": {"body": {"data": data}}}
    )
    # deebot-client reports docked while the mops are washed
    await _notify(hass, entry, StateEvent(State.DOCKED))
    await _device(entry).execute_command(GetWorkState())
    await _notify(hass, entry, StateEvent(State.CLEANING))
    assert hass.states.get(VACUUM).state == "cleaning"

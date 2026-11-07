"""Messages recorded from a real T50 MAX PRO OMNI (c8rj4y, firmware 1.62.0)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

from deebot_client.events import StateEvent
from deebot_client.models import State
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ecovacs_omni.const import DOMAIN

from .test_rooms import VACUUM, _area_values, _notify


def _raw(data: Any) -> bytes:
    return json.dumps({"body": {"data": data}, "header": {"fwVer": "1.62.0"}}).encode()


def _clean_data(room_id: int | None, status: int = 2) -> bytes:
    content = [] if room_id is None else [{"id": room_id, "status": status, "type": 1}]
    return _raw({"cid": 1794056806, "content": content, "type": "freeClean"})


LAST_TIME_STATS = _raw(
    {"area": 2, "start": "1794056928", "time": 120, "type": "freeClean"}
)


async def _message(hass: HomeAssistant, entry: MockConfigEntry, name: str, raw: bytes):
    device = entry.runtime_data.devices[0]
    device._handle_message(name, raw)
    await asyncio.gather(*device.events._tasks)
    await hass.async_block_till_done()


async def test_current_room_and_last_job(
    hass: HomeAssistant, rooms_entry: MockConfigEntry
) -> None:
    """Current room and last job come from the T50 specific messages."""
    # Planned room is shown while the robot still washes its mops
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(2, status=1))
    assert hass.states.get("sensor.robbi_current_room").state == "Flur"
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(2))
    assert hass.states.get("sensor.robbi_current_room").state == "Flur"

    await _message(hass, rooms_entry, "onLastTimeStats", LAST_TIME_STATS)
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(None))
    assert hass.states.get("sensor.robbi_current_room").state == "unknown"
    assert hass.states.get("sensor.robbi_last_job").state == "finished"


async def test_last_job_manually_stopped(
    hass: HomeAssistant, rooms_entry: MockConfigEntry
) -> None:
    """A stop through Home Assistant is reported as manually stopped."""
    await _notify(hass, rooms_entry, StateEvent(State.CLEANING))
    await hass.services.async_call(
        "vacuum", "stop", {"entity_id": VACUUM}, blocking=True
    )
    await _message(hass, rooms_entry, "onLastTimeStats", LAST_TIME_STATS)
    assert hass.states.get("sensor.robbi_last_job").state == "manually_stopped"


async def test_sequential_queue_with_t50_messages(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """The room queue follows the real T50 flow.

    Recorded: the robot accepted the room right away, washed its mops in the
    station for 22 minutes (reported as docked) and only then left. The queue
    must not give up while the robot is still in the station.
    """
    mock_execute.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"entity_id": VACUUM, "rooms": ["Küche", "Flur"]},
        blocking=True,
    )
    assert _area_values(mock_execute) == ["1,1"]
    manager = rooms_entry.runtime_data.room_manager(rooms_entry.runtime_data.devices[0])

    # Robot accepts the job and washes its mops first (reported as docked)
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(1, status=1))
    await _notify(hass, rooms_entry, StateEvent(State.DOCKED))
    started = manager._queue_task is not None and not manager._queue_task.done()
    assert started

    # Leaves the station, cleans the room, room done, job ends
    await _notify(hass, rooms_entry, StateEvent(State.CLEANING))
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(1))
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(1, status=3))
    await _message(hass, rooms_entry, "onLastTimeStats", LAST_TIME_STATS)
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(None))
    assert _area_values(mock_execute) == ["1,1", "1,2"]
    assert hass.states.get("sensor.robbi_room_queue").state == "Flur"


async def test_queue_accepts_room_from_job_report_without_cleaning_state(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """The job report alone marks the room as started."""
    mock_execute.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"entity_id": VACUUM, "rooms": ["Küche", "Flur"]},
        blocking=True,
    )
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(1, status=1))
    # No CLEANING state at all, the job end still moves on to the next room
    await _message(hass, rooms_entry, "onLastTimeStats", LAST_TIME_STATS)
    assert _area_values(mock_execute) == ["1,1", "1,2"]


def _work_state(robot: str, station: str, paused: int = 0) -> bytes:
    robot_state: dict[str, Any] = {"state": robot, "trigger": "app"}
    if robot == "cleaning":
        robot_state["cleanState"] = {"cid": "122", "type": "freeClean"}
    return _raw(
        {
            "paused": paused,
            "robotState": robot_state,
            "stationState": {"state": station, "trigger": "app"},
        }
    )


async def test_state_cleaning_while_washing_mops(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A running job is shown as cleaning, also while the mops are washed."""
    entry = init_integration
    # Job accepted, mops are washed before the robot leaves (recorded T50)
    await _message(hass, entry, "onWorkState", _work_state("cleaning", "washing"))
    assert hass.states.get(VACUUM).state == "cleaning"
    assert hass.states.get("sensor.robbi_station").state == "washing_mop"
    # Charging contacts touched while washing mid-job
    await _message(hass, entry, "onChargeState", _raw({"isCharging": 1}))
    assert hass.states.get(VACUUM).state == "cleaning"
    # Paused stays paused
    await _message(
        hass, entry, "onWorkState", _work_state("cleaning", "washing", paused=1)
    )
    assert hass.states.get(VACUUM).state == "paused"
    # Job done: returning, then docked while the station empties the dust bin
    await _message(hass, entry, "onWorkState", _work_state("idle", "goCharging"))
    assert hass.states.get(VACUUM).state == "returning"
    await _message(hass, entry, "onWorkState", _work_state("idle", "emptying"))
    await _message(hass, entry, "onChargeState", _raw({"isCharging": 1}))
    assert hass.states.get(VACUUM).state == "docked"


async def test_clean_area_updates_selection(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """Rooms cleaned via vacuum.clean_area show up as selected rooms."""
    entity = hass.data["entity_components"]["vacuum"].get_entity(VACUUM)
    await entity.async_clean_segments(["m1_3", "m1_1"])
    await hass.async_block_till_done()
    assert hass.states.get("sensor.robbi_selected_rooms").state == "Wohnzimmer → Küche"
    assert hass.states.get("switch.robbi_room_wohnzimmer").state == "on"
    assert hass.states.get("switch.robbi_room_flur").state == "off"

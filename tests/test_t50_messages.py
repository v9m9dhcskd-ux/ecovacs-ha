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
    await _message(hass, rooms_entry, "onCleanDataUpdate_V3", _clean_data(2, status=1))
    assert hass.states.get("sensor.robbi_current_room").state == "unknown"
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
    """The room queue continues when the T50 reports the end of a job."""
    mock_execute.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"entity_id": VACUUM, "rooms": ["Küche", "Flur"]},
        blocking=True,
    )
    assert _area_values(mock_execute) == ["1,1"]
    # Mop pre-wash is reported as docked, then the robot starts cleaning
    await _notify(hass, rooms_entry, StateEvent(State.DOCKED))
    await _notify(hass, rooms_entry, StateEvent(State.CLEANING))
    await _message(hass, rooms_entry, "onLastTimeStats", LAST_TIME_STATS)
    assert _area_values(mock_execute) == ["1,1", "1,2"]

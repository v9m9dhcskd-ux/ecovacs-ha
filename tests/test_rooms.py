"""Tests for room selection and ordered room cleaning."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from deebot_client.events import (
    CleanJobStatus,
    ReportStatsEvent,
    StateEvent,
)
from deebot_client.models import State
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ecovacs_omni.const import DOMAIN

VACUUM = "vacuum.robbi"


def _device(entry: MockConfigEntry):
    return entry.runtime_data.devices[0]


async def _notify(hass: HomeAssistant, entry: MockConfigEntry, event: Any) -> None:
    bus = _device(entry).events
    bus.notify(event)
    await asyncio.gather(*bus._tasks)
    await hass.async_block_till_done()


def _sent(mock_execute: AsyncMock, name: str) -> list[dict[str, Any]]:
    """Return the args of all sent commands with the given name."""
    return [
        call.args[0]._args
        for call in mock_execute.call_args_list
        if name == call.args[0].NAME
    ]


def _area_values(mock_execute: AsyncMock) -> list[str]:
    return [
        args["content"]["value"]
        for args in _sent(mock_execute, "clean_V2")
        if args["content"].get("type") in ("freeClean", "spotArea")
    ]


async def test_room_switches_and_order(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """Room switches are created and the selection order is kept."""
    assert hass.states.get("switch.robbi_room_kuche") is not None
    assert hass.states.get("switch.robbi_room_wohnzimmer") is not None

    for room in ("wohnzimmer", "kuche"):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": f"switch.robbi_room_{room}"},
            blocking=True,
        )
    state = hass.states.get("sensor.robbi_selected_rooms")
    assert state.state == "Wohnzimmer → Küche"
    assert state.attributes["room_ids"] == [3, 1]
    assert hass.states.get("switch.robbi_room_kuche").attributes["order"] == 2

    # Direct mode: one command with all rooms in the selected order
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.robbi_clean_rooms_one_after_another"},
        blocking=True,
    )
    mock_execute.reset_mock()
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.robbi_clean_selected_rooms"},
        blocking=True,
    )
    assert _area_values(mock_execute) == ["1,3;1,1"]

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.robbi_clear_room_selection"},
        blocking=True,
    )
    assert hass.states.get("sensor.robbi_selected_rooms").state == "unknown"


async def test_sequential_queue(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """Rooms are sent one after another when the previous one is finished."""
    mock_execute.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"entity_id": VACUUM, "rooms": ["Flur", "küche", "3"], "fan_speed": "max"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert _sent(mock_execute, "setSpeed") == [{"speed": 1}]
    assert _area_values(mock_execute) == ["1,2"]
    queue = hass.states.get("sensor.robbi_room_queue")
    assert queue.state == "Flur"
    assert queue.attributes["remaining"] == ["Küche", "Wohnzimmer"]

    # Room 1 running and finished
    await _notify(hass, rooms_entry, StateEvent(State.CLEANING))
    await _notify(
        hass,
        rooms_entry,
        ReportStatsEvent(None, None, "freeClean", "c1", CleanJobStatus.CLEANING, []),
    )
    await _notify(
        hass,
        rooms_entry,
        ReportStatsEvent(10, 300, "freeClean", "c1", CleanJobStatus.FINISHED, []),
    )
    await _notify(hass, rooms_entry, StateEvent(State.RETURNING))
    assert _area_values(mock_execute) == ["1,2", "1,1"]
    assert hass.states.get("sensor.robbi_room_queue").state == "Küche"

    # Room 2: started and finished
    await _notify(hass, rooms_entry, StateEvent(State.CLEANING))
    await _notify(
        hass,
        rooms_entry,
        ReportStatsEvent(8, 200, "freeClean", "c2", CleanJobStatus.FINISHED, []),
    )
    assert _area_values(mock_execute) == ["1,2", "1,1", "1,3"]

    # Room 3 is stopped manually -> queue ends
    await _notify(hass, rooms_entry, StateEvent(State.RETURNING))
    await _notify(hass, rooms_entry, StateEvent(State.CLEANING))
    await _notify(
        hass,
        rooms_entry,
        ReportStatsEvent(3, 50, "freeClean", "c3", CleanJobStatus.MANUALLY_STOPPED, []),
    )
    await hass.async_block_till_done()
    queue = hass.states.get("sensor.robbi_room_queue")
    assert queue.state == "unknown"
    assert queue.attributes["running"] is False
    assert _area_values(mock_execute) == ["1,2", "1,1", "1,3"]


async def test_stop_cancels_queue(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """Stopping the vacuum cancels the queue."""
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"entity_id": VACUUM, "rooms": ["Flur", "Küche"]},
        blocking=True,
    )
    await _notify(hass, rooms_entry, StateEvent(State.CLEANING))
    assert hass.states.get(VACUUM).attributes["room_queue"]["running"] is True
    await hass.services.async_call(
        "vacuum", "stop", {"entity_id": VACUUM}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(VACUUM).attributes["room_queue"]["running"] is False
    assert _area_values(mock_execute) == ["1,2"]


async def test_unknown_room(hass: HomeAssistant, rooms_entry: MockConfigEntry) -> None:
    """Unknown rooms raise a helpful error."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "clean_rooms",
            {"entity_id": VACUUM, "rooms": ["Bad"]},
            blocking=True,
        )


async def test_get_rooms_and_select(
    hass: HomeAssistant, rooms_entry: MockConfigEntry
) -> None:
    """get_rooms returns the rooms, select_rooms sets the order."""
    await hass.services.async_call(
        DOMAIN,
        "select_rooms",
        {"entity_id": VACUUM, "rooms": ["Flur", "Küche"]},
        blocking=True,
    )
    result = await hass.services.async_call(
        DOMAIN, "get_rooms", {"entity_id": VACUUM}, blocking=True, return_response=True
    )
    data = result[VACUUM]
    assert [r["name"] for r in data["rooms"]] == ["Küche", "Flur", "Wohnzimmer"]
    assert data["selection"] == ["Flur", "Küche"]


async def test_station_action_and_clean_all(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """Station buttons and whole home cleaning send the right commands."""
    mock_execute.reset_mock()
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.robbi_wash_mops"}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN,
        "station_action",
        {"entity_id": VACUUM, "action": "dry_mop"},
        blocking=True,
    )
    assert _sent(mock_execute, "stationAction") == [
        {"act": 1, "type": 4},
        {"act": 1, "type": 2},
    ]
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.robbi_clean_whole_home"}, blocking=True
    )
    assert _sent(mock_execute, "clean_V2")[-1] == {
        "act": "start",
        "content": {"type": "auto"},
    }


async def test_clean_area_segments(
    hass: HomeAssistant, rooms_entry: MockConfigEntry
) -> None:
    """The vacuum offers its rooms as segments for vacuum.clean_area."""
    entity = hass.data["entity_components"]["vacuum"].get_entity(VACUUM)
    segments = await entity.async_get_segments()
    assert [(s.id, s.name, s.group) for s in segments] == [
        ("m1_1", "Küche", "EG"),
        ("m1_2", "Flur", "EG"),
        ("m1_3", "Wohnzimmer", "EG"),
    ]


async def test_free_clean_payload_and_passes(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """The T50 gets the app payload; passes are set with setCleanCount first."""
    mock_execute.reset_mock()
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {
            "entity_id": VACUUM,
            "rooms": ["Wohnzimmer", "Küche"],
            "sequential": False,
            "cleanings": 2,
        },
        blocking=True,
    )
    names = [call.args[0].NAME for call in mock_execute.call_args_list]
    assert names.index("setCleanCount") < names.index("clean_V2")
    assert _sent(mock_execute, "setCleanCount") == [{"count": 2}]
    assert _sent(mock_execute, "clean_V2") == [
        {"act": "start", "content": {"type": "freeClean", "value": "1,3;1,1"}}
    ]


async def test_fallback_to_spot_area(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """Robots answering 20011 "unknown type" get spotArea instead."""
    from deebot_client.command import DeviceCommandResult

    def respond(command):
        code = 0
        if (
            command.NAME == "clean_V2"
            and command._args["content"]["type"] == "freeClean"
        ):
            code = 20011
        return DeviceCommandResult(
            device_reached=True,
            raw_response={"ret": "ok", "resp": {"body": {"code": code, "msg": "x"}}},
        )

    mock_execute.reset_mock()
    mock_execute.side_effect = respond
    await hass.services.async_call(
        DOMAIN,
        "clean_rooms",
        {"entity_id": VACUUM, "rooms": ["Flur", "Küche"], "sequential": False},
        blocking=True,
    )
    assert _area_values(mock_execute) == ["1,2;1,1", "2,1"]
    assert rooms_entry.runtime_data.room_manager(_device(rooms_entry)).clean_type == (
        "spotArea"
    )
    # The detected type is remembered
    mock_execute.reset_mock()
    await hass.services.async_call(
        DOMAIN, "clean_rooms", {"entity_id": VACUUM, "rooms": ["Flur"]}, blocking=True
    )
    assert _area_values(mock_execute) == ["2"]


async def test_rejected_room_clean_raises(
    hass: HomeAssistant, rooms_entry: MockConfigEntry, mock_execute: AsyncMock
) -> None:
    """Other error codes are shown to the user."""
    from deebot_client.command import DeviceCommandResult
    from homeassistant.exceptions import HomeAssistantError

    mock_execute.return_value = DeviceCommandResult(
        device_reached=True,
        raw_response={"ret": "ok", "resp": {"body": {"code": 500, "msg": "busy"}}},
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "clean_rooms",
            {"entity_id": VACUUM, "rooms": ["Flur"]},
            blocking=True,
        )

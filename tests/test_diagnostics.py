"""Diagnostics tests."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import JSONEncoder
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ecovacs_omni.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Diagnostics can be created and serialized without secrets."""
    data = await async_get_config_entry_diagnostics(hass, init_integration)
    text = json.dumps(data, cls=JSONEncoder)
    assert "secret" not in text
    assert "user@example.com" not in text
    device = data["devices"][0]
    assert device["model_class"] == "c8rj4y"
    assert "clean.action" in device["capabilities"]
    assert device["rooms"]["clean_type"] == "freeClean"


async def test_diagnostics_records_messages(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Incoming robot messages and command responses end up in the diagnostics."""
    device = init_integration.runtime_data.devices[0]
    device._handle_message(
        "onCleanDataUpdate_V3", b'{"body": {"data": {"type": "freeClean", "id": 2}}}'
    )
    for _ in range(200):
        device._handle_message("onWorkProgressReport", b'{"body": {"data": {}}}')
    await hass.async_block_till_done()
    data = await async_get_config_entry_diagnostics(hass, init_integration)
    recorded = data["devices"][0]["recorded"]
    assert recorded["message_counts"]["onCleanDataUpdate_V3"] == 1
    assert recorded["messages"][-1]["data"]["body"]["data"]["type"] == "freeClean"
    names = {c["name"] for c in recorded["commands"]}
    # Rare messages survive a flood of progress reports
    latest = recorded["latest_by_name"]["onCleanDataUpdate_V3"][-1]
    assert latest["data"]["body"]["data"]["id"] == 2
    assert len(recorded["latest_by_name"]["onWorkProgressReport"]) == 3
    assert {"getWorkState", "getChargeState"} <= names
    # getCleanInfo_V2 answers "idle" on the T50 and must not be probed
    assert "getCleanInfo_V2" not in names
    assert set(data["devices"][0]["live_responses"]) >= {"getWorkState"}

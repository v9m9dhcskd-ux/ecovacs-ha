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

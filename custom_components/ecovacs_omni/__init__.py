"""Ecovacs Omni - full control of Ecovacs Deebot (Omni) robots."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .controller import EcovacsOmniConfigEntry, EcovacsOmniController
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VACUUM,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EcovacsOmniConfigEntry) -> bool:
    """Set up Ecovacs Omni from a config entry."""
    controller = EcovacsOmniController(hass, entry)
    try:
        await controller.initialize()
    except Exception:
        await controller.teardown()
        raise
    entry.async_on_unload(controller.teardown)
    entry.runtime_data = controller
    _async_remove_obsolete_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: EcovacsOmniConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# Entities of earlier versions (room selection switches and buttons, the
# "selected rooms" sensor, replaced by "rooms of the job")
_OBSOLETE_SUFFIXES = (
    "_clean_selected_rooms",
    "_clear_room_selection",
    "_selected_rooms",
)


def _async_remove_obsolete_entities(
    hass: HomeAssistant, entry: EcovacsOmniConfigEntry
) -> None:
    registry = er.async_get(hass)
    dids = [device.device_info["did"] for device in entry.runtime_data.devices]
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = reg_entry.unique_id
        if any(
            unique_id.startswith(f"{did}_room_") and reg_entry.domain == "switch"
            for did in dids
        ) or unique_id.endswith(_OBSOLETE_SUFFIXES):
            registry.async_remove(reg_entry.entity_id)

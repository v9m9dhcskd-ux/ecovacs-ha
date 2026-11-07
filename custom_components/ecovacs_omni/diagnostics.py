"""Diagnostics for Ecovacs Omni."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_ACCOUNT_CREDENTIALS
from .controller import EcovacsOmniConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_ACCOUNT_CREDENTIALS,
    "did",
    "name",
    "uid",
    "resource",
    "token",
    "mac",
    "ip",
    "ssid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EcovacsOmniConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    controller = entry.runtime_data
    devices = []
    for device in controller.devices:
        manager = controller.room_manager(device)
        info = dict(device.device_info)
        devices.append(
            {
                "device_info": async_redact_data(info, TO_REDACT),
                "model_class": info.get("class"),
                "capabilities": _capability_names(device.capabilities),
                "rooms": {
                    "map_id": manager.map_id,
                    "rooms": [asdict(room) for room in manager.rooms.values()],
                    "selection": manager.selection,
                    "sequential": manager.sequential,
                    "queue_running": manager.queue_running,
                },
            }
        )
    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "devices": devices,
    }


def _capability_names(obj: Any, prefix: str = "") -> list[str]:
    """List which capabilities a device has (only names, no values)."""
    names: list[str] = []
    if not is_dataclass(obj):
        return names
    for field_name in obj.__dataclass_fields__:
        value = getattr(obj, field_name)
        if value is None:
            continue
        path = f"{prefix}{field_name}"
        names.append(path)
        if is_dataclass(value):
            names.extend(_capability_names(value, f"{path}."))
    return names

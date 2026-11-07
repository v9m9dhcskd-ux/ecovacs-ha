"""Diagnostics for Ecovacs Omni."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
import logging
from typing import Any

from deebot_client.commands.json.charge_state import GetChargeState
from deebot_client.commands.json.stats import GetStats
from deebot_client.commands.json.work_state import GetWorkState
from deebot_client.device import Device
from deebot_client.events import StateEvent
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_ACCOUNT_CREDENTIALS
from .controller import EcovacsOmniConfigEntry

_LOGGER = logging.getLogger(__name__)

# Keys hidden in raw robot data (room names stay visible on purpose)
RAW_REDACT = {"did", "uid", "resource", "token", "mac", "ip", "ssid", "sn", "wifiName"}

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
        recorder = controller.recorder(device)
        live = await _probe(device)
        state = device.events.get_last_event(StateEvent)
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
                    "clean_type": manager.clean_type,
                    "queue_running": manager.queue_running,
                },
                "state": state.state.name if state else None,
                "live_responses": async_redact_data(live, RAW_REDACT),
                "recorded": async_redact_data(
                    recorder.as_dict() if recorder else {}, RAW_REDACT
                ),
            }
        )
    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "devices": devices,
    }


def _capability_names(obj: Any, prefix: str = "") -> list[str]:
    """List which capabilities a device has (only names, no values)."""
    names: list[str] = []
    if not _is_instance(obj):
        return names
    for field_name in obj.__dataclass_fields__:
        value = getattr(obj, field_name)
        if value is None:
            continue
        path = f"{prefix}{field_name}"
        names.append(path)
        if _is_instance(value):
            names.extend(_capability_names(value, f"{path}."))
    return names


def _is_instance(value: Any) -> bool:
    """Dataclass instance (is_dataclass is also True for dataclass classes)."""
    return is_dataclass(value) and not isinstance(value, type)


async def _probe(device: Device) -> dict[str, Any]:
    """Ask the robot for its current state and return the raw answers."""
    result: dict[str, Any] = {}
    # Only commands whose answers deebot-client interprets correctly for the
    # T50: getCleanInfo_V2 always answers "idle" there and would set the
    # vacuum to idle in the middle of a cleaning.
    for command in (GetWorkState(), GetChargeState(), GetStats()):
        try:
            async with asyncio.timeout(15):
                result[command.NAME] = await device.execute_command(command)
        except Exception as err:
            _LOGGER.debug("Diagnostics probe %s failed", command.NAME, exc_info=True)
            result[command.NAME] = f"error: {err!r}"
    return result

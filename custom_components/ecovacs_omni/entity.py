"""Base entities for Ecovacs Omni."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from deebot_client.capabilities import Capabilities
from deebot_client.device import Device
from deebot_client.events import AvailabilityEvent
from deebot_client.events.base import Event
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN


def device_info_for(device: Device) -> DeviceInfo:
    """Return the Home Assistant device info for a robot."""
    info_raw = device.device_info
    info = DeviceInfo(
        identifiers={(DOMAIN, info_raw["did"])},
        manufacturer="Ecovacs",
        serial_number=info_raw["name"],
        model_id=info_raw["class"],
    )
    if nick := info_raw.get("nick"):
        info["name"] = nick
    if model := info_raw.get("deviceName"):
        info["model"] = model
    if fw_version := device.fw_version:
        info["sw_version"] = fw_version
    if mac := device.mac:
        info["connections"] = {(dr.CONNECTION_NETWORK_MAC, mac)}
    return info


class EcovacsOmniEntity[CapabilityT](Entity):
    """Base entity bound to one robot and (optionally) one capability."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _always_available: bool = False

    def __init__(self, device: Device, capability: CapabilityT, **kwargs: Any) -> None:
        """Initialize the entity."""
        super().__init__(**kwargs)
        self._device = device
        self._capability = capability
        self._attr_unique_id = (
            f"{device.device_info['did']}_{self.entity_description.key}"
        )
        self._attr_device_info = device_info_for(device)
        self._subscribed_events: set[type[Event]] = set()

    async def async_added_to_hass(self) -> None:
        """Subscribe to availability."""
        await super().async_added_to_hass()
        if not self._always_available:

            async def on_available(event: AvailabilityEvent) -> None:
                self._attr_available = event.available
                self.async_write_ha_state()

            self._subscribe(AvailabilityEvent, on_available)

    def _subscribe[EventT: Event](
        self,
        event_type: type[EventT],
        callback: Callable[[EventT], Coroutine[Any, Any, None]],
    ) -> None:
        self._subscribed_events.add(event_type)
        self.async_on_remove(self._device.events.subscribe(event_type, callback))

    async def async_update(self) -> None:
        """Request a refresh (only used by homeassistant.update_entity)."""
        for event_type in self._subscribed_events:
            self._device.events.request_refresh(event_type)


class EcovacsOmniDescriptionEntity[CapabilityT](EcovacsOmniEntity[CapabilityT]):
    """Entity using an entity description."""

    def __init__(
        self,
        device: Device,
        capability: CapabilityT,
        entity_description: EntityDescription,
        **kwargs: Any,
    ) -> None:
        """Initialize the entity."""
        self.entity_description = entity_description
        super().__init__(device, capability, **kwargs)


@dataclass(kw_only=True, frozen=True)
class EcovacsOmniCapabilityEntityDescription[CapabilityT](EntityDescription):
    """Entity description with a capability lookup."""

    capability_fn: Callable[[Capabilities], CapabilityT | None]


def get_supported_entities(
    devices: list[Device],
    entity_class: type[EcovacsOmniDescriptionEntity],
    descriptions: tuple[EcovacsOmniCapabilityEntityDescription, ...],
) -> list[EcovacsOmniEntity]:
    """Create all entities whose capability the device has."""
    return [
        entity_class(device, capability, description)
        for device in devices
        for description in descriptions
        if (capability := description.capability_fn(device.capabilities)) is not None
    ]

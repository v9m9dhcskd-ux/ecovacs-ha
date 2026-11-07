"""Map image for Ecovacs Omni."""

from __future__ import annotations

from typing import cast

from deebot_client.capabilities import CapabilityMap
from deebot_client.device import Device
from deebot_client.events.map import CachedMapInfoEvent, MapChangedEvent
from deebot_client.map import Map
from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import EcovacsOmniConfigEntry
from .entity import EcovacsOmniEntity
from .map_trace import MapTraceUpdater


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcovacsOmniConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the map image."""
    async_add_entities(
        EcovacsOmniMap(device, caps, hass)
        for device in entry.runtime_data.devices
        if (caps := device.capabilities.map)
    )


class EcovacsOmniMap(EcovacsOmniEntity[CapabilityMap], ImageEntity):
    """Live map as SVG."""

    _attr_content_type = "image/svg+xml"
    entity_description = EntityDescription(key="map", translation_key="map")

    def __init__(
        self, device: Device, capability: CapabilityMap, hass: HomeAssistant
    ) -> None:
        """Initialize the map."""
        super().__init__(device, capability, hass=hass)
        self._attr_extra_state_attributes = {}
        self._map = cast(Map, device.map)
        self._trace_updater = MapTraceUpdater(hass, device, capability)

    def image(self) -> bytes | None:
        """Return the SVG map."""
        if svg := self._map.get_svg_map():
            return svg.encode()
        return None

    async def async_added_to_hass(self) -> None:
        """Subscribe to map changes."""
        await super().async_added_to_hass()

        async def on_info(event: CachedMapInfoEvent) -> None:
            for map_obj in event.maps:
                if map_obj.using:
                    self._attr_extra_state_attributes["map_name"] = map_obj.name

        async def on_changed(event: MapChangedEvent) -> None:
            self._attr_image_last_updated = event.when
            self.async_write_ha_state()

        self._subscribe(self._capability.cached_info.event, on_info)
        self._subscribe(self._capability.changed.event, on_changed)

        # Keep the driven line up to date while the robot is cleaning
        self._trace_updater.async_start()
        self.async_on_remove(self._trace_updater.async_stop)

    async def async_update(self) -> None:
        """Refresh the map (homeassistant.update_entity)."""
        await super().async_update()
        self._map.refresh()

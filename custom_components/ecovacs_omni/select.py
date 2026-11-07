"""Select entities for Ecovacs Omni."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deebot_client.capabilities import CapabilityMap, CapabilitySet, CapabilitySetTypes
from deebot_client.command import CommandWithMessageHandling
from deebot_client.device import Device
from deebot_client.events import EfficiencyModeEvent, WorkModeEvent, auto_empty
from deebot_client.events.base import Event
from deebot_client.events.map import CachedMapInfoEvent, MajorMapEvent
from deebot_client.events.water_info import WaterAmountEvent
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import EcovacsOmniConfigEntry
from .entity import (
    EcovacsOmniCapabilityEntityDescription,
    EcovacsOmniDescriptionEntity,
    EcovacsOmniEntity,
    get_supported_entities,
)


@dataclass(kw_only=True, frozen=True)
class EcovacsOmniSelectEntityDescription[EventT: Event](
    EcovacsOmniCapabilityEntityDescription, SelectEntityDescription
):
    """Select description."""

    current_option_fn: Callable[[EventT], str | None]
    options_fn: Callable[[CapabilitySetTypes], list[str]] = lambda cap: [
        t.name.lower() for t in cap.types
    ]
    set_option_fn: Callable[[CapabilitySetTypes, str], CommandWithMessageHandling] = (
        lambda cap, option: cap.set(option)
    )


ENTITY_DESCRIPTIONS: tuple[EcovacsOmniSelectEntityDescription, ...] = (
    EcovacsOmniSelectEntityDescription[WorkModeEvent](
        key="work_mode",
        translation_key="work_mode",
        capability_fn=lambda caps: caps.clean.work_mode,
        current_option_fn=lambda e: e.mode.name.lower(),
    ),
    EcovacsOmniSelectEntityDescription[WaterAmountEvent](
        key="water_amount",
        translation_key="water_amount",
        capability_fn=lambda caps: (
            caps.water.amount
            if caps.water and isinstance(caps.water.amount, CapabilitySetTypes)
            else None
        ),
        current_option_fn=lambda e: e.value.name.lower(),
    ),
    EcovacsOmniSelectEntityDescription[auto_empty.AutoEmptyEvent](
        key="auto_empty_frequency",
        translation_key="auto_empty_frequency",
        capability_fn=lambda caps: caps.station.auto_empty if caps.station else None,
        current_option_fn=lambda e: e.frequency.name.lower() if e.frequency else None,
        set_option_fn=lambda cap, option: cap.set(None, option),
        entity_category=EntityCategory.CONFIG,
    ),
    EcovacsOmniSelectEntityDescription[EfficiencyModeEvent](
        key="efficiency_mode",
        translation_key="efficiency_mode",
        capability_fn=lambda caps: caps.settings.efficiency_mode,
        current_option_fn=lambda e: e.efficiency.name.lower(),
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcovacsOmniConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    controller = entry.runtime_data
    entities: list[EcovacsOmniEntity] = get_supported_entities(
        controller.devices, EcovacsOmniSelect, ENTITY_DESCRIPTIONS
    )
    entities.extend(
        ActiveMapSelect(device, map_cap, major)
        for device in controller.devices
        if (map_cap := device.capabilities.map)
        and isinstance(major := map_cap.major, CapabilitySet)
    )
    async_add_entities(entities)


class EcovacsOmniSelect[EventT: Event](
    EcovacsOmniDescriptionEntity[CapabilitySetTypes[EventT, [str], str]], SelectEntity
):
    """A select backed by a capability."""

    entity_description: EcovacsOmniSelectEntityDescription
    _attr_current_option: str | None = None

    def __init__(
        self,
        device: Device,
        capability: CapabilitySetTypes[EventT, [str], str],
        entity_description: EcovacsOmniSelectEntityDescription,
        **kwargs: Any,
    ) -> None:
        """Initialize the select."""
        super().__init__(device, capability, entity_description, **kwargs)
        self._attr_options = entity_description.options_fn(capability)

    async def async_added_to_hass(self) -> None:
        """Subscribe to the event."""
        await super().async_added_to_hass()

        async def on_event(event: EventT) -> None:
            if (option := self.entity_description.current_option_fn(event)) is not None:
                self._attr_current_option = option
                self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)

    async def async_select_option(self, option: str) -> None:
        """Change the option."""
        await self._device.execute_command(
            self.entity_description.set_option_fn(self._capability, option)
        )


class ActiveMapSelect(EcovacsOmniEntity[CapabilityMap], SelectEntity):
    """Select the active map (multi floor)."""

    entity_description = SelectEntityDescription(
        key="active_map",
        translation_key="active_map",
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(
        self,
        device: Device,
        capability: CapabilityMap,
        major: CapabilitySet[MajorMapEvent, [str]],
    ) -> None:
        """Initialize the select."""
        super().__init__(device, capability)
        self._major = major
        self._option_to_id: dict[str, str] = {}
        self._id_to_option: dict[str, str] = {}
        self._attr_options = []
        self._attr_current_option = None

    def _handle_cached_map(self, event: CachedMapInfoEvent) -> None:
        self._option_to_id.clear()
        self._id_to_option.clear()
        current = None
        for map_info in event.maps:
            name = map_info.name or map_info.id
            self._id_to_option[map_info.id] = name
            self._option_to_id[name] = map_info.id
            if map_info.using:
                current = name
        self._attr_current_option = current
        self._attr_options = sorted(
            self._option_to_id, key=lambda x: (x.isdigit(), x.lower())
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the map events."""
        await super().async_added_to_hass()

        async def on_cached_map(event: CachedMapInfoEvent) -> None:
            self._handle_cached_map(event)
            self.async_write_ha_state()

        async def on_major_map(event: MajorMapEvent) -> None:
            self._attr_current_option = self._id_to_option.get(event.map_id)
            self.async_write_ha_state()

        self._subscribe(self._capability.cached_info.event, on_cached_map)
        self._subscribe(self._major.event, on_major_map)

    async def async_select_option(self, option: str) -> None:
        """Switch the map."""
        await self._device.execute_command(self._major.set(self._option_to_id[option]))

"""Number entities for Ecovacs Omni."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from deebot_client.capabilities import CapabilityNumber, CapabilitySet
from deebot_client.device import Device
from deebot_client.events import CleanCountEvent, VolumeEvent
from deebot_client.events.base import Event
from deebot_client.events.water_info import WaterCustomAmountEvent
from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import EcovacsOmniConfigEntry
from .entity import (
    EcovacsOmniCapabilityEntityDescription,
    EcovacsOmniDescriptionEntity,
    get_supported_entities,
)


@dataclass(kw_only=True, frozen=True)
class EcovacsOmniNumberEntityDescription[EventT: Event](
    EcovacsOmniCapabilityEntityDescription, NumberEntityDescription
):
    """Number description."""

    value_fn: Callable[[EventT], float | None]
    native_max_value_fn: Callable[[EventT], float | int | None] = lambda _: None


ENTITY_DESCRIPTIONS: tuple[EcovacsOmniNumberEntityDescription, ...] = (
    EcovacsOmniNumberEntityDescription[CleanCountEvent](
        key="clean_count",
        translation_key="clean_count",
        capability_fn=lambda caps: caps.clean.count,
        value_fn=lambda e: e.count,
        native_min_value=1,
        native_max_value=4,
        native_step=1,
        mode=NumberMode.BOX,
    ),
    EcovacsOmniNumberEntityDescription[WaterCustomAmountEvent](
        key="water_amount",
        translation_key="water_amount",
        capability_fn=lambda caps: (
            caps.water.amount
            if caps.water and isinstance(caps.water.amount, CapabilityNumber)
            else None
        ),
        value_fn=lambda e: e.value,
        native_step=1,
        mode=NumberMode.SLIDER,
    ),
    EcovacsOmniNumberEntityDescription[VolumeEvent](
        key="volume",
        translation_key="volume",
        capability_fn=lambda caps: caps.settings.volume,
        value_fn=lambda e: e.volume,
        native_max_value_fn=lambda e: e.maximum,
        native_min_value=0,
        native_max_value=10,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcovacsOmniConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the numbers."""
    async_add_entities(
        get_supported_entities(
            entry.runtime_data.devices, EcovacsOmniNumber, ENTITY_DESCRIPTIONS
        )
    )


class EcovacsOmniNumber[EventT: Event](
    EcovacsOmniDescriptionEntity[CapabilitySet[EventT, [int]]], NumberEntity
):
    """A number backed by a capability."""

    entity_description: EcovacsOmniNumberEntityDescription

    def __init__(
        self,
        device: Device,
        capability: CapabilitySet[EventT, [int]],
        entity_description: EcovacsOmniNumberEntityDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(device, capability, entity_description)
        if isinstance(capability, CapabilityNumber):
            self._attr_native_min_value = capability.min
            self._attr_native_max_value = capability.max

    async def async_added_to_hass(self) -> None:
        """Subscribe to the event."""
        await super().async_added_to_hass()

        async def on_event(event: EventT) -> None:
            self._attr_native_value = self.entity_description.value_fn(event)
            if maximum := self.entity_description.native_max_value_fn(event):
                self._attr_native_max_value = maximum
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        await self._device.execute_command(self._capability.set(int(value)))

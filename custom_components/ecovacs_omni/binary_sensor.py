"""Binary sensors for Ecovacs Omni."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from deebot_client.capabilities import CapabilityEvent
from deebot_client.events import Event, StateEvent
from deebot_client.events.water_info import MopAttachedEvent
from deebot_client.models import State
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import EcovacsOmniConfigEntry
from .entity import (
    EcovacsOmniCapabilityEntityDescription,
    EcovacsOmniDescriptionEntity,
    get_supported_entities,
)


@dataclass(kw_only=True, frozen=True)
class EcovacsOmniBinarySensorEntityDescription[EventT: Event](
    EcovacsOmniCapabilityEntityDescription, BinarySensorEntityDescription
):
    """Binary sensor description."""

    value_fn: Callable[[EventT], bool | None]


ENTITY_DESCRIPTIONS: tuple[EcovacsOmniBinarySensorEntityDescription, ...] = (
    EcovacsOmniBinarySensorEntityDescription[MopAttachedEvent](
        key="water_mop_attached",
        translation_key="water_mop_attached",
        capability_fn=lambda caps: caps.water.mop_attached if caps.water else None,
        value_fn=lambda e: e.value,
    ),
    EcovacsOmniBinarySensorEntityDescription[StateEvent](
        key="docked",
        translation_key="docked",
        capability_fn=lambda caps: caps.state,
        value_fn=lambda e: e.state is State.DOCKED,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcovacsOmniConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_add_entities(
        get_supported_entities(
            entry.runtime_data.devices, EcovacsOmniBinarySensor, ENTITY_DESCRIPTIONS
        )
    )


class EcovacsOmniBinarySensor[EventT: Event](
    EcovacsOmniDescriptionEntity[CapabilityEvent[EventT]], BinarySensorEntity
):
    """A binary sensor backed by an event."""

    entity_description: EcovacsOmniBinarySensorEntityDescription

    async def async_added_to_hass(self) -> None:
        """Subscribe to the event."""
        await super().async_added_to_hass()

        async def on_event(event: EventT) -> None:
            self._attr_is_on = self.entity_description.value_fn(event)
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)

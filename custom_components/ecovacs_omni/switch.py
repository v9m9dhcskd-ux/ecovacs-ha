"""Switches for Ecovacs Omni (robot settings, room queue mode)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deebot_client.capabilities import CapabilitySet
from deebot_client.command import CommandWithMessageHandling
from deebot_client.device import Device
from deebot_client.events import EnableEvent
from deebot_client.events.auto_empty import AutoEmptyEvent
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
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
from .rooms import RoomManager


@dataclass(kw_only=True, frozen=True)
class EcovacsOmniSwitchEntityDescription(
    EcovacsOmniCapabilityEntityDescription, SwitchEntityDescription
):
    """Switch description."""

    set_fn: Callable[[CapabilitySet, bool], CommandWithMessageHandling] = (
        lambda cap, enabled: cap.set(enabled)
    )


def _setting(
    key: str, fn: Callable[[Any], Any], *, enabled: bool = True
) -> EcovacsOmniSwitchEntityDescription:
    return EcovacsOmniSwitchEntityDescription(
        key=key,
        translation_key=key,
        capability_fn=fn,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=enabled,
    )


ENTITY_DESCRIPTIONS: tuple[EcovacsOmniSwitchEntityDescription, ...] = (
    _setting("continuous_cleaning", lambda c: c.clean.continuous),
    _setting("carpet_auto_fan_boost", lambda c: c.settings.carpet_auto_fan_boost),
    _setting("clean_preference", lambda c: c.clean.preference),
    _setting("child_lock", lambda c: c.settings.child_lock),
    _setting("true_detect", lambda c: c.settings.true_detect),
    _setting("advanced_mode", lambda c: c.settings.advanced_mode, enabled=False),
    _setting("voice_assistant", lambda c: c.settings.voice_assistant, enabled=False),
    _setting("ota", lambda c: c.settings.ota, enabled=False),
    _setting(
        "multimap_state", lambda c: c.map.multi_state if c.map else None, enabled=False
    ),
    EcovacsOmniSwitchEntityDescription(
        key="auto_empty",
        translation_key="auto_empty",
        capability_fn=lambda c: c.station.auto_empty if c.station else None,
        # Passing no frequency keeps the one set with the select entity
        set_fn=lambda cap, enabled: cap.set(enabled, None),
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcovacsOmniConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    controller = entry.runtime_data
    entities: list[EcovacsOmniEntity] = get_supported_entities(
        controller.devices, EcovacsOmniSwitch, ENTITY_DESCRIPTIONS
    )

    for device in controller.devices:
        if not (device.capabilities.map and device.capabilities.clean.action.area):
            continue
        entities.append(SequentialRoomsSwitch(device, controller.room_manager(device)))

    async_add_entities(entities)


class EcovacsOmniSwitch(
    EcovacsOmniDescriptionEntity[CapabilitySet[EnableEvent | AutoEmptyEvent, ...]],
    SwitchEntity,
):
    """A robot setting."""

    entity_description: EcovacsOmniSwitchEntityDescription
    _attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Subscribe to the setting."""
        await super().async_added_to_hass()

        async def on_event(event: EnableEvent | AutoEmptyEvent) -> None:
            self._attr_is_on = event.enabled
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the setting."""
        await self._device.execute_command(
            self.entity_description.set_fn(self._capability, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the setting."""
        await self._device.execute_command(
            self.entity_description.set_fn(self._capability, False)
        )


class SequentialRoomsSwitch(EcovacsOmniEntity[None], SwitchEntity):
    """On: rooms are cleaned strictly one after another in the given order."""

    _always_available = True
    entity_description = SwitchEntityDescription(
        key="sequential_rooms",
        translation_key="sequential_rooms",
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, device: Device, manager: RoomManager) -> None:
        """Initialize the switch."""
        super().__init__(device, None)
        self._manager = manager

    async def async_added_to_hass(self) -> None:
        """Listen to the room manager."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._manager.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        """Return if sequential mode is on."""
        return self._manager.sequential

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable sequential mode."""
        self._manager.async_set_sequential(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable sequential mode."""
        self._manager.async_set_sequential(False)

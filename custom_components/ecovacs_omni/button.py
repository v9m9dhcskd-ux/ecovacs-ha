"""Buttons for Ecovacs Omni."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from deebot_client.capabilities import CapabilityLifeSpan
from deebot_client.commands import StationAction
from deebot_client.commands.json.station_action import (
    StationAction as StationActionCommand,
)
from deebot_client.device import Device
from deebot_client.events import LifeSpan
from deebot_client.models import CleanAction
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import STATION_ACTIONS, SUPPORTED_LIFESPANS
from .controller import EcovacsOmniConfigEntry
from .entity import EcovacsOmniEntity
from .rooms import RoomManager


@dataclass(kw_only=True, frozen=True)
class RoomButtonDescription(ButtonEntityDescription):
    """Description for buttons acting on the room manager."""

    press_fn: Callable[[RoomManager, Device], Awaitable[None]]


async def _clean_all(manager: RoomManager, device: Device) -> None:
    await manager.async_cancel_queue()
    await device.execute_command(
        device.capabilities.clean.action.command(CleanAction.START)
    )


async def _clean_selected(manager: RoomManager, device: Device) -> None:
    await manager.async_clean(manager.selection)


async def _clear_selection(manager: RoomManager, device: Device) -> None:
    manager.async_set_selection([])


async def _cancel_queue(manager: RoomManager, device: Device) -> None:
    await manager.async_cancel_queue()


CLEAN_ALL = RoomButtonDescription(
    key="clean_all", translation_key="clean_all", press_fn=_clean_all
)

ROOM_BUTTONS: tuple[RoomButtonDescription, ...] = (
    RoomButtonDescription(
        key="clean_selected_rooms",
        translation_key="clean_selected_rooms",
        press_fn=_clean_selected,
    ),
    RoomButtonDescription(
        key="clear_room_selection",
        translation_key="clear_room_selection",
        press_fn=_clear_selection,
    ),
    RoomButtonDescription(
        key="cancel_room_queue",
        translation_key="cancel_room_queue",
        press_fn=_cancel_queue,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcovacsOmniConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    controller = entry.runtime_data
    entities: list[EcovacsOmniEntity] = []
    for device in controller.devices:
        caps = device.capabilities
        manager = controller.room_manager(device)
        entities.append(RoomManagerButton(device, manager, CLEAN_ALL))
        if caps.map and caps.clean.action.area:
            entities.extend(
                RoomManagerButton(device, manager, description)
                for description in ROOM_BUTTONS
            )
        if caps.station:
            entities.extend(
                StationActionButton(device, action, action in caps.station.action.types)
                for action in STATION_ACTIONS
            )
        entities.extend(
            ResetLifespanButton(device, caps.life_span, component)
            for component in SUPPORTED_LIFESPANS
            if component in caps.life_span.types
        )
        if caps.map and caps.map.relocation:
            entities.append(RelocateButton(device, caps.map.relocation))
    async_add_entities(entities)


class RoomManagerButton(EcovacsOmniEntity[None], ButtonEntity):
    """Button acting on the room manager."""

    entity_description: RoomButtonDescription

    def __init__(
        self, device: Device, manager: RoomManager, description: RoomButtonDescription
    ) -> None:
        """Initialize the button."""
        self.entity_description = description
        super().__init__(device, None)
        self._manager = manager

    async def async_press(self) -> None:
        """Handle the press."""
        await self.entity_description.press_fn(self._manager, self._device)


class StationActionButton(EcovacsOmniEntity[None], ButtonEntity):
    """Run a station action."""

    def __init__(self, device: Device, action: StationAction, listed: bool) -> None:
        """Initialize the button."""
        key = f"station_action_{action.name.lower()}"
        self.entity_description = ButtonEntityDescription(key=key, translation_key=key)
        super().__init__(device, None)
        self._action = action
        # Actions not listed by deebot-client for this model are still offered,
        # they are marked so it is visible that they are untested.
        self._attr_extra_state_attributes = {"listed_by_library": listed}

    async def async_press(self) -> None:
        """Run the station action."""
        await self._device.execute_command(StationActionCommand(self._action))


class ResetLifespanButton(EcovacsOmniEntity[CapabilityLifeSpan], ButtonEntity):
    """Reset a consumable after replacing it."""

    def __init__(
        self, device: Device, capability: CapabilityLifeSpan, component: LifeSpan
    ) -> None:
        """Initialize the button."""
        key = f"reset_lifespan_{component.name.lower()}"
        self.entity_description = ButtonEntityDescription(
            key=key,
            translation_key=key,
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=False,
        )
        super().__init__(device, capability)
        self._component = component

    async def async_press(self) -> None:
        """Reset the lifespan."""
        await self._device.execute_command(self._capability.reset(self._component))


class RelocateButton(EcovacsOmniEntity, ButtonEntity):
    """Let the robot relocate itself on the map."""

    entity_description = ButtonEntityDescription(
        key="relocate",
        translation_key="relocate",
        entity_category=EntityCategory.CONFIG,
    )

    async def async_press(self) -> None:
        """Relocate."""
        await self._device.execute_command(self._capability.execute())

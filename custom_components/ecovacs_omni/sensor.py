"""Sensors for Ecovacs Omni."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deebot_client.capabilities import CapabilityEvent, CapabilityLifeSpan
from deebot_client.device import Device
from deebot_client.events import (
    BatteryEvent,
    CleanJobStatus,
    ErrorEvent,
    Event,
    LifeSpanEvent,
    NetworkInfoEvent,
    ReportStatsEvent,
    StatsEvent,
    TotalStatsEvent,
    station,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import SUPPORTED_LIFESPANS
from .controller import EcovacsOmniConfigEntry
from .entity import (
    EcovacsOmniCapabilityEntityDescription,
    EcovacsOmniDescriptionEntity,
    EcovacsOmniEntity,
    get_supported_entities,
)
from .rooms import RoomManager


@dataclass(kw_only=True, frozen=True)
class EcovacsOmniSensorEntityDescription[EventT: Event](
    EcovacsOmniCapabilityEntityDescription, SensorEntityDescription
):
    """Sensor description."""

    value_fn: Callable[[EventT], StateType]


_JOB_STATUS = {
    CleanJobStatus.CLEANING: "cleaning",
    CleanJobStatus.FINISHED: "finished",
    CleanJobStatus.FINISHED_WITH_WARNINGS: "finished_with_warnings",
    CleanJobStatus.MANUALLY_STOPPED: "manually_stopped",
}

ENTITY_DESCRIPTIONS: tuple[EcovacsOmniSensorEntityDescription, ...] = (
    EcovacsOmniSensorEntityDescription[BatteryEvent](
        key="battery_level",
        capability_fn=lambda caps: caps.battery,
        value_fn=lambda e: e.value,
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    EcovacsOmniSensorEntityDescription[StatsEvent](
        key="stats_area",
        translation_key="stats_area",
        capability_fn=lambda caps: caps.stats.clean,
        value_fn=lambda e: e.area,
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
    ),
    EcovacsOmniSensorEntityDescription[StatsEvent](
        key="stats_time",
        translation_key="stats_time",
        capability_fn=lambda caps: caps.stats.clean,
        value_fn=lambda e: e.time,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    EcovacsOmniSensorEntityDescription[ReportStatsEvent](
        key="last_job",
        translation_key="last_job",
        capability_fn=lambda caps: caps.stats.report,
        value_fn=lambda e: _JOB_STATUS.get(e.status),
        device_class=SensorDeviceClass.ENUM,
        options=list(_JOB_STATUS.values()),
    ),
    EcovacsOmniSensorEntityDescription[TotalStatsEvent](
        key="total_stats_area",
        translation_key="total_stats_area",
        capability_fn=lambda caps: caps.stats.total,
        value_fn=lambda e: e.area,
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    EcovacsOmniSensorEntityDescription[TotalStatsEvent](
        key="total_stats_time",
        translation_key="total_stats_time",
        capability_fn=lambda caps: caps.stats.total,
        value_fn=lambda e: e.time,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    EcovacsOmniSensorEntityDescription[TotalStatsEvent](
        key="total_stats_cleanings",
        translation_key="total_stats_cleanings",
        capability_fn=lambda caps: caps.stats.total,
        value_fn=lambda e: e.cleanings,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    EcovacsOmniSensorEntityDescription[station.StationEvent](
        key="station_state",
        translation_key="station_state",
        capability_fn=lambda caps: caps.station.state if caps.station else None,
        value_fn=lambda e: e.state.name.lower(),
        device_class=SensorDeviceClass.ENUM,
        options=[state.name.lower() for state in station.State],
    ),
    EcovacsOmniSensorEntityDescription[NetworkInfoEvent](
        key="network_ip",
        translation_key="network_ip",
        capability_fn=lambda caps: caps.network,
        value_fn=lambda e: e.ip,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    EcovacsOmniSensorEntityDescription[NetworkInfoEvent](
        key="network_rssi",
        translation_key="network_rssi",
        capability_fn=lambda caps: caps.network,
        value_fn=lambda e: e.rssi,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    EcovacsOmniSensorEntityDescription[NetworkInfoEvent](
        key="network_ssid",
        translation_key="network_ssid",
        capability_fn=lambda caps: caps.network,
        value_fn=lambda e: e.ssid,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcovacsOmniConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    controller = entry.runtime_data
    entities: list[EcovacsOmniEntity] = get_supported_entities(
        controller.devices, EcovacsOmniSensor, ENTITY_DESCRIPTIONS
    )
    for device in controller.devices:
        caps = device.capabilities
        entities.extend(
            EcovacsOmniLifespanSensor(device, caps.life_span, component)
            for component in SUPPORTED_LIFESPANS
            if component in caps.life_span.types
        )
        if caps.error:
            entities.append(EcovacsOmniErrorSensor(device, caps.error))
        if caps.map and caps.clean.action.area:
            manager = controller.room_manager(device)
            entities.append(SelectedRoomsSensor(device, manager))
            entities.append(RoomQueueSensor(device, manager))
    async_add_entities(entities)


class EcovacsOmniSensor(EcovacsOmniDescriptionEntity[CapabilityEvent], SensorEntity):
    """Generic event based sensor."""

    entity_description: EcovacsOmniSensorEntityDescription

    async def async_added_to_hass(self) -> None:
        """Subscribe to the event."""
        await super().async_added_to_hass()

        async def on_event(event: Event) -> None:
            value = self.entity_description.value_fn(event)
            if value is None:
                return
            self._attr_native_value = value
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)


class EcovacsOmniLifespanSensor(EcovacsOmniEntity[CapabilityLifeSpan], SensorEntity):
    """Remaining lifespan of a consumable in percent."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, device: Device, capability: CapabilityLifeSpan, component: Any
    ) -> None:
        """Initialize the sensor."""
        key = f"lifespan_{component.name.lower()}"
        self.entity_description = SensorEntityDescription(key=key, translation_key=key)
        super().__init__(device, capability)
        self._component = component

    async def async_added_to_hass(self) -> None:
        """Subscribe to lifespan events."""
        await super().async_added_to_hass()

        async def on_event(event: LifeSpanEvent) -> None:
            if event.type == self._component:
                self._attr_native_value = event.percent
                self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)


class EcovacsOmniErrorSensor(
    EcovacsOmniEntity[CapabilityEvent[ErrorEvent]], SensorEntity
):
    """Last error reported by the robot."""

    _always_available = True
    entity_description = SensorEntityDescription(
        key="error", translation_key="error", entity_category=EntityCategory.DIAGNOSTIC
    )

    async def async_added_to_hass(self) -> None:
        """Subscribe to errors."""
        await super().async_added_to_hass()

        async def on_event(event: ErrorEvent) -> None:
            self._attr_native_value = event.code
            self._attr_extra_state_attributes = {"description": event.description}
            self.async_write_ha_state()

        self._subscribe(self._capability.event, on_event)


class _RoomManagerSensor(EcovacsOmniEntity[None], SensorEntity):
    """Sensor fed by the room manager."""

    def __init__(self, device: Device, manager: RoomManager) -> None:
        """Initialize the sensor."""
        super().__init__(device, None)
        self._manager = manager

    async def async_added_to_hass(self) -> None:
        """Listen to the room manager."""
        await super().async_added_to_hass()

        @callback
        def on_change() -> None:
            self.async_write_ha_state()

        self.async_on_remove(self._manager.async_add_listener(on_change))


class SelectedRoomsSensor(_RoomManagerSensor):
    """Shows the selected rooms in cleaning order."""

    entity_description = SensorEntityDescription(
        key="selected_rooms", translation_key="selected_rooms"
    )

    @property
    def native_value(self) -> StateType:
        """Return the selected rooms, e.g. "Küche → Flur"."""
        names = [self._manager.room_name(i) for i in self._manager.selection]
        if not names:
            return None
        text = " → ".join(names)
        return text if len(text) <= 255 else f"{text[:252]}..."

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return ids and names."""
        selection = self._manager.selection
        return {
            "room_ids": selection,
            "room_names": [self._manager.room_name(i) for i in selection],
            "count": len(selection),
        }


class RoomQueueSensor(_RoomManagerSensor):
    """Shows the room currently being cleaned by the room queue."""

    entity_description = SensorEntityDescription(
        key="room_queue", translation_key="room_queue"
    )

    @property
    def native_value(self) -> StateType:
        """Return the current room of the queue."""
        if self._manager.current_room is None:
            return None
        return self._manager.room_name(self._manager.current_room)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return progress of the queue."""
        manager = self._manager
        done = (
            manager.queue_total
            - len(manager.queue)
            - (1 if manager.current_room is not None else 0)
        )
        return {
            "running": manager.queue_running,
            "remaining": [manager.room_name(i) for i in manager.queue],
            "done": max(done, 0),
            "total": manager.queue_total,
        }

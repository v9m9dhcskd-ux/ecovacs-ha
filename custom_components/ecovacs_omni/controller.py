"""Connection handling for the Ecovacs cloud (REST + MQTT)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from functools import partial
import logging

from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.device import Device
from deebot_client.events import FirmwareEvent, NetworkInfoEvent
from deebot_client.exceptions import (
    DeebotError,
    DeviceVerificationRequiredError,
    InvalidAuthenticationError,
)
from deebot_client.models import AccountCredentials
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client, device_registry as dr

from .const import CONF_ACCOUNT_CREDENTIALS, DOMAIN
from .recorder import DebugRecorder
from .rooms import RoomManager

_LOGGER = logging.getLogger(__name__)


class EcovacsOmniController:
    """Holds the Ecovacs account connection and all devices of it."""

    def __init__(self, hass: HomeAssistant, entry: EcovacsOmniConfigEntry) -> None:
        """Initialize the controller."""
        self._hass = hass
        self._entry = entry
        config = entry.data
        self._device_id: str = config[CONF_DEVICE_ID]
        self._country: str = config[CONF_COUNTRY]
        self._devices: list[Device] = []
        self._room_managers: dict[str, RoomManager] = {}
        self._recorders: dict[str, DebugRecorder] = {}
        self._unsubscribers: list[Callable[[], None]] = []

        self._authenticator = Authenticator(
            create_rest_config(
                aiohttp_client.async_get_clientsession(hass),
                device_id=self._device_id,
                alpha_2_country=self._country,
            ),
            config[CONF_USERNAME],
            md5(config[CONF_PASSWORD]),
            # Renewing with stored account credentials avoids repeated
            # password logins, which can trigger device verification.
            account_credentials=(
                AccountCredentials(**account)
                if (account := config.get(CONF_ACCOUNT_CREDENTIALS))
                else None
            ),
        )
        self._unsubscribers.append(
            self._authenticator.subscribe_account_credentials(
                self._async_store_account_credentials
            )
        )
        self._api_client = ApiClient(self._authenticator)
        self._mqtt_client: MqttClient | None = None

    async def initialize(self) -> None:
        """Log in, discover the devices and connect them via MQTT."""
        try:
            devices = await self._api_client.get_devices()
            await self._authenticator.authenticate()

            if devices.mqtt:
                mqtt = await self._get_mqtt_client()

                async def _init(device: Device) -> None:
                    # Must be installed before initialize() subscribes to MQTT
                    self._recorders[device.device_info["did"]] = DebugRecorder(device)
                    await device.initialize(mqtt)
                    self._subscribe_device_info_events(device)
                    self._devices.append(device)

                await asyncio.gather(
                    *(_init(Device(info, self._authenticator)) for info in devices.mqtt)
                )

            for device_config in (*devices.xmpp, *devices.not_supported):
                _LOGGER.warning(
                    'Device "%s" (%s) is not supported by this integration',
                    device_config.get("nick") or device_config.get("deviceName"),
                    device_config.get("class"),
                )
        except DeviceVerificationRequiredError as ex:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="device_verification_required",
            ) from ex
        except InvalidAuthenticationError as ex:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from ex
        except DeebotError as ex:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="setup_error"
            ) from ex

        for device in self._devices:
            manager = RoomManager(self._hass, device)
            await manager.async_load()
            self._room_managers[device.device_info["did"]] = manager

    async def _get_mqtt_client(self) -> MqttClient:
        if self._mqtt_client is None:
            config = await self._hass.async_add_executor_job(
                partial(
                    create_mqtt_config,
                    device_id=self._device_id,
                    country=self._country,
                )
            )
            client = MqttClient(config, self._authenticator)
            await client.verify_config()
            self._mqtt_client = client
        return self._mqtt_client

    async def _async_store_account_credentials(
        self, account: AccountCredentials
    ) -> None:
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, CONF_ACCOUNT_CREDENTIALS: asdict(account)},
        )

    def _subscribe_device_info_events(self, device: Device) -> None:
        """Keep firmware version and MAC in the device registry up to date."""
        identifier = (DOMAIN, device.device_info["did"])
        registry = dr.async_get(self._hass)

        def device_entry() -> dr.DeviceEntry | None:
            return registry.async_get_device(identifiers={identifier})

        async def on_firmware(event: FirmwareEvent) -> None:
            if entry := device_entry():
                registry.async_update_device(entry.id, sw_version=event.version)

        async def on_network(event: NetworkInfoEvent) -> None:
            if event.mac and (entry := device_entry()):
                registry.async_update_device(
                    entry.id,
                    new_connections={(dr.CONNECTION_NETWORK_MAC, event.mac)},
                )

        self._unsubscribers.extend(
            (
                device.events.subscribe(FirmwareEvent, on_firmware),
                device.events.subscribe(NetworkInfoEvent, on_network),
            )
        )

    async def teardown(self) -> None:
        """Disconnect everything."""
        for manager in self._room_managers.values():
            await manager.async_shutdown()
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        for device in self._devices:
            await device.teardown()
        if self._mqtt_client is not None:
            await self._mqtt_client.disconnect()
        await self._authenticator.teardown()

    @property
    def devices(self) -> list[Device]:
        """Return all connected devices."""
        return self._devices

    def recorder(self, device: Device) -> DebugRecorder | None:
        """Return the debug recorder of a device."""
        return self._recorders.get(device.device_info["did"])

    def room_manager(self, device: Device) -> RoomManager:
        """Return the room manager of a device."""
        return self._room_managers[device.device_info["did"]]


type EcovacsOmniConfigEntry = ConfigEntry[EcovacsOmniController]

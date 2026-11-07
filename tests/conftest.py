"""Fixtures for the Ecovacs Omni tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from deebot_client import const
from deebot_client.command import DeviceCommandResult
from deebot_client.device import Device
from deebot_client.exceptions import ApiError
from deebot_client.models import AccountCredentials, Credentials
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.ecovacs_omni.const import DOMAIN

ENTRY_DATA = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_COUNTRY: "DE",
    CONF_DEVICE_ID: "AAAAAAAA",
}


@pytest.fixture
def custom_integrations_enabled() -> bool:
    """Load the integrations from custom_components."""
    return True


@pytest.fixture
def device_json() -> dict[str, Any]:
    """Return the device as returned by the Ecovacs API."""
    return json.loads(
        (Path(__file__).parent / "fixtures" / "device_c8rj4y.json").read_text()
    )


@pytest.fixture
def mock_authenticator(device_json: dict[str, Any]) -> Generator[Mock]:
    """Mock the authenticator."""
    with (
        patch(
            "custom_components.ecovacs_omni.controller.Authenticator", autospec=True
        ) as mock,
        patch("custom_components.ecovacs_omni.config_flow.Authenticator", new=mock),
    ):
        authenticator = mock.return_value
        authenticator.authenticate.return_value = Credentials("token", "user_id", 0)
        authenticator.account_credentials = AccountCredentials("acc_token", "acc_user")

        async def post_authenticated(path: str, json: dict[str, Any], **_: Any) -> dict:
            match path:
                case const.PATH_API_APPSVR_APP:
                    return {"code": 0, "devices": [device_json], "errno": "0"}
                case const.PATH_API_USERS_USER:
                    return {"todo": "result", "result": "ok", "devices": [device_json]}
            raise ApiError(f"Path not mocked: {path}")

        authenticator.post_authenticated.side_effect = post_authenticated
        yield authenticator


@pytest.fixture
def mock_mqtt_client(mock_authenticator: Mock) -> Generator[Mock]:
    """Mock the MQTT client."""
    with (
        patch(
            "custom_components.ecovacs_omni.controller.MqttClient", autospec=True
        ) as mock,
        patch("custom_components.ecovacs_omni.config_flow.MqttClient", new=mock),
    ):
        client = mock.return_value
        client._authenticator = mock_authenticator
        client.subscribe.return_value = lambda: None
        yield client


@pytest.fixture
def mock_execute() -> Generator[AsyncMock]:
    """Record all commands sent to the robot."""
    with patch.object(
        Device,
        "_execute_command",
        return_value=DeviceCommandResult(device_reached=True),
    ) as mock:
        yield mock


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=ENTRY_DATA[CONF_USERNAME],
        data=ENTRY_DATA,
        unique_id=ENTRY_DATA[CONF_USERNAME],
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_authenticator: Mock,
    mock_mqtt_client: Mock,
    mock_execute: AsyncMock,
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    yield mock_config_entry
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

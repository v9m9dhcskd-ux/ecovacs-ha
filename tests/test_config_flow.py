"""Config flow tests."""

from __future__ import annotations

from unittest.mock import Mock, patch

from deebot_client.exceptions import (
    DeviceVerificationRequiredError,
    InvalidAuthenticationError,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ecovacs_omni.const import CONF_ACCOUNT_CREDENTIALS, DOMAIN

USER_INPUT = {"username": "user@example.com", "password": "secret", "country": "DE"}


def _no_setup():
    return patch("custom_components.ecovacs_omni.async_setup_entry", return_value=True)


async def test_user_flow(
    hass: HomeAssistant, mock_authenticator: Mock, mock_mqtt_client: Mock
) -> None:
    """Login works and creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with _no_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert len(result["data"]["device_id"]) == 8
    assert result["data"][CONF_ACCOUNT_CREDENTIALS] == {
        "access_token": "acc_token",
        "user_id": "acc_user",
    }


async def test_invalid_auth(
    hass: HomeAssistant, mock_authenticator: Mock, mock_mqtt_client: Mock
) -> None:
    """Wrong password shows an error."""
    mock_authenticator.authenticate.side_effect = InvalidAuthenticationError
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_device_verification(
    hass: HomeAssistant, mock_authenticator: Mock, mock_mqtt_client: Mock
) -> None:
    """A verification code is requested when Ecovacs asks for it."""
    mock_authenticator.authenticate.side_effect = DeviceVerificationRequiredError
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "device_verification"
    mock_authenticator.request_device_verification_code.assert_awaited_once()
    with _no_setup():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"verification_code": " 123456 "}
        )
    mock_authenticator.verify_device.assert_awaited_once_with("123456")
    assert result["type"] is FlowResultType.CREATE_ENTRY

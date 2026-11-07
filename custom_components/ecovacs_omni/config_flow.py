"""Config flow for Ecovacs Omni."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from functools import partial
import logging
import random
import string
from typing import Any

from aiohttp import ClientError
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.exceptions import (
    DeviceVerificationRequiredError,
    InvalidAuthenticationError,
    InvalidVerificationCodeError,
    MqttError,
)
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers import aiohttp_client, selector
import probatio

from .const import CONF_ACCOUNT_CREDENTIALS, CONF_VERIFICATION_CODE, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _new_client_device_id() -> str:
    """Return a random client id, Ecovacs uses it to identify Home Assistant."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


class EcovacsOmniConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._input: dict[str, Any] = {}
        self._authenticator: Authenticator | None = None

    # ----------------------------------------------------------- helpers

    async def _create_authenticator(self) -> Authenticator:
        await self._teardown_authenticator()
        self._input.setdefault(CONF_DEVICE_ID, _new_client_device_id())
        self._authenticator = Authenticator(
            create_rest_config(
                aiohttp_client.async_get_clientsession(self.hass),
                device_id=self._input[CONF_DEVICE_ID],
                alpha_2_country=self._input[CONF_COUNTRY],
            ),
            self._input[CONF_USERNAME],
            md5(self._input[CONF_PASSWORD]),
        )
        return self._authenticator

    async def _teardown_authenticator(self) -> None:
        if self._authenticator is not None:
            await self._authenticator.teardown()
            self._authenticator = None

    @callback
    def async_remove(self) -> None:
        """Clean up when the flow is removed."""
        super().async_remove()
        if self._authenticator is not None:
            self.hass.async_create_background_task(
                self._teardown_authenticator(),
                name=f"{DOMAIN}_config_flow_teardown",
            )

    async def _validate_login(self) -> dict[str, str]:
        """Log in. Raises DeviceVerificationRequiredError if a code is needed."""
        authenticator = await self._create_authenticator()
        try:
            await authenticator.authenticate()
        except DeviceVerificationRequiredError:
            raise
        except ClientError:
            _LOGGER.debug("Cannot connect", exc_info=True)
            return {"base": "cannot_connect"}
        except InvalidAuthenticationError:
            return {"base": "invalid_auth"}
        except Exception:
            _LOGGER.exception("Unexpected exception during login")
            return {"base": "unknown"}
        return await self._validate_mqtt()

    async def _validate_mqtt(self) -> dict[str, str]:
        assert self._authenticator is not None
        config = await self.hass.async_add_executor_job(
            partial(
                create_mqtt_config,
                device_id=self._input[CONF_DEVICE_ID],
                country=self._input[CONF_COUNTRY],
            )
        )
        try:
            await MqttClient(config, self._authenticator).verify_config()
        except MqttError:
            _LOGGER.debug("Cannot connect to MQTT", exc_info=True)
            return {"base": "cannot_connect"}
        except InvalidAuthenticationError:
            return {"base": "invalid_auth"}
        except Exception:
            _LOGGER.exception("Unexpected exception during MQTT verification")
            return {"base": "unknown"}
        return {}

    async def _login_or_verify(self) -> tuple[dict[str, str], ConfigFlowResult | None]:
        try:
            errors = await self._validate_login()
        except DeviceVerificationRequiredError:
            assert self._authenticator is not None
            try:
                await self._authenticator.request_device_verification_code()
            except ClientError:
                return {"base": "cannot_connect"}, None
            except Exception:
                _LOGGER.exception("Unexpected exception requesting verification code")
                return {"base": "unknown"}, None
            return {}, await self.async_step_device_verification()
        if errors:
            return errors, None
        return {}, self._finish()

    def _finish(self) -> ConfigFlowResult:
        if self._authenticator is not None and (
            account := self._authenticator.account_credentials
        ):
            self._input[CONF_ACCOUNT_CREDENTIALS] = asdict(account)
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=self._input
            )
        return self.async_create_entry(
            title=self._input[CONF_USERNAME], data=self._input
        )

    # ------------------------------------------------------------- steps

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Ecovacs account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            self._input = {**self._input, **user_input}
            errors, result = await self._login_or_verify()
            if result is not None:
                return result

        schema = probatio.Schema(
            {
                probatio.Required(CONF_USERNAME): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.EMAIL, autocomplete="username"
                    )
                ),
                probatio.Required(
                    probatio.Secret(CONF_PASSWORD)
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    )
                ),
                probatio.Required(CONF_COUNTRY): selector.CountrySelector(),
            }
        )
        suggested = user_input or {CONF_COUNTRY: self.hass.config.country}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_device_verification(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the code Ecovacs sent by e-mail."""
        errors: dict[str, str] = {}
        if user_input is not None and self._authenticator is not None:
            try:
                await self._authenticator.verify_device(
                    user_input[CONF_VERIFICATION_CODE].strip()
                )
            except InvalidVerificationCodeError:
                errors["base"] = "invalid_verification_code"
            except ClientError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception verifying device")
                errors["base"] = "unknown"
            else:
                errors = await self._validate_mqtt()
                if not errors:
                    return self._finish()

        return self.async_show_form(
            step_id="device_verification",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_VERIFICATION_CODE): selector.TextSelector()}
            ),
            description_placeholders={CONF_USERNAME: self._input[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        self._input = dict(entry_data)
        self._input.pop(CONF_ACCOUNT_CREDENTIALS, None)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._input[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            errors, result = await self._login_or_verify()
            if result is not None:
                return result

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        probatio.Secret(CONF_PASSWORD)
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
            description_placeholders={CONF_USERNAME: self._input[CONF_USERNAME]},
            errors=errors,
        )

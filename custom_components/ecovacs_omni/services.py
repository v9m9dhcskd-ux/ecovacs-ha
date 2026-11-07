"""Services of the Ecovacs Omni integration."""

from __future__ import annotations

from deebot_client.events import FanSpeedLevel, WorkMode
from homeassistant.components.vacuum import DOMAIN as VACUUM_DOMAIN
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv, service
import probatio

from .const import (
    ATTR_ACTION,
    ATTR_CLEANINGS,
    ATTR_FAN_SPEED,
    ATTR_ROOMS,
    ATTR_SEQUENTIAL,
    ATTR_WATER_AMOUNT,
    ATTR_WORK_MODE,
    DOMAIN,
    SERVICE_CANCEL_ROOM_QUEUE,
    SERVICE_CLEAN_ROOMS,
    SERVICE_GET_ROOMS,
    SERVICE_STATION_ACTION,
    STATION_ACTIONS,
)

_CLEAN_OPTIONS = {
    probatio.Optional(ATTR_SEQUENTIAL): cv.boolean,
    probatio.Optional(ATTR_CLEANINGS): probatio.All(
        probatio.Coerce(int), probatio.Range(min=1, max=4)
    ),
    probatio.Optional(ATTR_WORK_MODE): probatio.In(
        [mode.name.lower() for mode in WorkMode]
    ),
    probatio.Optional(ATTR_FAN_SPEED): probatio.In(
        [level.name.lower() for level in FanSpeedLevel]
    ),
    probatio.Optional(ATTR_WATER_AMOUNT): cv.string,
}

_ROOMS = probatio.All(probatio.EnsureList(), [cv.string])


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the entity services on our vacuum entities."""

    def register(name, schema, func, supports_response=SupportsResponse.NONE):  # type: ignore[no-untyped-def]
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=VACUUM_DOMAIN,
            schema=schema,
            func=func,
            supports_response=supports_response,
        )

    register(
        SERVICE_CLEAN_ROOMS,
        {
            probatio.Required(ATTR_ROOMS): probatio.All(_ROOMS, probatio.Length(min=1)),
            **_CLEAN_OPTIONS,
        },
        "async_clean_rooms",
    )
    register(SERVICE_CANCEL_ROOM_QUEUE, None, "async_cancel_room_queue")
    register(
        SERVICE_STATION_ACTION,
        {
            probatio.Required(ATTR_ACTION): probatio.In(
                [action.name.lower() for action in STATION_ACTIONS]
            )
        },
        "async_station_action",
    )
    register(SERVICE_GET_ROOMS, None, "async_get_rooms", SupportsResponse.ONLY)

"""Constants for the Ecovacs Omni integration."""

from __future__ import annotations

from deebot_client.commands import StationAction
from deebot_client.events import LifeSpan

DOMAIN = "ecovacs_omni"

CONF_ACCOUNT_CREDENTIALS = "account_credentials"
CONF_VERIFICATION_CODE = "verification_code"

# Lifespan components we create sensors/buttons for (if the device reports them).
SUPPORTED_LIFESPANS: tuple[LifeSpan, ...] = (
    LifeSpan.BRUSH,
    LifeSpan.SIDE_BRUSH,
    LifeSpan.FILTER,
    LifeSpan.HAND_FILTER,
    LifeSpan.UNIT_CARE,
    LifeSpan.ROUND_MOP,
    LifeSpan.CLEANING_SOLUTION,
    LifeSpan.HEAVY_DUTY_CLEANING_SOLUTION,
    LifeSpan.DUST_BAG,
    LifeSpan.STATION_FILTER,
    LifeSpan.MOP_WASHING_TRAY,
    LifeSpan.SEWAGE_BOX,
    LifeSpan.STRAINER,
    LifeSpan.WATER_SINK,
    LifeSpan.UV_SANITIZER,
    LifeSpan.AIR_FRESHENER,
)

# Station actions. deebot-client only lists "empty dustbin" for the
# T50 Max Pro Omni, but the station also washes and dries the mops.
# We therefore offer all actions for every device with a station.
STATION_ACTIONS: tuple[StationAction, ...] = (
    StationAction.EMPTY_DUSTBIN,
    StationAction.WASH_MOP,
    StationAction.DRY_MOP,
    StationAction.CLEAN_BASE,
)

# Room queue
STORAGE_VERSION = 1
# seconds until the robot must report "cleaning" (the T50 first washes its
# mops for ~2 minutes before it leaves the station)
SEQUENTIAL_START_TIMEOUT = 600
SEQUENTIAL_ROOM_TIMEOUT = 4 * 60 * 60  # max seconds for a single room

SERVICE_CLEAN_ROOMS = "clean_rooms"
SERVICE_CLEAN_SELECTED_ROOMS = "clean_selected_rooms"
SERVICE_SELECT_ROOMS = "select_rooms"
SERVICE_CANCEL_ROOM_QUEUE = "cancel_room_queue"
SERVICE_STATION_ACTION = "station_action"
SERVICE_GET_ROOMS = "get_rooms"

ATTR_ROOMS = "rooms"
ATTR_SEQUENTIAL = "sequential"
ATTR_CLEANINGS = "cleanings"
ATTR_WORK_MODE = "work_mode"
ATTR_FAN_SPEED = "fan_speed"
ATTR_WATER_AMOUNT = "water_amount"
ATTR_ACTION = "action"

SIGNAL_ROOMS_UPDATED = f"{DOMAIN}_rooms_updated_{{}}"

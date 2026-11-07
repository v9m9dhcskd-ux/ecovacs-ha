"""Commands not (yet) available in deebot-client."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deebot_client.commands.json.clean import CleanV2
from deebot_client.models import CleanAction

FREE_CLEAN = "freeClean"
SPOT_AREA = "spotArea"
UNKNOWN_TYPE_CODE = 20011


class CleanRoomsV2(CleanV2):
    """Start a room cleaning with clean_V2.

    Newer robots (e.g. T50 MAX PRO OMNI, class c8rj4y) reject "spotArea" with
    code 20011 "unknown type". Like the Ecovacs app they expect
    ``{"type": "freeClean", "value": "1,<room>;1,<room>"}`` where ``1`` is the
    area type "room". Rooms are cleaned in the given order. The number of
    passes is set beforehand with setCleanCount.
    Older robots use ``{"type": "spotArea", "value": "<room>,<room>"}``.
    """

    def __init__(self, room_ids: Sequence[int], clean_type: str = FREE_CLEAN) -> None:
        """Initialize the command."""
        if clean_type == FREE_CLEAN:
            value = ";".join(f"1,{room_id}" for room_id in room_ids)
        else:
            value = ",".join(str(room_id) for room_id in room_ids)
        self._content = {"type": clean_type, "value": value}
        super().__init__(CleanAction.START)

    def _get_args(self, action: CleanAction) -> dict[str, Any]:
        args = super()._get_args(action)
        if action == CleanAction.START:
            args["content"] = dict(self._content)
        return args


def response_code(raw_response: dict[str, Any]) -> int | None:
    """Return the body code of a raw command response."""
    body = raw_response.get("resp", {}).get("body", {})
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, int) else None

"""Messages of newer robots (e.g. T50 MAX PRO OMNI) deebot-client ignores.

Recorded from a real T50 MAX PRO OMNI (class c8rj4y, firmware 1.62.0):

- ``onCleanDataUpdate_V3`` / ``onCleanDataUpdate_V2``: the rooms of the
  running job, e.g. ``{"content": [{"id": 0, "status": 2, "type": 1}]}``.
  ``type`` 1 is a room, ``status`` 1 means planned, 2 means being cleaned,
  3 means done.
  An empty ``content`` means no room is open anymore.
- ``onLastTimeStats``: sent once when a job ends, e.g.
  ``{"area": 0, "start": "1794056928", "time": 120, "type": "freeClean"}``.
  The T50 does not send ``reportStats``, which deebot-client waits for.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from deebot_client.device import Device
from deebot_client.events import CleanJobStatus, ReportStatsEvent

from .rooms import RoomManager

_LOGGER = logging.getLogger(__name__)

_ROOM_TYPE = 1
_STATUS_CLEANING = 2


def _body_data(raw: Any) -> Any:
    if isinstance(raw, bytes | bytearray):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        body = raw.get("body", raw)
        if isinstance(body, dict):
            return body.get("data", body)
    return raw


class ExtraMessageHandler:
    """Turns unsupported robot messages into events and room manager updates."""

    def __init__(self, device: Device, manager: RoomManager) -> None:
        """Initialize the handler."""
        self._device = device
        self._manager = manager

    def __call__(self, name: str, raw: Any) -> None:
        """Handle a message."""
        if name == "onLastTimeStats":
            self._on_last_time_stats(_body_data(raw))
        elif name in ("onCleanDataUpdate_V3", "onCleanDataUpdate_V2"):
            self._on_clean_data(_body_data(raw))

    def _on_last_time_stats(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        status = (
            CleanJobStatus.MANUALLY_STOPPED
            if self._manager.stop_requested
            else CleanJobStatus.FINISHED
        )
        self._manager.stop_requested = False
        _LOGGER.debug("Job finished (%s): %s", status.name, data)
        self._device.events.notify(
            ReportStatsEvent(
                area=data.get("area"),
                time=data.get("time"),
                type=data.get("type"),
                cleaning_id=str(data.get("start", "")),
                status=status,
                content=[],
            )
        )

    def _on_clean_data(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        content = data.get("content")
        if not isinstance(content, list):
            return
        active = next(
            (
                int(entry["id"])
                for entry in content
                if isinstance(entry, dict)
                and entry.get("type") == _ROOM_TYPE
                and entry.get("status") == _STATUS_CLEANING
                and "id" in entry
            ),
            None,
        )
        job_rooms = {
            int(entry["id"])
            for entry in content
            if isinstance(entry, dict)
            and entry.get("type") == _ROOM_TYPE
            and "id" in entry
        }
        self._manager.async_set_job(job_rooms, active)

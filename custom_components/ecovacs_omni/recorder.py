"""Records raw robot messages and command responses for diagnostics.

New robots (e.g. T50) send messages deebot-client does not understand yet.
To support them we need to see what they really send. The recorder keeps the
last messages and command responses in memory, they are shown in the
diagnostics download.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
import json
import logging
from typing import Any

from deebot_client.command import Command, DeviceCommandResult
from deebot_client.device import Device

_LOGGER = logging.getLogger(__name__)

_MAX_ENTRIES = 150
_MAX_LENGTH = 4000

_LATEST_PER_NAME = 3

# High frequency messages, kept out of the timeline (only the latest of each
# type is kept, see latest_by_name)
_SKIP_MESSAGES = frozenset(
    {
        "onPos",
        "onPosLf",
        "onMapTrace",
        "onMapTrace_V2",
        "onMinorMap",
        "onWorkProgressReport",
        "onFwBuryPoint-bd_cri04",
        "onFwBuryPoint-bd_basicinfo",
        "onFwBuryPoint-common-setting",
        "onFwBuryPoint-formulate-setting",
    }
)


def _shorten(value: Any) -> Any:
    """Return JSON-friendly data, decoded from bytes and limited in size."""
    if isinstance(value, bytes | bytearray):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value[:_MAX_LENGTH]
    try:
        text = json.dumps(value, default=str)
    except TypeError, ValueError:
        return str(value)[:_MAX_LENGTH]
    if len(text) > _MAX_LENGTH:
        return text[:_MAX_LENGTH] + "...(truncated)"
    return value


class DebugRecorder:
    """Wraps a device to record incoming messages and command responses."""

    def __init__(self, device: Device) -> None:
        """Install the recorder on the device instance."""
        self.messages: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)
        self.commands: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)
        self.message_names: dict[str, int] = {}
        # The last few payloads of every message type, so rare messages
        # (e.g. the end of a cleaning job) are never pushed out by others
        self.latest_by_name: dict[str, deque[dict[str, Any]]] = {}
        self._device = device
        # Called for every incoming message after deebot-client handled it
        self.extra_handlers: list[Callable[[str, Any], None]] = []

        original_handle = device._handle_message
        original_execute = device._execute_command

        def handle_message(message_name: str, message_data: Any) -> None:
            self.message_names[message_name] = (
                self.message_names.get(message_name, 0) + 1
            )
            entry = {
                "time": datetime.now(UTC).isoformat(timespec="seconds"),
                "name": message_name,
                "data": _shorten(message_data),
            }
            self.latest_by_name.setdefault(
                message_name, deque(maxlen=_LATEST_PER_NAME)
            ).append(entry)
            if message_name not in _SKIP_MESSAGES:
                self.messages.append(entry)
            original_handle(message_name, message_data)
            for handler in self.extra_handlers:
                try:
                    handler(message_name, message_data)
                except Exception:
                    _LOGGER.exception("Error handling message %s", message_name)

        async def execute_command(command: Command) -> DeviceCommandResult:
            result = await original_execute(command)
            if command.NAME not in ("getMapTrace", "getMinorMap", "getPos"):
                self.commands.append(
                    {
                        "time": datetime.now(UTC).isoformat(timespec="seconds"),
                        "name": command.NAME,
                        "args": _shorten(command._args),
                        "reached": result.device_reached,
                        "response": _shorten(result.raw_response),
                    }
                )
            return result

        # The MQTT client keeps a reference to the bound method from
        # Device.initialize(), so this must run before initialize().
        device._handle_message = handle_message  # type: ignore[method-assign]
        device._execute_command = execute_command  # type: ignore[method-assign]

    def as_dict(self) -> dict[str, Any]:
        """Return the recorded data."""
        return {
            "message_counts": dict(sorted(self.message_names.items())),
            "messages": list(self.messages),
            "latest_by_name": {
                name: list(entries)
                for name, entries in sorted(self.latest_by_name.items())
            },
            "commands": list(self.commands),
        }

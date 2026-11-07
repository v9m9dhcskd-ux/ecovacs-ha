"""Records raw robot messages and command responses for diagnostics.

New robots (e.g. T50) send messages deebot-client does not understand yet.
To support them we need to see what they really send. The recorder keeps the
last messages and command responses in memory, they are shown in the
diagnostics download.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
import logging
from typing import Any

from deebot_client.command import Command, DeviceCommandResult
from deebot_client.device import Device

_LOGGER = logging.getLogger(__name__)

_MAX_ENTRIES = 150
_MAX_LENGTH = 4000

# High frequency messages, not interesting for diagnostics
_SKIP_MESSAGES = frozenset({"onPos", "onMapTrace", "onMinorMap", "onMapTrace_V2"})


def _shorten(value: Any) -> Any:
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except TypeError, ValueError:
        text = str(value)
    if len(text) > _MAX_LENGTH:
        return text[:_MAX_LENGTH] + "...(truncated)"
    try:
        return json.loads(text) if not isinstance(value, str) else text
    except ValueError:
        return text


class DebugRecorder:
    """Wraps a device to record incoming messages and command responses."""

    def __init__(self, device: Device) -> None:
        """Install the recorder on the device instance."""
        self.messages: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)
        self.commands: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)
        self.message_names: dict[str, int] = {}
        self._device = device

        original_handle = device._handle_message
        original_execute = device._execute_command

        def handle_message(message_name: str, message_data: Any) -> None:
            self.message_names[message_name] = (
                self.message_names.get(message_name, 0) + 1
            )
            if message_name not in _SKIP_MESSAGES:
                self.messages.append(
                    {
                        "time": datetime.now(UTC).isoformat(timespec="seconds"),
                        "name": message_name,
                        "data": _shorten(message_data),
                    }
                )
            original_handle(message_name, message_data)

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
            "commands": list(self.commands),
        }

"""Pytest plugin loaded first: Home Assistant must alias httpx before respx imports it."""

import homeassistant  # noqa: F401

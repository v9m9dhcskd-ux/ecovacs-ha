"""Setup tests."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry


async def test_setup_creates_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The T50 is set up with its entities."""
    assert init_integration.state is ConfigEntryState.LOADED
    entries = er.async_entries_for_config_entry(
        entity_registry, init_integration.entry_id
    )
    ids = sorted(e.entity_id for e in entries)
    print("\n".join(ids))
    assert "vacuum.robbi" in ids
    assert (
        "button.robbi_ganze_wohnung_reinigen" in ids
        or "button.robbi_clean_whole_home" in ids
    )
    state = hass.states.get("vacuum.robbi")
    assert state is not None


async def test_service_descriptions(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """services.yaml is valid and all services are registered."""
    from homeassistant.helpers.service import async_get_all_descriptions

    descriptions = await async_get_all_descriptions(hass)
    assert set(descriptions["ecovacs_omni"]) == {
        "clean_rooms",
        "clean_selected_rooms",
        "select_rooms",
        "cancel_room_queue",
        "station_action",
        "get_rooms",
    }

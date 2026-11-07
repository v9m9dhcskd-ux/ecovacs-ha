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
        "cancel_room_queue",
        "station_action",
        "get_rooms",
    }


async def test_obsolete_entities_removed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_authenticator,
    mock_mqtt_client,
    mock_execute,
    entity_registry: er.EntityRegistry,
) -> None:
    """Room switches, selection buttons and the old sensor are cleaned up."""
    mock_config_entry.add_to_hass(hass)
    did = "E0000000000000000001"
    old = [
        ("switch", f"{did}_room_m1_1"),
        ("button", f"{did}_clean_selected_rooms"),
        ("button", f"{did}_clear_room_selection"),
        ("sensor", f"{did}_selected_rooms"),
    ]
    for domain, unique_id in old:
        entity_registry.async_get_or_create(
            domain, "ecovacs_omni", unique_id, config_entry=mock_config_entry
        )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    for domain, unique_id in old:
        assert (
            entity_registry.async_get_entity_id(domain, "ecovacs_omni", unique_id)
            is None
        )
    # Current entities are kept
    assert entity_registry.async_get_entity_id(
        "switch", "ecovacs_omni", f"{did}_sequential_rooms"
    )
    assert entity_registry.async_get_entity_id(
        "sensor", "ecovacs_omni", f"{did}_job_rooms"
    )
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

"""Tests for the Ceska Posta coordinator: fetching, caching and events.

The parcel mapping itself is covered by ``test_parcels.py``.
"""
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ceska_posta.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
    ParcelStatus,
)
from custom_components.ceska_posta.coordinator import CeskaPostaCoordinator

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    NOT_FOUND_CODE,
    OTHER_CODE,
    PICKUP_CODE,
    active_sample,
    delivered_sample,
    in_transit_sample,
    not_found_sample,
    pickup_sample,
)


def _client_result(sample: dict) -> dict:
    """The shape ``async_get_parcels`` returns per code (no ``id`` — the
    coordinator adds that itself)."""
    return {"backbone": sample["backbone"], "enrichment": sample["enrichment"]}


def _entry_with(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        # Keep-most-recent-100 so the delivered-retention filter never trims
        # the (old, fixed-date) sample parcels these tests assert on.
        options={
            CONF_PARCELS: parcels,
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
        unique_id=DOMAIN,
    )


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


async def test_update_merges_multiple_parcels(hass):
    entry = _entry_with(
        [{CONF_TRACKING_CODE: ACTIVE_CODE}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = lambda codes: {
        c: _client_result(active_sample(c) if c == ACTIVE_CODE else delivered_sample(c))
        for c in codes
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    assert len(data) == 1  # one active
    assert data[0]["barcode"] == ACTIVE_CODE
    assert len(coordinator.delivered) == 1
    assert coordinator.last_success_time is not None


async def test_update_shows_unknown_for_not_found_code(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: NOT_FOUND_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {
        NOT_FOUND_CODE: _client_result(not_found_sample(NOT_FOUND_CODE))
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    data = await coordinator._async_update_data()

    assert len(data) == 1
    assert data[0]["barcode"] == NOT_FOUND_CODE
    assert data[0]["status"] == ParcelStatus.UNKNOWN


async def test_update_keeps_cached_payload_on_error(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {
        DELIVERED_CODE: _client_result(delivered_sample())
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)
    await coordinator._async_update_data()  # populates the cache

    client.async_get_parcels.return_value = {}  # neither surface answered this time
    await coordinator._async_update_data()  # falls back to the cached raw
    assert len(coordinator.delivered) == 1


async def test_update_raises_when_every_parcel_fails(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {}
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_update_reraises_unexpected_exceptions(hass):
    """Only a genuine bug propagates — the client itself never raises for a
    single code's fetch failure."""
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = ValueError("boom")
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    with pytest.raises(ValueError):
        await coordinator._async_update_data()


async def test_update_skips_items_missing_a_tracking_code(hass):
    entry = _entry_with(
        [{CONF_TRACKING_CODE: ""}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {
        DELIVERED_CODE: _client_result(delivered_sample())
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    await coordinator._async_update_data()
    client.async_get_parcels.assert_awaited_once_with([DELIVERED_CODE])


async def test_update_prunes_cache_for_untracked_parcels(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {
        DELIVERED_CODE: _client_result(delivered_sample())
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)
    coordinator._raw_cache["GONE"] = {"id": "GONE", "backbone": None, "enrichment": None}

    await coordinator._async_update_data()

    assert "GONE" not in coordinator._raw_cache
    assert DELIVERED_CODE in coordinator._raw_cache


async def test_update_makes_one_batched_call_not_one_per_parcel(hass):
    entry = _entry_with(
        [{CONF_TRACKING_CODE: ACTIVE_CODE}, {CONF_TRACKING_CODE: DELIVERED_CODE}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = lambda codes: {
        c: _client_result(active_sample(c) if c == ACTIVE_CODE else delivered_sample(c))
        for c in codes
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    await coordinator._async_update_data()
    assert client.async_get_parcels.await_count == 1


async def test_cache_only_poll_does_not_stamp_last_success(hass):
    """A poll served entirely from cache must not look like a success."""
    entry = _entry_with([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {
        DELIVERED_CODE: _client_result(delivered_sample())
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)
    await coordinator._async_update_data()
    stamp = coordinator.last_success_time
    assert stamp is not None

    client.async_get_parcels.return_value = {}  # served from cache
    await coordinator._async_update_data()
    assert coordinator.last_success_time == stamp


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


async def test_first_refresh_fires_nothing(hass):
    """Otherwise every restart floods the user with "registered" events."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {ACTIVE_CODE: _client_result(active_sample())}
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    fired = []
    for suffix in (
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
    ):
        hass.bus.async_listen(f"{DOMAIN}_{suffix}", lambda e: fired.append(e))

    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_event_carries_device_id(hass):
    from homeassistant.helpers import device_registry as dr

    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    client = AsyncMock()
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = {
        ACTIVE_CODE: _client_result(in_transit_sample())
    }
    await coordinator._async_update_data()
    client.async_get_parcels.return_value = {ACTIVE_CODE: _client_result(active_sample())}
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events[0].data["device_id"] == device.id


async def test_fires_status_changed_event(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = {
        ACTIVE_CODE: _client_result(in_transit_sample())
    }
    await coordinator._async_update_data()  # first refresh: suppressed

    client.async_get_parcels.return_value = {ACTIVE_CODE: _client_result(active_sample())}
    await coordinator._async_update_data()  # out for delivery
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_status"] == ParcelStatus.IN_TRANSIT
    assert events[0].data["new_status"] == ParcelStatus.OUT_FOR_DELIVERY


async def test_delivery_fires_delivered_event_and_not_status_changed(hass):
    """The hop to delivered fires exactly one, dedicated event."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    delivered = []
    changed = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: delivered.append(e))
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_status_changed", lambda e: changed.append(e)
    )

    client.async_get_parcels.return_value = {
        ACTIVE_CODE: _client_result(active_sample(ACTIVE_CODE))
    }
    await coordinator._async_update_data()
    client.async_get_parcels.return_value = {
        ACTIVE_CODE: _client_result(delivered_sample(ACTIVE_CODE))
    }
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert changed == []
    assert len(delivered) == 1
    assert delivered[0].data["barcode"] == ACTIVE_CODE
    assert delivered[0].data["status"] == ParcelStatus.DELIVERED


async def test_no_events_for_parcel_first_seen_delivered(hass):
    """A parcel already delivered when first tracked fires nothing at all."""
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.side_effect = lambda codes: {
        c: _client_result(active_sample(c) if c == ACTIVE_CODE else delivered_sample(c))
        for c in codes
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: fired.append(e))
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", lambda e: fired.append(e))

    await coordinator._async_update_data()  # first refresh seeds the state

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PARCELS: [
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                {CONF_TRACKING_CODE: DELIVERED_CODE},
            ],
        },
    )
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert fired == []


async def test_fires_registered_event_for_new_parcel(hass):
    entry = _entry_with([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcels.return_value = {
        ACTIVE_CODE: _client_result(active_sample(ACTIVE_CODE))
    }
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", lambda e: events.append(e))

    await coordinator._async_update_data()  # first refresh: suppressed

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PARCELS: [
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                {CONF_TRACKING_CODE: OTHER_CODE},
            ],
        },
    )
    client.async_get_parcels.side_effect = lambda codes: {
        c: _client_result(active_sample(c)) for c in codes
    }
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["barcode"] == OTHER_CODE


async def test_fires_delivery_time_changed_event(hass):
    """Balikovna's ``storedTo`` collection deadline moving is the real-world
    case a pickup-point parcel's ``planned_to`` can change."""
    entry = _entry_with([{CONF_TRACKING_CODE: PICKUP_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = {
        PICKUP_CODE: _client_result(pickup_sample())
    }
    await coordinator._async_update_data()  # first refresh: suppressed

    moved = pickup_sample()
    moved["enrichment"]["storedTo"] = "2026-05-02"
    client.async_get_parcels.return_value = {PICKUP_CODE: _client_result(moved)}
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["old_planned_to"] == "2026-04-29"
    assert events[0].data["new_planned_to"] == "2026-05-02"


async def test_losing_the_eta_is_silent(hass):
    """value -> null just means the carrier lost the window; not worth an alert."""
    entry = _entry_with([{CONF_TRACKING_CODE: PICKUP_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = CeskaPostaCoordinator(hass, client, entry)

    events = []
    hass.bus.async_listen(
        f"{DOMAIN}_parcel_delivery_time_changed", lambda e: events.append(e)
    )

    client.async_get_parcels.return_value = {
        PICKUP_CODE: _client_result(pickup_sample())
    }
    await coordinator._async_update_data()

    dropped = pickup_sample()
    dropped["enrichment"]["storedTo"] = None
    client.async_get_parcels.return_value = {PICKUP_CODE: _client_result(dropped)}
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert events == []

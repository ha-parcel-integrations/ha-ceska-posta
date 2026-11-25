"""Tests for the contents action, the track_parcel field and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ceska_posta.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.ceska_posta.contents import CONTENTS_STORE_MAX_CODES

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    OTHER_CODE,
    active_sample,
    delivered_sample,
)

FETCH = "custom_components.ceska_posta.api.CeskaPostaApiClient.async_get_parcels"
REFRESH = "custom_components.ceska_posta.coordinator.CeskaPostaCoordinator"
UNTRACKED = "XY9999999999Z"
STORE_KEY = f"{DOMAIN}.contents.%s"


def _result(codes: list[str]) -> dict:
    out = {}
    for code in codes:
        sample = (
            delivered_sample(code) if code == DELIVERED_CODE else active_sample(code)
        )
        out[code] = {"backbone": sample["backbone"], "enrichment": sample["enrichment"]}
    return out


async def _setup(hass, codes: list[str] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={
            CONF_PARCELS: [{CONF_TRACKING_CODE: c} for c in codes or []],
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 100,
        },
    )
    entry.add_to_hass(hass)
    with patch(FETCH, new=AsyncMock(return_value=_result(codes or []))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service: str, data: dict) -> AsyncMock:
    fetch = AsyncMock(side_effect=lambda codes: _result(list(codes)))
    with patch(FETCH, new=fetch):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()
    return fetch


def _contents(entry: MockConfigEntry, code: str) -> str | None:
    coordinator = entry.runtime_data.coordinator
    parcels = {p["barcode"]: p for p in [*coordinator.data, *coordinator.delivered]}
    return parcels[code]["contents"]


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    code = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "update_contents", {**code, "contents": " Running  shoes "})
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(hass, "update_contents", {**code, "contents": "Winter socks"})
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(hass, "update_contents", {**code, "contents": "  "})
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_without_text_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    code = {CONF_TRACKING_CODE: ACTIVE_CODE}
    await _call(hass, "update_contents", {**code, "contents": "Running shoes"})

    await _call(hass, "update_contents", code)

    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_normalizes_the_code(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {
            CONF_TRACKING_CODE: f"{ACTIVE_CODE[:2]}-{ACTIVE_CODE[2:]}".lower(),
            "contents": "Running shoes",
        },
    )
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_update_contents_reaches_a_delivered_parcel(hass):
    entry = await _setup(hass, [DELIVERED_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: DELIVERED_CODE, "contents": "Running shoes"},
    )
    assert _contents(entry, DELIVERED_CODE) == "Running shoes"


async def test_update_contents_works_for_a_code_that_is_not_tracked(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )

    assert entry.runtime_data.coordinator.contents_store.get(UNTRACKED) == (
        "Running shoes"
    )
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_a_contents_change_never_fetches_or_refreshes(hass):
    await _setup(hass, [ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh:
        for payload in (data, data, {**data, "contents": "Socks"}):
            fetch = await _call(hass, "update_contents", payload)
            assert fetch.await_count == 0
        fetch = await _call(hass, "track_parcel", {**data, "contents": "Boots"})
        assert fetch.await_count == 0
    assert refresh.await_count == 0


async def test_a_contents_change_notifies_listeners_once_when_changed(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    with patch.object(type(coordinator), "async_update_listeners") as notify:
        await _call(hass, "update_contents", data)
        await _call(hass, "update_contents", data)
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: UNTRACKED})
    assert notify.call_count == 1


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: ""})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )


async def test_track_parcel_stores_contents_for_a_new_code_and_refreshes(hass):
    entry = await _setup(hass)
    fetch = await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert fetch.await_count >= 1
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_track_parcel_without_contents_is_unchanged(hass):
    entry = await _setup(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert _contents(entry, ACTIVE_CODE) is None


async def test_track_parcel_replaces_and_clears_contents_of_a_tracked_code(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    track = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "track_parcel", {**track, "contents": "Running shoes"})
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(hass, "track_parcel", {**track, "contents": "Winter socks"})
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(hass, "track_parcel", {**track, "contents": ""})
    assert _contents(entry, ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_untrack_parcel_drops_the_contents(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == []


async def test_untrack_parcel_with_a_not_loaded_entry_still_untracks(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()):
        await hass.services.async_call(
            DOMAIN, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE}, blocking=True
        )
        await hass.async_block_till_done()
    assert entry.options[CONF_PARCELS] == []
    entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_the_cap_drops_the_oldest_code_at_201(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    store = entry.runtime_data.coordinator.contents_store
    for number in range(CONTENTS_STORE_MAX_CODES + 1):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: f"CAP{number}", "contents": f"Item {number}"},
        )
    assert store.get("CAP0") is None
    assert store.get("CAP1") == "Item 1"
    assert store.get(f"CAP{CONTENTS_STORE_MAX_CODES}") == (
        f"Item {CONTENTS_STORE_MAX_CODES}"
    )


async def test_an_entry_without_a_store_file_works(hass, hass_storage):
    assert STORE_KEY % "x" not in hass_storage
    entry = await _setup(hass, [ACTIVE_CODE, OTHER_CODE])
    assert _contents(entry, ACTIVE_CODE) is None
    assert _contents(entry, OTHER_CODE) is None


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())

    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(FETCH, new=AsyncMock(return_value=_result([ACTIVE_CODE]))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass):
    await _setup(hass, [ACTIVE_CODE])
    events = []
    for name in ("status_changed", "registered", "delivered", "delivery_time_changed"):
        hass.bus.async_listen(f"{DOMAIN}_parcel_{name}", events.append)

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert events == []

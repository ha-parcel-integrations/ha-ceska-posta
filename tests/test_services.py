"""Tests for the Ceska Posta services (track_parcel / untrack_parcel)."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ceska_posta.const import (
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import active_sample

NEW_CODE = "CD2345678901D"

_PATCH_TARGET = "custom_components.ceska_posta.api.CeskaPostaApiClient.async_get_parcels"


def _entry_result(code: str) -> dict:
    sample = active_sample(code)
    return {"backbone": sample["backbone"], "enrichment": sample["enrichment"]}


def _result(code: str) -> dict:
    return {code: _entry_result(code)}


async def _setup(hass, parcels: list[dict] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels or []},
    )
    entry.add_to_hass(hass)
    codes = [p[CONF_TRACKING_CODE] for p in (parcels or [])]
    with patch(
        _PATCH_TARGET, new=AsyncMock(return_value={c: _entry_result(c) for c in codes})
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_track_parcel_adds_to_options(hass):
    entry = await _setup(hass)
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_result(NEW_CODE))):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: NEW_CODE},
            blocking=True,
        )
        await hass.async_block_till_done()

    parcels = entry.options[CONF_PARCELS]
    assert parcels == [{CONF_TRACKING_CODE: NEW_CODE}]


async def test_track_parcel_normalizes_code(hass):
    entry = await _setup(hass)
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_result(NEW_CODE))):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: f"{NEW_CODE[:2]}-{NEW_CODE[2:]}".lower()},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: NEW_CODE}]


async def test_track_parcel_rejects_invalid_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: "abc"}, blocking=True
        )


async def test_track_parcel_duplicate_is_noop(hass):
    entry = await _setup(hass)
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_result(NEW_CODE))):
        for _ in range(2):
            await hass.services.async_call(
                DOMAIN,
                "track_parcel",
                {CONF_TRACKING_CODE: NEW_CODE},
                blocking=True,
            )
            await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1


async def test_untrack_parcel_removes_from_options(hass):
    entry = await _setup(hass, parcels=[{CONF_TRACKING_CODE: NEW_CODE}])
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_result(NEW_CODE))):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: NEW_CODE},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == []


async def test_untrack_unknown_code_is_noop(hass):
    entry = await _setup(hass, parcels=[{CONF_TRACKING_CODE: NEW_CODE}])
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_result(NEW_CODE))):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "ZZ0000000000Z"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1

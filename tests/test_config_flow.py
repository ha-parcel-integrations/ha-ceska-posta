"""Tests for the Ceska Posta config and options flow."""
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ceska_posta.config_flow import (
    normalize_tracking_code,
    valid_tracking_code,
)
from custom_components.ceska_posta.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_REFRESH_INTERVAL,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import active_sample, not_found_sample

VALID_CODE = "AB1234567890C"  # CZ domestic shape: 2 letters + 10 digits + 1 letter
VALID_CODE_2 = "CD2345678901D"
VALID_S10_CODE = "RR123456789CZ"  # UPU S10 shape: 2 letters + 9 digits + 2 letters

_PATCH_TARGET = "custom_components.ceska_posta.api.CeskaPostaApiClient.async_get_parcels"


def _known(code: str) -> dict:
    sample = active_sample(code)
    return {code: {"backbone": sample["backbone"], "enrichment": sample["enrichment"]}}


def _unknown(code: str) -> dict:
    sample = not_found_sample(code)
    return {code: {"backbone": sample["backbone"], "enrichment": sample["enrichment"]}}


def test_normalize_tracking_code_strips_and_uppercases():
    assert normalize_tracking_code("ab-1234567890 c") == "AB1234567890C"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


def test_valid_tracking_code_bounds():
    assert valid_tracking_code(VALID_CODE)
    assert valid_tracking_code(VALID_S10_CODE)
    assert not valid_tracking_code("ABC")  # too short
    assert not valid_tracking_code("A" * 13)  # no digits
    assert not valid_tracking_code("AB123456789012")  # no trailing letter / wrong length


async def test_user_flow_creates_hub_without_input(hass):
    """No account, no postcode — the entry is created straight away."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Ceska Posta"
    assert result["options"][CONF_PARCELS] == []


async def test_second_hub_rejected(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort"
    # single_config_entry in the manifest aborts before the flow runs.
    assert result["reason"] == "single_instance_allowed"


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )


def _init_input(
    *, add="", remove=None, history=False,
    interval="30",
    filter_type="days", amount=7,
) -> dict:
    """Build the sectioned options-form submission."""
    parcels: dict = {"add": add}
    if remove is not None:
        parcels["remove"] = remove
    return {
        "parcels": parcels,
        "delivered": {
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        "history": {CONF_INCLUDE_HISTORY: history},
        "polling": {CONF_REFRESH_INTERVAL: interval},
    }


async def _open_options_step(hass, entry, step_id: str):
    """Start the options flow and select one of its two top-level routes."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_parcel_list_can_be_cleared(hass):
    """A submitted empty list removes the final manually tracked parcel."""
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_PARCELS: [{CONF_TRACKING_CODE: "EXAMPLE111111"}]})
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_settings_preserve_parcel_list(hass):
    """Saving settings must never replace the manually tracked parcel list."""
    parcels = [{CONF_TRACKING_CODE: "EXAMPLE111111"}]
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_PARCELS: parcels})
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DELIVERED_FILTER_TYPE: "days", CONF_DELIVERED_FILTER_AMOUNT: 7, CONF_INCLUDE_HISTORY: False, CONF_REFRESH_INTERVAL: "30"}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == parcels

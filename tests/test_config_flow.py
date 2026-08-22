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


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_known(VALID_CODE))):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add=VALID_CODE.lower())
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: VALID_CODE}]


async def test_options_add_code_with_separators(hass):
    """Pasted codes with spaces/dashes are sanitised like the consumer site."""
    entry = _hub([])
    entry.add_to_hass(hass)
    spaced = f"{VALID_CODE[:2]}-{VALID_CODE[2:6]} {VALID_CODE[6:]}"

    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_known(VALID_CODE))):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add=spaced)
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: VALID_CODE}]


async def test_options_add_invalid_tracking_code(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(add="abc")
    )
    assert result["errors"]["base"] == "invalid_tracking_code"


async def test_options_add_unknown_tracking_code_rejected(hass):
    """A live lookup that resolves to "not found" blocks the add."""
    entry = _hub([])
    entry.add_to_hass(hass)

    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_unknown(VALID_CODE))):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add=VALID_CODE)
        )
    assert result["errors"]["base"] == "unknown_tracking_code"
    assert result["type"] != "create_entry"


async def test_options_add_fails_open_on_lookup_failure(hass):
    """A transient fetch error must not block adding a parcel."""
    entry = _hub([])
    entry.add_to_hass(hass)

    with patch(_PATCH_TARGET, new=AsyncMock(return_value={})):  # neither surface answered
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add=VALID_CODE)
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: VALID_CODE}]


async def test_options_add_duplicate_rejected(hass):
    entry = _hub([{CONF_TRACKING_CODE: VALID_CODE}])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(add=VALID_CODE, remove=[])
    )
    assert result["errors"]["base"] == "already_tracked"


async def test_options_remove_parcel(hass):
    entry = _hub([
        {CONF_TRACKING_CODE: VALID_CODE},
        {CONF_TRACKING_CODE: VALID_CODE_2},
    ])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(remove=[VALID_CODE])
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {VALID_CODE_2}


async def test_options_remove_then_readd_same_code(hass):
    """Remove-then-add order: re-adding a just-removed code works."""
    entry = _hub([{CONF_TRACKING_CODE: VALID_CODE}])
    entry.add_to_hass(hass)

    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_known(VALID_CODE))):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _init_input(add=VALID_CODE, remove=[VALID_CODE])
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: VALID_CODE}]


async def test_options_changes_interval_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _init_input(
            interval="120",
            history=True, filter_type="parcels", amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_REFRESH_INTERVAL] == 120
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5

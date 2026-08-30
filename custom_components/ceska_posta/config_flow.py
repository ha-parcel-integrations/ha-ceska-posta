"""Config flow for the Ceska Posta parcel tracker integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CeskaPostaApiClient
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_REFRESH_INTERVAL,
    CONF_TRACKING_CODE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_NEW_REFRESH_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    REFRESH_INTERVAL_AUTO,
    REFRESH_INTERVAL_OPTIONS,
)
from .parcels import is_not_found

_LOGGER = logging.getLogger(__name__)

# Two accepted shapes in one 13-character namespace:
#   CZ domestic:  2 letters + 10 digits + 1 letter   (e.g. AB1234567890C)
#   UPU S10:      2 letters + 9 digits  + 2 letters   (e.g. RR123456789CZ)
# Neither endpoint validates this — Balikovna checks length only, ParcelHistory
# checks nothing — so this regex plus the live lookup below are the only real
# validation. It is also what the ``track_parcel`` service and the
# e-mail-parsing example automation validate against.
_TRACKING_CODE_RE = re.compile(r"^[A-Za-z]{2}\d{9,10}[A-Za-z]{1,2}$")


def normalize_tracking_code(value: str) -> str:
    """Return the tracking code upper-cased with separators stripped.

    Uppercase matters here beyond cosmetics: both endpoints accept lowercase
    but echo it back verbatim, which would split one parcel into two entities
    across a case change.
    """
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


def valid_tracking_code(value: str) -> bool:
    """Whether ``value`` matches the Ceska Posta tracking-code format."""
    return len(value) == 13 and bool(_TRACKING_CODE_RE.match(value))


async def async_code_is_known(hass, tracking_code: str) -> bool:
    """Live-lookup a tracking code before accepting it.

    Neither endpoint validates the code format, so this is the only real
    check: reject a code whose only state is the ``-3``/``-4`` "no such
    consignment" sentinel. Fails **open** (treats the code as known) when the
    lookup itself errors — a transient network hiccup should not block adding
    a parcel; the next poll will surface the real state.
    """
    client = CeskaPostaApiClient(async_get_clientsession(hass))
    result = (await client.async_get_parcels([tracking_code])).get(tracking_code)
    if result is None or (result["backbone"] is None and result["enrichment"] is None):
        return True
    return not is_not_found({"id": tracking_code, **result})


def _current_parcels(entry: ConfigEntry) -> list[dict[str, str]]:
    """Return a mutable copy of the tracked parcels list."""
    return [dict(item) for item in entry.options.get(CONF_PARCELS, [])]


def _interval_selector() -> selector.SelectSelector:
    """Return the refresh-interval dropdown selector (options translated via strings)."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[REFRESH_INTERVAL_AUTO] + [str(m) for m in REFRESH_INTERVAL_OPTIONS],
            translation_key=CONF_REFRESH_INTERVAL,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class CeskaPostaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the Ceska Posta integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> CeskaPostaOptionsFlowHandler:
        """Return the options flow handler."""
        return CeskaPostaOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the Ceska Posta hub — single instance, no input needed.

        Tracking is keyed on the tracking code alone (no account, no postal
        code), so there is nothing to ask at setup: the entry is created
        straight away and parcels are added afterwards via the options flow,
        the ``ceska_posta.track_parcel`` service or a dashboard button.
        ``single_config_entry`` in the manifest enforces one hub. Ceska Posta
        keys tracking on the code alone — no postcode, no account, on either
        surface — so there is nothing per-parcel to collect beyond the code
        itself (added later via the options flow).
        """
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Ceska Posta",
            data={},
            options={
                CONF_PARCELS: [],
                CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                # New entries default to dynamic polling (dynamic-polling.md
                # Section 5.2); an entry that predates "auto" keeps reading
                # DEFAULT_REFRESH_INTERVAL via the coordinator's .get()
                # fallback instead.
                CONF_REFRESH_INTERVAL: DEFAULT_NEW_REFRESH_INTERVAL,
                CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
            },
        )


class CeskaPostaOptionsFlowHandler(OptionsFlow):
    """Manage tracked parcels, history and polling in one sectioned form.

    Mirrors the other suite carriers' section layout (here: ``parcels`` /
    ``delivered`` / ``history`` / ``polling``). Changes apply live via HA's
    options-update listener (which refreshes the coordinator), so new/removed
    per-parcel sensors appear and disappear immediately.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer parcel management separately from integration settings."""
        return self.async_show_menu(
            step_id="init", menu_options=["parcels", "settings"]
        )

    async def async_step_parcels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the complete tracked-code list."""
        errors: dict[str, str] = {}
        if user_input is not None:
            codes = list(
                dict.fromkeys(
                    normalize_tracking_code(code)
                    for code in user_input.get("tracking_codes", [])
                    if normalize_tracking_code(code)
                )
            )
            if any(not valid_tracking_code(code) for code in codes):
                errors["base"] = "invalid_tracking_code"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        **self.config_entry.options,
                        CONF_PARCELS: [{CONF_TRACKING_CODE: code} for code in codes],
                    },
                )

        current_codes = [
            parcel[CONF_TRACKING_CODE] for parcel in _current_parcels(self.config_entry)
        ]
        schema = vol.Schema(
            {
                vol.Optional("tracking_codes"): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id="parcels",
            data_schema=self.add_suggested_values_to_schema(
                schema, {"tracking_codes": current_codes}
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle non-parcel integration settings."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    CONF_DELIVERED_FILTER_TYPE: user_input[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        user_input[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(user_input[CONF_INCLUDE_HISTORY]),
                    CONF_REFRESH_INTERVAL: (
                        REFRESH_INTERVAL_AUTO
                        if user_input[CONF_REFRESH_INTERVAL] == REFRESH_INTERVAL_AUTO
                        else int(user_input[CONF_REFRESH_INTERVAL])
                    ),
                },
            )

        current = self.config_entry.options
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DELIVERED_FILTER_TYPE,
                        default=current.get(
                            CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["days", "parcels"],
                            translation_key=CONF_DELIVERED_FILTER_TYPE,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_DELIVERED_FILTER_AMOUNT,
                        default=current.get(
                            CONF_DELIVERED_FILTER_AMOUNT,
                            DEFAULT_DELIVERED_FILTER_AMOUNT,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_INCLUDE_HISTORY,
                        default=current.get(
                            CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                        ),
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_REFRESH_INTERVAL,
                        default=str(
                            current.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
                        ),
                    ): _interval_selector(),
                }
            ),
        )

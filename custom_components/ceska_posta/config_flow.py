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
from homeassistant.data_entry_flow import section
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
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
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
            options=[str(m) for m in REFRESH_INTERVAL_OPTIONS],
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
                CONF_REFRESH_INTERVAL: DEFAULT_REFRESH_INTERVAL,
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
        """Show and handle the single sectioned options form."""
        errors: dict[str, str] = {}
        parcels = _current_parcels(self.config_entry)

        if user_input is not None:
            parcels_section = user_input.get("parcels", {})
            delivered_section = user_input.get("delivered", {})
            history_section = user_input.get("history", {})
            polling_section = user_input.get("polling", {})

            # Remove first, then add — so re-adding a just-removed code works.
            to_remove = set(parcels_section.get("remove", []))
            parcels = [p for p in parcels if p[CONF_TRACKING_CODE] not in to_remove]

            add_code = normalize_tracking_code(parcels_section.get("add") or "")
            if add_code:
                if not valid_tracking_code(add_code):
                    errors["base"] = "invalid_tracking_code"
                elif any(p[CONF_TRACKING_CODE] == add_code for p in parcels):
                    errors["base"] = "already_tracked"
                elif not await async_code_is_known(self.hass, add_code):
                    errors["base"] = "unknown_tracking_code"
                else:
                    parcels.append({CONF_TRACKING_CODE: add_code})

            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_PARCELS: parcels,
                        CONF_DELIVERED_FILTER_TYPE: delivered_section[
                            CONF_DELIVERED_FILTER_TYPE
                        ],
                        CONF_DELIVERED_FILTER_AMOUNT: int(
                            delivered_section[CONF_DELIVERED_FILTER_AMOUNT]
                        ),
                        CONF_INCLUDE_HISTORY: bool(
                            history_section[CONF_INCLUDE_HISTORY]
                        ),
                        CONF_REFRESH_INTERVAL: int(
                            polling_section[CONF_REFRESH_INTERVAL]
                        ),
                    },
                )

        current = self.config_entry.options

        parcels_fields: dict[Any, Any] = {vol.Optional("add", default=""): str}
        if parcels:
            parcels_fields[vol.Optional("remove", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=p[CONF_TRACKING_CODE],
                            label=p[CONF_TRACKING_CODE],
                        )
                        for p in parcels
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )

        schema = vol.Schema(
            {
                vol.Required("parcels"): section(
                    vol.Schema(parcels_fields), {"collapsed": False}
                ),
                vol.Required("delivered"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_DELIVERED_FILTER_TYPE,
                                default=current.get(
                                    CONF_DELIVERED_FILTER_TYPE,
                                    DEFAULT_DELIVERED_FILTER_TYPE,
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
                        }
                    ),
                    {"collapsed": True},
                ),
                vol.Required("history"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_INCLUDE_HISTORY,
                                default=current.get(
                                    CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                                ),
                            ): selector.BooleanSelector(),
                        }
                    ),
                    {"collapsed": True},
                ),
                vol.Required("polling"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_REFRESH_INTERVAL,
                                # str(): selector option values are strings, so a
                                # stored int default trips "expected str" on submit.
                                default=str(
                                    current.get(
                                        CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL
                                    )
                                ),
                            ): _interval_selector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )

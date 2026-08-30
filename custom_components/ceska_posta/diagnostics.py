"""Diagnostics support for the Ceska Posta parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CeskaPostaConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Non-optional here: the
# Balikovna surface returns the recipient's real name, e-mail and phone number
# to any anonymous caller who knows the tracking number, which also makes the
# tracking number itself a PII dereference key. Redacted by key name so both
# the flat enrichment body and the nested pickupPlace object are covered.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "receiver",
    "url",
    # Balikovna enrichment payload — recipient identity
    "recipient",
    "recipientEmail",
    "recipientPhone",
    "telefonCislo",
    "telefonNazev",
    "telefonTyp",
    # pickupPlace — address / GPS / phone, one flat set covers both nesting
    # levels the redaction has to reach
    "address",
    "street",
    "houseNumber",
    "orientationNumber",
    "postCode",
    "addressPostCode",
    "latitude",
    "longitude",
    "phone",
    # the tracking number itself dereferences to all of the above
    "packageId",
    "id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CeskaPostaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Ceska Posta config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "polling": {
            "current_tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }

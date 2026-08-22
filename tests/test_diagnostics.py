"""Tests for Ceska Posta diagnostics."""
from unittest.mock import MagicMock

from custom_components.ceska_posta.diagnostics import (
    async_get_config_entry_diagnostics,
)

CODE = "AB1234567890C"


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": CODE}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": CODE,
            "sender": "Example Shop",
            "receiver": "Example Recipient",
            "status": "out_for_delivery",
            "raw": {
                "id": CODE,
                "backbone": {"id": CODE, "attributes": {"weight": 1.0}},
                "enrichment": {
                    "packageId": CODE,
                    "recipient": "Example Recipient",
                    "recipientEmail": "recipient@example.test",
                    "recipientPhone": "+420000000000",
                    "sender": "Example Shop",
                    "pickupPlace": {
                        "name": "Example Point",
                        "address": "Example street 1, 100 00, Prague",
                        "street": "Example street",
                        "postCode": "100 00",
                        "latitude": "50.0",
                        "longitude": "14.0",
                        "phone": "+420111111111",
                    },
                },
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    raw = result["incoming"][0]["raw"]
    assert raw["id"] == "**REDACTED**"
    assert raw["backbone"]["id"] == "**REDACTED**"
    assert raw["enrichment"]["packageId"] == "**REDACTED**"
    assert raw["enrichment"]["recipient"] == "**REDACTED**"
    assert raw["enrichment"]["recipientEmail"] == "**REDACTED**"
    assert raw["enrichment"]["recipientPhone"] == "**REDACTED**"
    assert raw["enrichment"]["pickupPlace"]["address"] == "**REDACTED**"
    assert raw["enrichment"]["pickupPlace"]["street"] == "**REDACTED**"
    assert raw["enrichment"]["pickupPlace"]["postCode"] == "**REDACTED**"
    assert raw["enrichment"]["pickupPlace"]["latitude"] == "**REDACTED**"
    assert raw["enrichment"]["pickupPlace"]["longitude"] == "**REDACTED**"
    assert raw["enrichment"]["pickupPlace"]["phone"] == "**REDACTED**"
    # the merchant name is not redacted — it is not personal data
    assert raw["enrichment"]["sender"] == "Example Shop"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"

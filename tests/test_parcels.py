"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping can be tested
as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.ceska_posta.parcels as parcels_module
from custom_components.ceska_posta.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.ceska_posta.parcels import (
    DESIGNATION_CODES,
    NOT_FOUND_CODES,
    NOTIFICATION_CODES,
    STATUS_RANK,
    apply_delivered_filter,
    build_history,
    derive_status,
    is_not_found,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracking_url,
)

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    NOT_FOUND_CODE,
    OTHER_CODE,
    active_sample,
    backbone_body,
    backbone_event,
    delivered_sample,
    enrichment_body,
    enrichment_event,
    in_transit_sample,
    not_found_sample,
    pickup_sample,
    raw,
)


@pytest.fixture(autouse=True)
def _reset_one_shot_warnings():
    """One-shot warning state is module-global — reset it so tests don't
    depend on execution order."""
    parcels_module._unmapped_statuses_logged.clear()
    parcels_module._not_found_logged.clear()
    parcels_module._bg_anomaly_logged.clear()
    parcels_module._eta_populated_warned = False
    parcels_module._bg1_before_terminal_warned = False
    yield


# ---------------------------------------------------------------------------
# STATUS_RANK / derive_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("-L", ParcelStatus.REGISTERED),
        ("-M", ParcelStatus.REGISTERED),
        ("21", ParcelStatus.IN_TRANSIT),
        ("-F", ParcelStatus.IN_TRANSIT),
        ("-I", ParcelStatus.IN_TRANSIT),
        ("-B", ParcelStatus.IN_TRANSIT),
        ("-K", ParcelStatus.IN_TRANSIT),
        ("5K", ParcelStatus.IN_TRANSIT),
        ("51", ParcelStatus.IN_TRANSIT),
        ("53", ParcelStatus.OUT_FOR_DELIVERY),
        ("P2", ParcelStatus.AT_PICKUP_POINT),
        ("82", ParcelStatus.AT_PICKUP_POINT),
        ("91", ParcelStatus.DELIVERED),
    ],
)
def test_status_rank_known_codes(code, expected):
    assert derive_status([{"id": code, "date": "2026-01-01"}]) == expected


def test_derive_status_empty_is_unknown():
    assert derive_status([]) == ParcelStatus.UNKNOWN


def test_derive_status_is_highest_rank_not_last_event():
    """BG1 has been observed appearing after the terminal 91 event — it must
    not make a delivered parcel look like it's still at a pickup point."""
    events = [
        {"id": "-M", "date": "2026-04-24"},
        {"id": "21", "date": "2026-04-25"},
        {"id": "53", "date": "2026-04-27"},
        {"id": "91", "date": "2026-04-27"},
        {"id": "BG1", "date": "2026-04-28", "text": ""},  # after the terminal event
    ]
    assert derive_status(events) == ParcelStatus.DELIVERED


def test_derive_status_excludes_notifications():
    events = [{"id": "21", "date": "2026-01-01"}, {"id": "42", "date": "2026-01-01"}]
    assert derive_status(events) == ParcelStatus.IN_TRANSIT


def test_derive_status_excludes_designation_records():
    events = [
        {"id": "-L", "date": "2026-01-01"},
        {"id": "BG4", "date": "2026-01-01", "text": ""},
        {"id": "BG5", "date": "2026-01-01", "text": ""},
    ]
    assert derive_status(events) == ParcelStatus.REGISTERED


def test_derive_status_excludes_not_found_sentinels():
    assert derive_status([{"id": "-3", "date": "2026-01-01"}]) == ParcelStatus.UNKNOWN
    assert derive_status([{"id": "-4", "date": "2026-01-01"}]) == ParcelStatus.UNKNOWN


def test_derive_status_unmapped_code_warns_once(caplog):
    events = [{"id": "TELEPORTED", "date": "2026-01-01", "idIcon": 9, "text": "??"}]
    assert derive_status(events) == ParcelStatus.UNKNOWN
    assert derive_status(events) == ParcelStatus.UNKNOWN
    assert caplog.text.count("TELEPORTED") == 1  # second call is a no-op
    assert caplog.text.count("issues/new") == 1


def test_status_rank_and_code_sets_are_disjoint():
    """A code cannot be both a live status and an excluded record."""
    excluded = NOTIFICATION_CODES | DESIGNATION_CODES | NOT_FOUND_CODES
    assert not (set(STATUS_RANK) & excluded)


# ---------------------------------------------------------------------------
# BG-family anomaly warnings
# ---------------------------------------------------------------------------


def test_unexpected_bg_code_warns_once(caplog):
    events = [{"id": "BG9", "date": "2026-01-01"}]
    derive_status(events)
    derive_status(events)
    assert caplog.text.count("BG9") == 2  # once per call site, deduped by code
    assert "unrecognised BG-family" in caplog.text


def test_bg1_before_terminal_warns_once(caplog):
    events = [
        {"id": "-L", "date": "2026-01-01"},
        {"id": "BG1", "date": "2026-01-02", "text": ""},
        {"id": "91", "date": "2026-01-03"},
    ]
    derive_status(events)
    derive_status(events)
    assert caplog.text.count("before the terminal") == 1


def test_bg1_after_terminal_does_not_warn(caplog):
    events = [
        {"id": "91", "date": "2026-01-01"},
        {"id": "BG1", "date": "2026-01-02", "text": ""},
    ]
    derive_status(events)
    assert "before the terminal" not in caplog.text


# ---------------------------------------------------------------------------
# is_not_found
# ---------------------------------------------------------------------------


def test_is_not_found_backbone_sentinel():
    raw_data = raw(
        NOT_FOUND_CODE,
        backbone=backbone_body(
            NOT_FOUND_CODE, events=[backbone_event("-3", "2026-01-01", "no record")]
        ),
        enrichment=None,
    )
    assert is_not_found(raw_data) is True


def test_is_not_found_enrichment_sentinel():
    raw_data = raw(
        NOT_FOUND_CODE, backbone=None, enrichment=enrichment_body(NOT_FOUND_CODE, sender="-")
    )
    assert is_not_found(raw_data) is True


def test_is_not_found_false_for_a_real_parcel():
    assert is_not_found(delivered_sample()) is False


def test_is_not_found_false_when_both_surfaces_missing():
    """No data at all is a fetch failure, not a confirmed "not found"."""
    assert is_not_found(raw(OTHER_CODE, backbone=None, enrichment=None)) is False


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_tracking_url():
    assert tracking_url(ACTIVE_CODE) == (
        f"https://www.balikovna.cz/cs/sledovat-balik/-/balik/{ACTIVE_CODE}"
    )
    assert tracking_url(None) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest_and_drops_excluded():
    events = [
        {"id": "-M", "date": "2026-04-24", "text": "registered"},
        {"id": "42", "date": "2026-04-24", "text": "sms alert"},
        {"id": "21", "date": "2026-04-25", "text": "in transit"},
        {"id": "BG4", "date": "2026-04-24", "text": ""},
        {"id": "91", "date": "2026-04-27", "text": "delivered"},
    ]
    history = build_history(events)
    assert [entry["raw_status"] for entry in history] == [
        "registered",
        "in transit",
        "delivered",
    ]
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [{"id": "21", "date": f"2026-04-{day:02d}", "text": "moved"} for day in range(1, 26)]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"id": None, "date": "2026-01-01"}]) == []
    assert build_history([{"id": "21"}]) == []  # no date -> no timestamp


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            {"id": "-L", "date": "2026-04-24", "text": "fine"},
            {"id": "21", "date": "not-a-date", "text": "odd"},
        ]
    )
    assert [entry["raw_status"] for entry in history] == ["fine", "odd"]


def test_build_history_falls_back_to_code_without_text():
    history = build_history([{"id": "21", "date": "2026-04-24", "text": ""}])
    assert history[0]["raw_status"] == "21"


def test_build_history_unmapped_code_has_null_status():
    history = build_history([{"id": "WEIRD", "date": "2026-04-24", "text": "?"}])
    assert history[0]["status"] is None


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_dimensions_is_not_a_declared_capability():
    """dimensionType is a size-class letter, not L x W x H — None by design."""
    assert "dimensions" not in CAPABILITIES
    assert normalize_parcel(delivered_sample())["dimensions"] is None


def test_capabilities_match_what_normalize_parcel_actually_returns():
    delivered = normalize_parcel(delivered_sample())
    pickup = normalize_parcel(pickup_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "delivery_window" in CAPABILITIES:
        assert pickup["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "ceska_posta"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["sender"] == "Example Shop"
    assert parcel["receiver"] == "Example Recipient"
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "The consignment was delivered."
    assert parcel["delivered"] is True
    # date + time merged in from the enrichment surface's matching event.
    assert parcel["delivered_at"] == "2026-04-27T16:42:00"
    # A delivered parcel drops its ETA — the window is meaningless once it has
    # arrived.
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["url"] == (
        f"https://www.balikovna.cz/cs/sledovat-balik/-/balik/{DELIVERED_CODE}"
    )
    assert parcel["weight"] == 1.25
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED
    assert parcel["history"][-1]["status"] == ParcelStatus.DELIVERED


def test_normalize_active_parcel():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_in_transit_parcel():
    parcel = normalize_parcel(in_transit_sample())
    assert parcel["status"] == ParcelStatus.IN_TRANSIT


def test_normalize_pickup_parcel_uses_stored_to_as_planned_to():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == (
        "Example Point Central Station, Example street 1, 100 00, Prague"
    )
    # The named ETA fields are never populated; storedTo (the collection
    # deadline) is what a user actually needs while awaiting pickup.
    assert parcel["planned_to"] == "2026-04-29"


def test_normalize_not_found_parcel_is_unknown_and_warns_once(caplog):
    sample = not_found_sample()
    parcel = normalize_parcel(sample)
    normalize_parcel(sample)  # second call must not warn again
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["sender"] is None  # "-" sentinel maps to None
    assert parcel["receiver"] is None  # "" maps to None
    assert caplog.text.count("have no record for tracked parcel") == 1


def test_normalize_no_data_at_all_is_unknown_without_warning(caplog):
    """Neither surface answered — different from a confirmed not-found."""
    parcel = normalize_parcel(raw(OTHER_CODE, backbone=None, enrichment=None))
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["weight"] is None
    assert parcel["history"] is None
    assert "have no record" not in caplog.text


def test_normalize_zero_weight_is_none():
    sample = active_sample()
    sample["backbone"]["attributes"]["weight"] = 0
    assert normalize_parcel(sample)["weight"] is None


def test_normalize_dash_sender_is_none():
    sample = active_sample()
    sample["enrichment"]["sender"] = "-"
    assert normalize_parcel(sample)["sender"] is None


def test_normalize_empty_recipient_is_none():
    sample = active_sample()
    sample["enrichment"]["recipient"] = ""
    assert normalize_parcel(sample)["receiver"] is None


def test_normalize_falls_back_to_status_message_without_event_text():
    sample = active_sample()
    for event in sample["backbone"]["states"]["state"]:
        event["text"] = ""
    sample["enrichment"]["statusMessage"] = "OUT FOR DELIVERY"
    assert normalize_parcel(sample)["raw_status"] == "OUT FOR DELIVERY"


def test_normalize_falls_back_to_enrichment_events_when_backbone_missing():
    """A failed backbone chunk still leaves the enrichment call's own events."""
    enrichment = enrichment_body(
        OTHER_CODE,
        events=[
            enrichment_event("-M", "2026-01-01", "registered"),
            enrichment_event("21", "2026-01-02", "in transit", icon_id="2"),
        ],
    )
    parcel = normalize_parcel(raw(OTHER_CODE, backbone=None, enrichment=enrichment))
    assert parcel["status"] == ParcelStatus.IN_TRANSIT


def test_normalize_eta_populated_warns_once_ever(caplog):
    sample = active_sample()
    sample["backbone"]["attributes"]["dorucovaniOd"] = "2026-04-29T13:00:00Z"
    normalize_parcel(sample)
    normalize_parcel(sample)
    assert caplog.text.count("ETA fields were populated") == 1


def test_normalize_keeps_raw_payload():
    sample = active_sample()
    assert normalize_parcel(sample)["raw"] is sample


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels

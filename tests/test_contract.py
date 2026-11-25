"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.ceska_posta.const import ParcelStatus
from custom_components.ceska_posta.parcels import normalize_parcel, parse_iso

from .payloads import (
    OTHER_CODE,
    active_sample,
    backbone_event,
    delivered_sample,
    in_transit_sample,
    not_found_sample,
    pickup_sample,
    raw,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
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


def _collected() -> dict:
    """A Balikovna parcel whose terminal event follows the deposit."""
    sample = pickup_sample()
    states = sample["backbone"]["states"]["state"]
    states.append(backbone_event("91", "2026-04-28", "The consignment was picked up."))
    return sample


def _moment_only() -> dict:
    sample = active_sample()
    sample["backbone"]["attributes"]["dorucovaniOd"] = "2026-04-29T13:00:00Z"
    return sample


def _date_only() -> dict:
    sample = active_sample()
    sample["backbone"]["attributes"]["dorucovaniOd"] = "2026-04-29"
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "in_transit": in_transit_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "moment_only": _moment_only,
    "date_only": _date_only,
    "not_found": not_found_sample,
    "bare": lambda: raw(OTHER_CODE, backbone=None, enrichment=None),
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-28T00:00:00"
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Point Central Station"


def test_a_door_delivery_is_not_pickup():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_the_collection_deadline_is_not_a_delivery_window():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    parcel = normalize_parcel(_moment_only())
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(parcel["planned_from"]).astimezone(end.tzinfo).date()


def test_a_date_covers_the_whole_day():
    parcel = normalize_parcel(_date_only())
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()

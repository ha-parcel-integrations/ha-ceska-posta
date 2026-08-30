"""Constants for the Ceska Posta parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "ceska_posta"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping a carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Every value not listed here must come back as a literal ``None`` from
# normalize_parcel() in parcels.py (or, for "history", CONF_INCLUDE_HISTORY
# must not be wired to anything real) — never omit the key, just leave it
# empty. The docs site's carrier comparison table is generated straight from
# this constant, so drift here does not stay a local mistake, it becomes a
# wrong claim on the website.
#
# "dimensions" is deliberately absent: the only carrier field that looks like
# it (``dimensionType``) is a size-class letter ("M"), not L x W x H, so
# ``dimensions`` is always ``None`` by design, not a gap to fill in later.
CAPABILITIES = frozenset(
    {"weight", "delivery_window", "pickup_point", "url", "history"}
)

# Two keyless, unauthenticated JSON surfaces on the same backend.
#
# PARCEL_HISTORY_URL — the batched backbone. ``{ids}`` is up to
# BACKBONE_CHUNK_SIZE tracking codes joined with ``;`` (the cap is enforced by
# silent truncation, not an error, so chunking is the caller's job). Answers a
# 200 with a JSON array, one object per requested id, in request order. An
# unknown id comes back as a single event with id ``-3``/``-4`` rather than an
# HTTP error.
PARCEL_HISTORY_URL = (
    "https://b2c.cpost.cz/services/ParcelHistory/getDataAsJson"
    "?idParcel={ids}&language=en"
)
BACKBONE_CHUNK_SIZE = 10

# PACKAGE_URL — the Balikovna enrichment call. One tracking code per request
# (``;`` only keeps the first code, so this can't batch); adds sender, the
# pickup point and the storage deadline. An unknown code answers 200 with
# ``sender: "-"``.
PACKAGE_URL = "https://www.balikovna.cz/o/cpb/package/{tracking_code}?lang=en_US"

# TRACKING_URL is the human-facing deep link surfaced on each parcel's ``url``
# field. Deliberately the Balikovna form, not the postaonline.cz one — see
# this carrier's CLAUDE.md.
TRACKING_URL = "https://www.balikovna.cz/cs/sledovat-balik/-/balik/{tracking_code}"

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional across the suite, no
# user-facing interval option (see scaffold/CLAUDE.md's "Dynamic polling"
# section for the full algorithm and the reasoning behind it).
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight. This is a barcode-based coordinator (Section 2.1): when every
# tracked parcel is delivered, or nothing is tracked, polling stops entirely
# instead of falling to the mid tier — see coordinator.py's
# ``_hottest_tier_minutes``.
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20

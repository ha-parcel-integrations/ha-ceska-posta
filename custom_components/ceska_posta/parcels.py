"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options (logging aside, which is the
established one-shot-warning pattern used throughout the suite).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status/shape we do not handle yet. The ``?template=``
# parameter matters: without it the link opens a blank form, missing the
# version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-ceska-posta/issues/new"
    "?template=unrecognised_status.yml"
)

# Rank ladder, not a flat map: status is the highest rank *reached*, because
# the BG* designation records can appear after the terminal event — a
# last-event derivation would report a delivered parcel as still awaiting
# pickup.
STATUS_RANK: dict[str, tuple[int, ParcelStatus]] = {
    "-L": (10, ParcelStatus.REGISTERED),  # Receipt of data about consignment
    "-M": (10, ParcelStatus.REGISTERED),  # Receipt of data about consignment
    "21": (20, ParcelStatus.IN_TRANSIT),  # sent for transportation
    "-F": (20, ParcelStatus.IN_TRANSIT),  # is transported
    "-I": (20, ParcelStatus.IN_TRANSIT),  # left the logistics hub
    "-B": (30, ParcelStatus.IN_TRANSIT),  # to the delivering post office
    "-K": (30, ParcelStatus.IN_TRANSIT),  # to Balikovna
    "5K": (30, ParcelStatus.IN_TRANSIT),  # to Balikovna
    "51": (40, ParcelStatus.IN_TRANSIT),  # being prepared for delivery
    "53": (50, ParcelStatus.OUT_FOR_DELIVERY),  # being delivered
    "P2": (60, ParcelStatus.AT_PICKUP_POINT),  # deposited to Balikovna
    "82": (60, ParcelStatus.AT_PICKUP_POINT),  # deposited to post office
    "91": (90, ParcelStatus.DELIVERED),  # delivered / picked up
}

# Excluded before ranking — these are not parcel states.
NOTIFICATION_CODES = frozenset({"42", "43"})  # SMS / e-mail alert records
DESIGNATION_CODES = frozenset({"BG1", "BG4", "BG5"})  # pickup-point records, empty text
NOT_FOUND_CODES = frozenset({"-3", "-4"})  # "no such consignment" sentinels
_EXCLUDED_FROM_RANK = NOTIFICATION_CODES | DESIGNATION_CODES | NOT_FOUND_CODES

# One-shot warning bookkeeping, mirrored across the suite: log once per HA
# session, not on every poll.
_unmapped_statuses_logged: set[str] = set()
_not_found_logged: set[str] = set()
_bg_anomaly_logged: set[str] = set()
_eta_populated_warned = False
_bg1_before_terminal_warned = False


def _warn_unmapped_status(code: str, id_icon: Any, text: str | None) -> None:
    """Log an unmapped stateId once, with the code, its idIcon and the text.

    The event text is logged after the code — carrier copy observed so far is
    generic, never a person's data, but the code is what actually identifies
    the gap, so it comes first.
    """
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Ceska Posta status — help us map it. Open an issue and "
        "paste this line: %s\n  stateId=%s idIcon=%s text=%r → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
        id_icon,
        text,
    )


def _warn_not_found(tracking_code: str) -> None:
    """Log once that neither surface has a record for a tracked code."""
    if tracking_code in _not_found_logged:
        return
    _not_found_logged.add(tracking_code)
    _LOGGER.warning(
        "Ceska Posta / Balikovna have no record for tracked parcel %s; "
        "showing it as unknown until it clears upstream.",
        tracking_code,
    )


def _warn_eta_populated(date_val: Any, od_val: Any, do_val: Any) -> None:
    """Log once, ever, the first time the named ETA fields are non-null.

    Inverse of the usual warning: these fields have never been observed
    populated, so the first real sighting is the datum that closes the ETA
    open question in the research doc.
    """
    global _eta_populated_warned
    if _eta_populated_warned:
        return
    _eta_populated_warned = True
    _LOGGER.warning(
        "Ceska Posta's ETA fields were populated for the first time "
        "(dorucovaniDate=%r dorucovaniOd=%r dorucovaniDo=%r) — please open "
        "an issue (%s) and paste this line so the ETA window can be mapped.",
        date_val,
        od_val,
        do_val,
        NEW_ISSUE_URL,
    )


def _warn_unexpected_bg_code(code: str) -> None:
    """Log once per code: a BG-family record outside BG1/BG4/BG5."""
    if code in _bg_anomaly_logged:
        return
    _bg_anomaly_logged.add(code)
    _LOGGER.warning(
        "Ceska Posta reported an unrecognised BG-family designation code "
        "%s — please open an issue (%s) and paste this line.",
        code,
        NEW_ISSUE_URL,
    )


def _warn_bg1_before_terminal() -> None:
    """Log once, ever: BG1 appeared somewhere other than after delivery."""
    global _bg1_before_terminal_warned
    if _bg1_before_terminal_warned:
        return
    _bg1_before_terminal_warned = True
    _LOGGER.warning(
        "Ceska Posta reported BG1 before the terminal delivery event — its "
        "meaning is unresolved and the status derivation assumes it is "
        "harmless. Please open an issue (%s) and paste the event sequence.",
        NEW_ISSUE_URL,
    )


def _check_bg_codes(events: list[dict]) -> None:
    """Warn on any BG* code we don't already know, and BG1 out of place."""
    ids = [event.get("id") for event in events]
    for code in ids:
        if code and code.startswith("BG") and code not in DESIGNATION_CODES:
            _warn_unexpected_bg_code(code)
    if "BG1" in ids:
        terminal_seen = False
        for code in ids:
            if code == "91":
                terminal_seen = True
            elif code == "BG1" and not terminal_seen:
                _warn_bg1_before_terminal()
                break


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds**; strings pass through
    untouched (their consumers are guarded by :func:`parse_iso`). Neither
    Ceska Posta surface has stamped a number in any sample, but the two
    unconfirmed ETA fields could in principle.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _event_timestamp(date: str | None, time: str | None) -> str | None:
    """Combine a Ceska Posta event's separate ``date`` + ``time`` into one ISO string.

    Most events are date-only; only a deposit event (``P2``) has carried a
    time on any sample. A bare date is treated as midnight.
    """
    if not date:
        return None
    return f"{date}T{time}:00" if time else f"{date}T00:00:00"


def _normalized_events(raw: dict) -> list[dict]:
    """Return status events as ``{id, date, time, text, idIcon}``, either surface.

    Surface A (the ParcelHistory backbone) has no per-event ``time`` field at
    all; Surface B (Balikovna) does, but it is only ever non-null on a deposit
    event. Both surfaces return the identical code sequence for the same
    consignment, so backbone events are preferred (richer, and what the
    diagnostics-safe ``idIcon``/``text`` come from) with the matching
    enrichment event's ``time`` folded in by ``(id, date)``. Falls back to the
    enrichment's own event list when the backbone fetch failed outright.
    """
    backbone_events = (
        ((raw.get("backbone") or {}).get("states") or {}).get("state") or []
    )
    enrichment_events = (raw.get("enrichment") or {}).get("packageStates") or []

    if backbone_events:
        times: dict[tuple[Any, Any], str] = {}
        for event in enrichment_events:
            if isinstance(event, dict) and event.get("time"):
                times.setdefault((event.get("stateId"), event.get("date")), event["time"])
        normalized = []
        for event in backbone_events:
            if not isinstance(event, dict):
                continue
            code, date = event.get("id"), event.get("date")
            normalized.append(
                {
                    "id": code,
                    "date": date,
                    "time": times.get((code, date)),
                    "text": event.get("text"),
                    "idIcon": event.get("idIcon"),
                }
            )
        return normalized

    return [
        {
            "id": event.get("stateId"),
            "date": event.get("date"),
            "time": event.get("time"),
            "text": event.get("text"),
            "idIcon": event.get("iconId"),
        }
        for event in enrichment_events
        if isinstance(event, dict)
    ]


def _is_backbone_not_found(raw: dict) -> bool:
    """Whether the backbone's only event is the ``-3``/``-4`` sentinel."""
    backbone = raw.get("backbone")
    if not backbone:
        return False
    events = (backbone.get("states") or {}).get("state") or []
    return len(events) == 1 and events[0].get("id") in NOT_FOUND_CODES


def _is_enrichment_not_found(raw: dict) -> bool:
    """Whether the enrichment call's ``sender == "-"`` not-found sentinel fired."""
    enrichment = raw.get("enrichment")
    return bool(enrichment) and enrichment.get("sender") == "-"


def is_not_found(raw: dict) -> bool:
    """Whether either surface reports "no such consignment" for this code."""
    return _is_backbone_not_found(raw) or _is_enrichment_not_found(raw)


def derive_status(events: list[dict]) -> ParcelStatus:
    """Return the canonical status: the highest rank reached, never the last event.

    Excludes SMS/e-mail notifications, pickup-point designation records
    (empty text, can appear after the terminal event) and the not-found
    sentinels before ranking. A code that is none of those and not in
    :data:`STATUS_RANK` is unmapped: it warns once and does not count.
    """
    _check_bg_codes(events)

    ranked: list[tuple[int, ParcelStatus]] = []
    for event in events:
        code = event.get("id")
        if not code or code in _EXCLUDED_FROM_RANK:
            continue
        rank = STATUS_RANK.get(code)
        if rank is None:
            _warn_unmapped_status(code, event.get("idIcon"), event.get("text"))
            continue
        ranked.append(rank)

    return max(ranked)[1] if ranked else ParcelStatus.UNKNOWN


def _terminal_event(events: list[dict]) -> dict | None:
    """Return the ``91`` (delivered/picked-up) event, if present."""
    for event in events:
        if event.get("id") == "91":
            return event
    return None


def _last_status_text(events: list[dict]) -> str | None:
    """Return the most recent non-excluded event's text, for ``raw_status``.

    Notification and designation records are skipped — their text is empty or
    generic alert copy, not a status description.
    """
    for event in reversed(events):
        if event.get("id") in NOTIFICATION_CODES | DESIGNATION_CODES:
            continue
        if event.get("text"):
            return event["text"]
    return None


def build_history(
    events: list[dict] | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the normalized event list.

    Both surfaces already return events oldest→newest; sorting here is a
    safety net. Drops notification (``42``/``43``) and designation
    (``BG1``/``BG4``/``BG5``) records — not parcel states — and caps at
    ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        code = event.get("id")
        if not code or code in NOTIFICATION_CODES | DESIGNATION_CODES:
            continue
        timestamp = _event_timestamp(event.get("date"), event.get("time"))
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": STATUS_RANK[code][1] if code in STATUS_RANK else None,
            "raw_status": event.get("text") or code,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the Balikovna consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict merging the two Ceska Posta surfaces.

    ``raw`` is ``{"id": tracking_code, "backbone": {...} | None, "enrichment":
    {...} | None}`` as built by :meth:`CeskaPostaApiClient.async_get_parcels`.
    """
    tracking_code = raw.get("id")
    backbone = raw.get("backbone") or {}
    enrichment = raw.get("enrichment") or {}
    attributes = backbone.get("attributes") or {}
    pickup_place = enrichment.get("pickupPlace") or {}

    events = _normalized_events(raw)

    if is_not_found(raw) and tracking_code:
        _warn_not_found(tracking_code)

    eta_date = attributes.get("dorucovaniDate")
    eta_from = attributes.get("dorucovaniOd")
    eta_to = attributes.get("dorucovaniDo")
    if eta_date or eta_from or eta_to:
        _warn_eta_populated(eta_date, eta_from, eta_to)

    status = derive_status(events)
    delivered = status is ParcelStatus.DELIVERED
    terminal = _terminal_event(events) if delivered else None
    delivered_at = (
        _event_timestamp(terminal.get("date"), terminal.get("time"))
        if terminal
        else None
    )

    # The named ETA fields have never been observed populated; while
    # at_pickup_point, storedTo (the collection deadline) is the date a user
    # actually needs, so it takes priority when the named window is empty.
    planned_from = to_iso_timestamp(eta_from)
    planned_to = to_iso_timestamp(eta_to)
    if status is ParcelStatus.AT_PICKUP_POINT and not planned_to:
        planned_to = to_iso_timestamp(enrichment.get("storedTo"))

    weight = attributes.get("weight")
    if not weight:  # 0 and missing both mean "unknown", not zero
        weight = None

    sender = enrichment.get("sender")
    if sender in (None, "-"):
        sender = None

    pickup_point = None
    point_name = pickup_place.get("name")
    if point_name:
        point_address = pickup_place.get("address")
        pickup_point = f"{point_name}, {point_address}" if point_address else point_name

    return {
        "carrier": "ceska_posta",
        "barcode": tracking_code,
        "sender": sender,
        "receiver": enrichment.get("recipient") or None,
        "status": status,
        "raw_status": _last_status_text(events) or enrichment.get("statusMessage"),
        "delivered": delivered,
        "delivered_at": delivered_at if delivered else None,
        "planned_from": None if delivered else planned_from,
        "planned_to": None if delivered else planned_to,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": pickup_point,
        "url": tracking_url(tracking_code),
        "weight": weight,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]

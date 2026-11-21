"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options (logging aside, which is the
established one-shot-warning pattern used throughout the suite).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any
from zoneinfo import ZoneInfo

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

_PRAGUE = ZoneInfo("Europe/Prague")

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
    global _eta_populated_warned  # noqa: PLW0603 - one-shot per-process flag
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
    global _bg1_before_terminal_warned  # noqa: PLW0603 - one-shot per-process flag
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
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
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
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
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
    backbone_events = ((raw.get("backbone") or {}).get("states") or {}).get(
        "state"
    ) or []
    enrichment_events = (raw.get("enrichment") or {}).get("packageStates") or []

    if backbone_events:
        times: dict[tuple[Any, Any], str] = {}
        for event in enrichment_events:
            if isinstance(event, dict) and event.get("time"):
                times.setdefault(
                    (event.get("stateId"), event.get("date")), event["time"]
                )
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
    if not enrichment:
        return False
    return enrichment.get("sender") == "-"


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


def _out_for_delivery_event(events: list[dict]) -> dict | None:
    """Return the last ``53`` (being delivered) event, if present."""
    for event in reversed(events):
        if event.get("id") == "53":
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


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


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

    planned_from, planned_to = planned_window(
        to_iso_timestamp(eta_from), to_iso_timestamp(eta_to), zone=_PRAGUE
    )

    # OUT_FOR_DELIVERY is defined as "on a delivery vehicle today" (see
    # ParcelStatus), so the ``53`` event's own date *is* the delivery day even
    # though the named ETA window is never populated — no time-of-day is ever
    # given, so the fallback spans the whole day.
    if status is ParcelStatus.OUT_FOR_DELIVERY and not planned_from:
        ofd_event = _out_for_delivery_event(events)
        ofd_date = ofd_event.get("date") if ofd_event else None
        if ofd_date:
            planned_from, planned_to = planned_window(ofd_date, None, zone=_PRAGUE)
    planned_from, planned_to = planned_for_status(status, planned_from, planned_to)

    weight = attributes.get("weight")
    if not weight:  # 0 and missing both mean "unknown", not zero
        weight = None

    sender = enrichment.get("sender")
    if sender in (None, "-"):
        sender = None

    pickup = is_pickup(
        bool(pickup_place),
        status,
        (
            STATUS_RANK[event["id"]][1] if event.get("id") in STATUS_RANK else None
            for event in events
        ),
    )

    return {
        "carrier": "ceska_posta",
        "barcode": tracking_code,
        "sender": sender,
        "receiver": enrichment.get("recipient") or None,
        "status": status,
        "raw_status": _last_status_text(events) or enrichment.get("statusMessage"),
        "delivered": delivered,
        "delivered_at": delivered_at if delivered else None,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": pickup_place.get("name") or None if pickup else None,
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
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]

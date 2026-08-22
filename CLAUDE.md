# Working in this repository

Home Assistant custom integration for **Ceska Posta (Czech Post), including
Balikovna** — Ceska Posta's own pickup-point/locker brand, same operator, same
tracking numbers, same backend. Distributed via HACS; not part of HA core. One
carrier in the [ha-parcel-integrations](https://github.com/ha-parcel-integrations)
suite, **generated from ha-carrier-template** — everything outside
*Carrier-specific notes* is suite-wide; when in doubt check the template or a
sibling repo. No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

**API mechanics live in `carrier-research/ceska-posta/api/`** (private research
repo) — the two endpoints, auth (none), batching, the `stateId` vocabulary and
its `ParcelStatus` derivation, the canonical field mapping and the timestamp
format. Not duplicated here; this section is integration-level decisions only.

**Two calls per refresh, not one.** `api.py`'s `async_get_parcels()` fetches
the ParcelHistory backbone (`b2c.cpost.cz`, batched — up to ten tracking codes
per call, chunked client-side) and then, per parcel, the Balikovna enrichment
call (`www.balikovna.cz`, cannot batch). The two are merged into one raw dict
per code — `{"id", "backbone", "enrichment"}` — before `parcels.py` ever sees
it. `coordinator.py` no longer fetches per-parcel; it iterates the merged
result and falls back to `_raw_cache` only when **both** halves are `None`
(a genuine fetch failure) — never for a normal "no such consignment" `200`,
which is real, current information and always shown.

**Not-found is a body condition, not an HTTP error.** Both surfaces answer
`200` for an unknown code (`stateId` `-3`/`-4` on the backbone, `sender ==
"-"` on the enrichment call). `parcels.is_not_found()` checks both signals;
`normalize_parcel` surfaces the result as a normal parcel in `unknown` status
(the `-3`/`-4` events are excluded from the status ladder, so this falls out
of the derivation naturally) plus a one-shot `WARNING` per tracking code.

**Status is a rank ladder, never the last event.** `parcels.STATUS_RANK` maps
each `stateId` to `(rank, ParcelStatus)`; the canonical status is the
highest rank reached across the parcel's events, after excluding SMS/e-mail
notification records (`42`/`43`) and pickup-point designation records
(`BG1`/`BG4`/`BG5`, empty text) — the latter because `BG1` has been observed
**after** the terminal `91` event, which would make a last-event derivation
report a delivered parcel as `at_pickup_point`. Three additional one-shot
`WARNING`s guard the edges the happy path doesn't cover: an unmapped
`stateId` (logs the code, `idIcon` and event text), the named
`dorucovaniDate`/`dorucovaniOd`/`dorucovaniDo` ETA fields ever going non-null
(never observed populated — the first sighting is data, not noise), and a
`BG*` code outside the known three, or a `BG1` seen before any terminal event.

**`dimensions` is always `None` by design**, not a gap — `dimensionType` is a
size-class letter (`"M"`), not L×W×H. `const.CAPABILITIES` omits `dimensions`
accordingly; keep the two in agreement if that ever changes.

**`planned_to` falls back to `storedTo`** (the Balikovna collection deadline)
while a parcel's status is `at_pickup_point`, since the named ETA window has
never been observed populated on any sample.

**The tracking-code format is validated three ways**, because neither
endpoint enforces it: `config_flow._TRACKING_CODE_RE` (13 chars, CZ domestic
*or* UPU S10 shape), then a **live lookup**
(`config_flow.async_code_is_known`) before the options flow accepts a new
code — it fails *open* on a transient fetch error (adds the code anyway; the
next poll will show the real state) and only rejects a code whose live result
resolves to `is_not_found`. The `ceska_posta.track_parcel` service does the
regex check only, not the live lookup — deliberately, so a service call never
blocks on a network round-trip.

**Diagnostics redaction is non-optional, not defensive.** The Balikovna
surface returns the recipient's real name, e-mail and phone to *any*
anonymous caller who knows the tracking number — the tracking number itself
is therefore a PII dereference key on that host, which is why `packageId`/`id`
are in `diagnostics.TO_REDACT` alongside the obvious name/e-mail/phone/address
keys. Side effect worth knowing: redacting the key `id` also blanks each raw
history event's `stateId` in a diagnostics dump (that field is reused as
`"id"` there too) — a deliberate over-redaction trade-off, not an oversight.

**Balikovna is a second brand for the same operator, not a separate
integration** — `README.md`, the HACS description and `manifest.json`
name-adjacent copy all say "Ceska Posta (Czech Post), including Balikovna" so
HACS search surfaces this integration under either name; many Czech users
don't know the two are the same operator. The user-facing tracking URL
(`const.TRACKING_URL`, used in `device.py` and README examples) is
deliberately the Balikovna form (`balikovna.cz/cs/sledovat-balik/-/balik/`),
not `postaonline.cz`.

**Do not build:** `postaonline.cz`'s HTML track-and-trace page (scrape
liability, no JSON, fine only as a user-facing link — never a data source);
the `b2b.postaonline.cz` contract API (business-only credential, HMAC-signed);
`getDataAsXml` (same data as the JSON sibling); an account/inbox model
(neither surface has one); a `dimensions` value.

## Options and reloads

The options flow is one sectioned form (`data_entry_flow.section`); changes apply
without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  retunes `coordinator.update_interval` and calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately.
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

The user-tunable poll interval is a deliberate HACS divergence (see
CONVENTIONS.md); a carrier that throttles is generated with a fixed cadence and no
polling option at all.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.ceska_posta
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in the `api/` subfolder of this carrier's
directory under the private `carrier-research/ceska-posta/`, never in this repo.

# Ceska Posta (Czech Post), including Balikovna — Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-ceska-posta.svg)](https://github.com/ha-parcel-integrations/ha-ceska-posta/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your **Ceska Posta (Czech
Post)** parcels — including parcels handled through **Balikovna**, Ceska
Posta's own pickup-point and locker brand. They are the same operator, the
same tracking numbers and the same backend; Balikovna is just kept visibly
separate in the market (own site, own app, own logo), so this integration
answers to both names. No account is needed for either — enter the tracking
code yourself, just like on the Ceska Posta or Balikovna website.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Dynamic polling](#dynamic-polling)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Ceska Posta / Balikovna parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `at_pickup_point` / `delivered` / …), the carrier's own status text, the expected delivery window, the pickup point (for Balikovna locker/post-office deliveries) and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar with the expected delivery windows
- `ceska_posta.track_parcel` / `ceska_posta.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- A Ceska Posta or Balikovna parcel and its tracking code (13 characters, from
  the shipping confirmation e-mail or the missed-delivery card) — no account
  needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-ceska-posta` as an **Integration**.
3. Install **Ceska Posta (Czech Post), including Balikovna** and restart Home Assistant.

### Manual

Copy `custom_components/ceska_posta` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Ceska Posta**. There is nothing to fill in: the hub is created immediately (Ceska Posta tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`ceska_posta.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls Ceska Posta on a
dynamic, status-driven schedule with nothing to configure. See
[Dynamic polling](#dynamic-polling) below.

## Dynamic polling

Rather than polling Ceska Posta at the same rate around the clock, the
integration adjusts its own cadence to what your tracked parcels are
actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every Ceska Posta hub
  out there polls at exactly the same second.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Ceska Posta → ⋮ → Delete**. Nothing is stored on Ceska Posta's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.ceska_posta_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.ceska_posta_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.ceska_posta_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.ceska_posta_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.ceska_posta_last_successful_update` | Diagnostic: when Ceska Posta was last polled successfully |
| `calendar.ceska_posta_deliveries` | Expected delivery dates for active parcels, read-only, no extra API calls |
| `button.ceska_posta_refresh` | Forces an immediate poll without waiting for the next scheduled interval |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. Ceska Posta's happy path — `registered` → `in_transit` → `out_for_delivery` → `at_pickup_point` (Balikovna) → `delivered` — is fully covered; `returning` and `problem` have no observed carrier code yet and are listed for completeness (a future carrier status may map to them once one is seen):

| Status | Meaning |
|---|---|
| `registered` | Announced / received by Ceska Posta |
| `in_transit` | In the sorting network |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Waiting for you at a Balikovna locker or post office |
| `delivered` | Delivered, or picked up from a pickup point |
| `returning` | Going back to the sender *(no Ceska Posta code maps here yet)* |
| `problem` | Ceska Posta reports an exception *(no Ceska Posta code maps here yet)* |
| `unknown` | Not yet scanned, no record found for the tracking code, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Ceska Posta device):

| Event | When |
|---|---|
| `ceska_posta_parcel_registered` | A new parcel appears in the active list |
| `ceska_posta_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `ceska_posta_parcel_delivered` | A parcel is delivered |
| `ceska_posta_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `ceska_posta.track_parcel` | `tracking_code` | Start tracking a parcel |
| `ceska_posta.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.ceska_posta: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Ceska Posta / Balikovna have no record for
  that tracking code yet (a "no such consignment" response is a normal `200`,
  not an error), or the code is wrong. It picks up automatically once
  scanned. Adding a genuinely unrecognised code in the options dialog is
  rejected up front by a live lookup.
- **A status logs "Unrecognised Ceska Posta status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-ceska-posta/issues/new) with the logged line so the mapping can be extended. Ceska Posta publishes no complete status-code list, so `returning`/`problem`/failed-delivery codes are expected to surface this way the first time they occur.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public, keyless tracking endpoints as the
Ceska Posta and Balikovna consumer websites. It is not affiliated with,
endorsed by, or supported by Ceska Posta. Be gentle with the polling
interval.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)

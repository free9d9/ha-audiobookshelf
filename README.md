<div align="center">

<img src="https://brands.home-assistant.io/audiobookshelf/icon.png" width="120" alt="Audiobookshelf">

# Audiobookshelf for Home Assistant

**Real-time.** Your library changes, and Home Assistant knows immediately.

[![Validate](https://github.com/free9d9/ha-audiobookshelf/actions/workflows/validate.yml/badge.svg)](https://github.com/free9d9/ha-audiobookshelf/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/)
[![Release](https://img.shields.io/github/v/release/free9d9/ha-audiobookshelf?display_name=tag&sort=semver)](https://github.com/free9d9/ha-audiobookshelf/releases)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.6%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/github/license/free9d9/ha-audiobookshelf)](LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen.svg)](#development)

</div>

---

[Audiobookshelf](https://www.audiobookshelf.org/) has always emitted a live event stream
over Socket.IO - the same one its own web UI listens to. Nothing consumed it. This
integration does, so your entities change the moment your library does, rather than
whenever the next poll happens to land. A REST poll runs behind it purely as a safety net.

Press play on your phone and the media player in Home Assistant turns `playing` in about a
tenth of a second. Add a book and the poster wall updates without a refresh.

**What it is not:** Audiobookshelf has no remote-control API, so nothing here pretends to
pause your phone. For casting and playback, use
[Music Assistant](https://www.music-assistant.io/music-providers/audiobookshelf/) - this
integration deliberately does not compete with it. What it *does* add is the part nobody
else has: real-time state, a recently-added feed, and a resume action that syncs progress
back to Audiobookshelf **as the right person**.

## What you get

**Per user** - `media_player.audiobookshelf_<user>`

What each person is listening to right now: title, author, cover art, duration and
position. Home Assistant extrapolates the position while it plays.

Servers accumulate accounts, so a player is only *enabled* for people who have listened
in the last 30 days. The rest are created but disabled - switch them on in the UI.

**Per library**

- `sensor.<library>_recently_added` - when the newest item landed, with the full
  recently-added feed in a `data` attribute. The format matches
  [upcoming-media-card](https://github.com/custom-cards/upcoming-media-card), so poster-wall
  cards can render it directly.
- `sensor.<library>_items`, `_duration`, `_size` - library totals.
- `sensor.<library>_issues` - items whose files have gone missing or unreadable. Anything
  above zero is a book Audiobookshelf still lists but can no longer play, which is usually
  a rename or an unmounted share. Better to hear about it now than at bedtime.
- `sensor.<library>_last_scan` - when the library was last scanned.
- `binary_sensor.<library>_scanning` - on while a scan is running.
- `button.<library>_scan_library` - trigger a scan.

**Listening statistics**, per user, in hours: `listening_today`, `listening_this_week`,
`listening_this_month`, `listening_this_year` and `listening_all_time`. Weeks start on
Monday and every bucket is computed against your local calendar, not the server's. The
all-time sensor also carries an `hours_by_weekday` breakdown.

**Server**

- `sensor.audiobookshelf_listening_now` - how many people are *actually* listening, with
  who and what in its attributes.
- `sensor.audiobookshelf_open_sessions` - raw open-session count. Deliberately separate
  from the above: Audiobookshelf never closes a session when a client stops, so this
  number counts sessions that may be days stale.
- `sensor.audiobookshelf_users_online` - people with an app or the web UI open. Different
  again: online is not listening.
- `sensor.audiobookshelf_users` and `sensor.audiobookshelf_libraries` - counts, with names.
- `binary_sensor.audiobookshelf_realtime_updates` - whether the live connection is up.
  When it's off, the integration is still working; updates just fall back to the
  five-minute poll.
- `update.audiobookshelf_server` - the version you run against the newest release, with
  the full changelog. There is no install button on purpose: pulling a new image and
  migrating its database is your call, not something an integration should do behind your
  back. This is also the only request the integration makes outside your network, once a
  day, to GitHub. Disable the entity and it stops.

Those three "how many people" sensors sound alike and are not. `listening_now` is who is
playing something right now, `open_sessions` is what Audiobookshelf has forgotten to close,
and `users_online` is who has the app open.

**Events** on the Home Assistant bus. `audiobookshelf_playback_started` and
`audiobookshelf_playback_stopped` each carry `user`, `title`, `author`, `item_id` and
`device`. This is what makes "tell me when the kid's bedtime story ends" a three-line
automation. `audiobookshelf_scan_completed` fires when a library finishes scanning,
carrying `library`, `library_id`, `failed`, `added`, `updated`, `missing`, `elapsed_ms`
and a human-readable `summary`.

Cover art is proxied through Home Assistant and served over signed URLs, so posters load
from outside your LAN and over HTTPS without exposing the Audiobookshelf host, and
without a token ever reaching the browser.

## Resuming a book on a speaker

Audiobookshelf cannot be told to play anything - it has no remote-control API. What it
can do is open a session and expose the audio over a URL. The `continue_listening` action
hands that URL to a speaker Home Assistant already controls, seeks to where you left off,
and syncs your position back afterwards.

```yaml
action: audiobookshelf.continue_listening
data:
  entity_id: media_player.kitchen_speaker
  user: Brian        # optional
  seek: true         # optional, default true
```

### Linking users

Every progress route on the Audiobookshelf server is `/me/progress/...`. **There is no
admin route to write another user's progress.** So a single shared "Home Assistant"
account would quietly build up its own bookmarks and never touch yours - playing your book
on the kitchen speaker would leave your phone none the wiser, and two people in a
household would clobber each other.

Instead, go to **Configure** on the integration and link the people you want. The admin
key you already gave it mints one Audiobookshelf API key per person - **no passwords are
collected** - and revokes them again when you unlink. Playback then acts as that person,
and their progress syncs correctly.

Each linked user can optionally be mapped to a Home Assistant account. Home Assistant puts
the invoking user's id on a service call's context, so with a mapping, a dashboard button
resumes *your* book without naming you. Automations run without a user context, so pass
`user:` explicitly there. Entity state is global in Home Assistant - a `media_player`
cannot show one thing to you and another to your partner - so this per-person behaviour
applies to actions only.

### Seeking, and why progress is sometimes not written back

Resume needs the target speaker to support `SEEK`. Two things about that are worth knowing,
because both were found the hard way on real hardware:

- **A player does not advertise `SEEK` until it has media loaded.** An idle Chromecast
  reports no seek capability and gains it a second after the stream starts. So the
  capability is checked *after* `play_media`, once the player reaches `playing` - never
  before.
- **If the speaker still cannot seek, progress is deliberately not written back.**
  Playback starts at the top of the track, which is a mild annoyance; syncing that
  position to Audiobookshelf would rewind the listener's book to the beginning, which is
  not. In that case a warning is logged and the session closes without touching their
  saved position.

The same rule holds when a player stops: a stopped player reports no position at all, so
the session is closed with no sync data and Audiobookshelf keeps whatever it last knew.
Progress is only ever written from a position the speaker actually reported.

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories**
2. Repository: `https://github.com/free9d9/ha-audiobookshelf` - Category: **Integration**
3. Install **Audiobookshelf**, then restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Audiobookshelf.**

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=audiobookshelf)

### Manual

Copy `custom_components/audiobookshelf/` into your `config/custom_components/` and restart.

## Setup

You need a server URL and an API key.

In Audiobookshelf: **Settings → API Keys → New API Key.** **Leave the expiry blank.** An
expiring key will eventually stop the integration until you enter a new one. (If that
happens, Home Assistant will prompt you to re-enter it rather than silently going stale.)

Use an **admin** key if you want server-wide sensors.

That single key is all the integration ever asks for. Audiobookshelf rejects API keys on
its Socket.IO handshake, so the realtime connection is bootstrapped by trading the key for
a user token via `/api/me`. You never have to hand Home Assistant a password.

## How data updates

Push, with a poll as backstop:

- The Socket.IO connection delivers `item_added`, `item_updated`, `item_removed`,
  `items_added` and `items_updated`, plus `user_stream_update` for playback.
- Events are **coalesced over a 5-second window.** A library scan emits one event per
  item; without coalescing a 500-book scan would trigger 500 refreshes. This is the exact
  problem that has kept webhook support stalled upstream
  ([advplyr/audiobookshelf#1857](https://github.com/advplyr/audiobookshelf/issues/1857)).
- While someone is listening the fallback poll tightens to 30 seconds, because
  Audiobookshelf emits **nothing at all when a client pauses**.
- If the socket drops, the REST poll keeps things fresh and
  `binary_sensor.audiobookshelf_realtime_updates` turns off.

Measured on a 2.35.1 server: a metadata change reaches Home Assistant in ~5.8 s; pressing
play shows up in ~0.1 s; stopping resolves in ~6.9 s.

### Why "playing" is harder than it looks

Three quirks of the Audiobookshelf API, all verified against 2.35.1 source and a live
server, shape how this works:

1. **Sessions are never closed.** `/api/sessions/open` returns sessions that stopped hours
   or days ago. Liveness is judged by whether the position was synced in the last 90
   seconds, not by presence in that list.
2. **A close looks exactly like a start.** `closeSession()` emits `user_stream_update`
   *before* `removeSession()`, so the payload still contains the session that is
   disappearing. `removeSession()` emits nothing, and `user_session_closed` only reaches
   the session's own owner - never an admin. We tell them apart by session id, then
   confirm against `/api/sessions/open`, which is authoritative.
3. **Admins never see other people's position ticks.** `user_item_progress_updated` is
   emitted to the owning user's sockets only. Position therefore comes from the last known
   sync, and Home Assistant extrapolates forward.

## Known limitations

- **No playback control.** Audiobookshelf has no remote-control API for its playing
  clients - you cannot pause someone's phone from Home Assistant. For playback, use
  [Music Assistant](https://www.music-assistant.io/music-providers/audiobookshelf/), which
  has a proper Audiobookshelf provider. This integration deliberately does not compete
  with it.
- **The `data` attribute is large.** It's what makes poster cards work, but every
  attribute is written to the recorder database on each state change. Item descriptions
  are already stripped for this reason. If your database is precious, exclude it:

  ```yaml
  recorder:
    exclude:
      entities:
        - sensor.audiobooks_recently_added
        - sensor.e_books_recently_added
  ```

- **Offline (downloaded) playback is invisible.** When the mobile app plays a book it has
  downloaded, it never opens a session on the server - it plays locally and posts the
  result to `/api/session/local` afterwards. Verified against 2.35.1: that sync creates no
  open session and emits no `user_stream_update`, not even to an admin. So a listener who
  downloads everything shows as `idle`, `listening_now` does not count them, and no
  `playback_started` / `playback_stopped` events fire.

  Their **progress still arrives** whenever the app syncs, so `continue_listening` resumes
  correctly - at the last position the phone managed to upload. If the phone is still
  offline, you resume behind.

  This is Audiobookshelf's design, not something the integration can paper over. The one
  signal that *is* emitted is `user_item_progress_updated`, and it goes only to that user's
  own sockets - never to an admin.
- **Talkback speakers are refused.** Security-camera speakers and doorbell chimes register
  as `media_player` entities with `device_class: speaker`, indistinguishable from a real
  speaker by name or class. They give themselves away by offering `PLAY_MEDIA` and `STOP`
  but no `PAUSE`. `continue_listening` refuses them rather than play an audiobook into
  your driveway, and the entity picker filters them out.
- **No discovery.** Audiobookshelf doesn't advertise itself over mDNS, so the server URL
  must be entered by hand.
- Audiobookshelf types both audiobook and e-book libraries as `book`. They're told apart
  by whether their items carry an audio duration, which also decides cover aspect ratio
  (1:1 for audiobooks, 2:3 for e-books).

## Dashboards

**No custom card is needed, and none is shipped.** The entities are ordinary Home
Assistant entities, so the built-in cards render them properly. That is the point of
modelling playback as a real `media_player` rather than inventing attributes.

**Now playing.** The stock media control card gives you cover art, title, author, and a
progress bar that advances on its own - Home Assistant extrapolates it from
`media_position_updated_at`. Artwork is proxied and signed by Home Assistant, so nothing
reaches for your Audiobookshelf host.

```yaml
type: media-control
entity: media_player.audiobookshelf_brian
```

**Who is listening.** Remember that `open_sessions` counts sessions Audiobookshelf never
closed; `listening_now` counts people actually listening.

```yaml
type: entities
title: Audiobookshelf
entities:
  - sensor.audiobookshelf_listening_now
  - entity: sensor.audiobookshelf_open_sessions
    name: Open sessions (incl. stale)
  - binary_sensor.audiobookshelf_realtime_updates
  - sensor.audiobooks_items
  - sensor.audiobooks_duration
  - sensor.audiobooks_size
  - button.audiobooks_scan_library
```

**Recently added.** The `data` attribute is
[upcoming-media-card](https://github.com/custom-cards/upcoming-media-card) format, header
row and all, so that card works with no glue:

```yaml
type: custom:upcoming-media-card
entity: sensor.audiobooks_recently_added
title: Recently added audiobooks
image_style: poster
```

**A resume button.** Name no user: the action resolves whose book to resume from the Home
Assistant account that pressed it, provided that account is mapped in the integration's
options.

```yaml
type: button
name: Continue my book
icon: mdi:play
tap_action:
  action: perform-action
  perform_action: audiobookshelf.continue_listening
  data:
    entity_id: media_player.kitchen_speaker
```

## Starting a series over

`remove_progress` deletes a linked user's saved position, either on one item or on every
book in a series. It cannot be undone.

```yaml
action: audiobookshelf.remove_progress
data:
  user: Kid          # optional, resolved from the caller when mapped
  series: Mistborn   # or item_id: <library item id>
response_variable: removed
```

It returns what it deleted, so an automation can report it:

```yaml
  - action: notify.mobile_app
    data:
      message: "Reset {{ removed.count }} books for {{ removed.user }}."
```

Progress is per-user and only that user's own key may delete it, so the person must be
linked. Pass either `series` or `item_id`, never both.

## Migrating from wolffshots/hass-audiobookshelf

This integration takes the same `audiobookshelf` domain, so the two cannot be installed
side by side. It covers everything that one does, plus real-time updates, recently-added
feeds, per-user media players and listening statistics.

**Your entities will be recreated and their history will not carry over.** The unique ids
differ, and there is no safe way to claim another integration's entities. Expect to fix up
dashboards and automations once.

One behavioural note: that integration's `remove_my_progress` action never deleted
anything. Its filter reads `if metadata.series_name is str`, comparing a string against the
*type* `str`, which is always false. The equivalent here is `remove_progress`, and it works.

## What you can do with it

**Know when the bedtime story ends.** The single most-requested Audiobookshelf automation.
No polling, no template sensors - the event fires the moment the session closes.

```yaml
triggers:
  - trigger: event
    event_type: audiobookshelf_playback_stopped
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.user == 'Kid' }}"
actions:
  - action: light.turn_off
    target: { entity_id: light.nursery }
```

**Pause nothing, but dim everything.** Audiobookshelf has no remote control, so you cannot
pause someone's phone. You can react to them starting:

```yaml
triggers:
  - trigger: event
    event_type: audiobookshelf_playback_started
actions:
  - action: light.turn_on
    target: { entity_id: light.bedroom }
    data: { brightness_pct: 15 }
```

**Resume your book in the kitchen.** A dashboard button, with no user named - it resumes
whoever pressed it, provided their Home Assistant account is mapped.

```yaml
type: button
name: Continue my book
tap_action:
  action: perform-action
  perform_action: audiobookshelf.continue_listening
  data:
    entity_id: media_player.kitchen_speaker
```

**Announce new arrivals.** `sensor.<library>_recently_added` is a timestamp, so any change
means something landed.

```yaml
triggers:
  - trigger: state
    entity_id: sensor.audiobooks_recently_added
actions:
  - action: notify.mobile_app
    data:
      message: >-
        {{ state_attr('sensor.audiobooks_recently_added','data')[1].title }} was
        added by {{ state_attr('sensor.audiobooks_recently_added','data')[1].author }}
```

**Rebuild the library after a file drop.** `button.<library>_scan_library`, triggered by
whatever moves files onto your server.

**Tell me only when a scan found something wrong.** A rename or an unmounted share turns
books into dead entries, silently.

```yaml
triggers:
  - trigger: event
    event_type: audiobookshelf_scan_completed
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.missing > 0 or trigger.event.data.failed }}"
actions:
  - action: notify.mobile_app
    data:
      message: "{{ trigger.event.data.library }}: {{ trigger.event.data.summary }}"
```

**Poster walls.** The `data` attribute drops straight into
[upcoming-media-card](https://github.com/custom-cards/upcoming-media-card).

## Troubleshooting

**Everything is `unavailable` right after setup, or after changing options.**
Home Assistant reloads the config entry, and a reload takes a few seconds. Wait, then
reload the page. If it persists, check the entry state under Settings → Devices & Services.

**`binary_sensor.audiobookshelf_realtime_updates` is off.**
The Socket.IO connection is down and the integration has fallen back to polling every five
minutes. Everything still works, just slower. Usually the server restarted; it reconnects
on its own. If it stays off, check that nothing between Home Assistant and Audiobookshelf
is stripping WebSocket upgrades - a reverse proxy is the usual culprit.

**Setup fails with "Audiobookshelf rejected that API key."**
The key was revoked, expired, or belongs to a deleted user. Create a new one under
Settings → API Keys, leaving the expiry blank, then reconfigure.

**Server-wide sensors are missing or empty.**
`listening_now`, `open_sessions` and per-user media players need an **admin** key.
A key belonging to a normal user can only see that user.

**`continue_listening` says a speaker "cannot be paused."**
That entity is a talkback or notification speaker - a security camera's speaker, most
likely - not something to play an audiobook on. This is deliberate. Pick a real speaker.

**A book resumed from the beginning instead of where I left off.**
The target speaker does not support seeking. Progress was *not* written back, so nothing
was lost; play it on a speaker that can seek. Chromecast, Sonos and Music Assistant players
all can.

**Progress is not syncing back to Audiobookshelf.**
Either the user is not linked (Configure → link them), or the speaker cannot seek (see
above), or nothing is playing - position only syncs while the player reports `playing`.

**`sensor.audiobookshelf_open_sessions` shows listeners who are not listening.**
That is Audiobookshelf, not this integration: it never closes a session when a client
stops, so sessions linger for days. Use `sensor.audiobookshelf_listening_now`, which
filters on whether the position was synced recently.

## Removing the integration

Settings → Devices & Services → Audiobookshelf → ⋮ → **Delete**.

Deleting the entry closes any playback sessions it opened and stops the realtime
connection. Two things it does **not** clean up, because they live on the Audiobookshelf
server rather than in Home Assistant:

1. **The per-user API keys it minted.** Unlink users *before* deleting the entry and they
   are revoked for you. If you have already deleted it, remove them by hand in
   Audiobookshelf under Settings → API Keys - they are named `Home Assistant (<user>)`.
2. **The admin API key you created during setup.** Delete that one too, in the same place.

Then remove the integration from HACS.

## Development

```bash
pip install -r requirements-test.txt
pytest                                        # 141 tests, 99% coverage
mypy custom_components/audiobookshelf --strict
ruff check custom_components tests
```

Home Assistant requires Python 3.14 and cannot be imported on Windows. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the container recipe, and for the list of places
where Audiobookshelf's API documentation disagrees with the server.

### Quality scale

Tracked in [`quality_scale.yaml`](custom_components/audiobookshelf/quality_scale.yaml):
**47 rules done, 5 exempt, 2 outstanding.**

| Tier | Status |
| --- | --- |
| Bronze | Complete except `brands` (icon PR pending) |
| Silver | Complete - 99% coverage, reauth, graceful unavailability |
| Gold | Complete - diagnostics, devices, repairs, reconfigure, translations. `discovery` is *exempt*: Audiobookshelf advertises nothing over mDNS |
| Platinum | `async-dependency` and `inject-websession` done. `strict-typing` blocked only on [`aioaudiobookshelf`](https://github.com/music-assistant/aioaudiobookshelf) shipping a `py.typed` marker - our own code passes `mypy --strict` and ships one |

## Credits

- [wolffshots/hass-audiobookshelf](https://github.com/wolffshots/hass-audiobookshelf),
  the first Audiobookshelf integration for Home Assistant, and the source of the server
  and per-library sensor design.
- [Tech-Morph](https://github.com/wolffshots/hass-audiobookshelf/pull/127) - prior art on
  a media_player and per-user progress coordinator.
- [`aioaudiobookshelf`](https://pypi.org/project/aioaudiobookshelf/) - the async client
  library this is built on, also used by Music Assistant.

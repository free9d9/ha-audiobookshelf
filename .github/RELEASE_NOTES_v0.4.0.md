First public release.

A real-time Audiobookshelf integration, built because none existed. The only prior art
polls REST every 300 seconds and exposes aggregate counts with no attributes.
Audiobookshelf has emitted a Socket.IO event stream all along — the same one its own web UI
listens to. Nothing consumed it.

## What you get

- **Real-time state.** Press play and the media player turns `playing` in ~0.1s. A metadata
  change reaches a sensor in ~5.8s. The REST poll is a fallback, not the mechanism.
- **`media_player` per user** — title, author, cover art, duration, position. Read-only,
  because Audiobookshelf has no remote-control API and this integration will not pretend
  otherwise.
- **`sensor.<library>_recently_added`** in `upcoming-media-card` format, plus `items`,
  `duration` and `size`, and a `scan_library` button.
- **`sensor.audiobookshelf_listening_now`**, which counts people *actually* listening —
  deliberately separate from `open_sessions`, because Audiobookshelf never closes a session
  and that list is full of sessions days old.
- **`audiobookshelf.continue_listening`** — resume a linked user's book on any Home
  Assistant speaker, seeking to their saved position and syncing progress back **as them**.
- **Bus events** `audiobookshelf_playback_started` / `_stopped`, coalesced so a 500-book
  scan causes one refresh rather than five hundred.

## Setup takes one secret

Create a **non-expiring API key** in Audiobookshelf (Settings → API Keys). That is all.

Audiobookshelf rejects API keys on its Socket.IO handshake, so the realtime connection is
bootstrapped by trading the key for a user token via `/api/me`. You are never asked for a
password — not even to link other members of your household, whose keys are minted for them
through the admin API.

## Things learned the hard way

Several of these contradict the published API documentation, and every one was found by
trying it against a live 2.35.1 server:

- The documented cover endpoint 404s. The real one needs no auth.
- There is no `scan_start` / `scan_complete` socket event.
- `closeSession()` emits `user_stream_update` *before* `removeSession()`, so a close is
  byte-identical to a start. They are told apart by session id.
- Progress ticks are owner-scoped: an admin never sees them for anyone else.
- Nothing at all is emitted on pause.
- **Offline playback is invisible.** A downloaded book never opens a session, so the
  listener reads `idle` and no events fire. Their progress still arrives when the app syncs.

Two bugs, both caught by playing a real book on a real speaker, once rewound a listener's
audiobook to the beginning. A player does not advertise `SEEK` until media is loaded; and a
stopped player reports no position at all. Both are fixed and regression-tested. The rule
now is simple: never write a position the speaker did not report.

## Quality

141 tests, 99% coverage, `mypy --strict` clean, `ruff` clean. 47 of the 54 Home Assistant
quality-scale rules done, 5 exempt, 2 outstanding.

## Not in scope

Playback and casting belong to
[Music Assistant](https://www.music-assistant.io/music-providers/audiobookshelf/), which has
a proper Audiobookshelf provider. This integration does not compete with it.

## Credits

[wolffshots/hass-audiobookshelf](https://github.com/wolffshots/hass-audiobookshelf) for the
first Audiobookshelf integration and the sensor design; Tech-Morph for prior art on a
media_player; and [`aioaudiobookshelf`](https://github.com/music-assistant/aioaudiobookshelf),
the async client this is built on.

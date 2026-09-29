# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.12.0]

Verified against Audiobookshelf 2.37.0. Every server change from 2.35.1 through
2.37.0 was checked against the endpoints, socket events and auth this
integration uses; none of them breaks anything.

### Added

- **The realtime connection signs in with your API key on Audiobookshelf 2.37.0
  and later.** Older servers refused API keys on the Socket.IO handshake, so the
  key was traded for a legacy user token from `/api/me`, which Audiobookshelf
  now marks deprecated. From 2.37.0 the key itself is used, so revoking the key
  cuts off realtime updates too. Older servers keep the `/api/me` route.
- **A repair issue when Audiobookshelf and Home Assistant keep different
  clocks.** Audiobookshelf buckets listening time by its own calendar day, so a
  server left on the container default of UTC puts evening listening on the
  wrong day in the Today, This week and This month sensors. Audiobookshelf has
  reported its zone since 2.36.0; older servers are not checked.

### Changed

- **Realtime updates now means the server accepted us, not just that a socket
  is open.** Audiobookshelf answers a bad token with `auth_failed` and leaves
  the connection open, so the sensor used to read on while nothing was being
  delivered. It now turns on at the server's `init`, and turns off the moment
  the server refuses or the connection drops, rather than at the next poll. A
  refusal is logged with Audiobookshelf's reason, and triggers an immediate poll
  so a revoked key goes straight to the re-authentication prompt.

### Fixed

- **Real-time playback and scan updates were never arriving.** Since the move
  to aioaudiobookshelf-plus in 0.9.0, the library's `init_client()` registered
  its own handlers for `user_stream_update`, `task_started` and `task_finished`
  after ours, and Socket.IO keeps only one handler per event. So a new
  listening session only showed up on the next poll (up to five minutes), the
  per-library `scanning` sensors never turned on, and
  `audiobookshelf_plus_scan_completed` never fired. Our handlers are now
  registered after the library's, and a test runs the library's real
  `init_client()` so this cannot come back unseen. This is the likely cause of
  #5.
- The server update entity now has a title, "Audiobookshelf server". Settings >
  Updates heads each row with the device name and shows the title beside the
  new version, so with no title a new Audiobookshelf release appeared as
  "Audiobookshelf Plus 2.37.0" and read like an update to this integration. It
  still has no install button; upgrading the server stays your call.

## [0.11.0]

### Added

- **Actions can be pointed at a specific server.** `continue_listening`,
  `remove_progress` and `get_year_in_review` each take an optional
  `config_entry`, shown in the UI as a server picker. More than one
  Audiobookshelf server has always been configurable (the unique id is
  host:port), but the actions resolved through "the first loaded entry", so with
  two servers a dashboard button could act on the wrong one. `remove_progress`
  cannot be undone, which made that a real way to delete the wrong person's
  place in a book.

  With no `config_entry` given, an action now asks every configured server who
  the call is for and proceeds only when exactly one of them has a matching
  linked user, which is what happens with a single server and usually what
  happens with two. Where more than one matches, it raises and asks for the
  server rather than choosing. Nothing changes for a single-server setup.
- `remove_progress` and `get_year_in_review` name the server they acted on in
  their response, as `server`.

### Changed

- The integration is called **Audiobookshelf Plus** everywhere, matching
  `hacs.json`. It previously presented as "Audiobookshelf" in the manifest and
  the setup docs, which is the name wolffshots/hass-audiobookshelf already uses
  in HACS, so the two were hard to tell apart when browsing or on the
  integrations page.
- New config entries are titled `Audiobookshelf Plus (<host>)`. Existing entries
  keep the title they have; rename one from its ⋮ menu if you want the host in
  it. Device and entity names are unchanged.

### Fixed

- Unloading or reloading one server closed the playback sessions running against
  every other one. Sessions live in a single map keyed by speaker, and teardown
  walked all of it, so a listener mid-chapter on server B was hung up on because
  server A reloaded. Teardown is now scoped to the entry being unloaded.
- The signed cover-URL cache grew without bound. It was keyed by the full URL,
  which carries the item's `updatedAt`, so every edit to an item added a row and
  no row was ever removed. It is now keyed per item, so an edit replaces the row
  it supersedes; lapsed rows are swept when a new URL is signed, and an entry's
  rows are dropped when it unloads.

## [0.10.1]

### Fixed

- The "API key is about to expire" repair issue showed the expiry date in UTC
  rather than in Home Assistant's own timezone. The date comes from the key's
  `exp` claim, which is UTC, and west of Greenwich a key that lapses in the
  small hours UTC belongs to the previous local day. Anyone in a negative-offset
  zone was told their key survived a day longer than it does, and could act a
  day late on it. The date is now rendered with `dt_util.as_local`, so it
  follows whatever timezone the instance is configured for.
- The `last_updated` attribute on each user's media player rendered in UTC.
  Nothing parses it, so it exists purely to be read, and reading UTC digits in
  a negative-offset timezone can name the wrong day. It is now rendered in the
  instance's timezone. It remains offset-aware ISO-8601 and the same instant,
  so any template or card parsing it is unaffected.

Timestamps that Home Assistant renders itself are unchanged: the
`recently_added` and `last_scan` sensors hand over aware datetimes and are
already shown in local time, and the `data[]` entries in the recently-added
feed stay UTC ISO-8601 because they are transport for a card that formats them
itself.

## [0.10.0]

### Added

- Per-user last-session attributes on each user's media player, so a dashboard
  can show what someone was last listening to, and how far in, even when their
  live player is idle. Audiobookshelf reports a user's latest session for
  downloaded and offline playback too, which never opens a live session on the
  server, so this makes that otherwise invisible listening visible. When a user
  has a last session with a library item, the media player exposes `last_title`,
  `last_author`, `last_cover` (a signed cover-proxy URL, the same kind the
  recently-added feed uses), `last_position` and `last_duration` in seconds, and
  `last_updated`. The live playing, paused and idle behavior is unchanged.

## [0.9.0]

### Changed

- Switched the backing library from `aioaudiobookshelf` to `aioaudiobookshelf-plus`
  (version 0.2.0), a fully typed, PEP-561 compliant fork that ships a `py.typed`
  marker. Because the dependency's real types are now visible to `mypy --strict`,
  the integration no longer needs an `ignore_missing_imports` override for it, which
  satisfies the Platinum `strict-typing` quality-scale rule. This is a drop-in
  re-point with no behavioral change: the same client factories, schema classes,
  and client methods are used, only under the new import namespace.

## [0.8.0]

### Changed

- The integration domain changed from `audiobookshelf` to `audiobookshelf_plus`.
  A separate third-party integration already uses the `audiobookshelf` domain, and
  two integrations cannot share a `custom_components` folder name or a domain
  namespace on the same Home Assistant instance. Moving to `audiobookshelf_plus`
  lets this integration be installed side by side with the existing one without a
  folder or namespace collision.

### Migration notes

Because this is a brand-new domain, the old `audiobookshelf` domain simply ceases
to exist for this integration; there is no in-place registry migration from it.
After updating, the following names carry the new prefix:

- Entity ids now begin with the new domain, for example
  `sensor.audiobookshelf_listening_now` becomes
  `sensor.audiobookshelf_plus_listening_now`, and
  `media_player.audiobookshelf_<user>` becomes
  `media_player.audiobookshelf_plus_<user>`.
- Bus events are renamed: `audiobookshelf_playback_started`,
  `audiobookshelf_playback_stopped` and `audiobookshelf_scan_completed` become
  `audiobookshelf_plus_playback_started`, `audiobookshelf_plus_playback_stopped`
  and `audiobookshelf_plus_scan_completed`.
- Services move under the new domain: `audiobookshelf_plus.continue_listening`,
  `audiobookshelf_plus.remove_progress` and `audiobookshelf_plus.get_year_in_review`.

Update any automations, scripts and dashboards that reference the old entity ids,
events or services.

### Known issues

- The `audiobookshelf_plus` domain has no entry in `home-assistant/brands` yet, so
  the integration icon does not resolve until a brands PR is merged.

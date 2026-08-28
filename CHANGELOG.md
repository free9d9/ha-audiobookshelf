# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.1]

### Fixed

- The "API key is about to expire" repair issue showed the expiry date in UTC
  rather than in Home Assistant's own timezone. The date comes from the key's
  `exp` claim, which is UTC, and west of Greenwich a key that lapses in the
  small hours UTC belongs to the previous local day. Anyone in a negative-offset
  zone was told their key survived a day longer than it does, and could act a
  day late on it. The date is now rendered with `dt_util.as_local`, so it
  follows whatever timezone the instance is configured for.

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

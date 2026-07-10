# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

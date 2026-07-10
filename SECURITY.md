# Security policy

## Reporting a vulnerability

Please **do not** open a public issue. Use
[GitHub's private vulnerability reporting](https://github.com/free9d9/ha-audiobookshelf/security/advisories/new),
or email brian@freemanator.com.

You can expect an acknowledgement within a week.

## What this integration holds

Two kinds of credential, both of which are full account access on your Audiobookshelf
server. Treat a Home Assistant backup as you would treat those keys.

1. **The API key you enter during setup.** Stored in the config entry. An admin key is
   needed for server-wide sensors and for linking users.
2. **One API key per linked user**, minted by the integration through
   `POST /api/api-keys` so that no passwords are ever collected. Unlinking a user revokes
   their key on the server and removes it from Home Assistant.

Diagnostics redact both, along with every user id. If you paste diagnostics into an issue,
nothing sensitive goes with it — but read it first anyway.

## Design notes that are security-relevant

- **No token ever reaches the browser.** Cover art is proxied by Home Assistant and served
  over signed URLs (`async_sign_path`), never by pointing the frontend at your
  Audiobookshelf host with a token in the query string.
- **Audio is streamed from an unauthenticated, session-scoped URL**
  (`/public/session/{id}/track/{n}`), which Audiobookshelf exposes for exactly this
  purpose. The speaker never sees a credential. The URL stops working when the session
  closes.
- **A per-user key going bad does not trigger a reauth flow** for the whole integration.
  Only the entry's own key does.

## Scope

This integration talks to a server you run. It does not phone home, and it has no cloud
component.

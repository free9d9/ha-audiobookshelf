# Upstream work this integration depends on

Nothing here is required for the integration to run. Each is a patch to someone
else's repository, prepared but **not submitted** — that is a decision for the
repository owner to make under their own GitHub identity.

## 1. `music-assistant/aioaudiobookshelf` — ship `py.typed`

**Why:** Home Assistant's Platinum `strict-typing` rule requires the backing
library to be PEP-561 compliant. Our own code passes `mypy --strict` and ships
`py.typed`; this is the only thing standing between the integration and Platinum.

**Change:** add an empty `aioaudiobookshelf/py.typed`, and include it in the
package data. The library is already fully annotated — the marker is all that is
missing.

## 2. `music-assistant/aioaudiobookshelf` — bootstrap the socket from an API key

**Why:** `SocketClient` emits `auth` with whatever token it is given. Audiobookshelf
rejects API keys on the Socket.IO handshake (`auth_failed: Invalid token`), so a
caller holding only an API key cannot open a socket.

**Change:** when `SessionConfiguration.token` is an API key, fetch `/api/me` and
emit the `token` from that response instead. Verified working against 2.35.1.
This integration currently does the exchange itself.

## 3. `music-assistant/aioaudiobookshelf` — `sort` and `desc` on `get_library_items`

**Why:** there is no way to ask for a library's items newest-first. We use
`get_library_personalized_view()` and take the `recently-added` shelf instead,
which works but is a roundabout way to sort a list.

**Change:** pass `sort` and `desc` through to `/api/libraries/{id}/items`.

## 4. `music-assistant/aioaudiobookshelf` — a `user_stream_update` callback

**Why:** `SocketClient` has no hook for `user_stream_update`, which is the only
admin-visible signal that someone started or stopped playing. We register a
handler directly on the underlying `socketio.AsyncClient`.

**Change:** add `set_stream_update_callback()` alongside the existing
`set_item_callbacks()` and friends.

> Worth documenting upstream regardless: `closeSession()` emits
> `user_stream_update` *before* `removeSession()`, so the payload of a close still
> contains the session that is disappearing. A close is indistinguishable from a
> start except by session id.

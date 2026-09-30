"""Constants for the Audiobookshelf integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "audiobookshelf_plus"

# REST fallback poll. The socket carries the real-time signal; this is a safety net.
SCAN_INTERVAL: Final = timedelta(minutes=5)

# Audiobookshelf pushes when a session opens and closes, but NOT while it plays,
# and never on pause or resume. So playback state is polled on its own, from
# /api/sessions/open alone, which the server answers from memory. The full poll
# stays at SCAN_INTERVAL. See AudiobookshelfCoordinator._async_poll_sessions().
SESSION_POLL_INTERVAL: Final = timedelta(seconds=15)

# A playing client syncs its position every 10s (web) to 20s (iOS, measured on a
# live 2.37.0 server). A session not updated within this window has missed two
# syncs, so it is paused or abandoned, not playing. Audiobookshelf never closes
# sessions on its own, so /api/sessions/open is full of stale ones.
SESSION_FRESH_SECONDS: Final = 45

# Downloaded ("local") playback never enters /api/sessions/open and emits no
# socket event: the app posts it to /api/session/local, straight into the
# database, so it is read back from /api/sessions (newest first). AudioBooth
# syncs every ~20s of listening, but the official Android app only every 60s on
# a metered connection, which is exactly the away-from-home case, hence a wider
# window than streaming gets.
PLAY_METHOD_LOCAL: Final = 3
LOCAL_SESSION_FRESH_SECONDS: Final = 150
# A local session is never closed on the server, so after its last sync it
# shows as paused for this long, then the player goes idle.
LOCAL_SESSION_PAUSED_WINDOW: Final = timedelta(minutes=30)
# How many of the most recently updated sessions to scan for local ones.
RECENT_SESSIONS_LIMIT: Final = 20

# Audiobookshelf servers accumulate accounts. Only enable a media player by
# default for someone who has listened recently; the rest are created but
# disabled, and can be switched on from the UI.
USER_ENABLE_DAYS: Final = 30

# Events fired on the Home Assistant bus.
EVENT_PLAYBACK_STARTED: Final = f"{DOMAIN}_playback_started"
EVENT_PLAYBACK_STOPPED: Final = f"{DOMAIN}_playback_stopped"
EVENT_SCAN_COMPLETED: Final = f"{DOMAIN}_scan_completed"

# Audiobookshelf has no version-check endpoint of its own, so the only place to
# ask is GitHub. This is the integration's one outbound call; everything else
# talks to the local server. It is checked daily and never fails the refresh.
RELEASE_URL: Final = (
    "https://api.github.com/repos/advplyr/audiobookshelf/releases/latest"
)
RELEASE_CHECK_INTERVAL: Final = timedelta(days=1)

# Library scans arrive as generic tasks. There is no scan_start/scan_complete
# event despite what the docs' contents page implies.
TASK_LIBRARY_SCAN: Final = "library-scan"

# Linked users: {abs_user_id: {username, api_key, key_id, ha_user_id}}.
# Audiobookshelf only lets an account write its own progress, so resuming a book
# means acting as that person. The admin key mints one API key each -- no
# passwords are ever collected.
CONF_LINKED_USERS: Final = "linked_users"

# Optional field on every action: which config entry (which Audiobookshelf server)
# the call is for. One server is the normal case and needs no selector, but a
# household can run two -- and `remove_progress` is not undoable, so an action
# that cannot tell which server it means must refuse rather than guess.
CONF_CONFIG_ENTRY: Final = "config_entry"

SERVICE_CONTINUE_LISTENING: Final = "continue_listening"
SERVICE_REMOVE_PROGRESS: Final = "remove_progress"
SERVICE_YEAR_IN_REVIEW: Final = "get_year_in_review"

# Audiobookshelf rejects anything outside this range with a 400 (MeController).
MIN_STATS_YEAR: Final = 2000
MAX_STATS_YEAR: Final = 9999

# Listening statistics change only while someone listens, and cost one REST call
# per user. They get their own slow cadence rather than riding the 30s poll that
# runs while a session is live.
STATS_INTERVAL: Final = timedelta(minutes=15)

# Audiobookshelf API keys may be created with an expiry. Warn before one lapses
# rather than letting the integration die silently at midnight.
KEY_EXPIRY_WARN_DAYS: Final = 14
ISSUE_KEY_EXPIRING: Final = "api_key_expiring"

# Listening stats arrive bucketed by Audiobookshelf's calendar day. Warn when
# that is not Home Assistant's calendar day.
ISSUE_TIMEZONE_MISMATCH: Final = "timezone_mismatch"

# The first Audiobookshelf release that accepts an API key on the Socket.IO
# handshake (advplyr/audiobookshelf#4974). Older servers need the legacy token.
SOCKET_API_KEY_MIN_VERSION: Final = "2.37.0"

# How often to push a playing speaker's position back to Audiobookshelf. The
# official clients sync at roughly this cadence.
PROGRESS_SYNC_INTERVAL: Final = timedelta(seconds=20)

# A library scan emits one socket event per item. Coalesce them so a 500-book
# scan triggers one refresh, not five hundred. This is the exact problem that
# stalled webhook support upstream (advplyr/audiobookshelf#1857).
REFRESH_COOLDOWN_SECONDS: Final = 5.0

# Number of recently-added items exposed per library.
RECENT_LIMIT: Final = 10

# Items added within this window are flagged "new" for the card.
NEW_ITEM_DAYS: Final = 7

# Cover proxy. Covers are unauthenticated on ABS, but pointing a browser at the
# raw ABS host breaks off-LAN access and is blocked as mixed content under
# HTTPS, so we proxy and hand out signed HA URLs instead (see cover_proxy.py).
COVER_URL: Final = "/api/audiobookshelf_plus/cover/{entry_id}/{item_id}"

# Header object the Upcoming Media Card spec expects at data[0]. Consumers that
# do not understand it (our own card) simply slice it off.
PARSE_DICT_AUDIOBOOK: Final = {
    "title_default": "$title",
    "line1_default": "$number",
    "line2_default": "$release",
    "line3_default": "$runtime",
    "line4_default": "$genres",
    "icon": "mdi:headphones",
}
PARSE_DICT_EBOOK: Final = {
    "title_default": "$title",
    "line1_default": "$number",
    "line2_default": "$release",
    "line3_default": "$studio",
    "line4_default": "$genres",
    "icon": "mdi:book-open-page-variant",
}

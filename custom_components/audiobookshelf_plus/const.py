"""Constants for the Audiobookshelf integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "audiobookshelf_plus"

# REST fallback poll. The socket carries the real-time signal; this is a safety net.
SCAN_INTERVAL: Final = timedelta(minutes=5)

# Audiobookshelf pushes when a session opens and closes, but NOT while it plays,
# and never when it pauses. So while something is playing we poll faster in order
# to notice a pause. See AudiobookshelfCoordinator._next_interval().
SCAN_INTERVAL_LISTENING: Final = timedelta(seconds=30)

# A playing client syncs its position every ~15s. If a session has not been
# updated within this window it is paused or abandoned, not playing. Audiobookshelf
# never closes sessions on its own, so /api/sessions/open is full of stale ones.
SESSION_FRESH_SECONDS: Final = 90

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
# HTTPS, so we proxy and hand out signed HA URLs instead.
COVER_URL: Final = "/api/audiobookshelf_plus/cover/{entry_id}/{item_id}"
COVER_SIGN_TTL: Final = timedelta(days=7)
# Re-sign this long before expiry so the URL stays stable between polls and
# browsers keep their cached copy.
COVER_SIGN_RENEW: Final = timedelta(days=1)

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

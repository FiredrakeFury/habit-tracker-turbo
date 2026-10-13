"""Constants for the Habit Log integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "habit_log"
TITLE: Final = "Habit Log"
VERSION: Final = "0.3.0"

# The card is served from this integration's www/ folder and loaded on every
# dashboard automatically (no manual resource needed).
CARD_URL_BASE: Final = "/habit_log_static"
CARD_FILENAME: Final = "habit-log-card.js"

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1
SAVE_DELAY: Final = 1.0

# Folder (inside /config) that receives a JSON snapshot whenever a habit
# definition is deleted, before its entries are purged.
DELETED_SNAPSHOT_DIR: Final = "habit_log/deleted"

# Subentry types
SUBENTRY_HABIT: Final = "habit"
SUBENTRY_JOURNAL_FIELD: Final = "journal_field"

# Habit definition keys (subentry data)
CONF_NAME: Final = "name"
CONF_EMOJI: Final = "emoji"
CONF_COLOR: Final = "color"
CONF_KIND: Final = "kind"
CONF_TRACK_TIME: Final = "track_time"
CONF_ORDER: Final = "order"
CONF_ARCHIVED: Final = "archived"

KIND_COUNTER: Final = "counter"
KIND_STREAK: Final = "streak"
KINDS: Final = (KIND_COUNTER, KIND_STREAK)

# Journal field keys (subentry data)
CONF_FIELD_TYPE: Final = "field_type"
CONF_UNIT: Final = "unit"
CONF_MIN: Final = "min"
CONF_MAX: Final = "max"
CONF_STEP: Final = "step"
CONF_OPTIONS: Final = "options"
CONF_MULTILINE: Final = "multiline"
CONF_CREATE_SENSOR: Final = "create_sensor"
CONF_ACTIVE: Final = "active"

FIELD_NUMBER: Final = "number"
FIELD_RATING: Final = "rating"
FIELD_SELECT: Final = "select"
FIELD_BOOLEAN: Final = "boolean"
FIELD_TEXT: Final = "text"
FIELD_TYPES: Final = (FIELD_NUMBER, FIELD_RATING, FIELD_SELECT, FIELD_BOOLEAN, FIELD_TEXT)
NUMERIC_FIELD_TYPES: Final = (FIELD_NUMBER, FIELD_RATING)

# Integration options
CONF_ALLOWED_USERS: Final = "allowed_users"
CONF_WEEK_START: Final = "week_start"
CONF_CALENDAR_DETAILS: Final = "calendar_details"
WEEK_START_MONDAY: Final = "monday"
WEEK_START_SUNDAY: Final = "sunday"
DEFAULT_OPTIONS: Final = {
    CONF_ALLOWED_USERS: [],
    CONF_WEEK_START: WEEK_START_MONDAY,
    CONF_CALENDAR_DETAILS: False,
}

# Limits
MAX_NAME_LENGTH: Final = 40
MAX_EMOJI_LENGTH: Final = 16
MAX_NOTES_LENGTH: Final = 2000
MAX_TEXT_VALUE_LENGTH: Final = 2000
MAX_SELECT_OPTIONS: Final = 50
# Sanity limit only (typo guard); the card asks for confirmation above 24 h.
MAX_TIMED_DURATION: Final = timedelta(days=1100)
MAX_BACKFILL_DAYS: Final = 1100
FUTURE_TOLERANCE: Final = timedelta(minutes=5)

# Periods
PERIOD_DAY: Final = "day"
PERIOD_WEEK: Final = "week"
PERIOD_MONTH: Final = "month"
PERIOD_YEAR: Final = "year"
PERIOD_ALL: Final = "all"
PERIODS: Final = (PERIOD_DAY, PERIOD_WEEK, PERIOD_MONTH, PERIOD_YEAR, PERIOD_ALL)

# Services
SERVICE_LOG: Final = "log"
SERVICE_BACKFILL: Final = "backfill"
SERVICE_UPDATE_ENTRY: Final = "update_entry"
SERVICE_DELETE_ENTRY: Final = "delete_entry"
SERVICE_MERGE_HABITS: Final = "merge_habits"
SERVICE_LIST_ENTRIES: Final = "list_entries"
SERVICE_JOURNAL: Final = "journal"
SERVICE_UPDATE_JOURNAL: Final = "update_journal_entry"
SERVICE_RESET_COUNTER: Final = "reset_counter"
SERVICE_UNDO_RESET: Final = "undo_reset"

ATTR_HABIT: Final = "habit"
ATTR_DATE: Final = "date"
ATTR_START: Final = "start"
ATTR_END: Final = "end"
ATTR_DURATION: Final = "duration"
ATTR_NOTES: Final = "notes"
ATTR_ENTRY_ID: Final = "entry_id"
ATTR_START_DATE: Final = "start_date"
ATTR_END_DATE: Final = "end_date"
ATTR_DATES: Final = "dates"
ATTR_SOURCE: Final = "source"
ATTR_TARGET: Final = "target"
ATTR_VALUES: Final = "values"
ATTR_AT: Final = "at"

# Websocket
WS_PREFIX: Final = DOMAIN

# Dispatcher signal fired whenever data, definitions or the date change.
SIGNAL_CHANGED: Final = f"{DOMAIN}_changed"

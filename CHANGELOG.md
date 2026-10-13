# Changelog

## 0.3.0
- New: reset for counters (with or without time tracking).
  - Entries and all-time totals are kept; a reset only starts a new
    "since reset" cycle. All resets are stored, so the last one can be undone.
  - Card Stats tab: "All time vs since reset" table with Reset… (defaults to
    now, can be backdated, future blocked) and Undo last reset.
  - Sensors: Count since reset (counters) and Time since reset (time-tracked),
    `state_class: total` with `last_reset`.
  - Actions: `habit_log.reset_counter`, `habit_log.undo_reset`.
- Card: the quick-log row is now a "Log for today" panel of large buttons, one
  per habit, with emoji, name and today's status. It logs for the selected day
  and is shown on the Calendar tab only.
- Card: removed the "Add to this day" buttons under the day panel (the log
  panel does this now).
- Card: streak emoji in calendar days is now on by default
  (`show_streak_emoji: false` turns it off).
- Card: the "now" used for default times advances while the dashboard stays
  open, instead of staying at the time the data was last loaded.
- Storage: adds a `resets` key; no migration needed, older data loads as is.
- Packaging: installable and updatable through HACS as a custom repository.
  `manifest.json` gains `documentation`, `issue_tracker` and `codeowners`;
  brand icons added under `brand/` (shown by Home Assistant 2026.3 and newer).
- Files changed: `__init__.py`, `const.py`, `icons.json`, `manifest.json`,
  `runtime.py`, `sensor.py`, `services.py`, `services.yaml`, `stats.py`,
  `store.py`, `translations/en.json`, `websocket_api.py`,
  `www/habit-log-card.js`.

## 0.2.2
- Fix for Home Assistant 2026.10: the card could show "Configuration error"
  (Custom element doesn't exist) on some page loads.
  - Cause: the 2026.10 frontend swaps its custom element registry while
    starting up, and the card file could register before the swap.
  - Fix: the card now registers once the Home Assistant app element exists.
  - Verified on real HA 2026.10.0 and 2026.9.4 with repeated cold loads.
- Files changed: `const.py`, `manifest.json`, `www/habit-log-card.js`.

## 0.2.1
- Card: **Back-fill** form (date range and/or individual dates) with a preview
  of how many days will be added.
- Card: `show_streak_emoji` option shows streak habits' emoji in the day cells
  (and legend) in addition to their thread.
- Files changed: `const.py`, `manifest.json`, `www/habit-log-card.js`.

## 0.2.0
- New: Habit Log card, served by the integration and loaded on every dashboard
  automatically.
  - Calendar with streak threads and a featured streak fill.
  - Counter emoji markers and journal markers.
  - Day panel with add, edit and delete.
  - Journal forms generated from your fields.
  - Stats for week, month, year and all time, with a chart and the log.
  - Quick-log row with undo.
- Changed: maximum length of one time period raised from 14 days to about
  3 years. The card asks for confirmation above 24 hours.
- Added to the card's data API: today's per-habit summary, HA-local times for
  timed entries, and the version.

## 0.1.0
- Core integration:
  - Habits and journal fields managed in the UI.
  - Counters, streaks and time tracking.
  - Sensors, actions and a calendar entity.
  - Allowed users.
  - Deletion snapshots.

# Habit Log for Home Assistant (v0.3.0)

A local custom integration for habit counters, streaks, time tracking and a
configurable journal. No cloud services, no external Python packages, no
network access. Built and tested against Home Assistant **2026.9.4** and **2026.10.0**.

Includes the **Habit Log card**: month calendar with streak threads and a
featured streak fill, emoji markers for counters and streaks, a log panel with
one button per habit, a day panel to edit and delete entries, journal forms
built from your fields, week/month/year/all time statistics with a chart and the
full log, and resettable "since reset" totals for counters.

## Repository layout

| Path | Purpose |
|---|---|
| `custom_components/habit_log/` | **The integration.** This is what HACS (or a manual install) puts on your instance. |
| `dashboard-example.yaml` | Dashboard using the card |
| `CHANGELOG.md` | What changed per version |
| `tests/`, `pyproject.toml` | Test suite (server tests + browser tests for the card); not needed on your instance |
| `.github/workflows/` | CI: hassfest, HACS validation, tests, release on tag |

## 1. Install

### With HACS (recommended)

Requires [HACS](https://hacs.xyz/) and Home Assistant 2026.9 or newer.

1. HACS > three dots (top right) > **Custom repositories**.
2. Repository: `https://github.com/FiredrakeFury/habit-tracker-turbo`,
   Type: **Integration** > **Add**.
3. Search HACS for **Habit Log** > **Download** (latest release) > Download.
4. Restart Home Assistant. One log line at every startup is expected:
   `We found a custom integration habit_log which has not been tested by Home Assistant...`
5. Settings > Devices & services > **Add integration** > **Habit Log** > Submit.

**Updating:** new releases appear under **Settings > Updates** (and as
"Pending update" in HACS). Install, restart, then hard-reload the browser tab
(Ctrl+Shift+R; on the phone app: Settings > Companion app > Debugging > Reset
frontend cache). Your data is kept: it lives in `/config/.storage/habit_log`,
which updates never touch. Release notes are on the
[Releases page](https://github.com/FiredrakeFury/habit-tracker-turbo/releases).

**Moving from a manual install to HACS:** add the custom repository and
download as above. HACS replaces the `custom_components/habit_log` folder; your
config entry, habits and entries stay. Restart afterwards.

### Manual

1. Download the source zip of the latest
   [release](https://github.com/FiredrakeFury/habit-tracker-turbo/releases).
2. Copy the **whole** `custom_components/habit_log` folder to
   `/config/custom_components/habit_log` (for example with the Samba share app).
   The result must be `/config/custom_components/habit_log/manifest.json`.
3. Restart, then add the integration as in step 5 above.

## 2. Configure

**Options** (Habit Log > Configure):

| Option | Meaning |
|---|---|
| Allowed users | Only these users can log, edit, delete, or read entries through the card's data API. Empty = everyone. Automations are always allowed. |
| First day of the week | Used for "this week" sensors and the card. |
| Show notes and journal values in the calendar entity | Off by default. The calendar entity is readable by every HA user, so notes stay out of it unless you turn this on. |

**Habits** (Habit Log > **Add habit**):

| Setting | Notes |
|---|---|
| Name, emoji, color | Can be edited at any time. Past entries follow, because they reference the habit's permanent ID, not its name. |
| Type | **Counter**: counts occurrences, several per day allowed. **Streak**: one per day, consecutive-day streaks. Switching type keeps history and recalculates. |
| Track time | Counters only. Each entry is a period (start plus end or duration). You get entry counts **and** cumulative time. Periods crossing midnight show on both days; time is split between the days; the entry counts once, on its start day. |
| Sort order | Lower first. |
| Archived (edit only) | Hides the habit, removes its sensors, keeps its history. Turn off to restore. |

**Journal fields** (Habit Log > **Add journal field**), in two steps: name and
type, then type settings.

| Type | Settings |
|---|---|
| Number | Unit, min, max, step, optional sensor (e.g. Weight in kg) |
| Rating | Scale 1 to N (2 to 10), optional sensor (e.g. Energy 1-5) |
| Choice list | Your options; edit them any time (e.g. Workout: Great/Good/Poor) |
| Yes / no | None |
| Text | Single or multi-line |

Every journal entry stores the field's name, type and unit as they were when
recorded. Renaming, retyping or deactivating a field affects future entries
only; past entries keep showing what was recorded.

## 3. What gets created

Per habit there is one device with these sensors (entity IDs are fixed when
the habit is created and never change on rename):

| Habit type | Sensors |
|---|---|
| Counter | Count this week / month / year / all time, Count since reset, current streak, best streak, last logged |
| Counter + track time | The counter sensors plus Time this week / month / year / all time and Time since reset (hours) |
| Streak | Days this week / month / year / all time, current streak, best streak, last logged |

- Journal fields with "Create a sensor": `sensor.journal_<field>` holding the
  latest value, with long-term statistics for graphs.
- One calendar entity, `calendar.habit_log`, showing all entries, read-only.

Streak rule: a day you haven't logged yet doesn't break the streak until it
ends.

**Resetting counters** (counters only, with or without time tracking):
- A reset is a timestamp. Entries are never changed or deleted, and all-time
  totals keep counting everything.
- "Since reset" counts only what happened after the latest reset:
  - time-tracked periods: only the part after the reset; a period counts as an
    entry if it started after the reset;
  - date-based entries: entries for later days, or for the reset day if they
    were logged after the reset. Back-filling a day before the reset never
    changes "since reset".
- Every reset is kept, so "Undo last reset" goes back to the previous one.
- The since-reset sensors use `state_class: total` with `last_reset`, so HA's
  statistics treat the reset as a new cycle.

The sensors feed HA's long-term statistics going forward. For history,
including back-filled or edited data, the card computes statistics from the
raw entries.

## 4. The Habit Log card

The integration loads the card on every dashboard automatically. No resource
needs to be added. After installing or updating, reload the browser once.

Add it: Edit dashboard > Add card > search "Habit Log", or in YAML:

```yaml
type: custom:habit-log-card
title: Habits            # optional
habits: [Diet, Run]      # optional: which habits, in this order (default: all)
featured: Diet           # optional: streak that fills the day cells at first
show_quick_log: true     # log panel with one button per habit (Calendar tab)
show_stats: true         # Stats tab
show_journal: true       # 📝 markers, journal entries and forms
show_streak_emoji: true  # streak habits' emoji in the days; false = thread only
initial_view: calendar   # or stats
```

Using it:

- **Log panel** (Calendar tab only, at the top)
  - One large button per habit, with its emoji, name and today's status
    ("Tap to log", "Done", "2 logged", "1 h 30 min logged").
  - It logs for the selected day: "Log for today" by default; tap a past day
    in the calendar and it becomes "Log for Oct 3" with a "Back to today" link.
    Future days are disabled.
  - Streak: tap once. Counter: each tap adds one, with Undo. Time-tracked:
    opens a start + end/duration form. Journal: opens a form built from your
    fields.
- **Calendar**
  - Each streak is a colored thread along the bottom of the days; consecutive
    days join into one line.
  - Tap a streak in the legend to make it the **featured** streak, which fills
    the whole day. Tap it again to turn that off. The choice is remembered per
    device.
  - Counters and streaks show their emoji (counters with a count for more than
    one). Set `show_streak_emoji: false` to show streaks as threads only.
    Time-tracked entries show on every day they touch. 📝 marks journal days.
- **Back-fill** (button next to Today): pick a date-based habit, then a
  From/To range and/or individual dates.
  - A preview shows how many days will be added.
  - Days already logged are skipped, so overlaps are safe.
  - Future dates and reversed ranges are caught before anything is saved.
- **Day panel** (tap a day)
  - Shows everything logged that day, including notes and journal values.
  - Use the pencil to edit (date, times, notes, or move to another habit) and
    the bin to delete (asks first).
  - To add to that day, use the log panel above, which now targets that day.
- **Stats**
  - Pick a habit and Week / Month / Year / All time; use the arrows for
    earlier periods.
  - Tiles: total time, average per entry, entries, active days, current and
    best streaks (depending on the habit type).
  - The chart shows per day, month or year. Hover or tap a bar for its value.
  - "Show log" / "Show logged periods" lists every entry in the period, with
    edit and delete.
  - No logging buttons here; logging is on the Calendar tab.
  - Counters: an **All time vs since reset** table (time, entries, active
    days), with **Reset…** (defaults to now, can be backdated) and **Undo last
    reset**.
- **Long periods**
  - Any period over 24 hours asks you to confirm its length before saving, to
    catch date typos.
  - The server accepts periods up to about 3 years.
- **Updates**
  - Changes made anywhere (other devices, automations, Developer Tools) appear
    in the card within a fraction of a second.

## 5. Actions

All actions are under Developer Tools > Actions and usable in automations,
scripts and dashboard buttons. `habit` accepts the habit's **device** or its
habit ID.

| Action | Use |
|---|---|
| `habit_log.log` | Log now or for a date. Time-tracked habits take `start` plus `end` or `duration`. Optional `notes`. Returns the `entry_id`. Streak habits ignore a second log on the same day. |
| `habit_log.backfill` | Date range and/or a list of dates. Skips days already logged; safe to re-run. |
| `habit_log.update_entry` | Change date, times, notes, or move an entry to another habit. |
| `habit_log.delete_entry` | Delete a habit or journal entry. |
| `habit_log.merge_habits` | Move all entries from one habit to another, then archive the source. |
| `habit_log.list_entries` | Returns entries (with IDs) for a habit and/or date range. |
| `habit_log.journal` | Record journal values by field name, e.g. `{"Energy": 4, "Weight": 81.2}`. |
| `habit_log.update_journal_entry` | Change values; `null` removes a value. |
| `habit_log.reset_counter` | Start a new "since reset" cycle for a counter, now or at `at` (not in the future). Returns the new since-reset totals. Streak habits are rejected. |
| `habit_log.undo_reset` | Remove the latest reset; "since reset" goes back to the previous one (or all time). |

Examples:

```yaml
action: habit_log.log
data:
  habit: <pick the device>
  start: "2026-10-06 21:00:00"
  duration: { minutes: 45 }
  notes: Chapter 4
```

```yaml
action: habit_log.backfill
data:
  habit: <pick the device>
  start_date: "2026-09-01"
  end_date: "2026-09-30"
```

Validation:
- Future dates and times are rejected.
- Periods must have end after start, and can be at most about 3 years.
- Notes are limited to 2000 characters.
- Journal values are checked against each field's type, range and options.
- Errors appear as readable messages in the UI.

## 6. Changing or removing habits

| You want to... | Do this | History |
|---|---|---|
| Rename, change emoji or color | Edit habit | Follows automatically |
| Switch counter/streak | Edit habit | Kept, recalculated |
| Retire a habit | Edit habit > Archived | Kept, restorable |
| Combine two habits | `habit_log.merge_habits` | Moved to the target; for streak targets, same-day duplicates are dropped |
| Fix one entry | Card: day panel or Stats log (pencil / bin) | Changed or removed |
| Delete a habit permanently | Habit Log > habit > three dots > Delete | Entries are written to `/config/habit_log/deleted/<name>_<timestamp>.json`, then removed |

Turning off "Track time" is blocked while the habit has timed entries. Merge
them into another time-tracked habit first.

## 7. Data, backups, retention

- **Storage:** all entries and reset timestamps live in `/config/.storage/habit_log`.
  - The file is written atomically and with owner-only permissions.
  - It has no expiry; recorder purging does not touch it.
- **Backups:** standard Home Assistant backups include `/config/.storage` and
  `/config/custom_components`, so both the data and the code are backed up.
- **Deleting a habit:** a JSON snapshot is written to
  `/config/habit_log/deleted/` before its entries are removed.
- **Removing the integration:** a full export is written to the same folder
  before storage is deleted.
- **Diagnostics** (Habit Log > three dots > Download diagnostics): contains
  counts and settings only; no notes or journal values.

## 8. Access and security

- `Allowed users` is enforced on every action and on the card's data API.
  - Anyone else gets an "unauthorized" error.
  - Denials are logged as warnings.
- The sensors and the calendar entity are normal Home Assistant entities. Any
  logged-in user can read them; Home Assistant has no per-entity read
  permissions.
  - That's why notes and journal values stay out of the calendar entity by
    default.
  - Sensors only ever contain counts, times, dates and opted-in numbers.
- The integration:
  - has no network access, external packages, HTTP endpoints or ports;
  - adds only authenticated websocket commands (`habit_log/*`) and serves its
  card file from `/habit_log_static/` (static JS, no data);
  - runs inside Home Assistant like any integration, so the code is plain
    Python for you to read.

## 9. Update / uninstall

- **Update:** through HACS (Settings > Updates), or replace the `habit_log`
  folder manually. Restart afterwards. Data is kept.
- **Uninstall:**
  1. Delete the integration in Settings. A full export is written first.
  2. HACS > Habit Log > three dots > **Remove** (or delete the
     `custom_components/habit_log` folder for a manual install).
  3. Restart.

## Development

```bash
python3.14 -m venv venv && . venv/bin/activate
pip install "pytest-homeassistant-custom-component==0.13.371" ruff   # pins HA 2026.10.0
pytest
ruff check . && ruff format --check .
```

Browser tests for the card: see `tests/card/README.md`.

### Releasing

1. Bump `version` in `custom_components/habit_log/manifest.json` and `VERSION`
   in `const.py` and `www/habit-log-card.js` (all three must match; a test
   enforces it), and add a `CHANGELOG.md` section headed `## <version>`.
2. Commit to `main`.
3. GitHub > **Actions** > **Release** > **Run workflow** (branch `main`).
   Alternatively push a tag `v<version>` from a local clone.
4. The workflow checks the versions, runs the tests, creates tag
   `v<version>` and a GitHub release whose notes are that CHANGELOG section.
   HACS then offers the update under Settings > Updates.

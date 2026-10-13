"""Drive the card like a user and assert the exact service calls it makes."""

import asyncio
import json
import os

from playwright.async_api import async_playwright

SP = os.path.dirname(os.path.abspath(__file__))
with open(f"{SP}/fixture.json", encoding="utf-8") as fh:
    FX = json.load(fh)
ids = {h["name"]: h["id"] for h in FX["config"]["habits"]}
failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


ROOT = "document.querySelector('habit-log-card').shadowRoot"


async def calls(pg):
    return await pg.evaluate(
        "window.calls.filter(c => c.type === 'call_service' && c.service !== 'list_entries')"
    )


async def click_text(pg, sel, text):
    await pg.evaluate(
        f"""([sel, t]) => {{ const el = [...{ROOT}.querySelectorAll(sel)].find(e => e.textContent.includes(t)); if (!el) throw new Error('missing ' + t); el.click(); }}""",
        [sel, text],
    )


async def dlg(pg, js):
    return await pg.evaluate(f"(() => {{ const d = {ROOT}.querySelector('dialog'); {js} }})()")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 560, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(f"file://{SP}/index.html")
        await pg.wait_for_function("window.ready")
        await pg.wait_for_timeout(300)

        # 1. Quick log a counter -> log today, toast with Undo -> delete
        await click_text(pg, ".logpanel .logbtn", "Run")
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service"] == "log"
            and c["service_data"] == {"habit": ids["Run"], "date": "2026-10-07"}
            and c["return_response"],
            "log button (counter) sends habit id + today",
        )
        toast = await pg.evaluate(f"{ROOT}.querySelector('.toast')?.textContent")
        check(
            toast and "Logged Run for today" in toast and "Undo" in toast, f"toast with undo shown: {toast!r}"
        )
        pos = await pg.evaluate(
            f"[{ROOT}.querySelector('.toast').getBoundingClientRect().top, {ROOT}.querySelector('.cal-head').getBoundingClientRect().top]"
        )
        check(pos[0] < pos[1], "toast sits above the calendar (visible near the tapped chip)")
        await click_text(pg, ".toast button", "Undo")
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service"] == "delete_entry"
            and c["service_data"] == {"entry_id": "new"}
            and not c["return_response"],
            "undo deletes the new entry",
        )

        # 2. Timed: open from quick row, switch to duration, save
        await click_text(pg, ".logpanel .logbtn", "Reading")
        await pg.wait_for_timeout(100)
        start = await dlg(pg, "return d.querySelector('input[type=datetime-local]').value")
        check(start == "2026-10-07T13:05", f"timed form defaults to HA-local now minus 1 h: {start}")
        await dlg(
            pg, "[...d.querySelectorAll('.seg button')].find(b => b.textContent === 'Duration').click();"
        )
        await pg.wait_for_timeout(50)
        hm = await dlg(pg, "return [...d.querySelectorAll('input[type=number]')].map(i => i.value)")
        check(hm == ["1", "0"], f"duration prefilled from end time: {hm}")
        await dlg(
            pg,
            "const n = d.querySelectorAll('input[type=number]'); n[0].value = '0'; n[0].dispatchEvent(new Event('input')); n[1].value='45'; n[1].dispatchEvent(new Event('input')); d.querySelector('textarea').value='Ch. 5'; d.querySelector('textarea').dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();",
        )
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service"] == "log"
            and c["service_data"]
            == {
                "habit": ids["Reading"],
                "start": "2026-10-07T13:05:00",
                "notes": "Ch. 5",
                "duration": {"hours": 0, "minutes": 45},
            },
            f"timed log payload: {c['service_data']}",
        )
        check(not await dlg(pg, "return d.open"), "dialog closes after save")

        # 3. Long period requires a second confirmation
        await click_text(pg, ".logpanel .logbtn", "Reading")
        await pg.wait_for_timeout(50)
        n_before = len(await calls(pg))
        await dlg(
            pg,
            "const i = d.querySelectorAll('input[type=datetime-local]'); i[0].value='2026-06-01T09:00'; i[0].dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();",
        )
        await pg.wait_for_timeout(50)
        check(
            len(await calls(pg)) == n_before and "Check the dates" in (await dlg(pg, "return d.textContent")),
            "long period asks for confirmation first",
        )
        await dlg(pg, "d.querySelector('form').requestSubmit();")
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service_data"].get("end") == "2026-10-07T14:05:00"
            and c["service_data"]["start"] == "2026-06-01T09:00:00",
            "confirmed long period is sent",
        )

        # 4. Day panel: edit dated entry (move date) ; delete with confirmation
        await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.day')].find(b => b.getAttribute('aria-label').startsWith('Tuesday, October 6')).click()"
        )
        await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.panel .iconbtn')].find(b => b.getAttribute('aria-label') === 'Edit Run entry').click()"
        )
        await pg.wait_for_timeout(50)
        await dlg(
            pg,
            "const i = d.querySelector('input[type=date]'); i.value='2026-10-05'; i.dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();",
        )
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service"] == "update_entry"
            and c["service_data"]["date"] == "2026-10-05"
            and "habit" not in c["service_data"],
            f"edit sends update_entry with new date: {c['service_data']}",
        )
        await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.panel .iconbtn')].find(b => b.getAttribute('aria-label') === 'Delete Diet entry').click()"
        )
        await pg.wait_for_timeout(50)
        txt = await dlg(pg, "return d.textContent")
        check(
            "Delete this Diet entry from Tuesday, October 6" in txt,
            "delete asks for confirmation with habit and date",
        )
        await dlg(pg, "d.querySelector('form').requestSubmit();")
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(c["service"] == "delete_entry", "delete confirmed")

        # 5. Journal edit sends only changed values; cleared value -> null
        await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.panel .iconbtn')].find(b => b.getAttribute('aria-label') === 'Edit journal entry').click()"
        )
        await pg.wait_for_timeout(50)
        is_textarea = await dlg(
            pg,
            "return [...d.querySelectorAll('label.field')].find(l => l.textContent.startsWith('Notes')).querySelector('textarea') !== null",
        )
        check(is_textarea, "multi-line journal text opens in a textarea (keeps line breaks)")
        await dlg(pg, "[...d.querySelectorAll('.rating button')].find(b => b.textContent === '5').click();")
        await pg.wait_for_timeout(30)
        await dlg(
            pg,
            "const w = [...d.querySelectorAll('input[type=number]')][0]; w.value=''; w.dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();",
        )
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        fid = {f["name"]: f["id"] for f in FX["config"]["journal_fields"]}
        check(
            c["service"] == "update_journal_entry"
            and c["service_data"]["values"] == {fid["Energy"]: 5, fid["Weight"]: None},
            f"journal edit payload: {c['service_data']}",
        )

        # 6. New journal with nothing filled -> inline error, no call
        await click_text(pg, ".logpanel .logbtn", "Journal")
        await pg.wait_for_timeout(50)
        n_before = len(await calls(pg))
        await dlg(pg, "d.querySelector('form').requestSubmit();")
        await pg.wait_for_timeout(50)
        err = await dlg(pg, "return d.querySelector('.error')?.textContent")
        check(
            len(await calls(pg)) == n_before and err == "Fill in at least one field.",
            f"empty journal blocked: {err!r}",
        )
        await dlg(pg, "d.querySelector('.actions .textbtn').click();")

        # 7. Server validation error surfaces cleanly
        await pg.evaluate(
            "document.querySelector('habit-log-card')._quickLog(document.querySelector('habit-log-card')._cfg.habits.find(h=>h.name==='Run'), '2026-10-09')"
        )
        await pg.wait_for_timeout(100)
        toast = await pg.evaluate(f"{ROOT}.querySelector('.toast.err')?.textContent")
        check(toast and toast.startswith("2026-10-09 is in the future."), f"error toast text: {toast!r}")

        # 8. Featured streak toggles and persists
        await click_text(pg, ".legend button", "No sugar")
        await pg.wait_for_timeout(50)
        pressed = await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.legend button')].map(b => [b.textContent, b.getAttribute('aria-pressed')])"
        )
        check(
            any(t.endswith("No sugar") and v == "true" for t, v in pressed)
            and any(t.endswith("Diet") and v == "false" for t, v in pressed),
            f"featured switched: {pressed}",
        )
        saved = await pg.evaluate("localStorage.getItem('habit-log-card:featured:Habits')")
        check(saved == ids["No sugar"], "featured choice remembered")

        # 9. Live update: server push triggers refetch
        n_cfg = await pg.evaluate("window.calls.filter(c => c.type === 'habit_log/config').length")
        await pg.evaluate("window.push({changed: true})")
        await pg.wait_for_timeout(400)
        n_cfg2 = await pg.evaluate("window.calls.filter(c => c.type === 'habit_log/config').length")
        check(n_cfg2 == n_cfg + 1, "subscription event refreshes data")

        # 10. Keyboard: Escape closes dialog
        await click_text(pg, ".logpanel .logbtn", "Journal")
        await pg.wait_for_timeout(50)
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(50)
        check(not await dlg(pg, "return d.open"), "Escape closes the dialog")

        # 11. Back-fill form: range + extra dates, preview, payload, toast
        await click_text(pg, ".cal-head .textbtn", "Back-fill")
        await pg.wait_for_timeout(50)
        sel = await dlg(pg, "return d.querySelector('select').selectedOptions[0].textContent")
        check("No sugar" in sel, f"back-fill defaults to the featured streak: {sel!r}")
        await dlg(
            pg,
            """const [a, b] = d.querySelectorAll('input[type=date]');
            a.value='2026-09-01'; a.dispatchEvent(new Event('input')); b.value='2026-09-09'; b.dispatchEvent(new Event('input'));
            const t = d.querySelector('textarea'); t.value='2026-08-20, 2026-09-03'; t.dispatchEvent(new Event('input'));""",
        )
        prev = await dlg(pg, "return d.querySelector('[aria-live]').textContent")
        check(prev.startswith("10 days for No sugar"), f"preview counts unique days: {prev!r}")
        await dlg(pg, "d.querySelector('form').requestSubmit();")
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service"] == "backfill"
            and c["service_data"]
            == {
                "habit": ids["No sugar"],
                "start_date": "2026-09-01",
                "end_date": "2026-09-09",
                "dates": "2026-08-20, 2026-09-03",
            },
            f"back-fill payload: {c['service_data']}",
        )
        toast = await pg.evaluate(f"{ROOT}.querySelector('.toast')?.textContent")
        check(
            toast and "Added 9 days to No sugar. 2 were already logged." in toast,
            f"back-fill toast: {toast!r}",
        )

        # 12. Back-fill validation: future date and reversed range blocked client-side; dates-only works
        await click_text(pg, ".cal-head .textbtn", "Back-fill")
        await pg.wait_for_timeout(50)
        n_before = len(await calls(pg))
        await dlg(
            pg,
            """const [a, b] = d.querySelectorAll('input[type=date]'); a.value='2026-10-05'; a.dispatchEvent(new Event('input')); b.value='2026-10-09'; b.dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();""",
        )
        await pg.wait_for_timeout(50)
        err = await dlg(pg, "return d.querySelector('.error')?.textContent")
        check(
            len(await calls(pg)) == n_before and err == "2026-10-08 is in the future.",
            f"future blocked: {err!r}",
        )
        await dlg(
            pg,
            """const [a, b] = d.querySelectorAll('input[type=date]'); a.value='2026-10-05'; a.dispatchEvent(new Event('input')); b.value='2026-10-01'; b.dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();""",
        )
        err = await dlg(pg, "return d.querySelector('.error')?.textContent")
        check(err == "The end date must be on or after the start date.", f"reversed range blocked: {err!r}")
        await dlg(
            pg,
            """const [a, b] = d.querySelectorAll('input[type=date]'); a.value=''; a.dispatchEvent(new Event('input')); b.value=''; b.dispatchEvent(new Event('input'));
            const t = d.querySelector('textarea'); t.value='2026-09-02\\n2026-09-05'; t.dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();""",
        )
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service_data"] == {"habit": ids["No sugar"], "dates": "2026-09-02\n2026-09-05"},
            f"dates-only payload: {c['service_data']}",
        )

        # 13. Streak emoji are on by default
        marks = await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.day')].find(b => b.getAttribute('aria-label').startsWith('Sunday, October 4')).querySelector('.marks').textContent"
        )
        check(marks == "🥗🍬", f"streak emoji shown by default: {marks!r}")

        # 15. Log panel is clearly labelled, follows the selected day, and the
        #     day panel has no duplicate add buttons
        await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.day')].find(b => b.getAttribute('aria-label').startsWith('Wednesday, October 7')).click()"
        )
        head = await pg.evaluate(f"{ROOT}.querySelector('.logpanel .log-head').textContent")
        check("Log for today" in head and "Back to today" not in head, f"log panel heading: {head!r}")
        await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.day')].find(b => b.getAttribute('aria-label').startsWith('Friday, October 2')).click()"
        )
        head = await pg.evaluate(f"{ROOT}.querySelector('.logpanel .log-head').textContent")
        check(
            "Log for Friday, October 2" in head and "Back to today" in head,
            f"heading follows selected day: {head!r}",
        )
        status = await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.logbtn')].find(b => b.textContent.includes('Reading')).querySelector('.lb-status').textContent"
        )
        check(status.startswith("50 min"), f"status reflects the selected day: {status!r}")
        await click_text(pg, ".logpanel .logbtn", "Diet")
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service_data"] == {"habit": ids["Diet"], "date": "2026-10-02"},
            f"log button logs the selected day: {c['service_data']}",
        )
        add_section = await pg.evaluate(
            f"!!{ROOT}.querySelector('.panel .add') || {ROOT}.querySelector('.panel').textContent.includes('Add to this day')"
        )
        check(not add_section, "day panel has no duplicate 'Add to this day' buttons")
        await click_text(pg, ".log-head .textbtn", "Back to today")
        head = await pg.evaluate(f"{ROOT}.querySelector('.logpanel .log-head').textContent")
        check("Log for today" in head, f"Back to today resets the log panel: {head!r}")
        await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.day')].find(b => b.getAttribute('aria-label').startsWith('Saturday, October 10')).click()"
        )
        disabled = await pg.evaluate(f"[...{ROOT}.querySelectorAll('.logbtn')].every(b => b.disabled)")
        check(disabled, "log buttons are disabled for a future day")
        await click_text(pg, ".log-head .textbtn", "Back to today")

        # 16. Stats tab: no logging buttons; reset block for counters
        await click_text(pg, ".tab", "Stats")
        await pg.wait_for_timeout(200)
        check(
            not await pg.evaluate(f"!!{ROOT}.querySelector('.logpanel, .logbtn')"),
            "no log buttons on the Stats tab",
        )
        await pg.evaluate(
            f"(() => {{ const sel = {ROOT}.querySelector('select'); sel.value = '{ids['Reading']}'; sel.dispatchEvent(new Event('change')); }})()"
        )
        await pg.wait_for_timeout(200)
        reset_txt = await pg.evaluate(f"{ROOT}.querySelector('.reset')?.textContent || ''")
        check(
            "Last reset Oct 4, 2026" in reset_txt and "Undo last reset" in reset_txt,
            f"reset block shows last reset: {reset_txt[:90]!r}",
        )
        cmp_rows = await pg.evaluate(
            f"[...{ROOT}.querySelectorAll('.reset table.cmp tr')].map(r => [...r.children].map(c => c.textContent.trim()))"
        )
        check(
            len(cmp_rows) == 4
            and cmp_rows[0] == ["", "All time", "Since reset"]
            and [r[0] for r in cmp_rows[1:]] == ["Time", "Entries", "Active days"]
            and cmp_rows[1][1] != cmp_rows[1][2],
            f"all time vs since reset table: {cmp_rows}",
        )
        await click_text(pg, ".reset .textbtn", "Reset…")
        await pg.wait_for_timeout(50)
        at = await dlg(pg, "return d.querySelector('input[type=datetime-local]').value")
        check(at == "2026-10-07T14:05", f"reset defaults to HA-local now: {at}")
        await dlg(pg, "d.querySelector('form').requestSubmit();")
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service"] == "reset_counter" and c["service_data"] == {"habit": ids["Reading"]},
            f"untouched 'now' default lets the server use its exact clock: {c['service_data']}",
        )
        await click_text(pg, ".reset .textbtn", "Reset…")
        await pg.wait_for_timeout(50)
        await dlg(
            pg,
            "const i = d.querySelector('input[type=datetime-local]'); i.value='2026-10-09T09:00'; i.dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();",
        )
        await pg.wait_for_timeout(50)
        err = await dlg(pg, "return d.querySelector('.error')?.textContent")
        check(err == "The reset can't be in the future.", f"future reset blocked: {err!r}")
        await dlg(
            pg,
            "const i = d.querySelector('input[type=datetime-local]'); i.value='2026-10-06T08:30'; i.dispatchEvent(new Event('input')); d.querySelector('form').requestSubmit();",
        )
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service"] == "reset_counter"
            and c["service_data"] == {"habit": ids["Reading"], "at": "2026-10-06T08:30:00"},
            f"reset payload: {c['service_data']}",
        )
        await click_text(pg, ".reset .textbtn", "Undo last reset")
        await pg.wait_for_timeout(50)
        await dlg(pg, "d.querySelector('form').requestSubmit();")
        await pg.wait_for_timeout(100)
        c = (await calls(pg))[-1]
        check(
            c["service"] == "undo_reset" and c["service_data"] == {"habit": ids["Reading"]},
            "undo reset payload",
        )
        await pg.evaluate(
            f"(() => {{ const sel = {ROOT}.querySelector('select'); sel.value = '{ids['Diet']}'; sel.dispatchEvent(new Event('change')); }})()"
        )
        await pg.wait_for_timeout(200)
        check(not await pg.evaluate(f"!!{ROOT}.querySelector('.reset')"), "streak habits have no reset block")
        await click_text(pg, ".tab", "Calendar")

        check(not errs, f"no page errors {errs}")

        # 14. show_streak_emoji: true
        pg2 = await b.new_page(viewport={"width": 560, "height": 900})
        await pg2.goto(f"file://{SP}/index.html?emoji=1")
        await pg2.wait_for_function("window.ready")
        await pg2.wait_for_timeout(300)
        marks = await pg2.evaluate(
            f"[...{ROOT}.querySelectorAll('.day')].map(b => [b.getAttribute('aria-label'), b.querySelector('.marks').textContent])"
        )
        oct4 = next(m for a, m in marks if a.startswith("Sunday, October 4"))
        oct6 = next(m for a, m in marks if a.startswith("Tuesday, October 6"))
        check(oct4 == "🥗🍬", f"streak emoji shown in habit order: {oct4!r}")
        check(
            oct6.startswith("🥗🍬") and "+" in oct6, f"busy day keeps 3 markers plus overflow count: {oct6!r}"
        )
        await pg2.screenshot(path=f"{SP}/streak_emoji.png", full_page=True)
        pg3 = await b.new_page(viewport={"width": 560, "height": 900})
        await pg3.goto(f"file://{SP}/index.html?emoji=0")
        await pg3.wait_for_function("window.ready")
        await pg3.wait_for_timeout(300)
        off = await pg3.evaluate(
            f"[...{ROOT}.querySelectorAll('.day')].find(b => b.getAttribute('aria-label').startsWith('Sunday, October 4')).querySelector('.marks').textContent"
        )
        check(off == "", f"show_streak_emoji: false hides them: {off!r}")
        await b.close()
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


raise SystemExit(asyncio.run(main()))

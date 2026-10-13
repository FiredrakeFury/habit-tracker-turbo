import asyncio
import os

from playwright.async_api import async_playwright

SP = os.path.dirname(os.path.abspath(__file__))


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        errors = []

        async def shot(name, w, dark=False, view="calendar", actions=None, full=True):
            pg = await b.new_page(viewport={"width": w, "height": 900}, device_scale_factor=2)
            pg.on(
                "console",
                lambda m: (
                    errors.append(f"{name}: {m.type}: {m.text}") if m.type in ("error", "warning") else None
                ),
            )
            pg.on("pageerror", lambda e: errors.append(f"{name}: pageerror: {e}"))
            await pg.goto(f"file://{SP}/index.html?view={view}{'&dark=1' if dark else ''}")
            try:
                await pg.wait_for_function("window.ready === true", timeout=8000)
            except Exception:
                print("\n".join(errors))
                raise
            await pg.wait_for_timeout(300)
            if actions:
                await actions(pg)
            await pg.wait_for_timeout(250)
            await pg.screenshot(path=f"{SP}/{name}.png", full_page=full)
            await pg.close()

        async def click_day(pg, key="2026-10-06"):
            await pg.evaluate(
                """(k) => { const c = document.querySelector('habit-log-card').shadowRoot;
              [...c.querySelectorAll('.day')].find(b => b.getAttribute('aria-label').startsWith(k)) ; }""",
                key,
            )
            # click by visible date number within month: use aria-label prefix 'Tuesday, October 6'
            await pg.evaluate("""() => { const c = document.querySelector('habit-log-card').shadowRoot;
              [...c.querySelectorAll('.day')].find(b => b.getAttribute('aria-label').startsWith('Tuesday, October 6')).click(); }""")

        await shot("cal_light", 560, actions=click_day)
        await shot("cal_dark", 560, dark=True, actions=click_day)
        await shot("cal_mobile", 380, actions=click_day)

        async def stats_read(pg):
            await pg.evaluate("""() => { const c = document.querySelector('habit-log-card').shadowRoot;
              const sel = c.querySelector('select'); sel.value = [...sel.options].find(o => o.text.includes('Reading')).value; sel.dispatchEvent(new Event('change')); }""")
            await pg.wait_for_timeout(200)
            await pg.evaluate("""() => { const c = document.querySelector('habit-log-card').shadowRoot;
              [...c.querySelectorAll('button')].find(b => b.textContent.includes('Show logged periods')).click(); }""")
            await pg.wait_for_timeout(200)
            await pg.evaluate("""() => { const c = document.querySelector('habit-log-card').shadowRoot;
              const hits = c.querySelectorAll('.chart .hit'); hits[5].dispatchEvent(new PointerEvent('pointerenter')); }""")

        await shot("stats_light", 560, view="stats", actions=stats_read)
        await shot("stats_dark_mobile", 380, dark=True, view="stats")

        async def open_timed(pg):
            await pg.evaluate("""() => { const c = document.querySelector('habit-log-card').shadowRoot;
              [...c.querySelectorAll('.logpanel .logbtn')].find(b => b.textContent.includes('Reading')).click(); }""")
            await pg.wait_for_timeout(150)
            # set a long period to trigger confirmation
            await pg.evaluate("""() => { const d = document.querySelector('habit-log-card').shadowRoot.querySelector('dialog');
              const ins = d.querySelectorAll('input[type=datetime-local]'); ins[1].value = '2026-10-07T13:05'; ins[0].value='2026-09-20T09:00';
              ins[0].dispatchEvent(new Event('input')); ins[1].dispatchEvent(new Event('input'));
              d.querySelector('form').requestSubmit(); }""")

        await shot("dlg_timed_long", 560, actions=open_timed, full=False)

        async def open_journal(pg):
            await click_day(pg)
            await pg.evaluate("""() => { const c = document.querySelector('habit-log-card').shadowRoot;
              [...c.querySelectorAll('.panel .iconbtn')].find(b => b.getAttribute('aria-label') === 'Edit journal entry').click(); }""")

        await shot("dlg_journal", 560, dark=True, actions=open_journal, full=False)

        async def future_err(pg):
            await pg.evaluate(
                """() => { const c = document.querySelector('habit-log-card'); c._quickLog(c._cfg.habits.find(h=>h.name==='Run'), '2026-10-09'); }"""
            )
            await pg.wait_for_timeout(200)

        await shot("toast_err", 560, actions=future_err)

        async def open_backfill(pg):
            await pg.evaluate("""() => { const c = document.querySelector('habit-log-card').shadowRoot;
              [...c.querySelectorAll('.cal-head .textbtn')].find(b => b.textContent === 'Back-fill').click(); }""")
            await pg.wait_for_timeout(100)
            await pg.evaluate("""() => { const d = document.querySelector('habit-log-card').shadowRoot.querySelector('dialog');
              const [a, b] = d.querySelectorAll('input[type=date]'); a.value='2026-09-01'; a.dispatchEvent(new Event('input')); b.value='2026-09-21'; b.dispatchEvent(new Event('input')); }""")

        await shot("dlg_backfill", 380, actions=open_backfill, full=False)
        await b.close()
    print("\n".join(errors) or "no console errors")


asyncio.run(main())

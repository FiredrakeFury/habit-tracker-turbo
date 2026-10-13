# Card tests (browser)

Renders the real card in Chromium against payloads recorded from the real
integration, then drives it like a user and checks the exact actions it calls.

```bash
# 1. Record payloads from the integration (writes fixture.json + fixture.js)
HABIT_LOG_FIXTURE_OUT=tests/card/fixture.json pytest tests/test_card_api.py -k record
(printf 'window.FX = '; cat tests/card/fixture.json; printf ';\n') > tests/card/fixture.js
# 2. Interaction checks (needs: pip install playwright && playwright install chromium)
python tests/card/interact.py
# 3. Screenshots (light/dark, desktop/phone, dialogs) into tests/card/*.png
python tests/card/shoot.py
```

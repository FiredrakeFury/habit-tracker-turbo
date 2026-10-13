/*!
 * Habit Log card v0.3.0, bundled with the Habit Log integration.
 *
 * - Month calendar: streak habits are drawn as threads along the bottom of each
 *   day (joined across consecutive days); one "featured" streak also fills the
 *   whole day. Counter habits show their emoji (with a count); 📝 marks journal days.
 * - Day panel: everything logged that day, with edit / delete / add.
 * - Stats: week / month / year / all time per habit, with a chart and the log.
 * - Quick log row for today.
 *
 * Reads through the integration's authenticated websocket commands
 * (habit_log/*) and writes through its actions, so permissions and validation
 * live in one place (the server). No dependencies; all user text is rendered
 * with textContent, never innerHTML.
 */
(() => {
  "use strict";

  const VERSION = "0.3.0";
  const TAG = "habit-log-card";
  const DOMAIN = "habit_log";
  const LONG_PERIOD_SECONDS = 24 * 3600;
  const TOAST_MS = 6000;
  const REFRESH_DEBOUNCE_MS = 250;
  const MAX_MARKERS = 3;
  const MAX_THREADS = 5;

  // Validated categorical palette (fixed order) used when a habit has no color.
  const PALETTE = {
    light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
  };

  // ------------------------------------------------------------- calendar math
  // Day keys are "YYYY-MM-DD" in Home Assistant's time zone (computed by the
  // server). Arithmetic is done in UTC so the browser's own time zone and DST
  // never shift a day.
  const pad = (n) => String(n).padStart(2, "0");
  const keyOf = (y, m0, d) => `${y}-${pad(m0 + 1)}-${pad(d)}`;
  const partsOf = (key) => key.split("-").map(Number);
  const utcOf = (key) => {
    const [y, m, d] = partsOf(key);
    return Date.UTC(y, m - 1, d);
  };
  const addDays = (key, n) => {
    const t = new Date(utcOf(key) + n * 86400000);
    return keyOf(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate());
  };
  const weekdayOf = (key) => new Date(utcOf(key)).getUTCDay(); // 0 = Sunday
  const daysInMonth = (y, m0) => new Date(Date.UTC(y, m0 + 1, 0)).getUTCDate();

  /** Parse a naive local ISO string ("2026-10-06T21:00:00") into parts. */
  const naiveParts = (iso) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
    return m ? { key: `${m[1]}-${m[2]}-${m[3]}`, hh: +m[4], mm: +m[5], utc: Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) } : null;
  };

  // ------------------------------------------------------------------- dom
  /** Tiny element builder. Strings become text nodes (never HTML). */
  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "style" && typeof v === "object") {
          // Custom properties (--x) only work through setProperty.
          for (const [prop, val] of Object.entries(v)) {
            if (prop.startsWith("--")) el.style.setProperty(prop, val);
            else el.style[prop] = val;
          }
        }
        else if (k === "dataset") Object.assign(el.dataset, v);
        else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
        else if (k === "value") el.value = v;
        else if (v === true) el.setAttribute(k, "");
        else el.setAttribute(k, String(v));
      }
    }
    append(el, children);
    return el;
  }
  function append(el, children) {
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const SVG_NS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null) el.setAttribute(k, String(v));
    }
    append(el, children);
    return el;
  }
  const icon = (name) => h("ha-icon", { icon: name, "aria-hidden": "true" });

  // ---------------------------------------------------------------- format
  function fmtDuration(seconds) {
    const total = Math.round((seconds || 0) / 60);
    if (total < 60) return `${total} min`;
    const days = Math.floor(total / 1440);
    const hours = Math.floor((total % 1440) / 60);
    const mins = total % 60;
    if (days >= 2) return hours ? `${days} d ${hours} h` : `${days} d`;
    const allHours = Math.floor(total / 60);
    return mins ? `${allHours} h ${mins} min` : `${allHours} h`;
  }
  function fmtHoursTotal(seconds) {
    const hours = (seconds || 0) / 3600;
    if (hours < 1) return `${Math.round(hours * 60)} min`;
    if (hours < 100) {
      const whole = Math.floor(hours);
      const mins = Math.round((hours - whole) * 60);
      return mins ? `${whole} h ${mins} min` : `${whole} h`;
    }
    return `${Math.round(hours).toLocaleString()} h`;
  }

  /** Return a locale Intl accepts; HA or browsers can report tags like "en-US@posix". */
  function safeLocale(lang) {
    const candidates = [lang, String(lang || "").split(/[@_.]/)[0], "en"];
    for (const c of candidates) {
      try {
        if (c && Intl.DateTimeFormat.supportedLocalesOf([c]).length) return c;
      } catch (_e) {
        /* invalid tag */
      }
    }
    return "en";
  }

  class Fmt {
    constructor(lang) {
      const opt = (o) => ({ timeZone: "UTC", ...o });
      this.lang = lang;
      lang = safeLocale(lang);
      this.monthYear = new Intl.DateTimeFormat(lang, opt({ month: "long", year: "numeric" }));
      this.dayLong = new Intl.DateTimeFormat(lang, opt({ weekday: "long", month: "long", day: "numeric" }));
      this.dayShort = new Intl.DateTimeFormat(lang, opt({ month: "short", day: "numeric" }));
      this.dayShortYear = new Intl.DateTimeFormat(lang, opt({ month: "short", day: "numeric", year: "numeric" }));
      this.weekday = new Intl.DateTimeFormat(lang, opt({ weekday: "short" }));
      this.weekdayNarrow = new Intl.DateTimeFormat(lang, opt({ weekday: "narrow" }));
      this.monthShort = new Intl.DateTimeFormat(lang, opt({ month: "short" }));
      this.monthNarrow = new Intl.DateTimeFormat(lang, opt({ month: "narrow" }));
      this.time = new Intl.DateTimeFormat(lang, opt({ hour: "numeric", minute: "2-digit" }));
    }
    date(key, style = "long") {
      const d = new Date(utcOf(key));
      return (style === "long" ? this.dayLong : style === "year" ? this.dayShortYear : this.dayShort).format(d);
    }
    time_(iso) {
      const p = naiveParts(iso);
      return p ? this.time.format(new Date(p.utc)) : "";
    }
  }

  // ---------------------------------------------------------------- styles
  const STYLE = `
    :host { display: block; }
    [hidden] { display: none !important; }
    ha-card { overflow: hidden; }
    .wrap { padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 12px; }
    button { font: inherit; color: inherit; }
    .row { display: flex; align-items: center; gap: 8px; }
    .grow { flex: 1; min-width: 0; }
    .title { font-size: 1.15em; font-weight: 500; }
    .muted { color: var(--secondary-text-color); }
    .small { font-size: 0.85em; }
    .msg { padding: 16px; color: var(--secondary-text-color); line-height: 1.5; }

    .tabs { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 18px; padding: 2px; }
    .tab { border: 0; background: none; padding: 4px 12px; border-radius: 16px; cursor: pointer; color: var(--secondary-text-color); }
    .tab[aria-selected="true"] { background: var(--primary-color); color: var(--text-primary-color, #fff); }

    .iconbtn { border: 0; background: none; cursor: pointer; width: 36px; height: 36px; border-radius: 50%;
      display: inline-grid; place-items: center; color: var(--secondary-text-color); }
    .iconbtn:hover { background: var(--secondary-background-color); color: var(--primary-text-color); }
    .iconbtn[disabled] { opacity: .35; cursor: default; background: none; }
    .textbtn { border: 1px solid var(--divider-color); background: none; border-radius: 8px; padding: 4px 10px; cursor: pointer; }
    .textbtn:hover { background: var(--secondary-background-color); }
    :is(button, input, select, textarea, .day):focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }

    /* Log panel: solid action buttons, visually unlike the calendar legend */
    .logpanel { background: var(--secondary-background-color); border-radius: 12px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
    .log-head { display: flex; align-items: center; gap: 8px; font-weight: 500; }
    .log-head ha-icon { color: var(--primary-color); }
    .logbtns { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; }
    .logbtn { display: flex; align-items: center; gap: 10px; text-align: left; cursor: pointer; min-height: 52px; min-width: 0;
      padding: 8px 10px; border-radius: 10px; border: 1px solid var(--divider-color);
      border-left: 5px solid var(--c, var(--primary-color)); background: var(--card-background-color);
      box-shadow: 0 1px 2px rgba(0,0,0,.12); }
    .logbtn:hover:not([disabled]) { background: color-mix(in srgb, var(--c, var(--primary-color)) 10%, var(--card-background-color)); }
    .logbtn:active:not([disabled]) { transform: translateY(1px); box-shadow: none; }
    .logbtn[disabled] { opacity: .5; cursor: default; }
    .logbtn.done { background: color-mix(in srgb, var(--c) 16%, var(--card-background-color)); }
    .logbtn.journal { --c: var(--secondary-text-color); }
    .lb-emoji { font-size: 1.35em; line-height: 1; flex: none; }
    .lb-text { display: flex; flex-direction: column; min-width: 0; flex: 1; }
    .lb-name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .lb-status { font-size: .8em; color: var(--secondary-text-color); }
    .logbtn.done .lb-status { color: var(--primary-text-color); }
    .lb-action { color: var(--c, var(--primary-color)); flex: none; }
    .legend-cap { font-size: .8em; color: var(--secondary-text-color); width: 100%; }
    .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; background: var(--c); vertical-align: middle; }
    .reset { border: 1px dashed var(--divider-color); border-radius: 10px; padding: 8px 10px; display: flex; flex-direction: column; gap: 8px; }
    .reset .row { flex-wrap: wrap; }
    .cmp { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
    .cmp th, .cmp td { padding: 6px 8px; text-align: right; border-bottom: 1px solid var(--divider-color); }
    .cmp tbody tr:last-child th, .cmp tbody tr:last-child td { border-bottom: none; }
    .cmp th[scope=row], .cmp thead th:first-child { text-align: left; font-weight: 400; color: var(--secondary-text-color); }
    .cmp thead th { font-size: 12px; font-weight: 500; color: var(--secondary-text-color); }
    .cmp td { font-size: 18px; font-weight: 500; white-space: nowrap; }
    .reset .rtitle { flex: 1 1 180px; min-width: 0; }
    .reset .rbtns { display: flex; gap: 8px; flex-wrap: wrap; }
    @media (max-width: 480px) { .cmp td { font-size: 16px; } .cmp th, .cmp td { padding: 6px 4px; } }

    /* Chips (stats/legend/forms) */
    .quick { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 18px;
      border: 1px solid var(--divider-color); background: var(--card-background-color); cursor: pointer; line-height: 1.2; }
    .chip:hover { background: var(--secondary-background-color); }
    .chip[disabled] { opacity: .45; cursor: default; }
    .chip .sw { width: 10px; height: 10px; border-radius: 3px; background: var(--c); flex: none; }
    .chip .state { color: var(--secondary-text-color); font-size: .85em; }
    .chip.done { border-color: var(--c); }
    .chip.done .state { color: var(--primary-text-color); }

    /* Calendar */
    .cal-head { display: flex; align-items: center; gap: 4px; }
    .grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 3px; }
    .dow { text-align: center; font-size: .75em; color: var(--secondary-text-color); padding-bottom: 2px; }
    .day { position: relative; isolation: isolate; min-height: 58px; padding: 3px 4px calc(var(--threads, 0) * 6px + 4px); border-radius: 8px;
      border: 1px solid var(--divider-color); background: none; cursor: pointer; text-align: left;
      display: flex; flex-direction: column; gap: 2px; overflow: hidden; }
    .day::before { content: ""; position: absolute; inset: 0; z-index: -1; background: var(--fill, transparent); }
    .day.out { opacity: .4; }
    .day.future { cursor: pointer; }
    .day.future .num { color: var(--secondary-text-color); }
    .day.today { border: 2px solid var(--primary-color); padding: 2px 3px calc(var(--threads, 0) * 6px + 3px); }
    .day.sel { box-shadow: inset 0 0 0 2px var(--accent-color, var(--primary-color)); }
    .num { font-size: .8em; line-height: 1; }
    .marks { display: flex; flex-wrap: wrap; gap: 0 2px; font-size: .95em; line-height: 1.15; }
    .mark { white-space: nowrap; }
    .mark sup { font-size: .62em; color: var(--secondary-text-color); }
    .dot { display: inline-block; width: .55em; height: .55em; border-radius: 50%; background: var(--c); vertical-align: middle; }
    .threads { position: absolute; left: 0; right: 0; bottom: 3px; display: flex; flex-direction: column-reverse; gap: 2px; }
    .thread { height: 4px; margin: 0 4px; border-radius: 2px; background: var(--c); }
    .thread.jl { margin-left: -2px; border-top-left-radius: 0; border-bottom-left-radius: 0; }
    .thread.jr { margin-right: -2px; border-top-right-radius: 0; border-bottom-right-radius: 0; }
    .thread.off { visibility: hidden; }

    .legend { display: flex; flex-wrap: wrap; gap: 4px 6px; font-size: .85em; }
    .lg { display: inline-flex; align-items: center; gap: 6px; border: 1px solid transparent; background: none;
      border-radius: 14px; padding: 3px 8px; color: var(--secondary-text-color); }
    button.lg { cursor: pointer; }
    button.lg:hover { background: var(--secondary-background-color); }
    .lg[aria-pressed="true"] { border-color: var(--c); color: var(--primary-text-color); }
    .lg .bar { width: 14px; height: 4px; border-radius: 2px; background: var(--c); }
    .lg[aria-pressed="true"] .bar { height: 10px; border-radius: 3px; background: color-mix(in srgb, var(--c) 40%, transparent); box-shadow: inset 0 -4px 0 var(--c); }

    /* Day panel */
    .panel { border-top: 1px solid var(--divider-color); padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
    .panel h3 { margin: 0; font-size: 1em; font-weight: 500; }
    .items { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
    .item { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0; }
    .item + .item { border-top: 1px solid var(--divider-color); }
    .item .lead { width: 1.6em; text-align: center; flex: none; padding-top: 2px; }
    .item .body { flex: 1; min-width: 0; }
    .item .name { font-weight: 500; }
    .item .meta { color: var(--secondary-text-color); font-size: .85em; }
    .item .notes { white-space: pre-wrap; overflow-wrap: anywhere; font-size: .9em; margin-top: 2px; }
    .kv { display: grid; grid-template-columns: max-content 1fr; gap: 1px 10px; font-size: .9em; margin-top: 2px; }
    .kv dt { color: var(--secondary-text-color); }
    .kv dd { margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
    .add { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }

    /* Stats */
    .stats-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    select, input, textarea { font: inherit; color: var(--primary-text-color); background: var(--card-background-color);
      border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px 8px; box-sizing: border-box; }
    .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
    .seg button { border: 0; background: none; padding: 5px 10px; cursor: pointer; }
    .seg button + button { border-left: 1px solid var(--divider-color); }
    .seg button[aria-pressed="true"] { background: var(--primary-color); color: var(--text-primary-color, #fff); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
    .tile { border: 1px solid var(--divider-color); border-radius: 10px; padding: 8px 10px; min-width: 0; }
    .tile .v { font-size: 1.25em; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .tile .l { font-size: .8em; color: var(--secondary-text-color); }
    .chart svg { display: block; max-width: 100%; height: auto; overflow: visible; }
    .chart .axis { fill: var(--secondary-text-color); font-size: 10px; }
    .chart .grid-line { stroke: var(--divider-color); stroke-width: 1; }
    .chart .bar { fill: var(--c); }
    .chart .hit { fill: transparent; cursor: pointer; }
    .chart .hit:hover + .bar, .chart .bar.on { opacity: .75; }
    .readout { font-size: .9em; min-height: 1.4em; margin-top: 6px; font-variant-numeric: tabular-nums; }

    /* Toast */
    .toast { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 8px;
      background: var(--primary-text-color); color: var(--card-background-color); font-size: .9em; }
    .toast button { background: none; border: 0; color: inherit; font-weight: 600; cursor: pointer; text-decoration: underline; }
    .toast.err { background: var(--error-color, #db4437); color: #fff; }

    /* Dialog */
    dialog { border: 0; border-radius: 16px; padding: 0; width: min(440px, calc(100vw - 32px)); max-height: calc(100vh - 48px);
      background: var(--card-background-color, #fff); color: var(--primary-text-color); box-shadow: 0 12px 40px rgba(0,0,0,.35);
      overflow: auto; overscroll-behavior: contain; }
    dialog::backdrop { background: rgba(0,0,0,.45); }
    .dlg { display: flex; flex-direction: column; gap: 12px; padding: 18px; }
    .dlg h2 { margin: 0; font-size: 1.15em; font-weight: 500; }
    .field { display: flex; flex-direction: column; gap: 4px; }
    .field > span { font-size: .85em; color: var(--secondary-text-color); }
    .field input, .field select, .field textarea { width: 100%; }
    .field textarea { min-height: 72px; resize: vertical; }
    .two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .rating { display: flex; flex-wrap: wrap; gap: 4px; }
    .rating button { min-width: 36px; height: 36px; border-radius: 8px; border: 1px solid var(--divider-color); background: none; cursor: pointer; }
    .rating button[aria-pressed="true"] { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
    .unit { display: flex; align-items: center; gap: 6px; }
    .unit input { flex: 1; }
    .warn { border-left: 3px solid var(--warning-color, #ffa600); padding: 6px 10px; font-size: .9em; }
    .error { color: var(--error-color, #db4437); font-size: .9em; }
    .actions { display: flex; justify-content: flex-end; gap: 8px; }
    .primary { border: 0; border-radius: 8px; padding: 8px 14px; cursor: pointer; background: var(--primary-color); color: var(--text-primary-color, #fff); }
    .primary.danger { background: var(--error-color, #db4437); }
    .primary[disabled] { opacity: .5; cursor: default; }
    .past { font-size: .85em; color: var(--secondary-text-color); }

    @media (max-width: 420px) {
      .logbtn { gap: 6px; padding: 6px 8px; min-height: 46px; }
      .lb-emoji { font-size: 1.15em; }
      .lb-status { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .day { min-height: 50px; }
      .marks { font-size: .82em; }
    }
    @media (prefers-reduced-motion: no-preference) {
      .chip, .tab, .day { transition: background-color .12s ease; }
    }
  `;

  // ------------------------------------------------------------------- card
  class HabitLogCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._hass = null;
      this._config = null;
      this._connected = false;
      this._cfg = null; // integration config payload
      this._loadError = null;
      this._byDay = new Map();
      this._view = null; // {y, m0}
      this._selected = null;
      this._tab = "calendar";
      this._featured = undefined;
      this._stats = { habitId: null, period: "month", offset: 0, data: null, log: null, showLog: false, active: null };
      this._modal = null;
      this._toast = null;
      this._busy = false;
      this._unsub = null;
      this._refreshTimer = null;
      this._token = 0;
      this._fmt = new Fmt(navigator.language);
      this._dark = false;
    }

    static getStubConfig() {
      return { title: "Habits" };
    }

    setConfig(config) {
      if (!config || typeof config !== "object") throw new Error("Invalid configuration");
      const bool = (v, d) => (v === undefined ? d : !!v);
      if (config.habits !== undefined && !Array.isArray(config.habits)) throw new Error("'habits' must be a list of habit names");
      if (config.initial_view && !["calendar", "stats"].includes(config.initial_view)) {
        throw new Error("'initial_view' must be calendar or stats");
      }
      this._config = {
        title: typeof config.title === "string" ? config.title : "Habits",
        habits: (config.habits || []).map((x) => String(x).trim().toLowerCase()).filter(Boolean),
        show_quick_log: bool(config.show_quick_log, true),
        show_stats: bool(config.show_stats, true),
        show_journal: bool(config.show_journal, true),
        show_streak_emoji: bool(config.show_streak_emoji, true),
        featured: typeof config.featured === "string" ? config.featured.trim().toLowerCase() : null,
      };
      this._tab = config.initial_view === "stats" && this._config.show_stats ? "stats" : "calendar";
      this._build();
      this._render();
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      const lang = hass?.locale?.language || hass?.language || navigator.language;
      const dark = !!hass?.themes?.darkMode;
      const changed = lang !== this._fmt.lang || dark !== this._dark;
      if (lang !== this._fmt.lang) this._fmt = new Fmt(lang);
      this._dark = dark;
      if (first && this._connected) this._start();
      else if (changed) this._render();
    }

    connectedCallback() {
      this._connected = true;
      if (!this._resizeObs && "ResizeObserver" in window) {
        this._resizeObs = new ResizeObserver((items) => {
          const width = Math.floor(items[0].contentRect.width);
          if (width <= 0 || Math.abs(width - (this._chartWidth || 0)) < 8) return;
          this._chartWidth = width;
          if (this._tab === "stats") {
            cancelAnimationFrame(this._resizeRaf);
            this._resizeRaf = requestAnimationFrame(() => this._render());
          }
        });
      }
      if (this._resizeObs && this._root) this._resizeObs.observe(this._root);
      if (this._hass && this._config) this._start();
    }

    disconnectedCallback() {
      this._connected = false;
      if (this._resizeObs) this._resizeObs.disconnect();
      this._stop();
    }

    getCardSize() {
      return 12;
    }

    getGridOptions() {
      return { columns: 12, min_columns: 6 };
    }

    // ------------------------------------------------------------- data
    async _start() {
      this._stop();
      const token = ++this._token;
      try {
        const unsub = await this._hass.connection.subscribeMessage(() => this._scheduleRefresh(), {
          type: `${DOMAIN}/subscribe`,
        });
        if (token !== this._token) {
          unsub();
          return;
        }
        this._unsub = unsub;
      } catch (err) {
        this._loadError = err;
        this._render();
        return;
      }
      await this._refresh();
    }

    _stop() {
      this._token++;
      clearTimeout(this._refreshTimer);
      if (this._unsub) {
        const unsub = this._unsub;
        this._unsub = null;
        Promise.resolve().then(unsub).catch(() => {});
      }
    }

    _scheduleRefresh() {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = setTimeout(() => this._refresh(), REFRESH_DEBOUNCE_MS);
    }

    _ws(msg) {
      return this._hass.callWS({ type: `${DOMAIN}/${msg.type}`, ...msg.data });
    }

    _gridRange() {
      const { y, m0 } = this._view;
      const first = keyOf(y, m0, 1);
      const wsIdx = this._cfg?.week_start === "sunday" ? 0 : 1;
      const lead = (weekdayOf(first) - wsIdx + 7) % 7;
      const cells = Math.ceil((lead + daysInMonth(y, m0)) / 7) * 7;
      const start = addDays(first, -lead);
      return { start, end: addDays(start, cells - 1), cells };
    }

    async _refresh() {
      const token = this._token;
      try {
        const cfg = await this._ws({ type: "config", data: {} });
        if (token !== this._token) return;
        const prevToday = this._cfg?.today;
        this._cfg = cfg;
        this._cfgAt = Date.now();
        this._loadError = null;
        if (!this._view) {
          const [y, m] = partsOf(cfg.today);
          this._view = { y, m0: m - 1 };
          this._selected = cfg.today;
        } else if (prevToday && prevToday !== cfg.today && this._selected === prevToday) {
          this._selected = cfg.today; // day rolled over while open
        }
        if (this._featured === undefined) this._featured = this._initialFeatured();
        if (!this._stats.habitId || !cfg.habits.some((x) => x.id === this._stats.habitId)) {
          this._stats.habitId = this._visibleHabits(true)[0]?.id || null;
        }
        const { start, end } = this._gridRange();
        const tasks = [this._ws({ type: "entries", data: { start_date: start, end_date: end } })];
        if (this._tab === "stats" && this._stats.habitId) tasks.push(this._loadStats(token, false));
        const [entries] = await Promise.all(tasks);
        if (token !== this._token) return;
        this._indexEntries(entries);
      } catch (err) {
        if (token !== this._token) return;
        this._loadError = err;
      }
      this._render();
    }

    _indexEntries(payload) {
      const byDay = new Map();
      const slot = (k) => {
        if (!byDay.has(k)) byDay.set(k, { entries: [], journal: [] });
        return byDay.get(k);
      };
      for (const e of payload.entries) for (const d of e.days) slot(d).entries.push(e);
      for (const j of payload.journal) slot(j.day).journal.push(j);
      this._byDay = byDay;
    }

    async _loadStats(token = this._token, render = true) {
      const st = this._stats;
      if (!st.habitId) return;
      try {
        const data = await this._ws({
          type: "stats",
          data: { habit_id: st.habitId, period: st.period, offset: st.period === "all" ? 0 : st.offset },
        });
        if (token !== this._token) return;
        st.data = data;
        st.active = null;
        if (st.showLog) await this._loadLog(token, false);
      } catch (err) {
        if (token !== this._token) return;
        st.data = null;
        this._showToast(this._errText(err), true);
      }
      if (render) this._render();
    }

    async _loadLog(token = this._token, render = true) {
      const st = this._stats;
      const sum = st.data?.summary;
      if (!st.habitId || !sum) return;
      const data = { habit: st.habitId };
      if (sum.start) data.start_date = sum.start;
      if (sum.end) data.end_date = addDays(sum.end, -1);
      try {
        const res = await this._call("list_entries", data, true);
        if (token !== this._token) return;
        st.log = (res.entries || []).slice().reverse();
      } catch (err) {
        st.log = [];
        this._showToast(this._errText(err), true);
      }
      if (render) this._render();
    }

    async _call(service, data, wantResponse = false) {
      const res = await this._hass.callWS({
        type: "call_service",
        domain: DOMAIN,
        service,
        service_data: data,
        return_response: wantResponse,
      });
      return wantResponse ? res?.response || {} : res;
    }

    _errText(err) {
      if (!err) return "Something went wrong.";
      if (err.code === "unauthorized") return "Your user isn't in Habit Log's allowed users.";
      let text = String(err.message || err).replace(/^Validation error:\s*/i, "").trim();
      if (text && !/[.!?]$/.test(text)) text += ".";
      return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Something went wrong.";
    }

    // ----------------------------------------------------------- helpers
    _habit(id) {
      return this._cfg?.habits.find((x) => x.id === id) || null;
    }

    _visibleHabits(includeArchived = false) {
      if (!this._cfg) return [];
      const wanted = this._config.habits;
      let list = this._cfg.habits.filter((x) => includeArchived || !x.archived);
      if (wanted.length) {
        const pick = (x) => wanted.indexOf(x.name.toLowerCase()) !== -1 || wanted.indexOf(x.id.toLowerCase()) !== -1;
        list = list.filter(pick);
        const rank = (x) => Math.min(...[wanted.indexOf(x.name.toLowerCase()), wanted.indexOf(x.id.toLowerCase())].filter((i) => i >= 0));
        list.sort((a, b) => rank(a) - rank(b));
      }
      return list;
    }

    _color(habit) {
      if (habit.color) return habit.color;
      const idx = Math.max(0, this._cfg.habits.findIndex((x) => x.id === habit.id));
      const pal = this._dark ? PALETTE.dark : PALETTE.light;
      return pal[idx % pal.length];
    }

    _label(habit) {
      return habit.emoji ? `${habit.emoji} ${habit.name}` : habit.name;
    }

    _storageKey() {
      return `${TAG}:featured:${this._config.title}`;
    }

    _initialFeatured() {
      try {
        const saved = localStorage.getItem(this._storageKey());
        if (saved !== null) return saved || null;
      } catch (_e) {
        /* storage unavailable */
      }
      const streaks = this._visibleHabits().filter((x) => x.kind === "streak");
      if (this._config.featured) {
        const f = streaks.find((x) => x.name.toLowerCase() === this._config.featured || x.id === this._config.featured);
        if (f) return f.id;
      }
      return streaks[0]?.id || null;
    }

    _setFeatured(id) {
      this._featured = this._featured === id ? null : id;
      try {
        localStorage.setItem(this._storageKey(), this._featured || "");
      } catch (_e) {
        /* storage unavailable */
      }
      this._render();
    }

    _isFuture(key) {
      return this._cfg && key > this._cfg.today;
    }

    // ------------------------------------------------------------- build
    _build() {
      if (this._root) return;
      const style = h("style", null, STYLE);
      this._card = h("ha-card");
      this._root = h("div", { class: "wrap" });
      this._card.append(this._root);
      this._dialog = h("dialog", {
        "aria-labelledby": "dlg-title",
        oncancel: (e) => {
          e.preventDefault();
          this._closeModal();
        },
        onclick: (e) => {
          if (e.target === this._dialog) this._closeModal();
        },
      });
      this.shadowRoot.append(style, this._card, this._dialog);
    }

    _render() {
      if (!this._root || !this._config) return;
      const out = [];
      out.push(this._renderHeader());
      if (this._loadError) {
        out.push(this._renderLoadError());
      } else if (!this._cfg) {
        out.push(h("div", { class: "msg" }, "Loading…"));
      } else if (!this._visibleHabits(true).length && !this._cfg.journal_fields.length) {
        out.push(
          h(
            "div",
            { class: "msg" },
            "No habits yet. Add one in Settings → Devices & services → Habit Log → Add habit. It appears here automatically."
          )
        );
      } else {
        // Logging lives on the Calendar tab only; Stats is read-only.
        if (this._tab === "calendar" && this._config.show_quick_log) out.push(this._renderLogPanel());
        // Directly under the buttons that trigger it, so it is visible on phones.
        if (this._toast) out.push(this._renderToast());
        out.push(this._tab === "stats" ? this._renderStats() : this._renderCalendar());
      }
      if (this._toast && (this._loadError || !this._cfg)) out.push(this._renderToast());
      this._root.replaceChildren(...out.filter(Boolean));
    }

    _renderHeader() {
      const tabs = this._config.show_stats
        ? h(
            "div",
            { class: "tabs", role: "tablist" },
            ["calendar", "stats"].map((t) =>
              h(
                "button",
                {
                  class: "tab",
                  role: "tab",
                  "aria-selected": String(this._tab === t),
                  onclick: () => {
                    if (this._tab === t) return;
                    this._tab = t;
                    if (t === "stats") this._loadStats();
                    this._render();
                  },
                },
                t === "calendar" ? "Calendar" : "Stats"
              )
            )
          )
        : null;
      return h("div", { class: "row" }, h("div", { class: "title grow" }, this._config.title), tabs);
    }

    _renderLoadError() {
      const err = this._loadError;
      let text = this._errText(err);
      if (err?.code === "not_found" || err?.code === "unknown_command") {
        text = "Habit Log isn't set up. Add it in Settings → Devices & services, then reload this page.";
      }
      return h("div", { class: "msg", role: "alert" }, text, " ", h("button", { class: "textbtn", onclick: () => this._start() }, "Retry"));
    }

    _renderToast() {
      const t = this._toast;
      return h(
        "div",
        { class: `toast${t.error ? " err" : ""}`, role: t.error ? "alert" : "status" },
        h("span", { class: "grow" }, t.text),
        t.undo ? h("button", { onclick: () => t.undo() }, "Undo") : null,
        h("button", { "aria-label": "Dismiss", onclick: () => this._hideToast() }, "✕")
      );
    }

    _showToast(text, error = false, undo = null) {
      clearTimeout(this._toastTimer);
      this._toast = { text, error, undo };
      this._toastTimer = setTimeout(() => this._hideToast(), error ? TOAST_MS * 1.5 : TOAST_MS);
      this._render();
    }

    _hideToast() {
      clearTimeout(this._toastTimer);
      this._toast = null;
      this._render();
    }

    // --------------------------------------------------------- log panel
    /** Per-habit state for a day: today from the server summary, other days from loaded entries. */
    _dayState(habit, key) {
      if (key === this._cfg.today && this._cfg.today_summary?.[habit.id]) {
        const t = this._cfg.today_summary[habit.id];
        return { count: t.count, seconds: t.seconds };
      }
      const entries = (this._byDay.get(key)?.entries || []).filter((e) => e.habit_id === habit.id && e.count_day === key);
      return { count: entries.length, seconds: entries.reduce((a, e) => a + (e.seconds || 0), 0) };
    }

    _renderLogPanel() {
      const key = this._selected || this._cfg.today;
      const isToday = key === this._cfg.today;
      const future = this._isFuture(key);
      const fmt = this._fmt;
      const when = isToday ? "today" : fmt.date(key);
      const heading = h(
        "div",
        { class: "log-head" },
        icon("mdi:pencil-plus-outline"),
        h("span", { class: "grow" }, future ? `${fmt.date(key)} hasn't happened yet` : `Log for ${when}`),
        isToday ? null : h("button", { class: "textbtn", onclick: () => this._goToday() }, "Back to today")
      );
      const buttons = this._visibleHabits().map((habit) => {
        const st = this._dayState(habit, key);
        let status;
        let done = false;
        let action;
        if (habit.kind === "streak") {
          done = st.count > 0;
          status = done ? "Done" : "Tap to log";
          action = done ? "mdi:check-circle" : "mdi:plus-circle-outline";
        } else if (habit.track_time) {
          done = st.seconds > 0;
          status = done ? `${fmtDuration(st.seconds)} logged` : "Add time";
          action = "mdi:timer-plus-outline";
        } else {
          done = st.count > 0;
          status = done ? `${st.count} logged` : "Tap to add";
          action = "mdi:plus-circle-outline";
        }
        const label =
          habit.kind === "streak"
            ? done
              ? `${habit.name}: done for ${when}`
              : `Log ${habit.name} for ${when}`
            : habit.track_time
              ? `Log time for ${habit.name} on ${when}`
              : `Add one ${habit.name} for ${when}`;
        return h(
          "button",
          {
            class: `logbtn${done ? " done" : ""}`,
            style: { "--c": this._color(habit) },
            disabled: this._busy || future || null,
            "aria-label": label,
            title: label,
            onclick: () => this._quickLog(habit, key),
          },
          h("span", { class: "lb-emoji", "aria-hidden": "true" }, habit.emoji || h("span", { class: "sw" })),
          h("span", { class: "lb-text" }, h("span", { class: "lb-name" }, habit.name), h("span", { class: "lb-status" }, status)),
          h("span", { class: "lb-action" }, icon(action))
        );
      });
      if (this._config.show_journal && this._cfg.journal_fields.some((f) => f.active)) {
        const has = (this._byDay.get(key)?.journal || []).length > 0;
        buttons.push(
          h(
            "button",
            {
              class: "logbtn journal",
              disabled: future || null,
              "aria-label": `Add a journal entry for ${when}`,
              onclick: () => this._openJournal(null, key),
            },
            h("span", { class: "lb-emoji", "aria-hidden": "true" }, "📝"),
            h("span", { class: "lb-text" }, h("span", { class: "lb-name" }, "Journal"), h("span", { class: "lb-status" }, has ? "Add another entry" : "Add entry")),
            h("span", { class: "lb-action" }, icon("mdi:plus-circle-outline"))
          )
        );
      }
      if (!buttons.length) return null;
      return h("section", { class: "logpanel", "aria-label": `Log for ${when}` }, heading, h("div", { class: "logbtns" }, buttons));
    }

    async _quickLog(habit, day) {
      if (habit.track_time) {
        this._openTimed(habit, null, day);
        return;
      }
      if (this._busy) return;
      this._busy = true;
      this._render();
      try {
        const res = await this._call("log", { habit: habit.id, date: day }, true);
        const when = day === this._cfg.today ? "today" : this._fmt.date(day, "short");
        if (habit.kind === "streak" && res.created === false) {
          this._showToast(`${habit.name} is already logged for ${when}.`);
        } else {
          this._showToast(`Logged ${habit.name} for ${when}.`, false, () => this._undo(res.entry_id));
        }
      } catch (err) {
        this._showToast(this._errText(err), true);
      } finally {
        this._busy = false;
        this._render();
      }
    }

    async _undo(entryId) {
      this._hideToast();
      try {
        await this._call("delete_entry", { entry_id: entryId });
        this._showToast("Removed.");
      } catch (err) {
        this._showToast(this._errText(err), true);
      }
    }

    // ---------------------------------------------------------- calendar
    _renderCalendar() {
      const { y, m0 } = this._view;
      const fmt = this._fmt;
      const head = h(
        "div",
        { class: "cal-head" },
        h("button", { class: "iconbtn", "aria-label": "Previous month", onclick: () => this._shiftMonth(-1) }, icon("mdi:chevron-left")),
        h("div", { class: "grow", style: { textAlign: "center" }, "aria-live": "polite" }, fmt.monthYear.format(new Date(Date.UTC(y, m0, 1)))),
        h("button", { class: "iconbtn", "aria-label": "Next month", onclick: () => this._shiftMonth(1) }, icon("mdi:chevron-right")),
        h("button", { class: "textbtn", onclick: () => this._goToday() }, "Today"),
        this._backfillHabits().length
          ? h("button", { class: "textbtn", onclick: () => this._openBackfill() }, "Back-fill")
          : null
      );

      const habits = this._visibleHabits(true);
      const streaks = habits.filter((x) => x.kind === "streak" && !x.archived).slice(0, MAX_THREADS);
      const counters = habits.filter((x) => x.kind !== "streak");
      // Optional: streak emoji in the day as well as its thread (streaks first).
      const markerHabits = this._config.show_streak_emoji
        ? [...habits.filter((x) => x.kind === "streak"), ...counters]
        : counters;
      const visibleIds = new Set(habits.map((x) => x.id));
      const featured = streaks.find((x) => x.id === this._featured) || null;

      const { start, cells } = this._gridRange();
      const wsIdx = this._cfg.week_start === "sunday" ? 0 : 1;
      const grid = h("div", { class: "grid", "aria-label": "Habit calendar", style: { "--threads": String(streaks.length) } });
      for (let i = 0; i < 7; i++) {
        grid.append(h("div", { class: "dow", "aria-hidden": "true" }, fmt.weekday.format(new Date(Date.UTC(2024, 0, 7 + ((wsIdx + i) % 7))))));
      }

      // Which streak habits are active on each grid day (for thread joins).
      const keys = Array.from({ length: cells }, (_, i) => addDays(start, i));
      const activeOn = (key, habitId) =>
        !!this._byDay.get(key)?.entries.some((e) => e.habit_id === habitId);

      keys.forEach((key, i) => {
        const data = this._byDay.get(key) || { entries: [], journal: [] };
        const entries = data.entries.filter((e) => visibleIds.has(e.habit_id));
        const [, mm] = partsOf(key);
        const outside = mm - 1 !== m0;
        const classes = ["day"];
        if (outside) classes.push("out");
        if (key === this._cfg.today) classes.push("today");
        if (key === this._selected) classes.push("sel");
        if (this._isFuture(key)) classes.push("future");

        const aria = [fmt.date(key)];
        const cell = h("button", {
          class: classes.join(" "),
          "aria-pressed": String(key === this._selected),
          "aria-current": key === this._cfg.today ? "date" : null,
          onclick: () => {
            this._selected = key;
            this._render();
          },
        });

        if (featured && activeOn(key, featured.id)) {
          cell.style.setProperty("--fill", `color-mix(in srgb, ${this._color(featured)} ${this._dark ? 34 : 26}%, transparent)`);
        }

        const marks = h("span", { class: "marks" });
        let shown = 0;
        let hidden = 0;
        for (const habit of markerHabits) {
          const n = entries.filter((e) => e.habit_id === habit.id).length;
          if (!n) continue;
          const isStreak = habit.kind === "streak";
          if (!isStreak) aria.push(n > 1 ? `${habit.name} ×${n}` : habit.name);
          if (shown >= MAX_MARKERS) {
            hidden += 1;
            continue;
          }
          shown += 1;
          marks.append(
            h(
              "span",
              { class: "mark", style: { "--c": this._color(habit) } },
              habit.emoji ? habit.emoji : h("span", { class: "dot" }),
              n > 1 && !isStreak ? h("sup", null, String(n)) : null
            )
          );
        }
        if (hidden) marks.append(h("span", { class: "mark muted small" }, `+${hidden}`));
        if (this._config.show_journal && data.journal.length) {
          marks.append(h("span", { class: "mark" }, "📝"));
          aria.push("journal");
        }

        const threads = h("span", { class: "threads", "aria-hidden": "true" });
        const col = i % 7;
        for (const habit of streaks) {
          const on = activeOn(key, habit.id);
          if (on) aria.push(habit.name);
          const cls = ["thread"];
          if (!on) cls.push("off");
          else {
            if (col > 0 && activeOn(keys[i - 1], habit.id)) cls.push("jl");
            if (col < 6 && activeOn(keys[i + 1], habit.id)) cls.push("jr");
          }
          threads.append(h("span", { class: cls.join(" "), style: { "--c": this._color(habit) } }));
        }

        cell.setAttribute("aria-label", aria.join(", "));
        cell.append(h("span", { class: "num" }, String(partsOf(key)[2])), marks, threads);
        grid.append(cell);
      });

      const legend = h("div", { class: "legend" });
      if (streaks.length) legend.append(h("span", { class: "legend-cap" }, "Key. Tap a streak to fill its days."));
      for (const habit of streaks) {
        const pressed = this._featured === habit.id;
        legend.append(
          h(
            "button",
            {
              class: "lg",
              style: { "--c": this._color(habit) },
              "aria-pressed": String(pressed),
              title: pressed ? "Featured: fills each day. Click to turn off." : "Click to fill days with this streak",
              onclick: () => this._setFeatured(habit.id),
            },
            h("span", { class: "bar" }),
            this._config.show_streak_emoji && habit.emoji ? h("span", { "aria-hidden": "true" }, habit.emoji) : null,
            h("span", null, habit.name)
          )
        );
      }
      for (const habit of counters.filter((x) => !x.archived)) {
        legend.append(
          h(
            "span",
            { class: "lg", style: { "--c": this._color(habit) } },
            habit.emoji ? h("span", { "aria-hidden": "true" }, habit.emoji) : h("span", { class: "dot" }),
            h("span", null, habit.name),
            habit.track_time ? h("span", { class: "small" }, "(time)") : null
          )
        );
      }

      return h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } }, head, grid, legend, this._renderDayPanel());
    }

    _shiftMonth(delta) {
      const d = new Date(Date.UTC(this._view.y, this._view.m0 + delta, 1));
      this._view = { y: d.getUTCFullYear(), m0: d.getUTCMonth() };
      this._byDay = new Map();
      this._render();
      this._refresh();
    }

    _goToday() {
      const [y, m] = partsOf(this._cfg.today);
      this._selected = this._cfg.today;
      if (y === this._view.y && m - 1 === this._view.m0) return this._render();
      this._view = { y, m0: m - 1 };
      this._byDay = new Map();
      this._render();
      this._refresh();
    }

    // --------------------------------------------------------- day panel
    _renderDayPanel() {
      const key = this._selected;
      if (!key) return null;
      const fmt = this._fmt;
      const data = this._byDay.get(key) || { entries: [], journal: [] };
      const visibleIds = new Set(this._visibleHabits(true).map((x) => x.id));
      const order = new Map(this._cfg.habits.map((x, i) => [x.id, i]));
      const entries = data.entries
        .filter((e) => visibleIds.has(e.habit_id))
        .sort((a, b) => order.get(a.habit_id) - order.get(b.habit_id) || String(a.start || a.created).localeCompare(String(b.start || b.created)));
      const items = h("ul", { class: "items" });

      for (const e of entries) {
        const habit = this._habit(e.habit_id);
        if (!habit) continue;
        items.append(this._entryItem(e, habit, key));
      }
      if (this._config.show_journal) {
        for (const j of data.journal) items.append(this._journalItem(j));
      }
      if (!items.children.length) {
        items.append(h("li", { class: "item muted" }, this._isFuture(key) ? "This day hasn't happened yet." : "Nothing logged."));
      }

      return h(
        "section",
        { class: "panel", "aria-label": `Entries for ${fmt.date(key)}` },
        h("h3", null, fmt.date(key), key === this._cfg.today ? h("span", { class: "muted small" }, " (today)") : null),
        items
      );
    }

    _entryItem(e, habit, dayKey) {
      const fmt = this._fmt;
      let meta = null;
      if (e.start_local) {
        const s0 = naiveParts(e.start_local);
        const e0 = naiveParts(e.end_local);
        const sTxt = s0.key === dayKey ? fmt.time_(e.start_local) : `${fmt.date(s0.key, "short")} ${fmt.time_(e.start_local)}`;
        const eTxt = e0.key === dayKey ? fmt.time_(e.end_local) : `${fmt.date(e0.key, "short")} ${fmt.time_(e.end_local)}`;
        meta = `${sTxt} – ${eTxt} (${fmtDuration(e.seconds)})`;
      } else if (habit.kind === "streak") {
        meta = "Streak day";
      }
      return h(
        "li",
        { class: "item" },
        h("span", { class: "lead", "aria-hidden": "true", style: { "--c": this._color(habit) } }, habit.emoji || h("span", { class: "dot" })),
        h(
          "div",
          { class: "body" },
          h("div", { class: "name" }, habit.name, habit.archived ? h("span", { class: "muted small" }, " (archived)") : null),
          meta ? h("div", { class: "meta" }, meta) : null,
          e.notes ? h("div", { class: "notes" }, e.notes) : null
        ),
        h(
          "button",
          { class: "iconbtn", "aria-label": `Edit ${habit.name} entry`, onclick: () => (habit.track_time || e.start_local ? this._openTimed(habit, e) : this._openDated(habit, e)) },
          icon("mdi:pencil")
        ),
        h("button", { class: "iconbtn", "aria-label": `Delete ${habit.name} entry`, onclick: () => this._openDelete(e, habit) }, icon("mdi:delete-outline"))
      );
    }

    _journalItem(j) {
      const list = h("dl", { class: "kv" });
      for (const v of j.values) {
        list.append(h("dt", null, v.label), h("dd", null, this._fmtJournalValue(v)));
      }
      return h(
        "li",
        { class: "item" },
        h("span", { class: "lead", "aria-hidden": "true" }, "📝"),
        h("div", { class: "body" }, h("div", { class: "name" }, "Journal"), list),
        h("button", { class: "iconbtn", "aria-label": "Edit journal entry", onclick: () => this._openJournal(j, j.day) }, icon("mdi:pencil")),
        h("button", { class: "iconbtn", "aria-label": "Delete journal entry", onclick: () => this._openDelete(j, null) }, icon("mdi:delete-outline"))
      );
    }

    _fmtJournalValue(v) {
      if (v.field_type === "boolean") return v.value ? "Yes" : "No";
      if (v.field_type === "rating") {
        const f = this._cfg.journal_fields.find((x) => x.id === v.field_id);
        return f?.max ? `${v.value} / ${f.max}` : String(v.value);
      }
      return v.unit ? `${v.value} ${v.unit}` : String(v.value);
    }

    // -------------------------------------------------------------- stats
    _renderStats() {
      const st = this._stats;
      const habits = this._visibleHabits(true);
      if (!habits.length) return h("div", { class: "msg" }, "Add a habit to see statistics.");
      const habit = this._habit(st.habitId) || habits[0];
      const fmt = this._fmt;

      const select = h(
        "select",
        {
          "aria-label": "Habit",
          onchange: (e) => {
            st.habitId = e.target.value;
            st.data = null;
            st.log = null;
            this._loadStats();
          },
        },
        habits.map((x) => h("option", { value: x.id, selected: x.id === habit.id || null }, `${this._label(x)}${x.archived ? " (archived)" : ""}`))
      );
      const periods = [
        ["week", "Week"],
        ["month", "Month"],
        ["year", "Year"],
        ["all", "All time"],
      ];
      const seg = h(
        "div",
        { class: "seg", role: "group", "aria-label": "Period" },
        periods.map(([p, label]) =>
          h(
            "button",
            {
              "aria-pressed": String(st.period === p),
              onclick: () => {
                if (st.period === p) return;
                st.period = p;
                st.offset = 0;
                st.log = null;
                this._loadStats();
              },
            },
            label
          )
        )
      );

      const sum = st.data?.summary;
      const navDisabled = st.period === "all";
      const nav = h(
        "div",
        { class: "row" },
        h("button", { class: "iconbtn", "aria-label": "Previous period", disabled: navDisabled || null, onclick: () => this._shiftPeriod(-1) }, icon("mdi:chevron-left")),
        h("div", { class: "grow", style: { textAlign: "center" }, "aria-live": "polite" }, sum ? this._periodLabel(sum) : "…"),
        h(
          "button",
          { class: "iconbtn", "aria-label": "Next period", disabled: navDisabled || st.offset >= 0 || null, onclick: () => this._shiftPeriod(1) },
          icon("mdi:chevron-right")
        )
      );

      const out = [h("div", { class: "stats-head" }, select, seg), nav];
      if (!st.data) {
        out.push(h("div", { class: "msg" }, "Loading…"));
        return h("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } }, out);
      }

      out.push(this._renderTiles(habit, sum));
      if (habit.kind !== "streak" && st.data.since_reset) out.push(this._renderReset(habit, st.data.since_reset));
      out.push(this._renderChart(habit, st.data.buckets, sum));

      const logToggle = h(
        "button",
        {
          class: "textbtn",
          "aria-expanded": String(st.showLog),
          onclick: () => {
            st.showLog = !st.showLog;
            if (st.showLog && !st.log) this._loadLog();
            else this._render();
          },
        },
        st.showLog ? "Hide log" : habit.track_time ? "Show logged periods" : "Show log"
      );
      out.push(h("div", { class: "row" }, logToggle));
      if (st.showLog) out.push(this._renderLog(habit));
      return h("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } }, out);
    }

    _shiftPeriod(delta) {
      const st = this._stats;
      st.offset = Math.min(0, st.offset + delta);
      st.log = null;
      this._loadStats();
    }

    _periodLabel(sum) {
      const fmt = this._fmt;
      if (sum.period === "all") return sum.first_day ? `All time (since ${fmt.date(sum.first_day, "year")})` : "All time";
      if (sum.period === "year") return sum.start.slice(0, 4);
      if (sum.period === "month") return fmt.monthYear.format(new Date(utcOf(sum.start)));
      if (sum.period === "week") return `${fmt.date(sum.start, "short")} – ${fmt.date(addDays(sum.end, -1), "year")}`;
      return fmt.date(sum.start);
    }

    _renderTiles(habit, sum) {
      const tiles = [];
      const tile = (v, l) => tiles.push(h("div", { class: "tile" }, h("div", { class: "v" }, String(v)), h("div", { class: "l" }, l)));
      if (habit.kind === "streak") {
        tile(sum.active_days, sum.active_days === 1 ? "day" : "days");
        tile(`${sum.best_streak_in_period} d`, "longest run in period");
        tile(`${sum.current_streak} d`, "current streak");
        tile(`${sum.best_streak} d`, "best streak ever");
      } else {
        if (habit.track_time) {
          tile(fmtHoursTotal(sum.seconds), "total time");
          tile(sum.count ? fmtDuration(sum.seconds / sum.count) : "–", "average per entry");
        }
        tile(sum.count, sum.count === 1 ? "entry" : "entries");
        tile(sum.active_days, sum.active_days === 1 ? "active day" : "active days");
        if (!habit.track_time) tile(`${sum.current_streak} d`, "current daily streak");
      }
      return h("div", { class: "tiles" }, tiles);
    }

    _renderChart(habit, buckets, sum) {
      const st = this._stats;
      if (!buckets.length) return h("div", { class: "muted small" }, "Nothing logged yet.");
      const metric = habit.track_time ? "seconds" : habit.kind === "streak" ? "active_days" : "count";
      const values = buckets.map((b) => b[metric]);
      const max = Math.max(...values, metric === "seconds" ? 3600 : 1);
      const fmtVal = (v) => (metric === "seconds" ? fmtHoursTotal(v) : metric === "active_days" ? `${v} ${v === 1 ? "day" : "days"}` : `${v} ${v === 1 ? "entry" : "entries"}`);
      const fmt = this._fmt;
      const bucketLabel = (b) => {
        if (sum.period === "year") return fmt.monthShort.format(new Date(utcOf(b.start)));
        if (sum.period === "all") return b.key;
        return fmt.date(b.start, "short");
      };
      const tick = (b, i) => {
        if (sum.period === "week") return fmt.weekdayNarrow.format(new Date(utcOf(b.start)));
        if (sum.period === "month") return i === 0 || (i + 1) % 7 === 0 ? String(i + 1) : "";
        if (sum.period === "year") return fmt.monthNarrow.format(new Date(utcOf(b.start)));
        return buckets.length <= 12 || i % Math.ceil(buckets.length / 10) === 0 ? b.key : "";
      };

      const W = Math.max(240, Math.round(this._chartWidth || 320));
      const H = 140;
      const top = 16;
      const base = H - 18;
      const n = buckets.length;
      const slot = W / n;
      const bw = Math.max(2, Math.min(24, slot * 0.7));
      const color = this._color(habit);
      const svg = s("svg", {
        viewBox: `0 0 ${W} ${H}`,
        width: W,
        height: H,
        role: "img",
        "aria-label": `${habit.name}, ${this._periodLabel(sum)}: ${fmtVal(values.reduce((a, b) => a + b, 0))} in total`,
      });
      svg.append(s("line", { class: "grid-line", x1: 0, x2: W, y1: base + 0.5, y2: base + 0.5 }));
      svg.append(s("line", { class: "grid-line", x1: 0, x2: W, y1: top + 0.5, y2: top + 0.5, "stroke-dasharray": "2 3" }));
      svg.append(s("text", { class: "axis", x: W, y: top - 4, "text-anchor": "end" }, fmtVal(max)));

      buckets.forEach((b, i) => {
        const v = values[i];
        const x = i * slot + (slot - bw) / 2;
        const bh = v > 0 ? Math.max(2, ((base - top) * v) / max) : 0;
        const y = base - bh;
        const r = Math.min(4, bw / 2, bh);
        const select = () => {
          st.active = i;
          this._render();
        };
        svg.append(
          s("rect", {
            class: "hit",
            x: i * slot,
            y: top,
            width: slot,
            height: base - top + 18,
            tabindex: "0",
            "aria-label": `${bucketLabel(b)}: ${fmtVal(v)}`,
            onpointerenter: select,
            onclick: select,
            onfocus: select,
          })
        );
        if (bh > 0) {
          // Rounded data end, square at the baseline.
          const d = `M${x},${base} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${base} Z`;
          svg.append(s("path", { class: `bar${st.active === i ? " on" : ""}`, d, style: `--c:${color}`, "pointer-events": "none" }));
        }
        const t = tick(b, i);
        if (t) svg.append(s("text", { class: "axis", x: i * slot + slot / 2, y: H - 4, "text-anchor": "middle" }, t));
      });

      const active = st.active !== null && st.active !== undefined ? buckets[st.active] : null;
      const readout = active
        ? `${bucketLabel(active)}: ${fmtVal(values[st.active])}${metric === "seconds" && active.count ? `, ${active.count} ${active.count === 1 ? "entry" : "entries"}` : ""}`
        : `Total: ${fmtVal(values.reduce((a, b) => a + b, 0))}. Hover or tap a bar for details.`;
      return h(
        "div",
        { class: "chart", style: { "--c": color } },
        svg,
        h("div", { class: "readout", "aria-live": "polite" }, readout)
      );
    }

    _renderLog(habit) {
      const st = this._stats;
      if (!st.log) return h("div", { class: "muted small" }, "Loading…");
      if (!st.log.length) return h("div", { class: "muted small" }, "No entries in this period.");
      const fmt = this._fmt;
      const items = h("ul", { class: "items" });
      for (const e of st.log.slice(0, 500)) {
        let meta;
        if (e.start_local) {
          const s0 = naiveParts(e.start_local);
          const e0 = naiveParts(e.end_local);
          meta = `${fmt.date(s0.key, "year")} ${fmt.time_(e.start_local)} – ${s0.key === e0.key ? "" : `${fmt.date(e0.key, "short")} `}${fmt.time_(e.end_local)} (${fmtDuration(e.seconds)})`;
        } else {
          meta = fmt.date(e.count_day, "year");
        }
        items.append(
          h(
            "li",
            { class: "item" },
            h(
              "div",
              { class: "body" },
              h("div", { class: "meta", style: { color: "var(--primary-text-color)" } }, meta),
              e.notes ? h("div", { class: "notes" }, e.notes) : null
            ),
            h(
              "button",
              { class: "iconbtn", "aria-label": "Edit entry", onclick: () => (e.start_local ? this._openTimed(habit, e) : this._openDated(habit, e)) },
              icon("mdi:pencil")
            ),
            h("button", { class: "iconbtn", "aria-label": "Delete entry", onclick: () => this._openDelete(e, habit) }, icon("mdi:delete-outline"))
          )
        );
      }
      const more = st.log.length > 500 ? h("div", { class: "muted small" }, `Showing the latest 500 of ${st.log.length}.`) : null;
      return h("div", null, items, more);
    }

    // ------------------------------------------------------------ dialogs
    _openModal(state) {
      this._modal = { busy: false, error: null, confirmLong: false, ...state };
      this._renderModal();
      if (!this._dialog.open) this._dialog.showModal();
      const first = this._dialog.querySelector("input, select, textarea, .rating button");
      if (first) first.focus();
    }

    _closeModal() {
      this._modal = null;
      if (this._dialog.open) this._dialog.close();
      this._dialog.replaceChildren();
    }

    _renderModal() {
      const m = this._modal;
      if (!m) return;
      let body;
      if (m.type === "dated") body = this._dlgDated(m);
      else if (m.type === "timed") body = this._dlgTimed(m);
      else if (m.type === "journal") body = this._dlgJournal(m);
      else if (m.type === "backfill") body = this._dlgBackfill(m);
      else if (m.type === "reset") body = this._dlgReset(m);
      else if (m.type === "confirm") body = this._dlgConfirm(m);
      else body = this._dlgDelete(m);
      this._dialog.replaceChildren(body);
    }

    _field(label, control, hint) {
      return h("label", { class: "field" }, h("span", null, label), control, hint ? h("span", { class: "past" }, hint) : null);
    }

    _footer(m, saveLabel, onSave, danger = false) {
      return [
        m.error ? h("div", { class: "error", role: "alert" }, m.error) : null,
        h(
          "div",
          { class: "actions" },
          h("button", { class: "textbtn", type: "button", onclick: () => this._closeModal() }, "Cancel"),
          h("button", { class: `primary${danger ? " danger" : ""}`, type: "submit", disabled: m.busy || null }, saveLabel)
        ),
      ];
    }

    async _submit(m, fn) {
      if (m.busy) return;
      m.busy = true;
      m.error = null;
      this._renderModal();
      try {
        await fn();
        this._closeModal();
      } catch (err) {
        if (this._modal !== m) return;
        m.busy = false;
        m.error = this._errText(err);
        this._renderModal();
      }
    }

    // Date-based entry (new or edit)
    _openDated(habit, entry) {
      this._openModal({
        type: "dated",
        habit,
        entry,
        values: { date: entry ? entry.count_day : this._selected || this._cfg.today, notes: entry?.notes || "", habit_id: habit.id },
      });
    }

    _dlgDated(m) {
      const v = m.values;
      const others = this._cfg.habits.filter((x) => !x.archived && !x.track_time);
      const form = h(
        "form",
        {
          class: "dlg",
          onsubmit: (e) => {
            e.preventDefault();
            this._submit(m, async () => {
              if (m.entry) {
                const data = { entry_id: m.entry.id, date: v.date, notes: v.notes };
                if (v.habit_id !== m.entry.habit_id) data.habit = v.habit_id;
                await this._call("update_entry", data, true);
                this._showToast("Entry updated.");
              } else {
                await this._call("log", { habit: m.habit.id, date: v.date, notes: v.notes }, true);
                this._showToast(`Logged ${m.habit.name}.`);
              }
            });
          },
        },
        h("h2", { id: "dlg-title" }, m.entry ? `Edit ${m.habit.name}` : `Log ${m.habit.name}`),
        this._field("Date", h("input", { type: "date", required: true, max: this._cfg.today, value: v.date, oninput: (e) => (v.date = e.target.value) })),
        m.entry && others.length > 1
          ? this._field(
              "Habit",
              h(
                "select",
                { onchange: (e) => (v.habit_id = e.target.value) },
                others.map((x) => h("option", { value: x.id, selected: x.id === v.habit_id || null }, this._label(x)))
              ),
              "Move this entry to another habit."
            )
          : null,
        this._field("Notes", h("textarea", { maxlength: "2000", value: v.notes, oninput: (e) => (v.notes = e.target.value) })),
        this._footer(m, m.entry ? "Save" : "Log")
      );
      return form;
    }

    // Time-tracked entry (new or edit)
    _openTimed(habit, entry, day) {
      let start;
      let end;
      if (entry?.start_local) {
        start = entry.start_local.slice(0, 16);
        end = entry.end_local.slice(0, 16);
      } else {
        const d = day || this._selected || this._cfg.today;
        const nowUtc = this._nowUtc();
        if (d === this._cfg.today && nowUtc !== null) {
          start = this._isoFromUtc(nowUtc - 3600000);
          end = this._isoFromUtc(nowUtc);
        } else {
          start = `${d}T09:00`;
          end = `${d}T10:00`;
        }
      }
      this._openModal({
        type: "timed",
        habit,
        entry,
        values: { start, end, notes: entry?.notes || "", mode: "end", hours: 1, minutes: 0 },
      });
    }

    _isoFromUtc(ms) {
      const d = new Date(ms);
      return `${keyOf(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
    }

    _timedSeconds(v) {
      const s0 = naiveParts(v.start);
      if (!s0) return null;
      if (v.mode === "duration") return (Number(v.hours) || 0) * 3600 + (Number(v.minutes) || 0) * 60;
      const e0 = naiveParts(v.end);
      return e0 ? (e0.utc - s0.utc) / 1000 : null;
    }

    _dlgTimed(m) {
      const v = m.values;
      const secs = this._timedSeconds(v);
      const long = secs !== null && secs > LONG_PERIOD_SECONDS;
      const summary = h("div", { class: "past", "aria-live": "polite" }, secs !== null && secs > 0 ? `Length: ${fmtDuration(secs)}` : "");
      const rerenderSummary = () => {
        // Update length and long-period state without rebuilding inputs.
        const s2 = this._timedSeconds(v);
        if (!!(s2 !== null && s2 > LONG_PERIOD_SECONDS) !== long) {
          m.confirmLong = false;
          this._renderModal();
        } else {
          summary.textContent = s2 !== null && s2 > 0 ? `Length: ${fmtDuration(s2)}` : "";
        }
      };
      const modeSeg = h(
        "div",
        { class: "seg", role: "group", "aria-label": "Period entry" },
        [
          ["end", "End time"],
          ["duration", "Duration"],
        ].map(([mode, label]) =>
          h(
            "button",
            {
              type: "button",
              "aria-pressed": String(v.mode === mode),
              onclick: () => {
                if (v.mode === mode) return;
                if (mode === "duration" && secs !== null && secs > 0) {
                  v.hours = Math.floor(secs / 3600);
                  v.minutes = Math.round((secs % 3600) / 60);
                }
                if (mode === "end" && secs !== null && secs > 0) {
                  v.end = this._isoFromUtc(naiveParts(v.start).utc + secs * 1000);
                }
                v.mode = mode;
                this._renderModal();
              },
            },
            label
          )
        )
      );
      const endControl =
        v.mode === "end"
          ? this._field("End", h("input", { type: "datetime-local", required: true, value: v.end, oninput: (e) => ((v.end = e.target.value), rerenderSummary()) }))
          : h(
              "div",
              { class: "two" },
              this._field("Hours", h("input", { type: "number", min: "0", step: "1", value: v.hours, oninput: (e) => ((v.hours = e.target.value), rerenderSummary()) })),
              this._field("Minutes", h("input", { type: "number", min: "0", max: "59", step: "1", value: v.minutes, oninput: (e) => ((v.minutes = e.target.value), rerenderSummary()) }))
            );
      return h(
        "form",
        {
          class: "dlg",
          onsubmit: (e) => {
            e.preventDefault();
            const total = this._timedSeconds(v);
            if (!total || total <= 0) {
              m.error = "The end must be after the start.";
              this._renderModal();
              return;
            }
            if (total > LONG_PERIOD_SECONDS && !m.confirmLong) {
              m.confirmLong = true;
              this._renderModal();
              return;
            }
            const data = { start: `${v.start}:00`, notes: v.notes };
            if (v.mode === "duration") data.duration = { hours: Number(v.hours) || 0, minutes: Number(v.minutes) || 0 };
            else data.end = `${v.end}:00`;
            this._submit(m, async () => {
              if (m.entry) {
                await this._call("update_entry", { entry_id: m.entry.id, ...data }, true);
                this._showToast("Period updated.");
              } else {
                await this._call("log", { habit: m.habit.id, ...data }, true);
                this._showToast(`Logged ${fmtDuration(total)} of ${m.habit.name}.`);
              }
            });
          },
        },
        h("h2", { id: "dlg-title" }, m.entry ? `Edit ${m.habit.name} period` : `Log ${m.habit.name} time`),
        this._field("Start", h("input", { type: "datetime-local", required: true, value: v.start, oninput: (e) => ((v.start = e.target.value), rerenderSummary()) })),
        modeSeg,
        endControl,
        summary,
        long && m.confirmLong
          ? h("div", { class: "warn", role: "alert" }, `This period is ${fmtDuration(secs)} long. Check the dates, then select Save again to confirm.`)
          : null,
        this._field("Notes", h("textarea", { maxlength: "2000", value: v.notes, oninput: (e) => (v.notes = e.target.value) })),
        this._footer(m, long && m.confirmLong ? `Save ${fmtDuration(secs)}` : m.entry ? "Save" : "Log")
      );
    }

    // Journal (new or edit)
    _openJournal(entry, day) {
      const values = {};
      const original = {};
      if (entry) {
        for (const v of entry.values) {
          values[v.field_id] = v.value;
          original[v.field_id] = v.value;
        }
      }
      this._openModal({ type: "journal", entry, values: { date: entry ? entry.day : day || this._cfg.today, fields: values }, original });
    }

    _dlgJournal(m) {
      const v = m.values;
      const active = this._cfg.journal_fields.filter((f) => f.active);
      const activeIds = new Set(active.map((f) => f.id));
      const controls = active.map((f) => this._journalControl(f, v.fields));
      const pastOnly = m.entry ? m.entry.values.filter((x) => !activeIds.has(x.field_id)) : [];
      return h(
        "form",
        {
          class: "dlg",
          onsubmit: (e) => {
            e.preventDefault();
            this._submit(m, async () => {
              const payload = {};
              for (const f of active) {
                const val = v.fields[f.id];
                const empty = val === undefined || val === null || val === "";
                if (m.entry) {
                  const before = m.original[f.id];
                  if (empty && before !== undefined) payload[f.id] = null;
                  else if (!empty && val !== before) payload[f.id] = val;
                } else if (!empty) {
                  payload[f.id] = val;
                }
              }
              if (m.entry) {
                const data = { entry_id: m.entry.id };
                if (v.date !== m.entry.day) data.date = v.date;
                if (Object.keys(payload).length) data.values = payload;
                if (Object.keys(data).length === 1) return;
                await this._call("update_journal_entry", data, true);
                this._showToast("Journal entry updated.");
              } else {
                if (!Object.keys(payload).length) throw new Error("Fill in at least one field.");
                await this._call("journal", { date: v.date, values: payload }, true);
                this._showToast("Journal entry saved.");
              }
            });
          },
        },
        h("h2", { id: "dlg-title" }, m.entry ? "Edit journal entry" : "Journal"),
        this._field("Date", h("input", { type: "date", required: true, max: this._cfg.today, value: v.date, oninput: (e) => (v.date = e.target.value) })),
        controls,
        pastOnly.length
          ? h(
              "div",
              { class: "past" },
              "Recorded with fields that no longer exist: ",
              pastOnly.map((x, i) => `${i ? "; " : ""}${x.label} ${this._fmtJournalValue(x)}`)
            )
          : null,
        this._footer(m, "Save")
      );
    }

    _journalControl(f, values) {
      const cur = values[f.id];
      const set = (val) => (values[f.id] = val);
      switch (f.field_type) {
        case "number": {
          const input = h("input", {
            type: "number",
            inputmode: "decimal",
            min: f.min ?? null,
            max: f.max ?? null,
            step: f.step ?? "any",
            value: cur ?? "",
            oninput: (e) => set(e.target.value === "" ? "" : Number(e.target.value)),
          });
          return this._field(f.name, f.unit ? h("span", { class: "unit" }, input, h("span", { class: "muted" }, f.unit)) : input);
        }
        case "rating": {
          const max = f.max || 5;
          const group = h("div", { class: "rating", role: "group", "aria-label": f.name });
          for (let i = 1; i <= max; i++) {
            group.append(
              h(
                "button",
                {
                  type: "button",
                  "aria-pressed": String(cur === i),
                  onclick: () => {
                    set(cur === i ? "" : i);
                    this._renderModal();
                  },
                },
                String(i)
              )
            );
          }
          return h("div", { class: "field" }, h("span", null, `${f.name} (1–${max})`), group);
        }
        case "select":
          return this._field(
            f.name,
            h(
              "select",
              { onchange: (e) => set(e.target.value) },
              h("option", { value: "" }, "—"),
              f.options.map((o) => h("option", { value: o, selected: o === cur || null }, o)),
              cur && !f.options.includes(cur) ? h("option", { value: cur, selected: true }, `${cur} (old option)`) : null
            )
          );
        case "boolean":
          return h(
            "div",
            { class: "field" },
            h("span", null, f.name),
            h(
              "div",
              { class: "seg", role: "group", "aria-label": f.name },
              [
                ["", "—"],
                [true, "Yes"],
                [false, "No"],
              ].map(([val, label]) =>
                h(
                  "button",
                  {
                    type: "button",
                    "aria-pressed": String(cur === val || (val === "" && (cur === undefined || cur === ""))),
                    onclick: () => {
                      set(val);
                      this._renderModal();
                    },
                  },
                  label
                )
              )
            )
          );
        default:
          return this._field(
            f.name,
            f.multiline || String(cur ?? "").includes("\n")
              ? h("textarea", { maxlength: "2000", value: cur ?? "", oninput: (e) => set(e.target.value) })
              : h("input", { type: "text", maxlength: "2000", value: cur ?? "", oninput: (e) => set(e.target.value) })
          );
      }
    }

    // Back-fill a range and/or list of dates (date-based habits only)
    _backfillHabits() {
      return this._cfg ? this._visibleHabits().filter((x) => !x.track_time) : [];
    }

    _openBackfill() {
      const habits = this._backfillHabits();
      if (!habits.length) return;
      const preferred =
        habits.find((x) => x.id === this._featured) || habits.find((x) => x.kind === "streak") || habits[0];
      const day = this._selected && !this._isFuture(this._selected) ? this._selected : this._cfg.today;
      this._openModal({
        type: "backfill",
        values: { habit_id: preferred.id, start: day, end: day, dates: "" },
      });
    }

    /** Days the form would submit, validated like the server does. */
    _backfillDays(v) {
      const out = new Set();
      let error = null;
      if (v.start || v.end) {
        const start = v.start || v.end;
        const end = v.end || v.start;
        if (end < start) error = "The end date must be on or after the start date.";
        else {
          const n = Math.round((utcOf(end) - utcOf(start)) / 86400000) + 1;
          if (n > 1100) error = "That range is longer than 1100 days.";
          else for (let i = 0; i < n; i++) out.add(addDays(start, i));
        }
      }
      for (const token of String(v.dates || "").match(/\d{4}-\d{2}-\d{2}/g) || []) {
        const [y, m, d] = partsOf(token);
        const valid = m >= 1 && m <= 12 && d >= 1 && d <= daysInMonth(y, m - 1);
        if (!valid) {
          error = `${token} isn't a valid date.`;
          break;
        }
        out.add(token);
      }
      if (!error) {
        const future = [...out].filter((k) => this._isFuture(k)).sort();
        if (future.length) error = `${future[0]} is in the future.`;
      }
      return { days: [...out].sort(), error };
    }

    _dlgBackfill(m) {
      const v = m.values;
      const habits = this._backfillHabits();
      const preview = h("div", { class: "past", "aria-live": "polite" });
      const updatePreview = () => {
        const { days, error } = this._backfillDays(v);
        const habit = this._habit(v.habit_id);
        if (error) preview.textContent = error;
        else if (!days.length) preview.textContent = "Pick a date range or enter dates.";
        else {
          const first = this._fmt.date(days[0], "year");
          const last = this._fmt.date(days[days.length - 1], "year");
          preview.textContent = `${days.length} ${days.length === 1 ? "day" : "days"} for ${habit?.name || "this habit"}${
            days.length > 1 ? `, ${first} to ${last}` : `, ${first}`
          }. Days already logged are skipped.`;
        }
      };
      updatePreview();
      const onDate = (key) => (e) => {
        v[key] = e.target.value;
        updatePreview();
      };
      return h(
        "form",
        {
          class: "dlg",
          // The card validates (with clearer messages than the browser's).
          novalidate: true,
          onsubmit: (e) => {
            e.preventDefault();
            const { days, error } = this._backfillDays(v);
            if (error || !days.length) {
              m.error = error || "Pick a date range or enter dates.";
              this._renderModal();
              return;
            }
            const habit = this._habit(v.habit_id);
            const data = { habit: v.habit_id };
            if (v.start || v.end) {
              data.start_date = v.start || v.end;
              data.end_date = v.end || v.start;
            }
            if (String(v.dates || "").trim()) data.dates = v.dates;
            this._submit(m, async () => {
              const res = await this._call("backfill", data, true);
              const added = res.added ?? 0;
              const skipped = res.skipped ?? 0;
              this._showToast(
                `Added ${added} ${added === 1 ? "day" : "days"} to ${habit.name}.` +
                  (skipped ? ` ${skipped} ${skipped === 1 ? "was" : "were"} already logged.` : "")
              );
            });
          },
        },
        h("h2", { id: "dlg-title" }, "Back-fill days"),
        this._field(
          "Habit",
          h(
            "select",
            {
              onchange: (e) => {
                v.habit_id = e.target.value;
                updatePreview();
              },
            },
            habits.map((x) => h("option", { value: x.id, selected: x.id === v.habit_id || null }, this._label(x)))
          ),
          "Time-tracked habits are logged per period instead."
        ),
        h(
          "div",
          { class: "two" },
          this._field("From", h("input", { type: "date", max: this._cfg.today, value: v.start, oninput: onDate("start") })),
          this._field("To", h("input", { type: "date", max: this._cfg.today, value: v.end, oninput: onDate("end") }))
        ),
        this._field(
          "Individual dates (optional)",
          h("textarea", {
            placeholder: "2026-09-02, 2026-09-05",
            value: v.dates,
            oninput: (e) => {
              v.dates = e.target.value;
              updatePreview();
            },
          }),
          "YYYY-MM-DD, separated by commas, spaces or new lines. Clear From and To to add only these."
        ),
        preview,
        this._footer(m, "Back-fill")
      );
    }

    // --------------------------------------------------------------- resets
    _renderReset(habit, since) {
      const all = since.all_time || { count: since.count, seconds: since.seconds, active_days: since.active_days };
      const resetAt = since.reset_at ? this._localStamp(since.reset_at) : null;
      const rows = [];
      if (habit.track_time) rows.push(["Time", fmtHoursTotal(all.seconds), fmtHoursTotal(since.seconds)]);
      rows.push(["Entries", String(all.count), String(since.count)]);
      rows.push(["Active days", String(all.active_days), String(since.active_days)]);
      return h(
        "section",
        { class: "reset", "aria-label": "All time and since last reset" },
        h(
          "div",
          { class: "row" },
          h(
            "div",
            { class: "rtitle" },
            h("div", { style: { fontWeight: "500" } }, "All time vs since reset"),
            h("div", { class: "muted small" }, resetAt ? `Last reset ${resetAt}` : "Never reset, so both columns match")
          ),
          h(
            "div",
            { class: "rbtns" },
            h("button", { class: "textbtn", onclick: () => this._openReset(habit) }, "Reset…"),
            since.reset_at
              ? h(
                  "button",
                  {
                    class: "textbtn",
                    onclick: () =>
                      this._openConfirm({
                        title: "Undo last reset?",
                        text: `Remove the reset from ${resetAt}? "Since reset" totals will count from the previous reset${
                          since.resets > 1 ? "" : " (or all time, as there is none)"
                        }.`,
                        action: "Undo reset",
                        run: async () => {
                          await this._call("undo_reset", { habit: habit.id }, true);
                          this._showToast(`Undid the last ${habit.name} reset.`);
                        },
                      }),
                  },
                  "Undo last reset"
                )
              : null
          )
        ),
        h(
          "table",
          { class: "cmp" },
          h("thead", null, h("tr", null, h("th", null, ""), h("th", { scope: "col" }, "All time"), h("th", { scope: "col" }, "Since reset"))),
          h(
            "tbody",
            null,
            rows.map(([label, a, b]) => h("tr", null, h("th", { scope: "row" }, label), h("td", null, a), h("td", null, b)))
          )
        ),
        h("div", { class: "muted small" }, "Resetting never deletes entries. The tiles above follow the selected period.")
      );
    }

    /** Format a UTC ISO stamp in Home Assistant's time zone (not the browser's). */
    _localStamp(iso) {
      try {
        return new Intl.DateTimeFormat(safeLocale(this._fmt.lang), {
          timeZone: this._cfg.time_zone,
          month: "short",
          day: "numeric",
          year: "numeric",
          hour: "numeric",
          minute: "2-digit",
        }).format(new Date(iso));
      } catch (_e) {
        return iso.slice(0, 16).replace("T", " ");
      }
    }

    /** Current HA-local wall time as a naive UTC-ms number, advanced since the last config fetch. */
    _nowUtc() {
      const base = naiveParts(this._cfg?.now_local);
      if (!base) return null;
      return base.utc + Math.max(0, Date.now() - (this._cfgAt || Date.now()));
    }

    _openReset(habit) {
      const nowUtc = this._nowUtc();
      const at = nowUtc === null ? "" : this._isoFromUtc(nowUtc);
      // An untouched default means "now": the server then uses its exact clock,
      // so entries logged moments earlier in the same minute stay before the reset.
      this._openModal({ type: "reset", habit, defaultAt: at, values: { at } });
    }

    _dlgReset(m) {
      const v = m.values;
      return h(
        "form",
        {
          class: "dlg",
          novalidate: true,
          onsubmit: (e) => {
            e.preventDefault();
            const at = naiveParts(v.at);
            const nowUtc = this._nowUtc();
            if (!at) {
              m.error = "Pick when the reset happens.";
              this._renderModal();
              return;
            }
            if (nowUtc !== null && at.utc > nowUtc + 5 * 60000) {
              m.error = "The reset can't be in the future.";
              this._renderModal();
              return;
            }
            this._submit(m, async () => {
              const data = { habit: m.habit.id };
              if (v.at !== m.defaultAt) data.at = `${v.at}:00`;
              await this._call("reset_counter", data, true);
              this._showToast(`Reset ${m.habit.name}. Past entries and all-time totals are kept.`);
            });
          },
        },
        h("h2", { id: "dlg-title" }, `Reset ${m.habit.name}?`),
        h("div", null, `"Since reset" totals for ${m.habit.name} will start from the time below. Entries and all-time totals are kept, and you can undo this.`),
        this._field("Reset from", h("input", { type: "datetime-local", value: v.at, oninput: (e) => (v.at = e.target.value) }), "Defaults to now. Pick an earlier time to backdate it."),
        this._footer(m, "Reset")
      );
    }

    _openConfirm({ title, text, action, run, danger = false }) {
      this._openModal({ type: "confirm", title, text, action, run, danger });
    }

    _dlgConfirm(m) {
      return h(
        "form",
        {
          class: "dlg",
          onsubmit: (e) => {
            e.preventDefault();
            this._submit(m, m.run);
          },
        },
        h("h2", { id: "dlg-title" }, m.title),
        h("div", null, m.text),
        this._footer(m, m.action, m.danger)
      );
    }

    // Delete confirmation
    _openDelete(entry, habit) {
      this._openModal({ type: "delete", entry, habit });
    }

    _dlgDelete(m) {
      const what = m.habit ? `this ${m.habit.name} entry` : "this journal entry";
      const day = m.entry.count_day || m.entry.day;
      return h(
        "form",
        {
          class: "dlg",
          onsubmit: (e) => {
            e.preventDefault();
            this._submit(m, async () => {
              await this._call("delete_entry", { entry_id: m.entry.id });
              if (this._stats.log) this._stats.log = this._stats.log.filter((x) => x.id !== m.entry.id);
              this._showToast("Entry deleted.");
            });
          },
        },
        h("h2", { id: "dlg-title" }, "Delete entry?"),
        h("div", null, `Delete ${what} from ${this._fmt.date(day)}? This can't be undone.`),
        this._footer(m, "Delete", true)
      );
    }
  }

  // Registration timing (HA 2026.10+): the frontend installs a scoped custom
  // element registry polyfill when its app starts and swaps window.customElements.
  // This file is loaded early (integration-registered module), so defining
  // immediately can land in the old registry, which the dashboard no longer
  // reads ("Custom element doesn't exist"). Define once the app element exists,
  // always against the *current* global registry, and only once per registry.
  const register = () => {
    const registry = window.customElements;
    if (registry.get(TAG)) return;
    // A registry rejects a constructor it has already seen; a subclass is a
    // distinct constructor if we ever need to register into a second registry.
    const ctor = HabitLogCard.__registered ? class extends HabitLogCard {} : HabitLogCard;
    registry.define(TAG, ctor);
    HabitLogCard.__registered = true;
    window.customCards = window.customCards || [];
    if (!window.customCards.some((c) => c.type === TAG)) {
      window.customCards.push({
        type: TAG,
        name: "Habit Log",
        description: "Calendar, quick logging, journal and statistics for the Habit Log integration.",
        preview: false,
      });
    }
    // eslint-disable-next-line no-console
    console.info(`%c HABIT-LOG-CARD %c v${VERSION} `, "background:#2a78d6;color:#fff", "");
  };

  const safeRegister = () => {
    try {
      register();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("habit-log-card: registration failed", err);
    }
  };

  if (window.customElements.get("home-assistant")) {
    safeRegister();
  } else {
    window.customElements.whenDefined("home-assistant").then(safeRegister);
    // Outside the HA app (no <home-assistant>), register anyway after a while.
    setTimeout(safeRegister, 15000);
  }
})();

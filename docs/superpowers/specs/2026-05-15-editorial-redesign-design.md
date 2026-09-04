# Editorial Redesign — 妇科前沿研究助手

**Date:** 2026-05-15
**Scope:** `dashboard.html`, `login.html`
**Goal:** Replace the current Apple-inspired minimalism with an editorial / academic-journal aesthetic that communicates the site's identity (TCM gynecology research dashboard) instead of feeling like a generic SaaS product.

## Design Direction

**A2 — Modern Editorial.** Serif typography for titles/journal names/section headings (Times New Roman + 宋体); sans-serif (PingFang SC / SF Pro) for body text and UI controls. Visual model is NYT / Bloomberg / The Atlantic online — editorial gravitas without sacrificing screen readability for long-form bilingual content.

## Design Tokens

### Light mode — "Paper / 纸感"

| Token | Value | Use |
|---|---|---|
| `--bg` | `#faf8f3` | Page background — cream paper |
| `--bg-soft` | `#ede7d3` | Section bands, tagline strips |
| `--panel` | `#ffffff` | Paper cards |
| `--panel-2` | `#f0ead3` | TCM extension band, soft fills |
| `--border` | `#d4cdb8` | Hairlines |
| `--border-strong` | `#c4ba9c` | Dotted abstract dividers |
| `--text` | `#1a1a1a` | Headlines, body |
| `--text-soft` | `#2c2620` | Abstract body |
| `--text-muted` | `#6b5e44` | Authors, dates, meta |
| `--text-dim` | `#a8a08a` | Counts, faint meta |
| `--accent` | `#8b2c2c` | Burgundy — primary accent (NEJM-like) |
| `--accent-soft` | `rgba(139, 44, 44, 0.10)` | Accent backgrounds |
| `--tcm` | `#5a7547` | Moss-green — TCM band marker |
| `--tcm-bg` | `#f0ead3` | TCM band background |
| `--tcm-text` | `#3d4a30` | TCM band text |
| `--good` | `#5a7547` | Q1 badges, success |
| `--alert` | `#a8341a` | Warnings |
| `--star` | `#c9a55c` | News importance stars |

### Dark mode — "Reading lamp / 暖夜灯"

| Token | Value | Use |
|---|---|---|
| `--bg` | `#1c1814` | Warm brown-black |
| `--bg-soft` | `#14110d` | Deeper bands |
| `--panel` | `#25201a` | Cards |
| `--panel-2` | `rgba(122, 156, 95, 0.10)` | TCM band background |
| `--border` | `rgba(237, 231, 211, 0.10)` | Hairlines |
| `--border-strong` | `rgba(237, 231, 211, 0.16)` | Stronger dividers |
| `--text` | `#f0ead3` | Headlines, body |
| `--text-soft` | `#d4ccb6` | Abstract body |
| `--text-muted` | `#a8a08a` | Authors, dates, meta |
| `--text-dim` | `#6b6354` | Counts, faint meta |
| `--accent` | `#d49a4f` | Amber-gold — replaces burgundy |
| `--accent-soft` | `rgba(212, 154, 79, 0.14)` | Accent backgrounds |
| `--tcm` | `#7a9c5f` | Sage marker |
| `--tcm-text` | `#b8c9a3` | TCM band text |
| `--good` | `#b8c9a3` | Q1 badges |
| `--alert` | `#e0654a` | Warnings |
| `--star` | `#d49a4f` | Stars |

Tokens are CSS custom properties on `:root` / `:root[data-theme="dark"]`. Existing theme-toggle JS keeps working.

## Typography

| Element | Light/Dark | Font stack |
|---|---|---|
| Masthead title, paper title, news title, section heading, tab labels, journal name, dates | both | `"Times New Roman", "Songti SC", "STSong", serif` |
| Body / abstract / why-text / authors | both | `-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif` |
| Counts, IF numbers, Q1 badges, type tags, UI buttons | both | sans-serif (same as body) |
| English paper title, English news title, masthead subtitle | both | `"Times New Roman", serif` + `font-style: italic` |

**Letter-spacing rules:**
- Section headings / eyebrow labels — `letter-spacing: 0.22em; text-transform: uppercase;`
- Masthead eyebrow — `letter-spacing: 0.32em; text-transform: uppercase;`
- Serif Chinese titles — `letter-spacing: 0.01em` (Songti benefits from a tiny breath)
- Sans Chinese body — `letter-spacing: -0.005em` (current PingFang setting, keep)

## Component Specs

### Masthead (replaces current sticky header top portion)

Layout: centered, full-width band, `padding: 22px 24px 18px;`, separated from the navbar by a **double burgundy line** (`border-bottom: 3px double var(--accent)`).

Contents, top to bottom:
1. **Eyebrow** — `妇 科 前 沿 研 究 助 手` (spaced characters), `0.32em` tracking, color `--accent`, font-size `10px`.
2. **Title** — `每日学术看板`, serif, 26px, weight 700, color `--text`.
3. **Subtitle** — `PubMed 顶刊 · 学会与媒体动态 · 中医视角`, serif italic, 12px, color `--text-muted`.
4. **Date / Vol line** — `Friday, May 15, 2026 · Vol.1 · Issue NN`, serif, 11px, color `--text-muted`. Issue number derived from days since 2026-05-13 (project start) + 1.

The masthead is **NOT sticky** — it scrolls away. Only the navbar below sticks.

### Navbar (sticky, below masthead)

`position: sticky; top: 0`. Background `rgba(250,248,243,0.92)` (light) / `rgba(28,24,20,0.92)` (dark), with `backdrop-filter: blur(10px)`. Border-bottom: 1px hairline.

Contents (single row, no wrap on desktop):
- **Tabs** — serif (Times + Songti), 13px, `padding: 4px 0`, `border-bottom: 2px solid transparent`. Active state: color `--accent`, `border-bottom-color: --accent`, weight 700. Counts in sans `10px var(--text-dim)`.
- **Search input** — italic serif placeholder, hairline border, transparent background, `border-radius: 4px` (sharper than current 8px to feel editorial).
- **Refresh button + status** — sans-serif, hairline border, transparent. Keep existing spinner.
- **Gist status** — sans-serif, 11px, `--text-dim`.
- **Theme toggle** — sans-serif, hairline border. Label stays `亮` / `暗`.
- **Settings button** — sans-serif, hairline border, same style.

Tabs go on the **left**; controls (search/refresh/gist/theme/settings) go to the **right** via `margin-left: auto`.

### Alert banner (rotating)

Currently a top band above the header. New treatment:
- Background: `--bg-soft` (light) / `--bg-soft` (dark)
- Border-bottom: 1px hairline
- Eyebrow label `紧急 · NOTICE` in `--alert`, serif italic, `0.16em` tracking, uppercase
- Body text in sans-serif, 13px, `--text`
- Close button: hairline border, sans-serif, `border-radius: 4px`
- Rotation behavior unchanged (no fade transition per earlier feedback)

### Section heading (今日论文 / 今日要闻 / 学术动态)

```
§ Featured Papers · 今日论文
```
- Serif, `letter-spacing: 0.22em; text-transform: uppercase;` — so the leading character of each Latin word goes UPPERCASE; the Chinese half remains as-is.
- Color `--accent`, font-size 11px.
- Leading `§` glyph in serif italic 16px (no tracking).
- Border-bottom: 1px hairline, padding `22px 0 8px`.
- Replaces current `h3` (which was uppercase sans with bottom border).

### Paper card

- Background `--panel`, border `1px solid --border`, **left border `3px solid --accent`** (replaces grade-A/B/C colored bars — collapses into a single accent stripe), no border-radius (or 2px max — editorial cards are crisp).
- Padding `16px 20px`.
- No box-shadow in light mode (paper-on-paper); subtle `0 1px 0 rgba(0,0,0,0.04)` on dark for separation.
- Hover: `border-color: --border-strong`, no transform, no shadow change (more restrained than current).

**Card contents order:**
1. **Meta row** — journal name (serif italic, weight 700, `--accent`), IF (serif, `--text-muted`), Q1 badge (sans, 9px, sage-green flat pill), grade badge (sans, 9px, uppercase, `--accent` color on `--accent-soft`, e.g. `A` / `B` / `C`), date (serif, `--text-dim`, right-aligned via `margin-left: auto`).
2. **Chinese title** — serif, 17px, weight 700, `--text`, line-height 1.35.
3. **English title** — serif italic, 12px, `--text-muted`.
4. **Authors** — sans, 11px, `--text-muted`.
5. **Abstract block** — `.abstract-row` (中) and `.abstract-row.en`. Drop the colored mini-labels (`中` / `EN`); separate the two rows with a 1px **dotted** border (`border-top: 1px dotted --border-strong`) on the English row. Sans body, 12-13px, line-height 1.62-1.65.
6. **TCM extension band (📜 研究延伸思考)** — background `--tcm-bg`, left border `3px solid --tcm`, padding `10px 12px`. Label "研究延伸思考" in **宋体 + weight 700**, color `--tcm` (dark green / sage in dark mode). Body in sans.
7. **Expand/collapse toggle** — sans-serif text button, hairline border, `border-radius: 4px`.
8. **Links footer** — sans-serif, hairline top border.
9. **Favorite button** — top-right, sans-serif, hairline border, `border-radius: 4px`. Saved state uses `--accent` color + `--accent-soft` bg.

The current grade-A/B/C left bars (red/orange/blue) **collapse into a single accent bar**. Grade is communicated via a small sans-serif badge in the meta row (`A` / `B` / `C` in `--accent`) — grade still derivable but no longer competing with the journal accent.

### News item

**Switches from cards-grid to a single-column "front page" list.**

- No card background, no card border.
- Each item separated by `1px solid --border` (bottom border on each item except last).
- Padding `12px 0`.

**Item contents:**
1. **Top row** (baseline-aligned, gap 10px):
   - Importance: `★★★★★` (filled) `★★★★☆` (4 filled + 1 empty), color `--star`, monospaced via `letter-spacing: 0.1em`.
   - Source tag: serif italic, 10px, `--text-muted`, uppercase, `0.12em` tracking.
   - Type badge (guideline/major-trial/safety/etc.): sans bold, 9px, uppercase, colored fill — keep existing color logic, restyle to flat pills (2px radius) instead of current 5px radius.
2. **Chinese title** — serif, 14px, weight 700, `--text`, line-height 1.4.
3. **English title** — serif italic, 11px, `--text-muted`.
4. **Why-text** — sans, 11px, `--text-soft`, left-border `2px solid --border-strong`, padding-left 10px.
5. **+N 家 (also-reported)** — sans, 11px chip on `--accent-soft` / `--accent`. Click expands the also-list under the why-text.

The news strip view (within the dashboard.html News tab) becomes this list. The "今日要闻" preview area on the Papers tab also adopts this list style (no longer cards).

### Favorites view

- Tag sidebar: same panel + hairline treatment, no rounded corners (or 2px max). Sidebar headings in serif uppercase + `0.22em` tracking.
- Tag chips on paper cards: pill shape kept (12px radius — chips are exempt from the "sharp" rule because they're tag-like), background `--accent-soft`, color `--accent`. Sans-serif. Add button = dashed hairline border.
- Folder rename / drag-drop / new-folder UI: keep current interaction; restyle to new tokens.
- Tag picker dropdown: panel + hairline border, no border-radius. Animation kept.

### Settings modal

- Panel: `--panel` background, hairline border, no border-radius (or 4px max for the modal container).
- Title in serif, weight 700.
- Form labels in sans, 11px, uppercase, `0.06em` tracking, `--text-muted`.
- Inputs / selects: hairline border, transparent background, focused state uses `--accent` border. No background fill.
- Buttons: hairline border for secondary; `--accent` filled for primary action.

### Buttons (global)

Replace current `8px` radius and `--panel-2` fills with:
- **Default** — transparent, 1px hairline `--border`, sans 12-13px, `border-radius: 4px`. Hover: `border-color: --border-strong`, background `rgba(0,0,0,0.02)` (light) / `rgba(255,255,255,0.04)` (dark).
- **Primary** — background `--accent`, color white, `border-radius: 4px`. Hover: `filter: brightness(1.08)`.
- **Active press** — `transform: scale(0.97)`.

### Login page

Direct application of the same tokens.
- Centered card on `--bg`.
- Card: `--panel` bg, hairline border, **no shadow** (or extremely subtle), `border-radius: 4px`.
- Brand line in serif: `妇科前沿研究助手` (small eyebrow) above `每日学术看板` (larger serif).
- Subtitle in serif italic.
- Password input: hairline border, sans 15px.
- Login button: primary style.
- Theme toggle in top-right.

## Implementation Approach

Single in-place rewrite of the `<style>` blocks in `dashboard.html` and `login.html`. No new files, no preprocessor. CSS custom properties make light/dark switching automatic via existing `data-theme` attribute.

**Non-CSS changes required:**
1. **HTML restructure in `dashboard.html`** — split the current single `<header>` into:
   - `<header class="masthead">` (new, top, non-sticky)
   - `<nav class="navbar">` (sticky, tabs + controls)
2. **Inject "Vol/Issue/Date" line into masthead** — small JS to compute issue number from `(today - 2026-05-13) + 1` and format date.
3. **News tab markup** — change `.news-list` from grid-of-cards to flat list of items; update `renderNews()` JS accordingly.
4. **Paper card grade bar** — remove `.card.grade-A::before` etc. (the colored side bar). The single `--accent` left border replaces it.

No changes to: `fetch_papers.py`, `fetch_news.py`, `server.py`, data shapes, JSON files, API endpoints.

## Self-test before declaring done

1. Run `./run.sh`, dashboard opens at `http://localhost:8765/dashboard.html`.
2. Verify light mode: masthead, navbar sticky, paper cards, news list, tabs all match the mockup.
3. Toggle to dark mode: confirm warm-brown background, amber accent, no pure black anywhere.
4. Click into a paper card → expand sections → confirm TCM band styling.
5. Open Favorites tab → confirm sidebar + tag chips + folder picker look right in both modes.
6. Open Settings modal → confirm inputs / buttons match.
7. Log out → login page also in editorial style.
8. Trigger an alert banner → confirm correct treatment.
9. Test responsive at narrower widths (navbar wrap, paper card padding).

## Out of scope

- Animation system changes beyond minor restraint (no new animations, no removal of existing ones unless they conflict).
- Adding a print stylesheet.
- Changing data display logic (sort orders, filter behavior, IF thresholds, etc.).
- New components or features.

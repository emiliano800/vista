# Vista design system — "Field Notes"

The visual voice for Vista's web surfaces (marketing pages and the company
workspace in `src/web/public`). Derived from `designs/02-field-notes.html`.

## Idea

Vista's claim is that process maps should be drawn from evidence, not memory.
The design behaves like a well-kept field notebook: warm paper, one serif
voice for what we say, one sans voice for what we measured, a hand-ruled
ledger for data, and a single rust accent used sparingly for the thing that
matters on the page. Nothing glows, nothing floats, nothing is a gradient.

Tone words: honest, unhurried, editorial, ruled, warm.

## Colour

Tokens live on `:root`. Do not introduce new colours; tint with these.

| Token       | Hex       | Role                                                      |
| ----------- | --------- | --------------------------------------------------------- |
| `--cream`   | `#f4efe6` | Page background                                           |
| `--cream-2` | `#ebe4d6` | Hover fill for ghost buttons, secondary panels            |
| `--paper`   | `#fbf8f2` | Cards, tables, ledger artefacts (anything "written on")   |
| `--ink`     | `#1f1c18` | Primary text, primary button fill, dark closing sections  |
| `--ink-2`   | `#57514a` | Body copy in supporting paragraphs, nav links             |
| `--ink-3`   | `#8b8377` | Captions, axis labels, metadata, footer                   |
| `--rust`    | `#b3471f` | The one accent: italic emphasis, focus ring, "Q.", rework |
| `--moss`    | `#4d6b4a` | Positive/approved state, "A.", success                    |
| `--line`    | `#d9d1c2` | Every rule, border and divider                            |
| selection   | `#f0d5c3` | `::selection` background                                  |

Dark sections invert: background `--ink`, text `--cream`, muted `#c9bfae`,
buttons filled `--cream` with `--ink` text.

Status colours are reserved for meaning, not decoration: rust = attention /
rework / re-keying, moss = approval / success. Warnings use a paper tint with
a rust left border rather than a yellow box.

## Typography

Two families, loaded from Google Fonts with `display=swap`:

- **Newsreader** (variable, optical sizing) — headlines, ledes, long-form
  paragraphs, quotes, the logo. Weight 400 for display, 300 italic for the
  accent word in a headline, 500 for small serif headings.
- **Inter** — UI: navigation, buttons, labels, tables, metadata, form fields.
  Weights 400/500/600 only.

Fallbacks: `Georgia, serif` and `system-ui, sans-serif`.

Scale (desktop → mobile via `clamp`):

| Role         | Family     | Size / line                                                       | Notes                              |
| ------------ | ---------- | ----------------------------------------------------------------- | ---------------------------------- |
| Display h1   | Newsreader | `clamp(46px, 6.4vw, 92px) / .98`                                  | `letter-spacing: -.025em`, balance |
| Section h2   | Newsreader | `clamp(32px, 3.6vw, 48px) / 1.05`                                 | `letter-spacing: -.02em`           |
| Workspace h1 | Newsreader | `clamp(34px, 4vw, 48px) / 1.05`                                   | inside the app                     |
| Card h2 / h3 | Newsreader | `22px / 1.2` weight 500                                           |                                    |
| Lede         | Newsreader | `21px / 1.5`, colour `--ink-2`, max 46ch                          |                                    |
| Essay body   | Newsreader | `19px / 1.6`, colour `--ink-2`, max 62ch                          | drop cap on first paragraph        |
| UI body      | Inter      | `17px / 1.6` (page default)                                       |                                    |
| Small UI     | Inter      | `15px`, `13.5px`, `12.5px`                                        | nav, captions, tags                |
| Eyebrow      | Inter      | `12px`, weight 600, `letter-spacing: .12em`, uppercase, `--ink-3` |                                    |
| Table        | Inter      | `14px`; headers `11px` uppercase `.06em`                          | tabular numerals on numbers        |

Rules: one italic rust word per headline at most. Never use gradient text.
Use `text-wrap: balance` on headings. Numbers use
`font-variant-numeric: tabular-nums`.

## Layout & spacing

- Content column `max-width: 1120px`, side padding `28px` (18px under 700px).
- Section rhythm `96px` vertical on desktop, `64px` under 900px.
- Grids are asymmetric editorial splits: `4fr 8fr` (sticky heading + prose),
  `6fr 6fr` (hero), `1fr 1fr` (voice). All collapse to one column at 900px.
- Sticky elements (section headings, nav) stick at `top: 100px` / `top: 0`.
- Always `min-width: 0` on grid children so ledgers don't overflow.

## Surfaces

- **Paper cards**: `background: var(--paper); border: 1px solid var(--line);
border-radius: 4px`. Radius is deliberately small — this is paper, not
  glass. Shadow only on the hero artefact:
  `0 30px 50px -40px rgba(31,28,24,.5)`.
- **Ruled grids** (the six-step method): items separated by `1px` `--line`
  borders, no gaps, no card backgrounds. Grid draws the lines.
- **Ledger rows** (timelines, tables, report lists): dotted or solid `--line`
  rules under every row; label column fixed width (`96px`+), value lanes
  flex.
- **Dark closing band**: `--ink` background, centred serif h2, cream button.
- **Q/A panel**: paper card; question prefixed by a rust "Q.", answer by a
  moss "A." in serif; provenance shown as a pill tag.

## Components

**Buttons** — pill, `height: 44px`, `padding: 0 20px`, Inter 500 15px.
Primary: `--ink` fill, `--cream` text, hover `#3a342c`. Ghost: transparent
with `inset 0 0 0 1px var(--ink)`, hover `--cream-2`. Small variant
`height: 36px; padding: 0 14px; font-size: 14px` for tables and pagers.
Disabled: `opacity: .45`.

**Inputs / selects** — `--paper` background, `1px solid var(--line)` border,
radius `4px`, padding `12px 14px`, Inter 15px. Focus: rust outline (below).

**Labels** — Inter 13px weight 600, `--ink-2`, 6px above the field.

**Tags / provenance pills** — `padding: 3px 9px`, `border: 1px solid
var(--line)`, radius `999px`, 12.5px, `--ink-2`. Provenance values
(`observed`, `rule`, `filled`, `episode`, `human`) are rendered as tags;
`human` gets a moss border, `rule`/`filled` stay neutral.

**Metrics** — a ruled row, not four cards: label in eyebrow style, number in
Newsreader 400 `40px` with tabular numerals, separated by `--line` rules.

**Tables** — `--paper` background, 14px Inter, header row uppercase 11px
`--ink-3`, rows ruled with `--line`, `padding: 14px 12px`, numbers right-
aligned and `white-space: nowrap`. Wrap in an `overflow-x: auto` container
with a `min-width` so mobile scrolls instead of collapsing.

**Messages / status** — paper tint `--cream-2` with `3px solid var(--rust)`
left border, radius `4px`, Inter 15px.

**Empty states** — dashed `--line` border, centred Newsreader 19px `--ink-3`.

**Navigation** — `72px` tall, logo in Newsreader 500 26px, links Inter 15px
`--ink-2` with a rust underline on hover (`text-underline-offset: 4px`),
right-aligned ghost "Sign in"/"Sign out" pill. Links hide under 900px.

**Footer** — 14px `--ink-3`, two-sided flex, a `--line` rule above.

## Iconography & illustration

Line icons only: `40px`, `stroke: var(--ink)`, `stroke-width: 1.4`, round
caps and joins, no fill. Illustration is data drawn as ledgers — timeline
lanes, ruled tables, directly-follows edges — never stock imagery or 3D.

## Motion

Almost none. Buttons transition `background .2s`. Hover underlines appear
instantly. Anything animated must respect
`@media (prefers-reduced-motion: reduce)` and fall back to a static frame.

## Accessibility

- `:focus-visible { outline: 2px solid var(--rust); outline-offset: 3px; }`
  on every interactive element.
- Body text on cream is `--ink` (contrast ≈ 15:1); supporting copy `--ink-2`
  (≈ 7:1); `--ink-3` only for ≥ 12px captions.
- Rust on cream passes AA for text at 15px+; do not use rust below that for
  text.
- Every ledger figure carries an `aria-label` describing what it shows.

## Voice

Copy is plain and specific. Prefer a number to an adjective ("Acrobat →
QuickBooks, 42 times, 3.1 hours a week"). Say what stays on the device.
Separate observed facts from inference from testimony, and label them.
Headlines are sentences, sentence-cased, with one italic word.

# Vista Design System — "Field Notes"

The visual system for every Vista surface: the marketing site, the company
workspace (`src/web/public`), the employee dashboard and the always-on
overlay (`src/recorder/ui`). Tokens live in `src/web/public/tokens.css`;
every surface links that file and uses `var(--token)`. No raw hex outside it.

Reference mockup: `designs/02-field-notes.html`.

---

## 1. The idea in three lines

Vista's claim is that process maps should be drawn from evidence, not memory.
The design behaves like a well-kept field notebook: warm paper, one serif
voice for what we say, one sans voice for what we measured, ruled ledgers for
data, and a single rust accent for the one thing on the page that matters.
Nothing glows, nothing floats, nothing is a gradient.

Tone words: honest, unhurried, editorial, ruled, warm.

---

## 2. Colour tokens

### Palette

| Token       | Hex       | Role                                                              |
| ----------- | --------- | ----------------------------------------------------------------- |
| `--cream`   | `#f4efe6` | Page background                                                   |
| `--cream-2` | `#ebe4d6` | Hover fills, sunken panels, status tint                           |
| `--paper`   | `#fbf8f2` | Cards, tables, anything "written on"                              |
| `--ink`     | `#1f1c18` | Text, primary button fill, dark bands                             |
| `--ink-2`   | `#57514a` | Supporting copy, nav links                                        |
| `--ink-3`   | `#8b8377` | Captions, axis labels, metadata                                   |
| `--rust`    | `#b3471f` | The accent: emphasis word, focus ring, attention / rework, people |
| `--moss`    | `#4d6b4a` | Approval, success, agent / rule work                              |

### Semantic grounds and text

| Token                                             | Value                     | Use                                             |
| ------------------------------------------------- | ------------------------- | ----------------------------------------------- |
| `--surface`                                       | cream                     | App background                                  |
| `--surface-raised`                                | paper                     | Cards, inputs, sidebar active item              |
| `--surface-sunken` / `--surface-hover`            | cream-2                   | Bars, quiet buttons, hover                      |
| `--surface-selected`                              | `#e3d9c6`                 | Selected row / chip                             |
| `--line`                                          | `#d9d1c2`                 | Every rule, border and divider                  |
| `--line-strong`                                   | `#b9ae9b`                 | Input borders, idle dots, dashed placeholders   |
| `--ink-secondary` / `--ink-muted` / `--ink-faint` | ink-2 / ink-3 / `#a69d8f` | Descending emphasis; faint is placeholders only |
| `--ink-inverse`                                   | cream                     | Text on `--ink`                                 |

### Brand and accent

| Token                      | Value       | Use                                               |
| -------------------------- | ----------- | ------------------------------------------------- |
| `--brand` / `--on-brand`   | ink / cream | Primary button, orb, filled marks                 |
| `--brand-hover`            | `#3a342c`   | Primary hover                                     |
| `--brand-ink` / `--accent` | rust        | Accent text, active nav marker                    |
| `--accent-soft`            | `#f0d5c3`   | `::selection`, rust tint                          |
| `--link`                   | ink         | Links (underline on hover, rust underline colour) |

### Provenance

Who did the work is the product's central distinction, so it gets its own pair.

| Token                                    | Value                   | Use                                              |
| ---------------------------------------- | ----------------------- | ------------------------------------------------ |
| `--agent`, `--agent-ink`, `--agent-soft` | moss, moss, `#dfe6da`   | Agent-run, rule-derived, inferred labels         |
| `--human`, `--human-ink`, `--human-soft` | rust, rust, accent-soft | The employee's own word: notes, confirmed labels |

`--lime-*` and `--periwinkle-*` remain as aliases of agent / human for older
screens; do not use them in new code.

### Status

| Token                                        | Value                      |
| -------------------------------------------- | -------------------------- |
| `--success` / `--success-soft`               | moss / agent-soft          |
| `--warning` / `--warning-soft`               | `#8a5a12` / `#f3e4c4`      |
| `--danger` / `--danger-soft` / `--on-danger` | rust / accent-soft / cream |

Categorical series (`--chart-1…8`) for timelines and app bars: ink, rust,
moss, ochre `#c9a227`, slate `#6b7fa3`, plum `#8b6f8e`, tan `#a3826b`,
line-strong.

### Contrast rules

- Body text on cream is `--ink` (≈ 15:1); supporting copy `--ink-2` (≈ 7:1);
  `--ink-3` only for ≥ 12px captions.
- Rust and moss on cream pass AA for text at 12.5px+ when used as text or
  border, never as a fill behind text. Meaning is carried by border + text
  colour + the word itself, never a fill alone.
- Dark bands invert: `--ink` ground, `--cream` text, muted `#c9bfae`, buttons
  filled cream with ink text.

---

## 3. Type

Fonts are self-hosted WOFF2 in `src/web/public/fonts/` and declared in
`tokens.css` (the Worker CSP is `'self'` only; the Electron UI shares the file).

| Family var                   | Stack               | Use                                                                                                                   |
| ---------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `--font-display` (`--serif`) | Newsreader, Georgia | Headlines, ledes, KPI figures, quotes, the logo. 400 display, 300 italic for the accent word, 500 for small headings. |
| `--font-sans` (`--sans`)     | Inter, system-ui    | Everything UI: nav, buttons, labels, tables, forms. 400 / 500 / 600 only.                                             |
| `--font-mono`                | ui-monospace stack  | IDs, captured actions, timestamps.                                                                                    |

| Class            | Size / line                   | Family / weight               | Use                                                          |
| ---------------- | ----------------------------- | ----------------------------- | ------------------------------------------------------------ |
| `.display-xl`    | `clamp(46, 6.4vw, 92)` / 0.98 | Newsreader 400, -0.025em      | Marketing hero. One per page.                                |
| `.display`       | `clamp(32, 3.6vw, 48)` / 1.05 | Newsreader 400, -0.02em       | Marketing section headlines.                                 |
| `.display-serif` | inherit                       | Newsreader 300 italic, rust   | The one emphasised word in a headline.                       |
| `.h1`            | 28 / 34                       | Newsreader 400                | Product page title (dashboard uses 34 / 40).                 |
| `.h2`            | 22 / 28                       | Newsreader 500                | Card titles.                                                 |
| `.h3`            | 15 / 22                       | Inter 600                     | Group titles.                                                |
| `.body-lg`       | 17 / 27                       | Inter 400                     | Marketing paragraphs; page default in the workspace.         |
| `.body`          | 14 / 20                       | Inter 400                     | Forms, dialogs, dashboard default.                           |
| `.body-dense`    | 13 / 18                       | Inter 400                     | Table cells, list rows, sidebar.                             |
| `.small`         | 12.5 / 16                     | Inter 400                     | Metadata, helper text.                                       |
| `.label`         | 11 / 16                       | Inter 600, +0.06em, uppercase | Column headers, eyebrows (marketing eyebrow: 12px, +0.12em). |
| `.mono`          | 13 / 20                       | mono 400                      | IDs, captured actions.                                       |
| `.mono-figure`   | 13 / 18                       | Inter 500, tabular-nums       | Numbers in tables and lists. Right-align in tables.          |

Rules: at most one italic rust word per headline. Never gradient text.
`text-wrap: balance` on headings. Tabular numerals on every number.

---

## 4. Spacing, radius, elevation, size

| Spacing                                   | Value                       | Use                                     |
| ----------------------------------------- | --------------------------- | --------------------------------------- |
| `--space-1` … `--space-6`                 | 4 / 8 / 12 / 16 / 20 / 24px | Icon gap → between cards                |
| `--space-8` / `--space-10` / `--space-12` | 32 / 40 / 48px              | Section gap, page gutter, block padding |
| `--space-16` / `--space-24`               | 64 / 96px                   | Marketing section rhythm, hero padding  |

Content column `max-width: 1120px`; side padding 28px (18px under 700px).
Grids are asymmetric editorial splits (`4fr 8fr`, `6fr 5fr`) that collapse to
one column at 900px. Always `min-width: 0` on grid children.

| Radius                        | Value | Use                                               |
| ----------------------------- | ----- | ------------------------------------------------- |
| `--radius-sm`                 | 3px   | Swatches, cell highlights                         |
| `--radius-md` / `--radius-lg` | 4px   | Inputs, buttons, cards — this is paper, not glass |
| `--radius-xl`                 | 6px   | Floating overlay panel                            |
| `--radius-pill`               | 999px | Pill buttons, badges, chips                       |

| Shadow        | Use                       |
| ------------- | ------------------------- |
| `--shadow-sm` | Sticky table header       |
| `--shadow-md` | Overlay panel, menus      |
| `--shadow-lg` | Dialogs, the sign-in card |

Cards do **not** cast shadows. Structure comes from a 1px `--line` border.

| Size                                             | Value          |
| ------------------------------------------------ | -------------- |
| `--control-sm` / `--control-md` / `--control-lg` | 28 / 36 / 44px |
| `--row`                                          | 40px           |
| `--sidebar`                                      | 240px          |

---

## 5. Components

**Buttons** — pill. Primary: `--brand` fill, `--on-brand` text, hover
`--brand-hover`; one per view. Ghost (default): transparent with an inset 1px
`--ink` ring, hover `--cream-2`. Dashboard quiet button: paper fill, 1px
`--line-strong`, 4px radius. Danger / warning: soft tint fill with matching
text. Disabled `opacity: .45`.

**Inputs / selects** — `--paper` ground, 1px `--line` (workspace) or
`--line-strong` (dashboard) border, radius 4px, Inter 15px / 13px. Labels
Inter 13px 600 `--ink-2`, 6px above. Never rely on placeholder as label.

**Badges / pills** (`.badge`, dashboard `.pill`, `.conf`) — `height: 22px`,
`padding: 0 9px`, 1px `--line` border, transparent ground, 12.5px 500.
Tone changes border + text only: `.agent` moss, `.human` rust, `.success`,
`.warning`, `.danger`.

**Cards** — `--paper`, 1px `--line`, radius 4px, padding 28px (workspace) /
16px (dashboard). One idea per card.

**Metrics** — a ruled row, not four cards: eyebrow label, figure in
Newsreader 400 (40px workspace, 30px dashboard), separated by `--line`.

**Tables** — paper ground, 13–14px Inter, header row `.label` in `--ink-3`
with no fill, rows ruled with `--line`, numbers `.num` right-aligned and
`nowrap`. Wrap in `overflow-x: auto` with a `min-width` so mobile scrolls.

**Report / ledger lists** — one ruled row per item: serif title, figures in
`.mono-figure`, a small ghost button at the right.

**Messages / banners** — `--cream-2` or paper tint with a 3px `--rust` left
border, radius 4px.

**Empty states** — dashed `--line` border, centred Newsreader 19px `--ink-3`.

**Navigation** — 72px header, logo "Vista" in Newsreader 500 26px with the
`.logo-dot` ink square; links Inter 15px `--ink-2`, rust underline on hover.
Dashboard sidebar: active item has a paper ground and a 3px rust left bar.

**Overlay** — paper glass panel (`rgba(251,248,242,.96)` + blur), radius 6px,
`--shadow-md`. Idle orb is an `--ink` disc with a rust satellite / arc; the
recording dot is `--danger`, finishing is `--moss`.

---

## 6. Writing rules

- Plain, direct, operator-to-operator. Prefer a number to an adjective:
  "Acrobat → QuickBooks, 42 times, 3.1 hours a week."
- Sentence case everywhere; column headers are uppercased by CSS.
- Say what stays on the device. Separate observed facts, inferred labels and
  the employee's own word — and label which is which.
- Headlines are sentences with at most one italic word.
- No emoji, no exclamation marks, Vista is "Vista", never "we".

---

## 7. Signature motifs

- **The ruled ledger.** Timelines, tables and lists drawn as rows under
  hairline `--line` rules; illustration is data, never stock imagery.
- **The italic word.** One Newsreader 300 italic rust word in a headline.
- **The dark closing band.** `--ink` ground, centred serif h2, cream button.
- **Q / A.** A rust "Q." and a moss "A." in serif, provenance as a pill.

---

## 8. Icons and logo

- Icons: [Lucide](https://lucide.dev) is the standard — the only icon set in the
  product. Line only, `stroke: currentColor`, `stroke-width: 1.4–1.75`, round
  caps and joins, no fill. 14–16px in the product, 40px on marketing.
  - Web (React): `lucide-react`, import icons by name. Static/Electron pages:
    inline the icon's SVG paths from lucide.dev in a 24-unit `viewBox`,
    `aria-hidden` when next to a label.
  - Icon + label: `--space-1` gap, icon before the word. Icon-only buttons need
    a `title`. Never draw ad-hoc glyphs or use emoji/unicode symbols (✓, →, ⚠)
    where a Lucide icon exists.
  - Status pairs: submitted `cloud-check`, uploading `loader-circle`, failed
    `circle-alert`, edit `pencil`, confirm `check`, folder `folder`, upload
    `cloud-upload`.
- Logo: "Vista" in Newsreader 500 with `.logo-dot` — a 10px `--ink` square
  with 2px radius (the "observed" swatch from the ledger legend).

---

## 9. Do / Don't

**Do**

- Draw structure with 1px rules, not fills or shadows.
- Keep one rust accent per view; use moss only for approval / agent work.
- Set headlines and figures in Newsreader, everything measured in Inter.
- Keep tables dense and ruled; right-align numbers.

**Don't**

- Don't fill panels with rust or moss; tone lives in borders and text.
- Don't use radii over 6px, glass over content, or shadows on cards.
- Don't use gradients, gradient text, or neon.
- Don't load fonts from a third-party origin.
- Don't animate anything that does not fall back under
  `prefers-reduced-motion: reduce`.

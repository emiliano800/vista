# Vista Design System

Vista is process intelligence for private-equity operating teams. It mines what back-office staff do across a portfolio, draws the process pipelines, and hands repeatable steps to agents.

This README is for engineers who build Vista screens. It tells you which colours, type styles and components to use, and the rules that keep every screen looking like one product.

The living source of truth is the Vista design system artifact in Claude. This file is a copy for the repo. When the artifact changes, regenerate this file.

---

## 1. The idea in three lines

1. **Three neons, ink on top.** Neon green is the brand. Neon lime means "an agent did this". Electric periwinkle means "a person owns this". Text never sits inside a neon; dark ink sits on the neon.
2. **Slight purple ground, white panels.** The page is a pale purple. Cards, rows and inputs are white. That contrast makes the greens pop.
3. **Dense product, open marketing.** In the app: 13px table text, 36px rows, 32px controls. On the site: 56px headlines, big gaps, one loud lime button.

---

## 2. Colour tokens

All colours are CSS custom properties. Use `var(--token)`. Never type a hex value in a component.

### Grounds

| Token | Light | Dark | Use |
|---|---|---|---|
| `--surface` | `#f3f0ff` | `#0d0b14` | Page background. Slight purple. |
| `--surface-raised` | `#ffffff` | `#17141f` | Cards, table rows, menus, popovers. |
| `--surface-sunken` | `#e9e4ff` | `#08070f` | Table headers, sidebar, input wells. |
| `--surface-hover` | `#f4f2ff` | `#1d1a2b` | Row and menu hover. |
| `--surface-selected` | `#e0dcff` | `#2a2470` | Selected rows, active nav items. |
| `--line` | `#ddd8f0` | `#2a2638` | Hairlines. Decorative only. |
| `--line-strong` | `#877fa8` | `#6f6a85` | Input borders and any border that carries meaning. 3:1. |

### Text

| Token | Light | Dark | Use |
|---|---|---|---|
| `--ink` | `#120f24` | `#f3f1fa` | Primary text. |
| `--ink-secondary` | `#3f3a55` | `#c6c2d6` | Cell values, nav items at rest. |
| `--ink-muted` | `#605a78` | `#9b96ad` | Metadata, helper text, column headers. |
| `--ink-faint` | `#8b86a0` | `#736e88` | Placeholders and disabled labels only. 3:1, not 4.5:1. |
| `--ink-inverse` | `#f3f0ff` | `#0d0b14` | Text on an ink fill (tooltips). |

### Brand (neon green)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--brand` | `#00d26a` | `#00e676` | A **fill**: primary button, logo dot, progress. |
| `--on-brand` | `#04261a` | `#04261a` | Text on a brand fill. Dark ink, never white. |
| `--brand-ink` | `#00743a` | `#5cff9d` | Green as **text**: links, active tab labels. |
| `--brand-hover` | `#00bd5f` | `#33ff8f` | Primary button hover. |
| `--brand-soft` | `#d6ffe8` | `#0f3d26` | Pale ground behind brand-ink text. |
| `--link` | alias of `--brand-ink` | | Inline links. |

### Lime (agents)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--lime` | `#d8ff00` | `#d8ff00` | A **fill**: accent button, agent badge, progress, the tick on section rules, 2px border on agent nodes, 3px top rule on tables. |
| `--on-lime` | `#0c1f15` | `#0c1f15` | Text on a lime fill. |
| `--lime-ink` | `#3d6200` | `#e2ff5c` | "Agent" as text on a pale ground. |
| `--lime-soft` | `#eaff8a` | `#2c3d08` | Ground of an agent-owned pipeline node. |

### Periwinkle (people)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--periwinkle` | `#5b5bff` | `#8f8fff` | A **fill**: secondary button, human badge, 2px border on human nodes. |
| `--on-periwinkle` | `#ffffff` | `#0b0b3d` | Text on a periwinkle fill. |
| `--periwinkle-ink` | `#3f3fe0` | `#b3b3ff` | "Person" as text on a pale ground. Also the focus ring. |
| `--periwinkle-soft` | `#e0dcff` | `#2a2470` | Ground of a human-owned pipeline node. |
| `--focus-ring` | alias of `--periwinkle-ink` | | 2px solid ring, 2px outside the control. |

### Status

| Token | Light | Dark |
|---|---|---|
| `--success` / `--success-soft` | `#0f7a3a` / `#d6ffe8` | `#33ff8f` / `#0f3d26` |
| `--warning` / `--warning-soft` | `#9a4a00` / `#fff0b3` | `#ffc02e` / `#3f2e05` |
| `--danger` / `--danger-soft` | `#c41c08` / `#ffe0da` | `#ff6a55` / `#47150c` |
| `--on-danger` | `#ffffff` | `#2a0d0b` |

Status always carries a word or an icon. Never colour alone.

### Contrast rules

- Every text token meets 4.5:1 on the grounds its row names, in both themes.
- `--line-strong`, `--ink-faint` and `--focus-ring` meet 3:1.
- The neon fills are lighter than 3:1 against white. So a neon element always carries ink text, or sits inside a bordered card. Never signal meaning with a neon fill alone.

---

## 3. Type

Fonts load from Google Fonts. No font files in the repo.

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Instrument+Serif:ital@1&family=JetBrains+Mono:wght@400;500&display=swap">
```

| Family var | Stack | Use |
|---|---|---|
| `--font-sans` | Instrument Sans | Everything. |
| `--font-display` | Instrument Serif italic | One emphasised word in a hero headline. Never a sentence. |
| `--font-mono` | JetBrains Mono | Identifiers, captured actions, figures. |

| Class | Size / line | Weight | Use |
|---|---|---|---|
| `.display-xl` | 56 / 60 | 600, -0.025em | Marketing hero. One per page. |
| `.display` | 40 / 44 | 600, -0.02em | Marketing section headlines. |
| `.display-serif` | 56 / 60 | 400 italic | The one emphasised word inside `.display-xl`. |
| `.h1` | 28 / 34 | 600 | Product page title. |
| `.h2` | 20 / 26 | 600 | Card titles. |
| `.h3` | 15 / 22 | 600 | Group titles, pipeline steps. |
| `.body-lg` | 16 / 24 | 400 | Marketing paragraphs. |
| `.body` | 14 / 20 | 400 | Forms, dialogs. |
| `.body-dense` | 13 / 18 | 400 | Table cells, list rows, sidebar. |
| `.small` | 12 / 16 | 400 | Metadata, helper text. |
| `.label` | 11 / 16 | 600, +0.06em, uppercase | Column headers, eyebrows. Uppercase via CSS. |
| `.mono` | 13 / 20 | 400 | Captured actions, IDs. |
| `.mono-figure` | 13 / 18 | 500, tabular-nums | Numbers in tables. Right-align. |

---

## 4. Spacing, radius, elevation, size

| Spacing | Value | Use |
|---|---|---|
| `--space-1` | 4px | Icon-to-label gap. |
| `--space-2` | 8px | Between inline controls. |
| `--space-3` | 12px | Table cell padding, input padding. |
| `--space-4` | 16px | Card padding. |
| `--space-5` | 20px | Panel padding. |
| `--space-6` | 24px | Between cards; dialog padding. |
| `--space-8` | 32px | Between sections in a page. |
| `--space-10` | 40px | Page gutter. |
| `--space-12` | 48px | Marketing block padding. |
| `--space-16` | 64px | Between marketing sections. |
| `--space-24` | 96px | Hero padding. |

| Radius | Value | Use |
|---|---|---|
| `--radius-sm` | 4px | Checkboxes, cell highlights. |
| `--radius-md` | 6px | Inputs, quiet buttons, pipeline nodes. |
| `--radius-lg` | 10px | Cards, popovers. |
| `--radius-xl` | 16px | Marketing cards and image frames. |
| `--radius-pill` | 999px | Primary, accent and secondary buttons; badges. |

| Shadow | Use |
|---|---|
| `--shadow-sm` | Sticky table header. |
| `--shadow-md` | Menus, popovers. |
| `--shadow-lg` | Dialogs, command palette. |

Cards do **not** cast shadows. Structure comes from a 1px `--line` border.

| Size | Value | Use |
|---|---|---|
| `--control-sm` | 28px | Buttons and inputs in tables and toolbars. |
| `--control-md` | 32px | Default product controls. |
| `--control-lg` | 40px | Marketing CTAs, login form. |
| `--row` | 36px | Table row height. |
| `--sidebar` | 240px | Left nav width. |

---

## 5. Components

The bundle exposes `window.Vista` and expects React 18 on the page. Styles are in `bundle.css` and read the tokens above.

| Component | Key props | Rule |
|---|---|---|
| `Button` | `variant`: `primary` \| `accent` \| `secondary` \| `quiet` \| `danger`; `size`: `sm` \| `md` \| `lg` | Quiet is the default. One `primary` per view. `accent` (lime) is the loudest thing on the page: one per view, for "Get started" or "Automate". |
| `Input` | `label` (required), `hideLabel`, `helper`, `error`, `size` | Never rely on placeholder as the label. |
| `Badge` | `tone`: `neutral` \| `brand` \| `agent` \| `human` \| `success` \| `warning` \| `danger`; `dot` | Solid neon fills. `agent` = lime, `human` = periwinkle. Pass `dot` so the tone is not colour alone. |
| `Card` | `title`, `meta`, `actions`, `size`, `padding: 'none'` | One idea per card. `padding="none"` for a table. |
| `Table` | `columns`, `rows`, `selectable`, `selected`, `onSelect` | 36px rows, 13px cells, `label` headers, mono figures right-aligned. Truncate, never wrap. |
| `Nav` | `items`, `breadcrumb`, `actions`, `variant: 'product' \| 'marketing'` | Active tab: `brand-ink` text, 4px lime underline. |
| `ProcessNode` | `stage`, `step`, `duration`, `volume`, `owner: 'agent' \| 'human' \| 'none'`, `ownerName`, `status`, `connector` | Agent nodes: lime-soft ground, 2px lime border. Human nodes: periwinkle-soft, 2px periwinkle border. Unassigned: dashed `line-strong`. |

### Example

```jsx
const { Card, Table, Badge, Button } = window.Vista;

<Card title="Tasks" meta="4 of 12" padding="none"
      actions={<Button variant="accent" size="sm">Automate selected</Button>}>
  <Table
    columns={[
      { key: 'task', label: 'Task' },
      { key: 'owner', label: 'Owner' },
      { key: 'time', label: 'Median time', align: 'right' },
    ]}
    rows={[
      { id: 1, task: 'Match PO to invoice', owner: <Badge tone="agent" dot>Agent 07</Badge>, time: '38 s' },
      { id: 2, task: 'Approve over $50k', owner: <Badge tone="human" dot>Priya</Badge>, time: '2 d 4 h' },
    ]}
    selectable
  />
</Card>
```

---

## 6. Writing rules

- Plain, direct, operator-to-operator. Short sentences.
- Sentence case everywhere. Column headers are uppercased by CSS, never typed in caps.
- "You" is the reader. "Your portfolio" is the fund. "The company" is the target. Vista is "Vista", never "we".
- Numbers are the product. Show them in `.mono-figure` with units: `4.2 FTE`, `$1.28M/yr`, `18 min → 40 s`.
- Name the human and the agent: "Assigned to Priya", "Run by Agent 07". Never "the system".
- No emoji. No exclamation marks.

---

## 7. Signature motifs

- **The section rule.** A 4px `--ink` rule with a short `--lime` segment at its right end. Divides the hero from the first section.
- **The CTA bar.** A pill-shaped outline bar on white. One line of copy on the left, a periwinkle secondary and a lime accent button on the right.
- **The pipeline.** Process nodes joined left to right by 1.5px `--line-strong` connectors with a small arrowhead. Agent nodes lime, human nodes periwinkle.

---

## 8. Icons and logo

- Icons: [Lucide](https://lucide.dev) is the standard — the only icon set in the product. 16px in the product (14px inside `sm` controls), 20px on marketing, stroke 1.75, round caps and joins, `currentColor`, no fill.
  - Web (React): `lucide-react`, import icons by name. Static/Electron pages: inline the icon's SVG paths from lucide.dev in a 24-unit `viewBox`, `aria-hidden` when next to a label.
  - Icon + label: `--space-1` gap, icon before the word. Icon-only buttons need a `title`. Never draw ad-hoc glyphs or use emoji/unicode symbols (✓, →, ⚠) where a Lucide icon exists.
  - Status pairs: submitted `cloud-check`, uploading `loader-circle`, failed `circle-alert`, edit `pencil`, confirm `check`, folder `folder`, upload `cloud-upload`.
- Logo: none yet. Set "Vista" in Instrument Sans 600 with -0.02em tracking. The nav dot is a `--brand` circle with a `--lime` satellite top-right and a `--periwinkle` satellite bottom-left.

---

## 9. Do / Don't

**Do**

- Put ink on neon. Put neon on purple or white.
- Use one lime button per view.
- Keep tables dense: 13px, 36px rows, hairline rules.
- Use `--brand-ink` for green text, `--brand` for green fills.

**Don't**

- Don't use white text on green or lime.
- Don't tint whole panels green or lime.
- Don't use lime for anything that is not an agent, or periwinkle for anything that is not a person.
- Don't add shadows to cards.
- Don't use gradients.

# Groundwork — Company Portal (prototype)

High-fidelity interactive prototype of the portal used by a company being
acquired (portal 1 of 2; the PE-fund side is out of scope). A desktop agent
passively observes the business for one week; this portal is the acquired
company's window into that process.

## Stack

Plain HTML + CSS + vanilla JavaScript in a single file — no framework, no
build step, no backend. State lives in one `state` object; each change
re-renders the app from template-literal view functions. The only external
dependency is the Instrument Sans font from Google Fonts.

## Run it

Open `index.html` in a browser. That's it.

```bash
open company-portal/index.html
```

## Demo controls

The dark **Demo** pill in the bottom-right corner jumps to any state without
waiting on timers:

- **Phase 1 — Setup**: welcome, software scan (completed), sector
  confirmation, review screen
- **Phase 2 — Recording**: day 1 / 4 / 7 simulation, pause toggle
- **Phase 3 — Handoff**: uploading, processing (parallel accounting /
  supply-chain / tax modules, including the inline QuickBooks-export
  request), processing complete, extraction summary

All data is realistic mock data (vendors, invoice numbers, file names) for a
fictional 30-person HVAC company, "Summit Air & Heating."

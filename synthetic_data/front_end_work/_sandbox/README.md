# `_sandbox/` — the one writable demo fixture

`entry.html` is a plain vendor-invoice entry screen that stands in for the *destination*
system of the Ridgeway **Enter Bills** workflow (`industrial_goods/ridgeway_fasteners_and_supply/
04_receiving_ap/supplier_invoices.csv` is the source). It exists so the Computer Use Agent has
somewhere safe to act during a demo:

- `<input name>`s equal the CSV columns: `SUPPLIER_NAME`, `SUPPLIER_INVOICE_N`, `PO_NUMBER`,
  `INVOICE_DATE`, `DUE_DATE`, `MERCHANDISE_TOTAL`, `FREIGHT`, `SALES_TAX`, `INVOICE_TOTAL`,
  `GL_ACCOUNT` (select). Every control has an accessible name (`<label for>`), which is what an
  accessibility-tree-first browser harness enumerates.
- Exactly one irreversible control: **Submit invoice**. A `role="status"` line confirms
  “Saved invoice … as row N.”; the **Submitted invoices** table (from `localStorage`) is what
  an independent read-back verifies against.
- A duplicate invoice number is refused with a `role="alert"` — the “left for a person” case.
- **Export CSV** downloads the table; **Clear all** needs two clicks; `?reset=1` starts empty;
  `window.VISTA_SANDBOX.rows()` / `.csv()` expose the table to scripts.

Serve it with the other fixtures:

```bash
cd synthetic_data/front_end_work
python3 -m http.server 8765
# http://localhost:8765/_sandbox/entry.html?reset=1
```

Everything else under `front_end_work/` stays read-only.

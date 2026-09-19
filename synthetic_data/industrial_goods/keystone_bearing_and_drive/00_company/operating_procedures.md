# Keystone Bearing & Drive — How we do things

- Inside sales enters orders in NetSuite from email/phone. EDI customers come in automatically.
- Customer contract pricing is maintained in the price agreements list; check expiration before quoting (this is often missed).
- Buyers may use either Grainger vendor record; we've been meaning to clean this up.
- Warehouse ships from pick list; invoicing runs nightly from shipped orders. If a shipment is marked 'delivered' manually it can skip the invoice batch.
- Assembly cell builds conveyor drive packages from a simple BOM; no routings or labor tracking beyond timesheets.
- Quality: receiving inspection on motors/reducers only; NCRs tracked in uniPoint.

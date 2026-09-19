# Answer Key — planted anomalies and portfolio findings

Use this to check whether Vista's agents find what was planted. Items marked **TRAP** are
deliberately misleading and the correct behaviour is to *not* merge / *not* flag them.

## carrier_consolidation

### PORT-INS-01 — Same carriers appointed at all three agencies at different commission rates
- Companies: Meridian, Harborline, Castlebrook
- Travelers, Hartford, Liberty Mutual and CNA appear in carriers.csv for Meridian Risk Partners, LLC, Harborline Insurance Brokers, Inc., Castlebrook Agency; contracted commission_pct differs by agency for the same line (see carriers.csv commission column). Combined premium volume could support a single higher-tier agreement / contingent bonus.
- Evidence: `*/03_marketing_submissions/carriers.csv`, `*/02_policies_exposures/policies.csv`, `*/10_compliance_licensing/carrier_appointments.csv`
- Expected action: Aggregate written premium by carrier across agencies; compare commission schedules; identify carriers where volume tiers would be reached combined.

## certificate

### INS-CERT-01 — Certificate request exceeds in-force umbrella limit
- Companies: Meridian
- Turner Construction requires a $5M umbrella for Liberty Electric Inc.; policy LIB-UMB-2133128 carries $3M. Certificate must not be issued as requested.
- Evidence: `05_certificates/certificate_requests.csv`, `02_policies_exposures/coverages.csv`
- Expected action: Flag to account manager; quote excess layer increase or obtain holder waiver. Do not fabricate a certificate.

### INS-CERT-02 — Certificate requires waiver of subrogation not on policy
- Companies: Harborline
- Port Tampa Bay requires waiver of subrogation for Blue Ridge Electric LP; GL policy TRV-GL-5190492 has no CG 24 04 endorsement.
- Evidence: `05_certificates/certificate_requests.csv`, `06_endorsements/endorsement_requests.csv`
- Expected action: Create endorsement request to carrier; hold certificate until endorsement is bound.

## commission

### INS-COMM-01 — Carrier paid commission at 10% instead of contracted 12%
- Companies: Meridian
- Policy LIB-AUTO-3288560 premium 79224: expected 9506.88 at 12.0%, statement LIB-COMM-202602 paid 7922.4 at 10%. Variance -1584.48.
- Evidence: `09_carrier_payables_commissions/commission_statements.csv`, `02_policies_exposures/policies.csv`, `03_marketing_submissions/carriers.csv`
- Expected action: Create carrier inquiry task; request corrected statement.

### INS-COMM-02 — Commission statement line does not match any policy in the book
- Companies: Harborline
- Carrier statement includes 'Suncoast Dental Partners PA' which is not a client; likely a BOR to another agency or a carrier mis-post.
- Evidence: `09_carrier_payables_commissions/commission_statements.csv`, `02_policies_exposures/policies.csv`
- Expected action: Investigate with carrier before recognizing revenue; possible BOR letter or mis-posted agency code.

## compliance

### INS-COMP-01 — Producer writing business on an expired license
- Companies: Castlebrook
- Aisha Davis (PA) license expired 2026-02-18 but has policies effective after that date.
- Evidence: `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx#licenses`, `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx#policies`
- Expected action: Immediate escalation to compliance; stop new binds; file renewal.

### IND-EHS-01 — Employees operating forklifts with expired certification
- Companies: Northfield
- 3 employees have expired Forklift Certification but appear as putaway/shipping users in receipt_lines / pick_lists after expiry (e.g. E050, expired 2026-03-12).
- Evidence: `15_hr_ehs/training_certifications.csv`, `04_receiving_ap/receipt_lines.csv`, `06_warehouse_fulfillment/pick_lists.csv`
- Expected action: Schedule recertification; OSHA 1910.178 exposure.

## cross_sell

### PORT-XSELL-01 — Cardinal Foods Group buys from three portfolio companies as three unrelated plants
- Companies: Keystone, Ridgeway, Northfield
- Northfield sells to 'Cardinal Foods Group - Plant 07 - Rochelle, IL', Keystone to 'Cardinal Foods Group - Plant 12 - Hazleton, PA', Ridgeway to 'Cardinal Foods Plant 21'. Ridgeway's email notes the TN plant buys bearings from 'someone in PA' (Keystone) and wants to consolidate MRO suppliers; corporate is in Chicago.
- Evidence: `*/01_master_data/customers.csv`, `ridgeway_fasteners_and_supply/16_documents_emails/counter_sale_pricing_question.eml`, `*/02_sales_quote_to_order/sales_order_lines.csv`
- Expected action: Treat as one national account; propose a corporate agreement covering fasteners (Ridgeway), bearings/drives (Keystone) and machined components (Northfield).

### PORT-XSELL-TRAP-01 — Customers named 'Keystone ...' at Ridgeway/Northfield are not Keystone Bearing & Drive **TRAP**
- Companies: Keystone, Ridgeway, Northfield
- Customer-name word 'Keystone' (a common Pennsylvania brand word) appears in customer names at other companies; not an intercompany relationship.
- Evidence: `*/01_master_data/customers.csv`
- Expected action: Do not flag as intercompany sale.

### PORT-INS-02 — Industrial portfolio companies are prospects for the insurance agencies
- Companies: Meridian, Harborline, Castlebrook, Northfield, Keystone, Ridgeway
- Northfield, Keystone and Ridgeway each carry WC/GL/property/auto (see 12_hr_ehs incidents, fleets in shipments 'Company Truck'); none of the three agencies write them today.
- Evidence: `industrial_goods/*/00_company/company_profile.json`, `insurance_broking/*/01_clients_crm/clients.csv`
- Expected action: Intercompany placement is a real but sensitive opportunity; surface as a suggestion requiring human review (related-party).

## data_quality

### INS-DQ-03 — Renewal tracker expiration date disagrees with policy record
- Companies: Harborline
- Renewal REN-HAR-P00012 shows expiration 2026-08-27 while policies.csv shows 2026-07-27 for HAR-P00012.
- Evidence: `04_renewals/renewals.csv`, `02_policies_exposures/policies.csv`
- Expected action: Treat the policy record (carrier download) as source of truth; correct tracker; recompute 120/90/60/30 milestones.

### INS-DQ-01 — Same insured entered twice (legal name vs DBA)
- Companies: Castlebrook
- 'Lackawanna Hauling LLC' (CAS-C0004) and 'LH Express' (CAS-C0023) share the same address and producer; LH Express is the DBA and has no FEIN. Policies are split across both records.
- Evidence: `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx#clients`, `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx#policies`
- Expected action: Propose merge; keep legal name as primary, DBA in dba_name field; require human confirmation.

### INS-DQ-04 — Missing FEINs on multiple client records
- Companies: Castlebrook
- Several clients have blank FEIN; FEIN is required on ACORD 125 and for carrier submissions.
- Evidence: `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx#clients`
- Expected action: Create data-collection tasks for account managers; do not fabricate values.

### INS-DQ-05 — Duplicate contact records
- Companies: Castlebrook
- Contact Sandra Fitzgerald appears twice with different casing.
- Evidence: `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx#contacts`
- Expected action: Deduplicate case-insensitively; keep the record with the most recent activity.

### INS-DQ-02 — Vehicle schedule disagrees with ACORD application
- Companies: Castlebrook
- Vehicle schedule for TRV-AUTO-7698813 lists 45 units; the ACORD 127 application (see 13_documents_emails) and policies sheet say 47.
- Evidence: `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx#vehicles`, `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx#policies`, `13_documents_emails/acord_127_vehicle_schedule.json`
- Expected action: Flag exposure discrepancy; request updated schedule from client before renewal marketing.

### INS-DQ-06 — Swapped column headers in legacy policy export
- Companies: Castlebrook
- In the MASTER_WORKBOOK 'policies' sheet the EXP_DATE column actually holds effective dates and EFF_DATE holds expirations (all EXP_DATE < EFF_DATE). Similarly in claims.csv 'RESERVE' holds paid-to-date and 'PAID' holds outstanding reserve; in vendors.csv the vendor name column is labelled CUSTOMER.
- Evidence: `00_legacy_exports/MASTER_WORKBOOK_v7_FINAL (2).xlsx`, `07_claims/claims.csv`, `11_finance_gl/vendors.csv`
- Expected action: Infer semantics from values (date ordering, closed claims having reserve = 0); propose column mapping and require human confirmation.

### INS-DQ-07 — Datasets missing entirely
- Companies: Castlebrook
- Castlebrook has no workflow event log, no commission statements, no producer commission records, no GL detail, no renewal task tracker and no issued-certificate register. Commission income can only be inferred from bank deposits and the carriers table.
- Evidence: `00_company/company_profile.json`
- Expected action: Report gaps explicitly; recommend enabling carrier downloads and a commission reconciliation process; do not fabricate missing history.

### IND-UOM-01 — Unit-of-measure mismatch: box price stored against EA stock UoM
- Companies: Keystone
- Item KIM-G10 (nitrile gloves, 100/bx) has stock_uom EA but unit_cost 11.40 is the per-box price; on-hand quantities in inventory_balances are in boxes while sales order lines sell EA.
- Evidence: `01_master_data/items.csv`, `05_inventory/inventory_balances.xlsx`, `02_sales_quote_to_order/sales_order_lines.csv`
- Expected action: Correct stock_uom to BX (pack 100) or restate cost per EA before margin analysis.

### IND-VEND-01 — Duplicate supplier master records for Grainger
- Companies: Keystone
- KBD-V007 'GRAINGER' and KBD-V019 'W W Grainger Inc' are the same vendor with different terms; POs and AP invoices are split between them.
- Evidence: `03_procurement/suppliers.csv`, `03_procurement/purchase_orders.csv`, `04_receiving_ap/supplier_invoices.csv`
- Expected action: Merge vendor records; consolidate spend for negotiation.

### IND-CRM-01 — Duplicate customer record (same company, different casing/suffix, different terms)
- Companies: Ridgeway
- RFS-CUST0008 and RFS-CUST0025 are the same customer; open AR is split across both and terms disagree (Net 30 vs COD).
- Evidence: `00_legacy_exports/RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx#customers`, `11_billing_ar/customer_invoices.csv`
- Expected action: Merge records, pick surviving terms, consolidate AR aging.

### IND-DQ-02 — Mislabeled 'Max' column actually holds reorder quantity
- Companies: Ridgeway
- In the QuickBooks item list export the 'Max' column is the reorder quantity (order-up-to is not tracked), and 'Reorder Pt (Min)' is the reorder point. Stock UoM mixes EA / BX / C (hundreds) without a consistent pack factor.
- Evidence: `01_master_data/QuickBooks_Item_List_export.csv`, `00_legacy_exports/RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx#items`
- Expected action: Map Max->reorder_qty; normalize UoM before comparing prices per each.

### IND-DQ-01 — Sales order export has SO and customer-PO columns swapped (and 7% of rows entered swapped in the source)
- Companies: Ridgeway
- In the legacy workbook 'sales_orders' sheet the column labelled PO_NUM holds Ridgeway's own SO/invoice number and SO_NUM holds the customer PO. Independently, ~7% of rows were keyed with the two values reversed at data entry, so the label swap does not fix every row.
- Evidence: `00_legacy_exports/RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx#sales_orders`, `11_billing_ar/customer_invoices.csv`
- Expected action: Infer semantics from value patterns (5-digit numeric = SO; PO/P/4500/PUR- prefixes = customer PO); flag rows that do not fit either pattern.

## engineering_change

### IND-ENG-01 — Released ECO not reflected in BOM (BOM still calls obsolete component revision)
- Companies: Northfield
- ECO-2599 released 2026-01-15 moves A-20002 to rev C and replaces component P-20079 rev A; bills_of_material still lists rev A and work orders since then issued the old rev.
- Evidence: `13_engineering_compliance/engineering_change_orders.csv`, `01_master_data/bills_of_material.csv`, `08_manufacturing/work_orders.csv`
- Expected action: Update BOM; check WIP/FG built since 2026-01-15 for containment.

## freight_rate_gap

### PORT-FREIGHT-01 — Parcel freight cost per lb varies ~30% across industrial companies
- Companies: Northfield, Keystone, Ridgeway
- Effective parcel cost/lb from shipments.csv: Northfield: $0.595, Keystone: $0.657, Ridgeway: $0.848. Northfield has a negotiated UPS agreement; Ridgeway pays near list via WorldShip/FedEx Ship Manager.
- Evidence: `*/06_warehouse_fulfillment/shipments.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Extend Northfield's carrier agreement to the other two; audit accessorials in freight_invoices.

## inventory_integrity

### IND-INV-02 — Large unposted cycle-count shrink
- Companies: Northfield
- Count CC-0030 for A-20036 found 34 fewer than system ($100,152.10); variance not posted for 30+ days.
- Evidence: `05_inventory/cycle_counts.csv`, `05_inventory/inventory_balances.csv`
- Expected action: Investigate and post adjustment; inventory valuation is overstated.

### IND-INV-01 — Negative on-hand quantity
- Companies: Keystone
- Item TIMI-1000 shows on_hand -6 in MAIN; shipments were confirmed before receipts were posted.
- Evidence: `05_inventory/inventory_balances.xlsx`, `05_inventory/inventory_transactions.csv`
- Expected action: Post the missing receipt / investigate backflush timing.

## order_entry

### IND-O2C-02 — Customer PO quantity does not match entered sales order
- Companies: Keystone
- Customer PO P821082 line 1 shows qty 3; sales order SO-26006 line 1 was entered as 1.
- Evidence: `16_documents_emails/customer_po_P821082.json`, `02_sales_quote_to_order/sales_order_lines.csv`
- Expected action: Confirm with customer; correct SO before shipment.

### IND-O2C-03 — Customer PO quantity does not match entered sales order
- Companies: Ridgeway
- Customer PO PO180950 line 1 shows qty 27; sales order 15761 line 1 was entered as 25.
- Evidence: `16_documents_emails/customer_po_PO180950.json`, `00_legacy_exports/RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx#sales_order_lines`
- Expected action: Confirm with customer; correct SO before shipment.

## overpayment

### IND-AP-01 — Sales tax paid on resale inventory despite resale certificate
- Companies: Ridgeway
- 27 supplier invoices include TN sales tax (9.25%) on items purchased for resale, totalling $26,768.24.
- Evidence: `04_receiving_ap/supplier_invoices.csv`
- Expected action: File for refund / provide resale certificate to suppliers.

## purchasing_price_gap

### IND-PRICE-TRAP-01 — Generic import 6205 bearing is NOT the same item as SKF 6205-2RS1 **TRAP**
- Companies: Ridgeway, Northfield, Keystone
- Ridgeway's BRG-6205-IMP ($1.85, unbranded CN import) looks like the SKF 6205-2RS1 the others buy. A naive 'same size bearing' match would report a huge price gap; manufacturer and part number differ, so it is not a like-for-like comparison.
- Evidence: `00_legacy_exports/RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx#items`
- Expected action: Do not include in price-gap findings; at most flag as a possible spec substitution question.

### PORT-PRICE-6205-2RS1 — SKF 6205-2RS1: Ridgeway pays 46% more than Keystone
- Companies: Northfield, Keystone, Ridgeway
- Deep groove ball bearing 25x52x15, sealed (EA, pack 1). Northfield: 4.10 via Motion Industries (SKU P-10000, ~0 EA/yr on PO lines); Keystone: 3.55 via Applied Industrial Technologies (SKU SKF-6205-2RS1, ~313 EA/yr on PO lines); Ridgeway: 5.20 via Fastenal (SKU BRG-6205, ~315 EA/yr on PO lines). Moving everyone to Keystone's price saves ~$520/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-6308-2Z — SKF 6308-2Z: Ridgeway pays 45% more than Keystone
- Companies: Northfield, Keystone, Ridgeway
- Deep groove ball bearing 40x90x23, shielded (EA, pack 1). Northfield: 11.80 via Motion Industries (SKU P-10007, ~666 EA/yr on PO lines); Keystone: 10.25 via Applied Industrial Technologies (SKU SKF-6308-2Z, ~1152 EA/yr on PO lines); Ridgeway: 14.90 via Fastenal (SKU 6308-882, ~402 EA/yr on PO lines). Moving everyone to Keystone's price saves ~$2,902/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-LM11949-LM11910 — Timken LM11949/LM11910: Northfield pays 18% more than Keystone
- Companies: Northfield, Keystone
- Tapered roller bearing set (EA, pack 1). Northfield: 8.95 via Motion Industries (SKU P-10014, ~648 EA/yr on PO lines); Keystone: 7.60 via Applied Industrial Technologies (SKU TIM-LM11949-LM11910, ~686 EA/yr on PO lines). Moving everyone to Keystone's price saves ~$875/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-B62 — Gates B62: Ridgeway pays 41% more than Keystone
- Companies: Northfield, Keystone, Ridgeway
- Hi-Power II V-belt B62 (EA, pack 1). Northfield: 14.20 via Motion Industries (SKU P-10021, ~250 EA/yr on PO lines); Keystone: 12.70 via Kaman Industrial Technologies (SKU GAT-B62, ~3929 EA/yr on PO lines); Ridgeway: 17.85 via Grainger Industrial Supply (SKU B62-454, ~2535 EA/yr on PO lines). Moving everyone to Keystone's price saves ~$13,430/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-TXT315 — Dodge TXT315: Northfield pays 7% more than Keystone
- Companies: Keystone, Northfield
- Torque-Arm II shaft-mount reducer 15:1 (EA, pack 1). Keystone: 1180.00 via Applied Industrial Technologies (SKU DOD-TXT315, ~1145 EA/yr on PO lines); Northfield: 1265.00 via Motion Industries (SKU P-10028, ~1156 EA/yr on PO lines). Moving everyone to Keystone's price saves ~$98,260/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-243 — Henkel Loctite 243: Ridgeway pays 17% more than Northfield
- Companies: Northfield, Keystone, Ridgeway
- Threadlocker 243 medium strength blue, 50 mL (EA, pack 1). Northfield: 21.40 via W.W. Grainger, Inc. (SKU P-10035, ~5252 EA/yr on PO lines); Keystone: 22.10 via GRAINGER (SKU HEN-243, ~2480 EA/yr on PO lines); Ridgeway: 24.95 via Fastenal (SKU 243-180, ~424 EA/yr on PO lines). Moving everyone to Northfield's price saves ~$3,241/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-7447 — 3M 7447: Ridgeway pays 16% more than Northfield
- Companies: Northfield, Ridgeway
- Scotch-Brite hand pad 6x9 maroon, 20/bx (BX, pack 20). Northfield: 28.50 via MSC Industrial Supply (SKU P-10042, ~570 BX/yr on PO lines); Ridgeway: 33.20 via Grainger Industrial Supply (SKU 7447-552, ~2384 BX/yr on PO lines). Moving everyone to Northfield's price saves ~$11,205/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-G10 — Kimberly-Clark G10: Ridgeway pays 31% more than Northfield
- Companies: Northfield, Keystone, Ridgeway
- KleenGuard G10 nitrile gloves, large, 100/bx (BX, pack 100). Northfield: 9.85 via Cintas Corporation (SKU P-10049, ~4382 BX/yr on PO lines); Keystone: 11.40 via GRAINGER (SKU KIM-G10, ~2046 BX/yr on PO lines); Ridgeway: 12.95 via Grainger Industrial Supply (SKU G10-708, ~1730 BX/yr on PO lines). Moving everyone to Northfield's price saves ~$8,534/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-SF18-80 — Sigma Stretch Film SF18-80: Ridgeway pays 10% more than Northfield
- Companies: Northfield, Keystone, Ridgeway
- Machine stretch wrap 18in x 1500ft 80ga (RL, pack 1). Northfield: 24.60 via Uline (SKU P-10056, ~999 RL/yr on PO lines); Keystone: 24.60 via Uline (SKU SIG-SF18-80, ~896 RL/yr on PO lines); Ridgeway: 27.10 via Uline (SKU SF18-434, ~1118 RL/yr on PO lines). Moving everyone to Northfield's price saves ~$2,795/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-S-4123 — Uline S-4123: same price at every company (no gap) **TRAP**
- Companies: Northfield, Keystone, Ridgeway
- Corrugated box 12x12x12, 25/bd: all companies pay 31.0 from Uline. Correctly report as 'no opportunity'.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`
- Expected action: No action.

### PORT-PRICE-SF401AF — 3M SF401AF: Ridgeway pays 46% more than Northfield
- Companies: Northfield, Keystone, Ridgeway
- SecureFit 400 safety glasses clear anti-fog (EA, pack 1). Northfield: 3.15 via Cintas Corporation (SKU P-10070, ~2045 EA/yr on PO lines); Keystone: 4.05 via GRAINGER (SKU 3M-SF401AF, ~1686 EA/yr on PO lines); Ridgeway: 4.60 via Fastenal (SKU SF401AF-239, ~676 EA/yr on PO lines). Moving everyone to Northfield's price saves ~$2,498/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-49012 — WD-40 Company 49012: Ridgeway pays 8% more than Northfield
- Companies: Northfield, Ridgeway
- WD-40 Multi-Use 1 gal (GA, pack 1). Northfield: 27.80 via W.W. Grainger, Inc. (SKU P-10077, ~3922 GA/yr on PO lines); Ridgeway: 29.95 via Grainger Industrial Supply (SKU 49012-174, ~2028 GA/yr on PO lines). Moving everyone to Northfield's price saves ~$4,360/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-HHCS-0500-13-200-G8 — Brighton-Best HHCS-0500-13-200-G8: Keystone pays 54% more than Ridgeway
- Companies: Northfield, Ridgeway, Keystone
- Hex head cap screw 1/2-13 x 2 Gr 8 yellow zinc, 50/bx (BX, pack 50). Northfield: 38.40 via Apex Fastener Corp (SKU P-10084, ~535 BX/yr on PO lines); Ridgeway: 29.10 via Brighton-Best International (SKU HHCS-254, ~258 BX/yr on PO lines); Keystone: 44.75 via Fastenal (SKU BRI-HHCS-0500-13-200-G8, ~1037 BX/yr on PO lines). Moving everyone to Ridgeway's price saves ~$21,205/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

### PORT-PRICE-HN-0500-13-G8 — Brighton-Best HN-0500-13-G8: Northfield pays 32% more than Ridgeway
- Companies: Northfield, Ridgeway
- Hex nut 1/2-13 Gr 8 yellow zinc, 100/bx (BX, pack 100). Northfield: 19.20 via Apex Fastener Corp (SKU P-10091, ~578 BX/yr on PO lines); Ridgeway: 14.60 via Brighton-Best International (SKU HN-789, ~350 BX/yr on PO lines). Moving everyone to Ridgeway's price saves ~$2,659/yr on PO-line volume.
- Evidence: `*/01_master_data/items.csv`, `*/03_procurement/purchase_order_lines.csv`, `*/03_procurement/supplier_quotes.csv`
- Expected action: Match on manufacturer + manufacturer_part_number (NOT internal item_id); normalize UoM/pack; propose consolidated buy at best price; cite PO lines as evidence.

## quality

### IND-QA-01 — NCR open 45+ days with CAPA required but no CAPA record
- Companies: Northfield
- NCR-0007 opened 2025-12-29 (DAMAGE) is still open and flagged capa_required=Y; no matching row in corrective_actions.
- Evidence: `09_quality/nonconformance_reports.csv`, `09_quality/corrective_actions.csv`
- Expected action: Open CAPA; escalate to supplier if receiving-related.

## receivables

### INS-AR-01-MER — Agency-bill invoice >90 days past due with pending cancellation
- Companies: Meridian
- Invoice MER-INV00001 (MER-C0001) balance 21020.0 is more than 90 days past due; carrier notice of cancellation is pending.
- Evidence: `08_billing_ar/invoices.csv`, `08_billing_ar/payments.csv`
- Expected action: Escalate to producer; contact client; consider premium finance or return of policy.

### INS-AR-01-HAR — Agency-bill invoice >90 days past due with pending cancellation
- Companies: Harborline
- Invoice HAR-INV00014 (HAR-C0012) balance 11976.1 is more than 90 days past due; carrier notice of cancellation is pending.
- Evidence: `08_billing_ar/invoices.csv`, `08_billing_ar/payments.csv`
- Expected action: Escalate to producer; contact client; consider premium finance or return of policy.

### INS-AR-01-CAS — Agency-bill invoice >90 days past due with pending cancellation
- Companies: Castlebrook
- Invoice CAS-INV00010 (CAS-C0010) balance 27288.75 is more than 90 days past due; carrier notice of cancellation is pending.
- Evidence: `08_billing_ar/invoices.csv`, `08_billing_ar/payments.csv`
- Expected action: Escalate to producer; contact client; consider premium finance or return of policy.

## revenue_leakage

### IND-O2C-01 — Order shipped and delivered but never invoiced
- Companies: Keystone
- Shipment SH-50001 for SO-26001 ($6,847.56) shipped 2025-07-03 with POD on file; no customer invoice exists.
- Evidence: `06_warehouse_fulfillment/shipments.csv`, `06_warehouse_fulfillment/proof_of_delivery.csv`, `11_billing_ar/customer_invoices.csv`
- Expected action: Invoice immediately; add ship-not-invoiced control.

## software_overlap

### IND-SW-01 — Two separate Zoom subscriptions (one expensed to T&E)
- Companies: Keystone
- Keystone pays for Zoom Workplace twice: an IT-owned subscription on GL 6400 and a sales-team one on GL 6800 (corporate card).
- Evidence: `14_finance_gl/software_subscriptions.csv`, `14_finance_gl/general_ledger.csv`
- Expected action: Consolidate to one account.

### PORT-SW-AGENCY — Multiple products serving the same function: Agency Management System
- Companies: Meridian, Harborline, Castlebrook
- Meridian: Applied Epic; Harborline: Vertafore AMS360; Castlebrook: HawkSoft CMS Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-ERP — Multiple products serving the same function: ERP
- Companies: Northfield, Keystone, Ridgeway
- Northfield: Epicor Kinetic; Keystone: NetSuite; Ridgeway: QuickBooks Desktop + Fishbowl Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-QUALITY — Multiple products serving the same function: Quality Management System
- Companies: Northfield, Keystone
- Northfield: MasterControl ($12k flat); Keystone: uniPoint ($4.8k flat) Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-CAD — Multiple products serving the same function: CAD
- Companies: Northfield, Keystone
- Northfield: SolidWorks; Keystone: Autodesk Inventor Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-E-SIGNATURE — Multiple products serving the same function: e-Signature
- Companies: Meridian, Harborline
- Meridian: DocuSign; Harborline: Adobe Acrobat Sign Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-CRM — Multiple products serving the same function: CRM
- Companies: Meridian, Northfield, Keystone
- Meridian: Salesforce; Northfield: Salesforce; Keystone: HubSpot Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-VOIP — Multiple products serving the same function: VoIP Phone System
- Companies: Meridian, Northfield, Keystone, Harborline
- Meridian: RingCentral; Northfield: RingCentral; Keystone: 8x8; Harborline: 8x8 Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-PAYROLL — Multiple products serving the same function: Payroll / HRIS
- Companies: Meridian, Northfield, Harborline, Castlebrook, Ridgeway, Keystone
- ADP (Meridian, Northfield), Paychex (Harborline), Gusto (Castlebrook, Ridgeway), Paycom (Keystone) Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-PRODUCTIVITY — Multiple products serving the same function: Productivity Suite / Email
- Companies: Meridian, Harborline, Northfield, Keystone, Castlebrook, Ridgeway
- Microsoft 365 at 4 companies, Google Workspace at Castlebrook and Ridgeway Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-FILE — Multiple products serving the same function: File Storage
- Companies: Meridian, Northfield, Harborline, Keystone, Ridgeway
- Box (Meridian, Northfield), Dropbox (Harborline, Keystone, Ridgeway) Same-function tools at different vendors; consolidation or a portfolio agreement is the play.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Quantify seats x price per company; propose a single vendor per function (or a portfolio-level contract with the incumbent used by the largest company). Do not propose migrating a core system (AMS/ERP) inside a 12-month window without flagging switching cost.

### PORT-SW-TRAP-01 — Salesforce Sales Cloud vs Slack Business+ are both billed by Salesforce but are NOT overlapping tools **TRAP**
- Companies: Northfield, Keystone
- A vendor-name match would group CRM and team messaging together; functions differ.
- Evidence: `*/11_finance_gl/software_subscriptions.csv`, `*/14_finance_gl/software_subscriptions.csv`
- Expected action: Group by function, not vendor.

## three_way_match

### IND-P2P-01 — Supplier invoice priced 6% above PO unit cost
- Companies: Northfield
- Invoice INV3862958 bills P-20049 at 6.38 vs PO PO-31031 line 3 at 6.0200000000000005; outside 2% tolerance, unresolved.
- Evidence: `04_receiving_ap/supplier_invoices.csv`, `04_receiving_ap/three_way_match.csv`, `03_procurement/purchase_order_lines.csv`
- Expected action: Short-pay or request credit memo; record PPV.

### IND-P2P-02 — Supplier invoiced more units than received
- Companies: Keystone
- Invoice 99200340 bills 98 of SKF-6205-2RS1 against PO PO-31041; receipt shows 88. Invoice is on hold.
- Evidence: `04_receiving_ap/supplier_invoices.csv`, `04_receiving_ap/receipt_lines.csv`, `04_receiving_ap/three_way_match.csv`
- Expected action: Dispute 10 units with supplier; do not pay full amount.

## vendor_consolidation

### IND-VEND-TRAP-01 — Apex Fastening Systems LLC is not Apex Fastener Corp **TRAP**
- Companies: Ridgeway, Northfield
- Ridgeway buys from 'Apex Fastening Systems LLC' (Chattanooga distributor); Northfield buys from 'Apex Fastener Corp' (Elgin IL manufacturer). Similar names, different companies.
- Evidence: `00_legacy_exports/RIDGEWAY MASTER (Fishbowl sync) v7 FINAL (2).xlsx#suppliers`
- Expected action: Do not merge; do not report as shared vendor.

### PORT-VEND-IRON_MOUNTAIN — Iron Mountain Inc. used by 5 portfolio companies under 4 different names
- Companies: Meridian, Harborline, Castlebrook, Northfield, Keystone
- Variants: Meridian: 'Iron Mountain Inc.'; Harborline: 'IRON MOUNTAIN INFO MGMT'; Castlebrook: 'Iron Mtn'; Northfield: 'Iron Mountain Inc.'; Keystone: 'Iron Mountain Information Management, LLC'. Category: Records Storage / Shredding.
- Evidence: `*/11_finance_gl/vendors.csv`, `*/11_finance_gl/ap_vendor_invoices.csv`, `*/14_finance_gl/corporate_vendors.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.

### PORT-VEND-GRAINGER — W.W. Grainger, Inc. used by 4 portfolio companies under 4 different names
- Companies: Northfield, Keystone, Ridgeway, Meridian
- Variants: Northfield: 'W.W. Grainger, Inc.'; Keystone: 'GRAINGER'; Ridgeway: 'Grainger Industrial Supply'; Meridian: 'Grainger'. Category: MRO Supplies.
- Evidence: `*/11_finance_gl/vendors.csv`, `*/11_finance_gl/ap_vendor_invoices.csv`, `*/14_finance_gl/corporate_vendors.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.

### PORT-VEND-CINTAS — Cintas Corporation used by 3 portfolio companies under 3 different names
- Companies: Northfield, Keystone, Ridgeway
- Variants: Northfield: 'Cintas Corporation'; Keystone: 'Cintas Corp #472'; Ridgeway: 'CINTAS'. Category: Uniforms / Facility Services.
- Evidence: `*/11_finance_gl/vendors.csv`, `*/11_finance_gl/ap_vendor_invoices.csv`, `*/14_finance_gl/corporate_vendors.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.

### PORT-VEND-STAPLES — Staples Business Advantage used by 5 portfolio companies under 4 different names
- Companies: Meridian, Harborline, Castlebrook, Keystone, Ridgeway
- Variants: Meridian: 'Staples Business Advantage'; Harborline: 'Staples Advantage'; Castlebrook: 'Staples'; Keystone: 'STAPLES BUS ADV'; Ridgeway: 'Staples'. Category: Office Supplies.
- Evidence: `*/11_finance_gl/vendors.csv`, `*/11_finance_gl/ap_vendor_invoices.csv`, `*/14_finance_gl/corporate_vendors.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.

### PORT-VEND-WASTE_MGMT — Waste Management, Inc. used by 3 portfolio companies under 3 different names
- Companies: Northfield, Ridgeway, Harborline
- Variants: Northfield: 'Waste Management of Illinois'; Ridgeway: 'WM - Waste Management'; Harborline: 'Waste Management Inc of Florida'. Category: Waste / Recycling.
- Evidence: `*/11_finance_gl/vendors.csv`, `*/11_finance_gl/ap_vendor_invoices.csv`, `*/14_finance_gl/corporate_vendors.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.

### PORT-VEND-UPS — United Parcel Service used by 3 portfolio companies under 3 different names
- Companies: Northfield, Keystone, Ridgeway
- Variants: Northfield: 'UPS'; Keystone: 'United Parcel Service'; Ridgeway: 'UPS Freight / UPS'. Category: Parcel Freight.
- Evidence: `*/11_finance_gl/vendors.csv`, `*/11_finance_gl/ap_vendor_invoices.csv`, `*/14_finance_gl/corporate_vendors.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.

### PORT-VEND-FEDEX — FedEx Corporation used by 3 portfolio companies under 3 different names
- Companies: Northfield, Keystone, Ridgeway
- Variants: Northfield: 'FedEx'; Keystone: 'FEDEX'; Ridgeway: 'Federal Express'. Category: Parcel Freight.
- Evidence: `*/11_finance_gl/vendors.csv`, `*/11_finance_gl/ap_vendor_invoices.csv`, `*/14_finance_gl/corporate_vendors.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.

### PORT-VEND-SUNBELT — Sunbelt Rentals used by 2 portfolio companies under 2 different names
- Companies: Northfield, Ridgeway
- Variants: Northfield: 'Sunbelt Rentals'; Ridgeway: 'SUNBELT RENTALS INC'. Category: Equipment Rental.
- Evidence: `*/11_finance_gl/vendors.csv`, `*/11_finance_gl/ap_vendor_invoices.csv`, `*/14_finance_gl/corporate_vendors.csv`, `*/14_finance_gl/ap_vendor_invoices_indirect.csv`
- Expected action: Entity-resolve to one vendor; aggregate 12-month spend; negotiate portfolio rate.

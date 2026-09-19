(function () {
  const { rows, sum, count, money, fmtInt } = VistaUI;

  VistaUI.mount({
    nav: '#nav', content: '#content',
    modules: [
      { id: 'home', group: 'Agency', label: 'Summary',
        description: 'Castlebrook Agency · Scranton, PA · HawkSoft (partially adopted) + Excel. Note: most client/policy records only exist in MASTER_WORKBOOK_v7_FINAL (2).xlsx.',
        kpis: [
          { label: 'Clients (incl. dupes)', value: () => fmtInt(rows('clients').length) },
          { label: 'Policies', value: () => fmtInt(rows('policies').length) },
          { label: 'Premium (PREM col)', value: () => money(sum(rows('policies'), 'PREM')) },
          { label: 'Open claims', value: () => fmtInt(count(rows('claims'), (c) => c.STATUS !== 'Closed')) },
          { label: 'Invoice balance', value: () => money(sum(rows('invoices'), 'BALANCE')) },
        ],
        tables: [
          { title: 'Renewals (workbook sheet)', table: 'renewals', columns: ['POLICY_ID', 'CLIENT_ID', 'LINE_OF_BUSINESS', 'CARRIER_CODE', 'EXPIRATION_DATE', 'DAYS_TO_EXPIRATION', 'EXPIRING_PREMIUM', 'RENEWAL_STAGE'] },
          { title: 'Workflow event log', table: 'workflow_events', note: 'HawkSoft activity log was never exported.' },
        ] },
      { id: 'clients', group: 'Agency', label: 'Clients',
        tables: [
          { title: 'Clients', table: 'clients', columns: ['CLIENT_ID', 'CLIENT_NAME', 'DBA_NAME', 'INDUSTRY', 'BILLING_CITY', 'BILLING_STATE', 'PRODUCER_NAME', 'ACCOUNT_MANAGER_NA', 'CLIENT_SINCE', 'STATUS'],
            note: 'Duplicate client rows and truncated headers are present in the source workbook.',
            detail: { key: 'CLIENT_ID', titleCol: 'CLIENT_NAME', related: [
              { table: 'contacts', fk: 'CLIENT_ID', title: 'Contacts', columns: ['FULL_NAME', 'TITLE', 'EMAIL', 'PHONE'] },
              { table: 'policies', fk: 'CLIENT_ID', title: 'Policies', columns: ['POLICY_NUMBER', 'LINE_OF_BUSINESS', 'CARRIER_CODE', 'EXP_DATE', 'EFF_DATE', 'PREM', 'POLICY_STATUS'] },
              { table: 'invoices', fk: 'CLIENT_ID', title: 'Invoices', columns: ['INVOICE_ID', 'INVOICE_DATE', 'PREMIUM', 'BALANCE', 'STATUS'] },
              { table: 'claims', fk: 'CLIENT_ID', title: 'Claims', columns: ['CLAIM_ID', 'DATE_OF_LOSS', 'LOSS_DESCRIPTION', 'STATUS', 'INCURRED'] },
            ] } },
          { title: 'Contacts', table: 'contacts' },
          { title: 'Locations', table: 'locations' },
        ] },
      { id: 'policies', group: 'Agency', label: 'Policies',
        kpis: [
          { label: 'Vehicles scheduled', value: () => fmtInt(rows('vehicles').length) },
          { label: 'Drivers', value: () => fmtInt(rows('drivers').length) },
          { label: 'Coverage rows', value: () => fmtInt(rows('coverages').length) },
        ],
        tables: [
          { title: 'Policies', table: 'policies', note: 'Heads-up: EXP_DATE / EFF_DATE columns appear to be swapped in this export; PREM is annual premium.',
            columns: ['POLICY_ID', 'CLIENT_NAME', 'POLICY_NUMBER', 'LINE_OF_BUSINESS', 'CARRIER_NAME', 'EXP_DATE', 'EFF_DATE', 'PREM', 'COMM_EST', 'BILLING_TYPE', 'POLICY_STATUS'],
            detail: { key: 'POLICY_ID', titleCol: 'POLICY_NUMBER', related: [
              { table: 'coverages', fk: 'POLICY_ID', title: 'Coverages' },
              { table: 'vehicles', fk: 'POLICY_ID', title: 'Vehicles', columns: ['UNIT_NUMBER', 'YEAR', 'MAKE', 'MODEL', 'VIN', 'STATED_VALUE'] },
              { table: 'drivers', fk: 'POLICY_ID', title: 'Drivers', columns: ['DRIVER_NAME', 'LICENSE_STATE', 'CDL', 'VIOLATIONS_3YR'] },
              { table: 'payroll_exposures', fk: 'POLICY_ID', title: 'WC exposures' },
              { table: 'endorsement_requests', fk: 'POLICY_ID', title: 'Endorsements', columns: ['REQUEST_TYPE', 'REQUESTED_VALUE', 'EFFECTIVE_DATE', 'STATUS'] },
            ] } },
          { title: 'Vehicles', table: 'vehicles' },
          { title: 'Drivers', table: 'drivers' },
        ] },
      { id: 'marketing', group: 'Agency', label: 'Submissions & Quotes',
        tables: [
          { title: 'Submissions', table: 'submissions', detail: { key: 'SUBMISSION_ID', related: [{ table: 'quotes', fk: 'SUBMISSION_ID', title: 'Quotes' }] } },
          { title: 'Quotes', table: 'quotes' },
          { title: 'Declinations', table: 'declinations' },
          { title: 'Carriers', table: 'carriers' },
        ] },
      { id: 'service', group: 'Service', label: 'Certs / Endorsements / Claims',
        kpis: [
          { label: 'Cert requests', value: () => fmtInt(rows('certificate_requests').length) },
          { label: 'Endorsements', value: () => fmtInt(rows('endorsement_requests').length) },
          { label: 'Claims incurred', value: () => money(sum(rows('claims'), 'INCURRED')) },
        ],
        tables: [
          { title: 'Certificate requests', table: 'certificate_requests' },
          { title: 'Certificates issued', table: 'certificates_issued' },
          { title: 'Endorsement requests', table: 'endorsement_requests' },
          { title: 'Claims', table: 'claims', detail: { key: 'CLAIM_ID', titleCol: 'LOSS_DESCRIPTION', related: [] } },
          { title: 'Loss run requests', table: 'loss_run_requests' },
        ] },
      { id: 'money', group: 'Office', label: 'Billing & Money',
        kpis: [
          { label: 'Invoices', value: () => fmtInt(rows('invoices').length) },
          { label: 'Balance due', value: () => money(sum(rows('invoices'), 'BALANCE')) },
          { label: 'Payments in', value: () => money(sum(rows('payments'), 'AMOUNT')) },
        ],
        tables: [
          { title: 'Invoices', table: 'invoices', note: 'BASE_PREM vs PREMIUM: PREMIUM here is the invoice total.', detail: { key: 'INVOICE_ID', related: [{ table: 'payments', fk: 'INVOICE_ID', title: 'Payments' }] } },
          { title: 'Payments', table: 'payments' },
          { title: 'AR aging', table: 'ar_aging' },
          { title: 'Carrier statements', table: 'carrier_statements' },
          { title: 'Commission statements', table: 'commission_statements' },
          { title: 'Premium trust bank statement', table: 'bank_statement_premium_trust' },
        ] },
      { id: 'finance', group: 'Office', label: 'Bills & Vendors',
        tables: [
          { title: 'Vendor bills (AP)', table: 'ap_vendor_invoices' },
          { title: 'Vendors', table: 'vendors', note: 'Vendor name column is mislabeled CUSTOMER in the export.' },
          { title: 'Software subscriptions', table: 'software_subscriptions' },
          { title: 'Chart of accounts', table: 'chart_of_accounts' },
          { title: 'General ledger', table: 'general_ledger' },
        ] },
      { id: 'staff', group: 'Office', label: 'Staff & Licenses',
        tables: [
          { title: 'Employees', table: 'employees', detail: { key: 'EMPLOYEE_ID', titleCol: 'FULL_NAME', related: [{ table: 'payroll_register', fk: 'EMPLOYEE_ID', title: 'Payroll' }, { table: 'licenses', fk: 'EMPLOYEE_ID', title: 'Licenses' }] } },
          { title: 'Payroll register', table: 'payroll_register' },
          { title: 'Licenses', table: 'licenses' },
          { title: 'Carrier appointments', table: 'carrier_appointments' },
        ] },
      { id: 'docs', group: 'Office', label: 'Documents', docs: true, tables: [{ title: 'Document index', table: 'document_index' }] },
      { id: 'explorer', group: 'Office', label: 'All Files', explorer: true },
    ],
  });
})();

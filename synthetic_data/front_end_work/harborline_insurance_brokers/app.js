(function () {
  const { rows, sum, count, money, fmtInt } = VistaUI;

  VistaUI.mount({
    nav: '#nav', content: '#content',
    modules: [
      { id: 'home', group: 'Main', label: 'Dashboard',
        description: 'Harborline Insurance Brokers, Inc. · Tampa, FL · 29 employees · Vertafore AMS360',
        kpis: [
          { label: 'Clients', value: () => fmtInt(rows('clients').length) },
          { label: 'Policies in force', value: () => fmtInt(count(rows('policies'), (p) => p['Policy Status'] === 'In Force')) },
          { label: 'Annual premium', value: () => money(sum(rows('policies'), 'Annual Premium')) },
          { label: 'Open claims', value: () => fmtInt(count(rows('claims'), (c) => c.Status !== 'Closed')) },
          { label: 'AR outstanding', value: () => money(sum(rows('invoices'), 'Balance')) },
        ],
        tables: [
          { title: 'Upcoming renewals', table: 'renewals', filter: (r) => VistaUI.num(r['Days To Expiration']) <= 90,
            columns: ['Policy Id', 'Client Id', 'Line Of Business', 'Carrier Code', 'Expiration Date', 'Days To Expiration', 'Expiring Premium', 'Renewal Stage'] },
          { title: 'Recent activity', table: 'workflow_events', columns: ['Timestamp', 'Workflow Type', 'Object Id', 'Actor', 'Action', 'New State', 'Requires Review'] },
        ] },
      { id: 'clients', group: 'Main', label: 'Clients',
        kpis: [
          { label: 'Clients', value: () => fmtInt(rows('clients').length) },
          { label: 'Contacts', value: () => fmtInt(rows('contacts').length) },
          { label: 'Locations', value: () => fmtInt(rows('locations').length) },
        ],
        tables: [
          { title: 'Client book', table: 'clients', columns: ['Client Id', 'Client Name', 'Industry', 'Billing City', 'Billing State', 'Producer Name', 'Account Manager Name', 'Client Since', 'Status', 'Lines Of Business'],
            detail: { key: 'Client Id', titleCol: 'Client Name', related: [
              { table: 'contacts', fk: 'Client Id', title: 'Contacts', columns: ['Full Name', 'Title', 'Email', 'Phone'] },
              { table: 'policies', fk: 'Client Id', title: 'Policies', columns: ['Policy Number', 'Line Of Business', 'Carrier Code', 'Expiration Date', 'Annual Premium', 'Policy Status'] },
              { table: 'invoices', fk: 'Client Id', title: 'Invoices', columns: ['Invoice Id', 'Invoice Date', 'Total Amount', 'Balance', 'Status'] },
              { table: 'claims', fk: 'Client Id', title: 'Claims', columns: ['Claim Id', 'Date Of Loss', 'Loss Description', 'Status', 'Incurred'] },
              { table: 'account_activities', fk: 'Client Id', title: 'Activity log' },
            ] } },
          { title: 'Contacts', table: 'contacts' },
          { title: 'Account activities', table: 'account_activities', note: 'AMS360 activity export was not included in this data pull.' },
        ] },
      { id: 'policies', group: 'Main', label: 'Policies',
        kpis: [
          { label: 'Policies', value: () => fmtInt(rows('policies').length) },
          { label: 'Vehicles', value: () => fmtInt(rows('vehicles').length) },
          { label: 'Drivers', value: () => fmtInt(rows('drivers').length) },
          { label: 'Expected commission', value: () => money(sum(rows('policies'), 'Expected Commission')) },
        ],
        tables: [
          { title: 'Policies', table: 'policies', columns: ['Policy Id', 'Client Name', 'Policy Number', 'Line Of Business', 'Carrier Name', 'Effective Date', 'Expiration Date', 'Annual Premium', 'Billing Type', 'Policy Status'],
            detail: { key: 'Policy Id', titleCol: 'Policy Number', related: [
              { table: 'coverages', fk: 'Policy Id', title: 'Coverages' },
              { table: 'vehicles', fk: 'Policy Id', title: 'Vehicles', columns: ['Unit Number', 'Year', 'Make', 'Model', 'Vin'] },
              { table: 'drivers', fk: 'Policy Id', title: 'Drivers', columns: ['Driver Name', 'License State', 'Cdl', 'Violations 3yr'] },
              { table: 'endorsement_requests', fk: 'Policy Id', title: 'Endorsements', columns: ['Request Type', 'Requested Value', 'Effective Date', 'Status'] },
            ] } },
          { title: 'Coverages', table: 'coverages' },
          { title: 'Vehicles', table: 'vehicles' },
        ] },
      { id: 'marketing', group: 'Main', label: 'Marketing',
        tables: [
          { title: 'Submissions', table: 'submissions', detail: { key: 'Submission Id', related: [{ table: 'quotes', fk: 'Submission Id', title: 'Quotes' }, { table: 'declinations', fk: 'Submission Id', title: 'Declinations' }] } },
          { title: 'Quotes', table: 'quotes' },
          { title: 'Carriers', table: 'carriers' },
          { title: 'Underwriters', table: 'underwriters' },
        ] },
      { id: 'renewals', group: 'Main', label: 'Renewals',
        kpis: [
          { label: 'In pipeline', value: () => fmtInt(rows('renewals').length) },
          { label: 'Expiring premium', value: () => money(sum(rows('renewals'), 'Expiring Premium')) },
          { label: 'Open tasks', value: () => fmtInt(count(rows('renewal_tasks'), (t) => t.Status !== 'Complete')) },
        ],
        tables: [
          { title: 'Renewal pipeline', table: 'renewals', detail: { key: 'Renewal Id', related: [{ table: 'renewal_tasks', fk: 'Renewal Id', title: 'Tasks' }] } },
          { title: 'Tasks', table: 'renewal_tasks' },
        ] },
      { id: 'service', group: 'Main', label: 'Certificates & Claims',
        kpis: [
          { label: 'Cert requests', value: () => fmtInt(rows('certificate_requests').length) },
          { label: 'Claims', value: () => fmtInt(rows('claims').length) },
          { label: 'Total incurred', value: () => money(sum(rows('claims'), 'Incurred')) },
        ],
        tables: [
          { title: 'Certificate requests', table: 'certificate_requests', detail: { key: 'Certificate Request Id', related: [{ table: 'certificates_issued', fk: 'Certificate Request Id', title: 'Issued' }] } },
          { title: 'Endorsement requests', table: 'endorsement_requests' },
          { title: 'Claims', table: 'claims', detail: { key: 'Claim Id', titleCol: 'Loss Description', related: [] } },
          { title: 'Loss run requests', table: 'loss_run_requests' },
        ] },
      { id: 'accounting', group: 'Main', label: 'Accounting',
        kpis: [
          { label: 'Invoiced', value: () => money(sum(rows('invoices'), 'Total Amount')) },
          { label: 'Outstanding', value: () => money(sum(rows('invoices'), 'Balance')) },
          { label: 'Commission paid', value: () => money(sum(rows('commission_statements'), 'Commission Paid')) },
          { label: 'Unmatched statement lines', value: () => fmtInt(count(rows('commission_statements'), (r) => r['Match Status'] !== 'Matched')) },
        ],
        tables: [
          { title: 'AR aging (legacy workbook)', table: 'ar_aging' },
          { title: 'Invoices', table: 'invoices', detail: { key: 'Invoice Id', related: [{ table: 'payments', fk: 'Invoice Id', title: 'Payments' }, { table: 'installment_schedules', fk: 'Invoice Id', title: 'Installments' }] } },
          { title: 'Payments', table: 'payments' },
          { title: 'Commission statements', table: 'commission_statements' },
          { title: 'Carrier statements', table: 'carrier_statements' },
          { title: 'Producer commissions', table: 'producer_commissions' },
        ] },
      { id: 'finance', group: 'Main', label: 'Finance & Compliance',
        kpis: [
          { label: 'Software spend / yr', value: () => money(sum(rows('software_subscriptions'), 'Annual Cost')) },
          { label: 'AP invoices', value: () => fmtInt(rows('ap_vendor_invoices').length) },
          { label: 'Licensed producers', value: () => fmtInt(rows('licenses').length) },
        ],
        tables: [
          { title: 'General ledger', table: 'general_ledger' },
          { title: 'Vendor invoices', table: 'ap_vendor_invoices' },
          { title: 'Software subscriptions', table: 'software_subscriptions' },
          { title: 'Premium trust bank statement', table: 'bank_statement_premium_trust' },
          { title: 'Licenses', table: 'licenses' },
          { title: 'Carrier appointments', table: 'carrier_appointments' },
          { title: 'Surplus lines filings', table: 'surplus_lines_filings' },
        ] },
      { id: 'people', group: 'Main', label: 'People',
        tables: [
          { title: 'Employees', table: 'employees', detail: { key: 'Employee Id', titleCol: 'Full Name', related: [{ table: 'payroll_register', fk: 'Employee Id', title: 'Payroll' }, { table: 'pto_balances', fk: 'Employee Id', title: 'PTO' }] } },
          { title: 'Payroll register', table: 'payroll_register' },
          { title: 'PTO balances', table: 'pto_balances' },
        ] },
      { id: 'docs', group: 'Main', label: 'Documents', docs: true, tables: [{ title: 'Document index', table: 'document_index' }] },
      { id: 'explorer', group: 'Main', label: 'All Files', explorer: true },
    ],
  });
})();

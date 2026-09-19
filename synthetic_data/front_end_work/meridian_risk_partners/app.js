(function () {
  const { rows, sum, count, money, fmtInt } = VistaUI;
  const co = VISTA_DATA.company;

  VistaUI.mount({
    nav: '#nav', content: '#content',
    modules: [
      { id: 'home', group: 'Agency', label: 'Agency Overview', icon: '◈',
        description: `${co.company} · ${co.headquarters} · ${co.employees} employees · ${co.agency_management_system}`,
        kpis: [
          { label: 'Active clients', value: () => fmtInt(count(rows('clients'), (c) => c.status === 'Active')) },
          { label: 'Policies in force', value: () => fmtInt(count(rows('policies'), (p) => p.policy_status === 'In Force')) },
          { label: 'Written premium', value: () => money(sum(rows('policies'), 'annual_premium')) },
          { label: 'Expected commission', value: () => money(sum(rows('policies'), 'expected_commission')) },
          { label: 'Open claims', value: () => fmtInt(count(rows('claims'), (c) => c.status !== 'Closed')) },
          { label: 'AR balance', value: () => money(sum(rows('ar_aging'), 'total_balance')) },
        ],
        tables: [
          { title: 'Renewals due in next 90 days', table: 'renewals', filter: (r) => VistaUI.num(r.days_to_expiration) <= 90,
            columns: ['policy_id', 'client_id', 'line_of_business', 'carrier_code', 'expiration_date', 'days_to_expiration', 'expiring_premium', 'renewal_stage', 'renewal_strategy'] },
          { title: 'Recent workflow activity', table: 'workflow_events', columns: ['timestamp', 'workflow_type', 'object_id', 'actor', 'action', 'previous_state', 'new_state', 'requires_review'] },
        ] },
      { id: 'clients', group: 'Agency', label: 'Clients (CRM)', icon: '☷',
        kpis: [
          { label: 'Clients', value: () => fmtInt(rows('clients').length) },
          { label: 'Contacts', value: () => fmtInt(rows('contacts').length) },
          { label: 'Insured locations', value: () => fmtInt(rows('locations').length) },
          { label: 'Avg client revenue', value: () => money(sum(rows('clients'), 'annual_revenue') / Math.max(1, rows('clients').length)) },
        ],
        tables: [
          { title: 'Clients', table: 'clients', columns: ['client_id', 'client_name', 'dba_name', 'industry', 'billing_city', 'billing_state', 'producer_name', 'account_manager_name', 'client_since', 'status', 'lines_of_business'],
            detail: { key: 'client_id', titleCol: 'client_name', related: [
              { table: 'contacts', fk: 'client_id', title: 'Contacts', columns: ['full_name', 'title', 'email', 'phone', 'is_primary'] },
              { table: 'policies', fk: 'client_id', title: 'Policies', columns: ['policy_number', 'line_of_business', 'carrier_code', 'effective_date', 'expiration_date', 'annual_premium', 'policy_status'] },
              { table: 'locations', fk: 'client_id', title: 'Locations', columns: ['location_number', 'street', 'city', 'state', 'building_value', 'contents_value'] },
              { table: 'invoices', fk: 'client_id', title: 'Invoices', columns: ['invoice_id', 'invoice_date', 'total_amount', 'balance', 'status'] },
              { table: 'claims', fk: 'client_id', title: 'Claims', columns: ['claim_id', 'date_of_loss', 'loss_description', 'status', 'incurred'] },
              { table: 'account_activities', fk: 'client_id', title: 'Activity log', columns: ['activity_date', 'activity_type', 'logged_by', 'minutes_spent', 'closed'] },
            ] } },
          { title: 'Contacts', table: 'contacts' },
          { title: 'Account activities', table: 'account_activities' },
        ] },
      { id: 'policies', group: 'Service', label: 'Policies & Exposures', icon: '▤',
        kpis: [
          { label: 'Policies', value: () => fmtInt(rows('policies').length) },
          { label: 'Scheduled vehicles', value: () => fmtInt(rows('vehicles').length) },
          { label: 'Drivers', value: () => fmtInt(rows('drivers').length) },
          { label: 'Surplus lines', value: () => fmtInt(count(rows('policies'), (p) => p.surplus_lines === 'Y')) },
        ],
        tables: [
          { title: 'Policies', table: 'policies', columns: ['policy_id', 'client_name', 'policy_number', 'line_of_business', 'carrier_name', 'effective_date', 'expiration_date', 'annual_premium', 'commission_pct', 'billing_type', 'policy_status', 'new_or_renewal'],
            detail: { key: 'policy_id', titleCol: 'policy_number', related: [
              { table: 'coverages', fk: 'policy_id', title: 'Coverages' },
              { table: 'vehicles', fk: 'policy_id', title: 'Vehicles', columns: ['unit_number', 'year', 'make', 'model', 'vin', 'stated_value'] },
              { table: 'drivers', fk: 'policy_id', title: 'Drivers', columns: ['driver_name', 'license_state', 'cdl', 'violations_3yr', 'accidents_3yr'] },
              { table: 'payroll_exposures', fk: 'policy_id', title: 'WC payroll exposures' },
              { table: 'endorsement_requests', fk: 'policy_id', title: 'Endorsements', columns: ['request_type', 'requested_value', 'effective_date', 'status', 'premium_change'] },
              { table: 'invoices', fk: 'policy_id', title: 'Invoices', columns: ['invoice_id', 'invoice_date', 'total_amount', 'balance', 'status'] },
            ] } },
          { title: 'Vehicle schedule', table: 'vehicles' },
          { title: 'Drivers', table: 'drivers' },
        ] },
      { id: 'marketing', group: 'Service', label: 'Marketing & Submissions', icon: '➚',
        kpis: [
          { label: 'Submissions', value: () => fmtInt(rows('submissions').length) },
          { label: 'Quotes received', value: () => fmtInt(rows('quotes').length) },
          { label: 'Declinations', value: () => fmtInt(rows('declinations').length) },
          { label: 'Appointed carriers', value: () => fmtInt(rows('carriers').length) },
        ],
        tables: [
          { title: 'Submissions', table: 'submissions', detail: { key: 'submission_id', related: [{ table: 'quotes', fk: 'submission_id', title: 'Quotes' }] } },
          { title: 'Quotes', table: 'quotes' },
          { title: 'Declinations', table: 'declinations' },
          { title: 'Carriers', table: 'carriers' },
        ] },
      { id: 'renewals', group: 'Service', label: 'Renewals', icon: '↻',
        kpis: [
          { label: 'Renewals tracked', value: () => fmtInt(rows('renewals').length) },
          { label: 'Expiring premium', value: () => money(sum(rows('renewals'), 'expiring_premium')) },
          { label: 'Open tasks', value: () => fmtInt(count(rows('renewal_tasks'), (t) => t.status !== 'Complete')) },
        ],
        tables: [
          { title: 'Renewal pipeline', table: 'renewals', detail: { key: 'renewal_id', related: [{ table: 'renewal_tasks', fk: 'renewal_id', title: 'Tasks' }] } },
          { title: 'Renewal tasks', table: 'renewal_tasks' },
        ] },
      { id: 'certs', group: 'Service', label: 'Certificates & Endorsements', icon: '✓',
        kpis: [
          { label: 'Cert requests', value: () => fmtInt(rows('certificate_requests').length) },
          { label: 'Avg turnaround (h)', value: () => Math.round(sum(rows('certificate_requests'), 'turnaround_hours') / Math.max(1, count(rows('certificate_requests'), (c) => c.turnaround_hours))) },
          { label: 'Endorsement requests', value: () => fmtInt(rows('endorsement_requests').length) },
        ],
        tables: [
          { title: 'Certificate requests', table: 'certificate_requests', detail: { key: 'certificate_request_id', related: [{ table: 'certificates_issued', fk: 'certificate_request_id', title: 'Issued certificates' }] } },
          { title: 'Certificates issued', table: 'certificates_issued' },
          { title: 'Endorsement requests', table: 'endorsement_requests' },
        ] },
      { id: 'claims', group: 'Service', label: 'Claims', icon: '⚠',
        kpis: [
          { label: 'Claims', value: () => fmtInt(rows('claims').length) },
          { label: 'Open', value: () => fmtInt(count(rows('claims'), (c) => c.status !== 'Closed')) },
          { label: 'Total incurred', value: () => money(sum(rows('claims'), 'incurred')) },
          { label: 'Outstanding reserves', value: () => money(sum(rows('claims'), 'outstanding_reserve')) },
        ],
        tables: [
          { title: 'Claims', table: 'claims', detail: { key: 'claim_id', titleCol: 'loss_description', related: [] } },
          { title: 'Loss run requests', table: 'loss_run_requests' },
        ] },
      { id: 'billing', group: 'Accounting', label: 'Billing & AR', icon: '$',
        kpis: [
          { label: 'Invoices', value: () => fmtInt(rows('invoices').length) },
          { label: 'Billed', value: () => money(sum(rows('invoices'), 'total_amount')) },
          { label: 'Outstanding', value: () => money(sum(rows('invoices'), 'balance')) },
          { label: 'Over 90 days', value: () => money(sum(rows('ar_aging'), 'over_90')) },
        ],
        tables: [
          { title: 'AR aging', table: 'ar_aging' },
          { title: 'Invoices', table: 'invoices', detail: { key: 'invoice_id', related: [{ table: 'payments', fk: 'invoice_id', title: 'Payments' }, { table: 'installment_schedules', fk: 'invoice_id', title: 'Installments' }] } },
          { title: 'Payments received', table: 'payments' },
        ] },
      { id: 'commissions', group: 'Accounting', label: 'Carrier Payables & Commissions', icon: '⇄',
        kpis: [
          { label: 'Statement lines', value: () => fmtInt(rows('carrier_statements').length) },
          { label: 'Commission paid', value: () => money(sum(rows('commission_statements'), 'commission_paid')) },
          { label: 'Commission variance', value: () => money(sum(rows('commission_statements'), 'variance')) },
          { label: 'Unmatched rows', value: () => fmtInt(count(rows('commission_statements'), (r) => r.match_status !== 'Matched') + count(rows('carrier_statements'), (r) => r.match_status !== 'Matched')) },
        ],
        tables: [
          { title: 'Commission statements', table: 'commission_statements' },
          { title: 'Carrier statements', table: 'carrier_statements' },
          { title: 'Producer commissions', table: 'producer_commissions' },
        ] },
      { id: 'compliance', group: 'Accounting', label: 'Compliance & Licensing', icon: '§',
        tables: [
          { title: 'Producer licenses', table: 'licenses' },
          { title: 'Carrier appointments', table: 'carrier_appointments' },
          { title: 'Surplus lines filings', table: 'surplus_lines_filings' },
        ] },
      { id: 'finance', group: 'Accounting', label: 'Finance & GL', icon: '≡',
        kpis: [
          { label: 'GL entries', value: () => fmtInt(VISTA_DATA.tables.general_ledger.total) },
          { label: 'AP invoices', value: () => fmtInt(rows('ap_vendor_invoices').length) },
          { label: 'Software spend / yr', value: () => money(sum(rows('software_subscriptions'), 'annual_cost')) },
        ],
        tables: [
          { title: 'General ledger', table: 'general_ledger' },
          { title: 'Chart of accounts', table: 'chart_of_accounts' },
          { title: 'Vendor invoices (AP)', table: 'ap_vendor_invoices' },
          { title: 'Vendors', table: 'vendors' },
          { title: 'Software subscriptions', table: 'software_subscriptions' },
          { title: 'Premium trust bank statement', table: 'bank_statement_premium_trust' },
        ] },
      { id: 'hr', group: 'People', label: 'HR & Payroll', icon: '☺',
        kpis: [
          { label: 'Employees', value: () => fmtInt(count(rows('employees'), (e) => e.status === 'Active')) },
          { label: 'Annual salaries', value: () => money(sum(rows('employees'), 'annual_salary')) },
        ],
        tables: [
          { title: 'Employees', table: 'employees', detail: { key: 'employee_id', titleCol: 'full_name', related: [{ table: 'payroll_register', fk: 'employee_id', title: 'Payroll' }, { table: 'pto_balances', fk: 'employee_id', title: 'PTO' }, { table: 'licenses', fk: 'employee_id', title: 'Licenses' }] } },
          { title: 'Payroll register', table: 'payroll_register' },
          { title: 'PTO balances', table: 'pto_balances' },
        ] },
      { id: 'docs', group: 'Records', label: 'Documents & Emails', icon: '✉', docs: true, tables: [{ title: 'Document index', table: 'document_index' }] },
      { id: 'events', group: 'Records', label: 'Workflow Events', icon: '⏱', tables: [{ title: 'Workflow event log', table: 'workflow_events' }] },
      { id: 'explorer', group: 'Records', label: 'All Data Files', icon: '▦', explorer: true },
    ],
  });
})();

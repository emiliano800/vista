// Serialises the deterministic synthetic portfolio (src/web/public/lib/seed.js)
// into fixtures the backend seed script loads:
//   synthetic_data/portfolio_demo/portfolio.json   Harbor + Summit + Cedar canonical records,
//                                                  tasks, opportunities, findings, agents, runs, activity
//   src/web/public/demo/cedar/*.csv                Cedar's messy source exports for the import demo
// Re-run after changing seed.js:  node scripts/export_portfolio_fixture.mjs
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const lib = join(root, "src/web/public/lib");
const { seedState, generateCompany, COMPANY_SPECS, CATALOG } = await import(
  join(lib, "seed.js")
);
const { cedarSampleFiles, CEDAR_PROFILE } = await import(
  join(lib, "importer.js")
);

const state = seedState();
const cedar = generateCompany(COMPANY_SPECS.cedar);
const fixture = {
  generated_from: "src/web/public/lib/seed.js",
  catalog: CATALOG,
  companies: [
    ...state.companies,
    { ...cedar, description: CEDAR_PROFILE.description },
  ],
  tasks: state.tasks,
  opportunities: state.opportunities,
  findings: state.findings,
  agents: state.agents,
  runs: state.runs,
  activity: state.activity,
  nextIds: state.nextIds,
};
const out = join(root, "synthetic_data/portfolio_demo");
mkdirSync(out, { recursive: true });
writeFileSync(join(out, "portfolio.json"), JSON.stringify(fixture));

const cedarDir = join(root, "src/web/public/demo/cedar");
mkdirSync(cedarDir, { recursive: true });
const files = cedarSampleFiles();
for (const f of files) writeFileSync(join(cedarDir, f.name), f.text);
writeFileSync(
  join(cedarDir, "manifest.json"),
  JSON.stringify(
    {
      profile: CEDAR_PROFILE,
      files: files.map(({ name, type, group }) => ({ name, type, group })),
    },
    null,
    2,
  ),
);
console.log(
  `wrote ${out}/portfolio.json (${fixture.companies.map((c) => `${c.id}:${c.customers.length}c/${c.invoices.length}i/${c.purchases.length}p`).join(", ")}) and ${files.length} Cedar files`,
);

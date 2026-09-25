// The company workspace's app.js as a plain script for jsdom's `eval`: its one ES import
// (/lib/graph-review.js, shared with the firm's deployment view) is inlined as an IIFE so the
// tests keep exercising the real module without a module loader.
import fs from "node:fs";

const app = fs.readFileSync(new URL("../public/account/app.js", import.meta.url), "utf8");
const lib = fs.readFileSync(new URL("../public/lib/graph-review.js", import.meta.url), "utf8");

const IMPORT = /^import \{([^}]+)\} from "\/lib\/graph-review.js";\n/m;
const wanted = app.match(IMPORT)[1];
const inlined = lib
  .replace(/^import .*$/gm, "")
  .replace(/^export (async )?function /gm, "$1function ")
  .replace(/^export const /gm, "const ");
const exported = [...lib.matchAll(/^export (?:async )?(?:function|const) (\w+)/gm)].map((m) => m[1]);

export const source = app.replace(IMPORT, `const {${wanted}} = (() => {\n${inlined}\nreturn { ${exported.join(", ")} };\n})();\n`);

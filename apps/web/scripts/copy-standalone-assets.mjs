import { cpSync, existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const standalone = resolve(root, ".next/standalone/apps/web");
const staticSource = resolve(root, ".next/static");
const staticTarget = resolve(standalone, ".next/static");

if (!existsSync(standalone) || !existsSync(staticSource)) {
  throw new Error("Next.js standalone or static build output is missing");
}
mkdirSync(resolve(standalone, ".next"), { recursive: true });
cpSync(staticSource, staticTarget, { recursive: true, force: true });
const publicSource = resolve(root, "public");
if (existsSync(publicSource)) cpSync(publicSource, resolve(standalone, "public"), { recursive: true, force: true });
console.log("Copied Next.js static and public assets into the standalone runtime.");

#!/usr/bin/env node
/**
 * Fails if built client bundles contain anything that looks like a secret.
 * Scans: apps/web/.next/static, apps/desktop/dist (after building them).
 * Also checks that no value from the local .env other than PUBLIC client
 * variables leaked into a bundle.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const targets = ["apps/web/.next/static", "apps/desktop/dist"].map((p) => join(root, p));

const PATTERNS = [
  ["private key", /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ["AWS access key", /\bAKIA[0-9A-Z]{16}\b/],
  ["Anthropic/OpenAI-style API key", /\bsk-(ant-)?[A-Za-z0-9_-]{20,}\b/],
  ["GitHub token", /\bgh[pousr]_[A-Za-z0-9]{30,}\b/],
  [
    "connection URL with credentials",
    /\b(postgres(ql)?(\+\w+)?|amqp|redis):\/\/[^\s"'`/:@]+:[^\s"'`@]+@/,
  ],
];

const PUBLIC_PREFIXES = ["NEXT_PUBLIC_", "VITE_"];

function envSecrets() {
  const file = join(root, ".env");
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf8")
    .split(/\r?\n/)
    .map((line) => line.match(/^([A-Z0-9_]+)=(.*)$/))
    .filter(Boolean)
    .filter(
      ([, key, value]) => !PUBLIC_PREFIXES.some((p) => key.startsWith(p)) && value.length >= 12,
    )
    .map(([, key, value]) => [key, value]);
}

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (/\.(js|mjs|cjs|html|css|json|map|txt)$/.test(entry)) yield full;
  }
}

const secrets = envSecrets();
const findings = [];
let scanned = 0;
const existing = targets.filter(existsSync);
if (existing.length === 0) {
  console.error("No build output found. Build the web and desktop apps first.");
  process.exit(2);
}

for (const target of existing) {
  for (const file of walk(target)) {
    scanned += 1;
    const text = readFileSync(file, "utf8");
    for (const [name, pattern] of PATTERNS) {
      if (pattern.test(text)) findings.push(`${relative(root, file)}: ${name}`);
    }
    for (const [key, value] of secrets) {
      if (text.includes(value)) findings.push(`${relative(root, file)}: value of ${key} from .env`);
    }
  }
}

if (findings.length) {
  console.error(`Secret scan FAILED (${findings.length} finding(s)):\n  ${findings.join("\n  ")}`);
  process.exit(1);
}
console.log(
  `Secret scan passed: ${scanned} files in ${existing.map((t) => relative(root, t)).join(", ")}; ` +
    `${secrets.length} private .env values checked.`,
);

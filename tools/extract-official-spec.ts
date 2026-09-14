#!/usr/bin/env bun
/**
 * Normalize KIPRIS Plus 입출력값 정보 exports into a small per-service spec.
 *
 * Usage:
 *   bun run tools/extract-official-spec.ts [rawDir]
 *
 * rawDir defaults to specs/official/raw (untracked — the portal exports are not
 * redistributed). Output goes to specs/official/<service-id>.json, which is
 * committed and read by tools/build-catalog.ts as the authoritative source for
 * request parameter names.
 *
 * Rule of the house, same as the catalog build: never invent data. A service
 * name that is not in SERVICE_ID_BY_NAME aborts the run instead of guessing an
 * id — a wrong id would silently overwrite another service's parameters.
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Param {
  name: string;
  desc: string;
  note: string;
}

interface OfficialOperation {
  id: string;
  name: string;
  deprecated: boolean;
  params: Param[];
  response_fields: string[];
}

interface OfficialSpec {
  service_id: string;
  service_name: string;
  source_file: string;
  extracted_at: string;
  operations: OfficialOperation[];
  /** Operations the portal lists without an ASCII identifier; they cannot be called. */
  unnamed_operations: { name: string; note: string }[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const REPO_ROOT = resolve(dirname(Bun.fileURLToPath(import.meta.url)), "..");
const DEFAULT_RAW_DIR = join(REPO_ROOT, "specs/official/raw");
const OUT_DIR = join(REPO_ROOT, "specs/official");

/**
 * Portal 서비스명 → our catalog service id. The portal's display name is the
 * only identifier an export carries, and it does not match the catalog file
 * names, so the mapping is explicit and additive: one entry per export.
 */
const SERVICE_ID_BY_NAME: Record<string, string> = {
  "상표 출원 속보": "trademark",
};

/** Header cells of the export, in the order the portal writes them. */
const COLUMNS = [
  "순번",
  "서비스명",
  "API유형",
  "API분류",
  "API오퍼레이션명",
  "입출력",
  "레벨",
  "구분",
  "데이터항목",
  "설명",
  "부가정보",
] as const;

const UNNAMED_NOTE =
  "포털 명세에 ASCII 오퍼레이션 id가 없다. 호출 불가 — 확인되면 카탈로그에 추가한다.";

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

/** Portal exports are UTF-8 with a BOM; JSON.parse chokes on the leading ﻿. */
function readJsonRows(path: string): string[][] {
  const text = readFileSync(path, "utf8").replace(/^﻿/, "");
  const parsed = JSON.parse(text);
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error(`${basename(path)}: expected a non-empty array of rows`);
  }
  const header = parsed[0] as string[];
  for (const [i, expected] of COLUMNS.entries()) {
    if (header[i] !== expected) {
      throw new Error(
        `${basename(path)}: unexpected column ${i} — got ${JSON.stringify(header[i])}, expected ${JSON.stringify(expected)}`,
      );
    }
  }
  // Short rows are normal: 부가정보 (and sometimes 설명) is simply absent.
  return (parsed.slice(1) as string[][]).map((row) =>
    COLUMNS.map((_, i) => (row[i] ?? "").toString()),
  );
}

/** `단어(폐기예정) - getWordSearch` → id `getWordSearch`, name `단어`, deprecated. */
function splitOperationLabel(label: string): { id: string | null; name: string; deprecated: boolean } {
  const sep = label.lastIndexOf(" - ");
  const id = sep === -1 ? null : label.slice(sep + 3).trim();
  const korean = sep === -1 ? label : label.slice(0, sep);
  const deprecated = /폐기\s*예정/.test(korean);
  const name = korean.replace(/\(\s*폐기\s*예정\s*\)/g, "").trim();
  return { id: id === "" ? null : id, name, deprecated };
}

/**
 * `-` (and an empty cell) is the portal's placeholder for "this operation takes
 * no input", not a parameter called `-`.
 */
function isPlaceholder(item: string): boolean {
  return item === "" || item === "-";
}

function extract(path: string): OfficialSpec {
  const rows = readJsonRows(path);

  const names = new Set(rows.map((r) => r[1].trim()));
  if (names.size !== 1) {
    throw new Error(`${basename(path)}: expected one 서비스명, found ${[...names].join(", ")}`);
  }
  const serviceName = [...names][0];
  const serviceId = SERVICE_ID_BY_NAME[serviceName];
  if (serviceId === undefined) {
    throw new Error(
      `${basename(path)}: 서비스명 "${serviceName}" is not mapped to a catalog service id. ` +
        "Add it to SERVICE_ID_BY_NAME in tools/extract-official-spec.ts.",
    );
  }

  const operations = new Map<string, OfficialOperation>();
  const unnamed = new Map<string, { name: string; note: string }>();

  for (const row of rows) {
    const { id, name, deprecated } = splitOperationLabel(row[4].trim());
    if (id === null) {
      if (!unnamed.has(name)) unnamed.set(name, { name, note: UNNAMED_NOTE });
      continue;
    }
    let op = operations.get(id);
    if (op === undefined) {
      op = { id, name, deprecated, params: [], response_fields: [] };
      operations.set(id, op);
    }
    // Nested OUT fields are indented in 데이터항목; the nesting is in 레벨, which
    // the catalog's flat field list does not carry.
    const item = row[8].trim();
    if (isPlaceholder(item)) continue;
    if (row[5].trim() === "IN") {
      if (!op.params.some((p) => p.name === item)) {
        op.params.push({ name: item, desc: row[9].trim(), note: row[10].trim() });
      }
    } else if (!op.response_fields.includes(item)) {
      op.response_fields.push(item);
    }
  }

  return {
    service_id: serviceId,
    service_name: serviceName,
    source_file: basename(path),
    extracted_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    operations: [...operations.values()],
    unnamed_operations: [...unnamed.values()],
  };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function write(spec: OfficialSpec): string {
  const outPath = join(OUT_DIR, `${spec.service_id}.json`);
  // extracted_at would churn the committed file on every run, so keep the
  // previous stamp whenever nothing else about the spec changed.
  if (existsSync(outPath)) {
    try {
      const prev = JSON.parse(readFileSync(outPath, "utf8"));
      const a = JSON.stringify({ ...prev, extracted_at: null });
      const b = JSON.stringify({ ...spec, extracted_at: null });
      if (a === b && typeof prev.extracted_at === "string") spec.extracted_at = prev.extracted_at;
    } catch {
      // Unreadable previous spec: fall through and write a fresh one.
    }
  }
  writeFileSync(outPath, JSON.stringify(spec, null, 2) + "\n", "utf8");
  return outPath;
}

function main(): void {
  const rawDir = resolve(process.argv[2] ?? DEFAULT_RAW_DIR);
  if (!existsSync(rawDir)) {
    console.error(
      `raw export directory not found: ${rawDir}\n` +
        "Download 입출력값 정보 for the service from https://plus.kipris.or.kr and drop the " +
        "JSON there (see specs/official/README.md).",
    );
    process.exit(1);
  }
  const files = readdirSync(rawDir)
    .filter((f) => f.endsWith(".json"))
    .sort();
  if (files.length === 0) {
    console.error(`no portal exports (*.json) in ${rawDir}`);
    process.exit(1);
  }

  mkdirSync(OUT_DIR, { recursive: true });
  for (const f of files) {
    const spec = extract(join(rawDir, f));
    const outPath = write(spec);
    const params = spec.operations.reduce((n, o) => n + o.params.length, 0);
    console.log(
      `${f} -> ${outPath.slice(REPO_ROOT.length + 1)}: ${spec.service_id}, ` +
        `${spec.operations.length} operations, ${params} params, ` +
        `${spec.unnamed_operations.length} without an id`,
    );
  }
}

main();

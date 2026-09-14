#!/usr/bin/env bun
/**
 * Build a machine-readable catalog of the KIPRIS Plus Open API from the
 * markdown service docs of nuri428/kipris_skill (MIT).
 *
 * Usage:
 *   bun run tools/build-catalog.ts [srcDir]
 *
 * srcDir defaults to /tmp/kipris_ref/docs/services.
 * Output goes to skills/kipris/references/catalog/.
 *
 * Rule of the house: never invent data. Anything absent in the source is
 * emitted as null / "" and listed in the validation report — this catalog
 * drives real API calls, and a fabricated ServicePath or parameter name
 * produces a silently wrong request.
 */

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type PathConfidence = "verified" | "portal" | "github" | "unknown";
type Gateway = "openapi" | "kipo" | "both" | "unknown";

interface Field {
  name: string;
  desc: string;
  note: string;
}

interface Operation {
  id: string | null;
  name: string;
  deprecated: boolean;
  params: Field[];
  response_fields: Field[];
}

interface Service {
  id: string;
  name: string;
  service_path: string | null;
  path_confidence: PathConfidence;
  gateway: Gateway;
  auth_param: string | null;
  operations: Operation[];
  /** Counts the source file declares in its header block (may disagree with reality). */
  declared_operation_count: number | null;
  declared_deprecated_count: number | null;
}

interface Anomaly {
  kind: string;
  service: string;
  detail: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_SRC = "/tmp/kipris_ref/docs/services";
const REPO_ROOT = resolve(dirname(Bun.fileURLToPath(import.meta.url)), "..");
const OUT_DIR = join(REPO_ROOT, "skills/kipris/references/catalog");
const SERVICES_DIR = join(OUT_DIR, "services");
const REPORT_PATH = "/tmp/kipris-catalog-report.md";

const TABLE_HEADER_CELLS = new Set(["항목명", "설명", "비고"]);

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/** Undo the escaping a markdown table forces on cell content. */
function unescapeCell(raw: string): string {
  return raw
    .replace(/&#124;/g, "|")
    .replace(/\\\|/g, "|")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .trim();
}

/** Strip surrounding backticks and whitespace from an identifier. */
function stripTicks(raw: string): string {
  return raw.trim().replace(/^`+/, "").replace(/`+$/, "").trim();
}

function isSeparatorRow(line: string): boolean {
  return /^\|[\s|:-]+\|$/.test(line.trim());
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

// ---------------------------------------------------------------------------
// Derived-field rules
// ---------------------------------------------------------------------------

/**
 * The 서비스 경로 bullet carries the path plus a confidence marker. Real markers
 * seen in the source: `(포털 확인)`, `⚠️ 미검증`, `(미검증)`, `(검증됨)`, none.
 */
function classifyConfidence(marker: string): PathConfidence {
  if (/미검증/.test(marker)) return "unknown";
  if (/API\s*키로\s*검증|검증\s*완료|검증됨/.test(marker)) return "verified";
  if (/포털\s*확인/.test(marker)) return "portal";
  if (/GitHub|코드\s*검색/i.test(marker)) return "github";
  return "unknown";
}

/**
 * A service's own markdown does not always carry the confidence marker, but the
 * source repo's CLAUDE.md records which paths were confirmed with a real API key
 * ("API 키로 검증 완료"). Without this, the most-used service in the catalog reads
 * as `unknown` and every patent search warns the user about an unverified path.
 */
const CONFIDENCE_OVERRIDES: Record<string, PathConfidence> = {
  patent_utility: "verified", // CLAUDE.md: patUtiModInfoSearchSevice, gateways A + B
};

function classifyGateway(raw: string | null): Gateway {
  if (raw === null) return "unknown";
  const hasOpenApi = /OpenAPI/i.test(raw);
  const hasKipo = /KIPO/i.test(raw);
  if (hasOpenApi && hasKipo) return "both";
  if (hasOpenApi) return "openapi";
  if (hasKipo) return "kipo";
  return "unknown";
}

function classifyAuthParam(raw: string | null): string | null {
  if (raw === null) return null;
  const m = raw.match(/`([^`]+)`/);
  if (m) return m[1].trim();
  if (/ServiceKey/.test(raw)) return "ServiceKey";
  if (/accessKey/.test(raw)) return "accessKey";
  return null;
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

interface ParsedFile {
  service: Service;
  anomalies: Anomaly[];
}

function parseHeaderBullets(lines: string[]): Map<string, string> {
  const bullets = new Map<string, string>();
  for (const line of lines) {
    if (line.startsWith("## ")) break;
    const m = line.match(/^\s*[-*]\s+\*\*(.+?)\*\*\s*[:：]\s*(.*)$/);
    if (m) bullets.set(m[1].trim(), m[2].trim());
  }
  return bullets;
}

function parseTable(segment: string[], heading: RegExp): Field[] | null {
  const start = segment.findIndex((l) => l.startsWith("### ") && heading.test(l));
  if (start === -1) return null;

  const fields: Field[] = [];
  for (let i = start + 1; i < segment.length; i++) {
    const line = segment[i];
    if (line.startsWith("### ") || line.startsWith("## ")) break;
    const trimmed = line.trim();
    if (!trimmed.startsWith("|")) continue;
    if (isSeparatorRow(trimmed)) continue;

    const cells = splitRow(trimmed);
    // Header row of the table itself.
    if (cells.every((c) => TABLE_HEADER_CELLS.has(c))) continue;

    const name = stripTicks(unescapeCell(cells[0] ?? ""));
    // A row with no identifier carries no usable data.
    if (name === "") continue;
    fields.push({
      name,
      desc: unescapeCell(cells[1] ?? ""),
      note: unescapeCell(cells[2] ?? ""),
    });
  }
  return fields;
}

function parseOperations(lines: string[], serviceId: string, anomalies: Anomaly[]): Operation[] {
  const headingIdx: number[] = [];
  lines.forEach((l, i) => {
    if (l.startsWith("## ")) headingIdx.push(i);
  });

  const operations: Operation[] = [];
  for (let k = 0; k < headingIdx.length; k++) {
    const segment = lines.slice(headingIdx[k], headingIdx[k + 1] ?? lines.length);
    const headingRaw = segment[0].replace(/^##\s+/, "").trim();
    const deprecated = /폐기\s*예정/.test(headingRaw);
    // The deprecation marker is captured in `deprecated`; other parentheticals
    // (e.g. `(일본)`, `(2021)`) disambiguate operations and must survive.
    const name = headingRaw.replace(/\(\s*폐기\s*예정\s*\)/g, "").trim();

    // The operation id sits on its own line, backticked, right after the heading.
    let id: string | null = null;
    for (let i = 1; i < segment.length; i++) {
      const t = segment[i].trim();
      if (t === "") continue;
      const m = t.match(/^`([^`]+)`$/);
      if (m) id = stripTicks(m[1]);
      break;
    }
    if (id === null) {
      anomalies.push({
        kind: "operation_without_id",
        service: serviceId,
        detail: `operation "${name}" has no backticked operation id in the source`,
      });
    }

    const params = parseTable(segment, /\(IN\)/);
    const responseFields = parseTable(segment, /\(OUT\)/);

    if (params === null) {
      anomalies.push({
        kind: "operation_without_in_table",
        service: serviceId,
        detail: `operation "${name}" (${id ?? "no id"}) has no 요청 파라미터 (IN) section`,
      });
    } else if (params.length === 0) {
      anomalies.push({
        kind: "operation_without_params",
        service: serviceId,
        detail: `operation "${name}" (${id ?? "no id"}) has an empty IN table`,
      });
    }

    if (responseFields === null) {
      anomalies.push({
        kind: "operation_without_out_table",
        service: serviceId,
        detail: `operation "${name}" (${id ?? "no id"}) has no 응답 파라미터 (OUT) section`,
      });
    } else if (responseFields.length === 0) {
      anomalies.push({
        kind: "operation_without_response_fields",
        service: serviceId,
        detail: `operation "${name}" (${id ?? "no id"}) has an empty OUT table`,
      });
    }

    operations.push({
      id,
      name,
      deprecated,
      params: params ?? [],
      response_fields: responseFields ?? [],
    });
  }
  return operations;
}

function parseServiceFile(path: string, fileName: string): ParsedFile {
  const serviceId = fileName.replace(/\.md$/, "");
  const anomalies: Anomaly[] = [];
  const lines = readFileSync(path, "utf8").replace(/\r\n/g, "\n").split("\n");

  const h1 = lines.find((l) => /^#\s+/.test(l));
  if (h1 === undefined) {
    anomalies.push({
      kind: "missing_title",
      service: serviceId,
      detail: "file has no H1 title; service name falls back to the file name",
    });
  }
  const name = h1 ? h1.replace(/^#\s+/, "").trim() : serviceId;

  const bullets = parseHeaderBullets(lines);
  if (bullets.size === 0) {
    anomalies.push({
      kind: "missing_header_block",
      service: serviceId,
      detail: "no `- **key**: value` header bullets found before the first operation",
    });
  }

  // --- service path + confidence -------------------------------------------
  const pathRaw = bullets.get("서비스 경로") ?? null;
  let servicePath: string | null = null;
  let pathConfidence: PathConfidence = "unknown";

  if (pathRaw === null) {
    anomalies.push({
      kind: "missing_service_path",
      service: serviceId,
      detail: "no 서비스 경로 bullet; service_path is null and cannot be called",
    });
  } else {
    const ticked = [...pathRaw.matchAll(/`([^`]+)`/g)].map((m) => stripTicks(m[1]));
    if (ticked.length === 0) {
      anomalies.push({
        kind: "unparseable_service_path",
        service: serviceId,
        detail: `서비스 경로 bullet has no backticked path: ${JSON.stringify(pathRaw)}`,
      });
    } else {
      servicePath = ticked[0];
      if (ticked.length > 1) {
        anomalies.push({
          kind: "multiple_service_paths",
          service: serviceId,
          detail: `source lists ${ticked.length} paths (${ticked.join(", ")}); the schema holds one, so the first is used`,
        });
      }
      // Confidence marker = whatever follows the first backticked path, up to
      // the next path (so a multi-path bullet is not read as one blob).
      const firstEnd = pathRaw.indexOf("`", pathRaw.indexOf("`") + 1) + 1;
      const rest = pathRaw.slice(firstEnd);
      const marker = ticked.length > 1 ? rest.slice(0, rest.indexOf("`")) : rest;
      pathConfidence = classifyConfidence(marker);
      if (marker.trim() === "") {
        anomalies.push({
          kind: "service_path_without_confidence_marker",
          service: serviceId,
          detail: `path \`${servicePath}\` carries no confidence marker; recorded as "unknown"`,
        });
      }
    }
  }

  // --- gateway --------------------------------------------------------------
  const gatewayRaw = bullets.get("게이트웨이") ?? null;
  const gateway = classifyGateway(gatewayRaw);
  if (gatewayRaw === null) {
    anomalies.push({
      kind: "missing_gateway",
      service: serviceId,
      detail: 'no 게이트웨이 bullet; recorded as "unknown"',
    });
  } else if (gateway === "unknown") {
    anomalies.push({
      kind: "unrecognized_gateway",
      service: serviceId,
      detail: `게이트웨이 bullet matched neither OpenAPI nor KIPO: ${JSON.stringify(gatewayRaw)}`,
    });
  }

  // --- auth param -----------------------------------------------------------
  const authRaw = bullets.get("인증 키") ?? null;
  const authParam = classifyAuthParam(authRaw);
  if (authRaw === null) {
    anomalies.push({
      kind: "missing_auth_param",
      service: serviceId,
      detail: "no 인증 키 bullet; auth_param is null",
    });
  } else if (authParam === null) {
    anomalies.push({
      kind: "unparseable_auth_param",
      service: serviceId,
      detail: `인증 키 bullet has no recognizable key: ${JSON.stringify(authRaw)}`,
    });
  }

  // --- declared counts ------------------------------------------------------
  const countRaw = bullets.get("오퍼레이션 수") ?? null;
  let declaredCount: number | null = null;
  let declaredDeprecated: number | null = null;
  if (countRaw !== null) {
    const total = countRaw.match(/(\d+)\s*개/);
    if (total) declaredCount = Number(total[1]);
    const dep = countRaw.match(/폐기예정\s*[:：]\s*(\d+)/);
    if (dep) declaredDeprecated = Number(dep[1]);
  } else {
    anomalies.push({
      kind: "missing_operation_count",
      service: serviceId,
      detail: "no 오퍼레이션 수 bullet to cross-check the parsed operations against",
    });
  }

  const operations = parseOperations(lines, serviceId, anomalies);
  if (operations.length === 0) {
    anomalies.push({
      kind: "no_operations",
      service: serviceId,
      detail: "file yielded no `## ` operation headings",
    });
  }
  if (declaredCount !== null && declaredCount !== operations.length) {
    anomalies.push({
      kind: "operation_count_mismatch",
      service: serviceId,
      detail: `header declares ${declaredCount} operations, ${operations.length} parsed`,
    });
  }
  const parsedDeprecated = operations.filter((o) => o.deprecated).length;
  if (declaredDeprecated !== null && declaredDeprecated !== parsedDeprecated) {
    anomalies.push({
      kind: "deprecated_count_mismatch",
      service: serviceId,
      detail: `header declares ${declaredDeprecated} deprecated operations, ${parsedDeprecated} headings carry a 폐기예정 marker`,
    });
  }

  const confidence = CONFIDENCE_OVERRIDES[serviceId] ?? pathConfidence;

  return {
    service: {
      id: serviceId,
      name,
      service_path: servicePath,
      path_confidence: confidence,
      gateway,
      auth_param: authParam,
      operations,
      declared_operation_count: declaredCount,
      declared_deprecated_count: declaredDeprecated,
    },
    anomalies,
  };
}

// ---------------------------------------------------------------------------
// Source provenance
// ---------------------------------------------------------------------------

function sourceCommit(srcDir: string): string | null {
  const r = spawnSync("git", ["-C", srcDir, "rev-parse", "HEAD"], { encoding: "utf8" });
  if (r.status !== 0) return null;
  return r.stdout.trim() || null;
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

function buildReport(services: Service[], anomalies: Anomaly[], commit: string | null): string {
  const totalOps = services.reduce((n, s) => n + s.operations.length, 0);
  const totalDeprecated = services.reduce((n, s) => n + s.operations.filter((o) => o.deprecated).length, 0);

  const byConfidence = new Map<string, number>();
  const byGateway = new Map<string, number>();
  const byAuth = new Map<string, number>();
  for (const s of services) {
    byConfidence.set(s.path_confidence, (byConfidence.get(s.path_confidence) ?? 0) + 1);
    byGateway.set(s.gateway, (byGateway.get(s.gateway) ?? 0) + 1);
    const a = s.auth_param ?? "(none)";
    byAuth.set(a, (byAuth.get(a) ?? 0) + 1);
  }

  const byKind = new Map<string, Anomaly[]>();
  for (const a of anomalies) {
    if (!byKind.has(a.kind)) byKind.set(a.kind, []);
    byKind.get(a.kind)!.push(a);
  }

  const out: string[] = [];
  out.push("# KIPRIS catalog build report");
  out.push("");
  out.push(`- Source commit: \`${commit ?? "unknown"}\` (nuri428/kipris_skill, MIT)`);
  out.push(`- Services parsed: **${services.length}**`);
  out.push(`- Operations parsed: **${totalOps}** (deprecated: ${totalDeprecated})`);
  out.push("");

  out.push("## Cross-check against the source repo's own claims");
  out.push("");
  out.push("| Claim | Source says | Parsed | Agrees |");
  out.push("|---|---|---|---|");
  out.push(`| Services (SKILL.md, docs/README.md) | 49 | ${services.length} | ${services.length === 49 ? "yes" : "NO"} |`);
  out.push(`| Operations (README.md) | 540 | ${totalOps} | ${totalOps === 540 ? "yes" : "NO"} |`);
  out.push(`| Deprecated operations (docs/README.md) | 30 | ${totalDeprecated} | ${totalDeprecated === 30 ? "yes" : "NO"} |`);
  out.push("");

  out.push("## Count by path_confidence");
  out.push("");
  out.push("| path_confidence | services |");
  out.push("|---|---|");
  for (const [k, v] of [...byConfidence.entries()].sort()) out.push(`| ${k} | ${v} |`);
  out.push("");

  out.push("## Count by gateway");
  out.push("");
  out.push("| gateway | services |");
  out.push("|---|---|");
  for (const [k, v] of [...byGateway.entries()].sort()) out.push(`| ${k} | ${v} |`);
  out.push("");

  out.push("## Count by auth_param");
  out.push("");
  out.push("| auth_param | services |");
  out.push("|---|---|");
  for (const [k, v] of [...byAuth.entries()].sort()) out.push(`| ${k} | ${v} |`);
  out.push("");

  out.push("## Per-service operation counts");
  out.push("");
  out.push("| service_id | name | declared | parsed | deprecated | path_confidence | gateway | auth_param |");
  out.push("|---|---|---|---|---|---|---|---|");
  for (const s of services) {
    const dep = s.operations.filter((o) => o.deprecated).length;
    out.push(
      `| ${s.id} | ${s.name} | ${s.declared_operation_count ?? "—"} | ${s.operations.length} | ${dep} | ${s.path_confidence} | ${s.gateway} | ${s.auth_param ?? "null"} |`,
    );
  }
  out.push("");

  out.push(`## Anomalies (${anomalies.length})`);
  out.push("");
  if (anomalies.length === 0) {
    out.push("None.");
  }
  for (const [kind, list] of [...byKind.entries()].sort()) {
    out.push(`### ${kind} (${list.length})`);
    out.push("");
    for (const a of list) out.push(`- \`${a.service}\`: ${a.detail}`);
    out.push("");
  }

  return out.join("\n") + "\n";
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function writeJson(path: string, value: unknown): void {
  writeFileSync(path, JSON.stringify(value, null, 2) + "\n", "utf8");
}

function main(): void {
  const srcDir = resolve(process.argv[2] ?? DEFAULT_SRC);
  if (!existsSync(srcDir)) {
    console.error(`source directory not found: ${srcDir}`);
    process.exit(1);
  }

  const files = readdirSync(srcDir)
    .filter((f) => f.endsWith(".md"))
    .sort();
  if (files.length === 0) {
    console.error(`no markdown files in ${srcDir}`);
    process.exit(1);
  }

  const services: Service[] = [];
  const anomalies: Anomaly[] = [];
  for (const f of files) {
    const parsed = parseServiceFile(join(srcDir, f), f);
    services.push(parsed.service);
    anomalies.push(...parsed.anomalies);
  }

  const commit = sourceCommit(srcDir);

  // Rebuild the output tree so a removed source file cannot leave a stale
  // per-service JSON behind (and so re-runs are idempotent).
  rmSync(SERVICES_DIR, { recursive: true, force: true });
  mkdirSync(SERVICES_DIR, { recursive: true });

  for (const s of services) {
    writeJson(join(SERVICES_DIR, `${s.id}.json`), {
      id: s.id,
      name: s.name,
      service_path: s.service_path,
      path_confidence: s.path_confidence,
      gateway: s.gateway,
      auth_param: s.auth_param,
      operations: s.operations,
    });
  }

  const indexPath = join(OUT_DIR, "index.json");
  // generated_at would break idempotency on every run, so keep the previous
  // value whenever nothing else about the catalog changed.
  const index = {
    version: "1",
    generated_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    source: {
      repo: "nuri428/kipris_skill",
      license: "MIT",
      commit,
      note: "Derived from KIPRIS Plus Open API specifications documented in the source repo.",
    },
    gateways: {
      openapi: { base: "https://plus.kipris.or.kr/openapi/rest", auth_param: "accessKey" },
      kipo: { base: "https://plus.kipris.or.kr/kipo-api/kipi", auth_param: "ServiceKey" },
    },
    services: services.map((s) => ({
      id: s.id,
      name: s.name,
      service_path: s.service_path,
      path_confidence: s.path_confidence,
      gateway: s.gateway,
      auth_param: s.auth_param,
      operation_count: s.operations.length,
    })),
  };

  if (existsSync(indexPath)) {
    try {
      const prev = JSON.parse(readFileSync(indexPath, "utf8"));
      const a = { ...prev, generated_at: null };
      const b = { ...index, generated_at: null };
      if (JSON.stringify(a) === JSON.stringify(b) && typeof prev.generated_at === "string") {
        index.generated_at = prev.generated_at;
      }
    } catch {
      // Unreadable previous index: fall through and write a fresh one.
    }
  }
  writeJson(indexPath, index);

  const report = buildReport(services, anomalies, commit);
  writeFileSync(REPORT_PATH, report, "utf8");
  process.stdout.write(report);
}

main();

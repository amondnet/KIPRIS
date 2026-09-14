#!/usr/bin/env python3
"""KIPRIS Plus Open API client for the kipris skill.

Standard library only, so it runs unchanged wherever python3 exists -- Claude
Code on a developer machine and the Claude Desktop / Cowork sandbox alike.

Subcommands:
  doctor                      check key resolution and quota counter
  services [--grep TEXT]      list catalogued services
  describe SERVICE [--op ID]  show operations, request params, response fields
  call SERVICE OP -p k=v ...  call an operation and return parsed JSON
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

try:  # POSIX only; the counter still works without it, just unserialized.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

CATALOG_DIR = Path(__file__).resolve().parent.parent / "references" / "catalog"
CONFIG_DIR = Path(os.environ.get("KIPRIS_CONFIG_DIR") or (Path.home() / ".config" / "kipris"))
USAGE_FILE = CONFIG_DIR / "usage.json"
USAGE_LOCK = CONFIG_DIR / "usage.lock"
TIMEOUT = 30
MAX_RETRIES = 3
MIN_INTERVAL = 0.05  # KIPRIS blocks IPs above 50 requests/second; stay well under.

# KIPRIS exposes the same service through two gateways with different auth
# params. The catalog records the gateway per service, but a few services carry
# operations on both -- these are the ones confirmed by real calls. Operation
# ids repeat across services, so the key is (service id, operation id):
# freeSearchInfo means openapi under patent_utility but kipo under trademark.
OPERATION_GATEWAY = {
    ("patent_utility", "getAdvancedSearch"): "kipo",
    ("patent_utility", "getBibliographyDetailInfoSearch"): "kipo",
    ("patent_utility", "getBibliographySumryInfoSearch"): "kipo",
    ("patent_utility", "freeSearchInfo"): "openapi",
    ("patent_utility", "applicationNumberSearchInfo"): "openapi",
    ("patent_utility", "applicantNameSearchInfo"): "openapi",
    ("patent_utility", "rightHolerSearchInfo"): "openapi",
    ("foreign_patent", "freeSearch"): "openapi",
    ("foreign_patent", "applicationNumberSearch"): "openapi",
    ("foreign_patent", "internationalOpenNumberSearch"): "openapi",
    ("foreign_patent", "applicantSearch"): "openapi",
    ("foreign_patent", "internationalApplicationNumberSearch"): "openapi",
}


class KiprisError(Exception):
    """Anything the caller should show the user rather than a stack trace."""


# --------------------------------------------------------------------------- keys

def _read_env_file(path: Path, name: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip("\"'") or None
    return None


def resolve_key(gateway: str) -> tuple[str, str]:
    """Return (key, where_it_came_from).

    The two gateways may hold different keys -- KIPRIS Plus issues an accessKey,
    the public data portal issues a ServiceKey -- so a gateway-specific variable
    wins over the shared one. Claude Desktop has no shell env, which is why the
    file paths matter as much as the variables.
    """
    specific = "KIPRIS_ACCESS_KEY" if gateway == "openapi" else "KIPRIS_SERVICE_KEY"
    # A blank value counts as absent: an exported-but-empty variable must not
    # shadow the shared key that is actually set.
    for name in (specific, "KIPRIS_API_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, f"env:{name}"
    for filename in (".env.local", ".env"):
        for name in (specific, "KIPRIS_API_KEY"):
            value = (_read_env_file(Path.cwd() / filename, name) or "").strip()
            if value:
                return value, f"{filename}:{name}"
    for filename in (f"{gateway}_key", "api_key"):
        path = CONFIG_DIR / filename
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value, str(path)
    raise KiprisError(
        "No KIPRIS API key found. Set one of:\n"
        "  export KIPRIS_API_KEY='...'\n"
        f"  printf '%s' '...' > {CONFIG_DIR / 'api_key'}\n"
        "  KIPRIS_API_KEY=... in a .env.local beside the working directory\n"
        "Issue keys at https://plus.kipris.or.kr (accessKey) "
        "and https://www.data.go.kr (ServiceKey)."
    )


# --------------------------------------------------------------------------- quota

@contextlib.contextmanager
def _usage_lock():
    """Serialize the read-modify-write below. Fan-out searches run several
    invocations at once, and an unlocked counter silently loses increments.
    Locking is best effort: a read-only home or a filesystem without flock
    must degrade to the old unlocked behaviour, never fail the search."""
    handle = None
    try:
        USAGE_LOCK.parent.mkdir(parents=True, exist_ok=True)
        handle = open(USAGE_LOCK, "a+b")
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except (OSError, ValueError):
        if handle is not None:
            handle.close()
        handle = None
    try:
        yield
    finally:
        if handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (OSError, ValueError):
                pass
            handle.close()


def _usage_bump(count: int = 1) -> dict:
    """Track calls per month. The free tier allows 1,000/month, so a caller that
    is about to fan out over N results needs to know what it has already spent."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with _usage_lock():
        data = {}
        if USAGE_FILE.is_file():
            try:
                data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, ValueError):
                data = {}
        if not isinstance(data, dict) or data.get("month") != month:
            data = {"month": month, "calls": 0}
        data["calls"] = int(data.get("calls", 0)) + count
        try:
            USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Replace atomically so a concurrent reader never sees a half file.
            tmp = USAGE_FILE.with_name(f"{USAGE_FILE.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, USAGE_FILE)
        except OSError:
            pass  # A read-only home is not a reason to fail the search.
    return data


# --------------------------------------------------------------------------- catalog

def load_index() -> dict:
    path = CATALOG_DIR / "index.json"
    if not path.is_file():
        raise KiprisError(f"Catalog index missing at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_service(service_id: str) -> dict:
    path = CATALOG_DIR / "services" / f"{service_id}.json"
    if not path.is_file():
        index = load_index()
        known = ", ".join(s["id"] for s in index["services"][:10])
        raise KiprisError(f"Unknown service '{service_id}'. Try: {known} ... (see `services`)")
    return json.loads(path.read_text(encoding="utf-8"))


def pick_gateway(service: dict, operation_id: str, override: str | None) -> tuple[str, str]:
    """Return (gateway, why). Never guess silently -- the caller reports `why`
    so a wrong gateway is diagnosable instead of looking like a bad API key."""
    if override:
        return override, "explicit --gateway"
    verified = OPERATION_GATEWAY.get((service.get("id"), operation_id))
    if verified:
        return verified, "verified operation mapping"
    gateway = service.get("gateway")
    if gateway in ("openapi", "kipo"):
        return gateway, "service catalog"
    if gateway == "both":
        # Every confirmed KIPO operation is named get*; the portal-style gateway
        # uses that convention. Flagged in _meta so a miss is obvious.
        guess = "kipo" if operation_id.startswith("get") else "openapi"
        return guess, "heuristic (get* -> kipo); pass --gateway to override"
    raise KiprisError(
        f"Gateway unknown for service '{service['id']}'. Pass --gateway openapi|kipo."
    )


# --------------------------------------------------------------------------- http

def _fetch(url: str) -> tuple[bytes, str, dict]:
    """Return (body, content type, usage). Every attempt reaches KIPRIS and so
    spends quota, including the ones a retry replaces -- the counter is bumped
    per attempt and the latest snapshot travels back to the caller."""
    last: Exception | None = None
    usage: dict = {}
    for attempt in range(MAX_RETRIES):
        usage = _usage_bump()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "kipris-skill/1.0"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read(), response.headers.get_content_type(), usage
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == MAX_RETRIES - 1:
                raise KiprisError(f"HTTP {exc.code} {exc.reason} from KIPRIS") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == MAX_RETRIES - 1:
                raise KiprisError(
                    f"Could not reach KIPRIS ({exc}). The sandbox may block outbound "
                    "network access, or the service may be down."
                ) from exc
            last = exc
        time.sleep(2 ** attempt)
    raise KiprisError(f"Request failed: {last}")


def _xml_to_obj(element: ET.Element):
    children = list(element)
    if not children:
        return (element.text or "").strip()
    out: dict = {}
    for child in children:
        value = _xml_to_obj(child)
        tag = child.tag
        if tag in out:
            if not isinstance(out[tag], list):
                out[tag] = [out[tag]]
            out[tag].append(value)
        else:
            out[tag] = value
    return out


def _find_first(node, *names):
    """Depth-first search for the first of `names`. Response envelopes differ per
    operation (body.items.item vs body.items.PatentUtilityInfo vs body.item)."""
    if isinstance(node, dict):
        for name in names:
            if name in node:
                return node[name]
        for value in node.values():
            found = _find_first(value, *names)
            if found is not None:
                return found
    return None


def parse_response(payload: bytes, content_type: str) -> dict:
    text = payload.decode("utf-8", errors="replace")
    if "json" in content_type:
        return json.loads(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise KiprisError(f"Could not parse KIPRIS response as XML: {exc}\n{text[:400]}") from exc
    return {root.tag: _xml_to_obj(root)}


def check_result_code(parsed: dict) -> None:
    code = _find_first(parsed, "resultCode")
    message = _find_first(parsed, "resultMsg") or ""
    success = _find_first(parsed, "successYN")
    # JSON responses carry resultCode as a number, XML as text; compare as text
    # so a successful 0 is not reported as an error.
    code = None if code is None else str(code)
    if code not in (None, "", "00", "0", "000"):
        hint = ""
        if code == "101":
            hint = " -- this API is not registered to your key; apply for it on KIPRIS Plus."
        raise KiprisError(f"KIPRIS error {code}: {message}{hint}")
    if success == "N":
        raise KiprisError(f"KIPRIS reported failure: {message or 'no message'}")


# --------------------------------------------------------------------------- commands

def cmd_doctor(args: argparse.Namespace) -> int:
    index = load_index()
    print(f"catalog       : {len(index['services'])} services, {CATALOG_DIR}")
    for gateway in ("openapi", "kipo"):
        try:
            _, origin = resolve_key(gateway)
            print(f"{gateway:14}: key found ({origin})")
        except KiprisError:
            print(f"{gateway:14}: NO KEY")
    if USAGE_FILE.is_file():
        try:
            data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("usage counter is not an object")
        except (json.JSONDecodeError, OSError, ValueError):
            # doctor exists to diagnose a broken setup; a corrupt counter is one
            # of those setups, and _usage_bump resets it on the next call anyway.
            print(f"calls         : unreadable counter at {USAGE_FILE}; "
                  "it resets on the next call")
        else:
            print(f"calls         : {data.get('calls', 0)} in {data.get('month')} (free tier: 1000/month)")
    else:
        print("calls         : none recorded yet")
    return 0


def cmd_services(args: argparse.Namespace) -> int:
    index = load_index()
    rows = index["services"]
    if args.grep:
        needle = args.grep.lower()
        rows = [s for s in rows if needle in s["id"].lower() or needle in s["name"].lower()]
    print(json.dumps(
        [{k: s.get(k) for k in ("id", "name", "service_path", "gateway", "path_confidence", "operation_count")}
         for s in rows],
        ensure_ascii=False, indent=2))
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    service = load_service(args.service)
    if args.op:
        matches = [o for o in service["operations"] if o["id"] == args.op]
        if not matches:
            ids = ", ".join(o["id"] for o in service["operations"])
            raise KiprisError(f"Operation '{args.op}' not in {service['id']}. Available: {ids}")
        service = {**service, "operations": matches}
    elif not args.full:
        service = {**service, "operations": [
            {k: o[k] for k in ("id", "name", "deprecated")} for o in service["operations"]
        ]}
    print(json.dumps(service, ensure_ascii=False, indent=2))
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    service = load_service(args.service)
    service_path = args.service_path or service.get("service_path")
    if not service_path:
        raise KiprisError(
            f"Service '{service['id']}' has no confirmed ServicePath in the catalog, "
            "so its URL cannot be built. Pass --service-path if you know it."
        )
    operation_ids = {o["id"] for o in service["operations"]}
    if args.operation not in operation_ids and not args.force:
        raise KiprisError(
            f"Operation '{args.operation}' is not in the catalog for {service['id']}. "
            f"Run `describe {service['id']}` to list operations, or pass --force."
        )

    gateway, why = pick_gateway(service, args.operation, args.gateway)
    index = load_index()
    conf = index["gateways"][gateway]
    key, key_origin = resolve_key(gateway)

    params = {conf["auth_param"]: key}
    for pair in args.param or []:
        name, sep, value = pair.partition("=")
        if not sep:
            raise KiprisError(f"Bad --param '{pair}', expected name=value")
        name = name.strip()
        if name == conf["auth_param"]:
            # Overwriting the resolved key here would defeat the redaction below
            # and print the caller's credential in _meta.url.
            raise KiprisError(
                f"--param {name} is not allowed; the {gateway} gateway's auth key is "
                "supplied by the key resolution order (env, .env, config file)."
            )
        params[name] = value
    url = f"{conf['base']}/{service_path}/{args.operation}?" + urllib.parse.urlencode(
        params, quote_via=urllib.parse.quote, safe="")

    time.sleep(MIN_INTERVAL)
    payload, content_type, usage = _fetch(url)

    if args.raw:
        sys.stdout.write(payload.decode("utf-8", errors="replace"))
        return 0

    parsed = parse_response(payload, content_type)
    check_result_code(parsed)

    items = _find_first(parsed, "items", "item")
    meta = {
        "service": service["id"],
        "operation": args.operation,
        "gateway": gateway,
        "gateway_source": why,
        "key_source": key_origin,
        "path_confidence": service.get("path_confidence"),
        "total_count": _find_first(parsed, "totalCount", "TotalSearchCount"),
        "calls_this_month": usage.get("calls"),
        "url": url.replace(urllib.parse.quote(key, safe=""), "***").replace(key, "***"),
    }
    print(json.dumps({"_meta": meta, "items": items, "response": parsed if args.full else None},
                     ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="kipris.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check API key resolution and monthly call count")

    p_services = sub.add_parser("services", help="list catalogued services")
    p_services.add_argument("--grep", help="filter by id or Korean name")

    p_describe = sub.add_parser("describe", help="show a service's operations and parameters")
    p_describe.add_argument("service", help="service id from `services`")
    p_describe.add_argument("--op", help="show only this operation, with full IN/OUT fields")
    p_describe.add_argument("--full", action="store_true", help="include fields for every operation")

    p_call = sub.add_parser(
        "call",
        help="call an operation; spends one request against the monthly quota")
    p_call.add_argument("service", help="service id from `services`")
    p_call.add_argument("operation", help="operation id from `describe <service>`")
    p_call.add_argument("-p", "--param", action="append", metavar="NAME=VALUE",
                        help="request parameter, repeatable; exact names come from "
                             "`describe <service> --op <operation>`")
    p_call.add_argument("--gateway", choices=["openapi", "kipo"],
                        help="force the gateway instead of the catalogued or inferred one; "
                             "it decides which auth param and base URL are used")
    p_call.add_argument("--service-path", help="override the catalogued ServicePath")
    p_call.add_argument("--raw", action="store_true", help="print the raw XML response")
    p_call.add_argument("--full", action="store_true", help="include the whole parsed envelope")
    p_call.add_argument("--force", action="store_true", help="call an operation absent from the catalog")

    args = parser.parse_args()
    handlers = {"doctor": cmd_doctor, "services": cmd_services,
                "describe": cmd_describe, "call": cmd_call}
    try:
        return handlers[args.command](args)
    except KiprisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

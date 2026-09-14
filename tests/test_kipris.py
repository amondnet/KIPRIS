"""Tests for the kipris skill client.

Standard library only, matching the script itself -- these have to run in the
same bare environments the skill does.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "kipris" / "scripts" / "kipris.py"
CATALOG = ROOT / "skills" / "kipris" / "references" / "catalog"

_spec = importlib.util.spec_from_file_location("kipris", SCRIPT)
kipris = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kipris)


class KeyResolutionTest(unittest.TestCase):
    """Key lookup order decides which gateway authenticates, and a silent
    mis-pick looks exactly like an invalid key, so the order is pinned here."""

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("KIPRIS_API_KEY", "KIPRIS_ACCESS_KEY", "KIPRIS_SERVICE_KEY")}
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_dir = kipris.CONFIG_DIR
        kipris.CONFIG_DIR = Path(self._tmp.name)

    def tearDown(self):
        kipris.CONFIG_DIR = self._saved_dir
        self._tmp.cleanup()
        for key, value in self._saved.items():
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)

    def test_gateway_specific_env_beats_shared(self):
        os.environ["KIPRIS_API_KEY"] = "shared"
        os.environ["KIPRIS_ACCESS_KEY"] = "openapi-only"
        os.environ["KIPRIS_SERVICE_KEY"] = "kipo-only"
        self.assertEqual(kipris.resolve_key("openapi")[0], "openapi-only")
        self.assertEqual(kipris.resolve_key("kipo")[0], "kipo-only")

    def test_shared_env_used_when_no_specific_key(self):
        os.environ["KIPRIS_API_KEY"] = "shared"
        key, origin = kipris.resolve_key("kipo")
        self.assertEqual(key, "shared")
        self.assertEqual(origin, "env:KIPRIS_API_KEY")

    def test_config_file_used_when_env_absent(self):
        """Claude Desktop has no shell env, so the file path is the only route."""
        (Path(self._tmp.name) / "api_key").write_text("from-file\n")
        key, origin = kipris.resolve_key("openapi")
        self.assertEqual(key, "from-file")
        self.assertIn("api_key", origin)

    def test_blank_specific_env_falls_back_to_the_shared_key(self):
        """An exported-but-empty variable is a common shell accident; treating it
        as present would return an empty key and look like a rejected key."""
        os.environ["KIPRIS_ACCESS_KEY"] = "   "
        os.environ["KIPRIS_API_KEY"] = "shared"
        key, origin = kipris.resolve_key("openapi")
        self.assertEqual(key, "shared")
        self.assertEqual(origin, "env:KIPRIS_API_KEY")

    def test_missing_key_explains_how_to_set_one(self):
        with self.assertRaises(kipris.KiprisError) as ctx:
            kipris.resolve_key("openapi")
        self.assertIn("KIPRIS_API_KEY", str(ctx.exception))


class GatewaySelectionTest(unittest.TestCase):
    """patent_utility serves operations on both gateways; picking the wrong one
    wastes a call against the monthly quota, so every rule is covered."""

    BOTH = {"id": "patent_utility", "gateway": "both"}

    def test_explicit_override_wins(self):
        gateway, why = kipris.pick_gateway(self.BOTH, "freeSearchInfo", "kipo")
        self.assertEqual(gateway, "kipo")
        self.assertIn("explicit", why)

    def test_verified_mapping_beats_heuristic(self):
        self.assertEqual(kipris.pick_gateway(self.BOTH, "freeSearchInfo", None)[0], "openapi")
        self.assertEqual(kipris.pick_gateway(self.BOTH, "getAdvancedSearch", None)[0], "kipo")

    def test_single_gateway_service_uses_catalog(self):
        gateway, why = kipris.pick_gateway({"id": "trademark", "gateway": "kipo"}, "anyOp", None)
        self.assertEqual(gateway, "kipo")
        self.assertIn("catalog", why)

    def test_heuristic_is_reported_not_silent(self):
        gateway, why = kipris.pick_gateway(self.BOTH, "getSomethingUncatalogued", None)
        self.assertEqual(gateway, "kipo")
        self.assertIn("heuristic", why)

    def test_verified_mapping_does_not_leak_across_services(self):
        """freeSearchInfo exists in several services; the mapping is confirmed for
        patent_utility only, so other services must keep their catalogued gateway."""
        gateway, why = kipris.pick_gateway(
            {"id": "trademark", "gateway": "kipo"}, "freeSearchInfo", None)
        self.assertEqual(gateway, "kipo")
        self.assertIn("catalog", why)

    def test_unknown_gateway_refuses_rather_than_guessing(self):
        with self.assertRaises(kipris.KiprisError):
            kipris.pick_gateway({"id": "x", "gateway": "unknown"}, "op", None)


class ResponseParsingTest(unittest.TestCase):
    XML = """<?xml version="1.0" encoding="UTF-8"?>
    <response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>
    <body><count><totalCount>2</totalCount></count><items>
      <PatentUtilityInfo><InventionName>가</InventionName><ApplicationNumber>10</ApplicationNumber></PatentUtilityInfo>
      <PatentUtilityInfo><InventionName>나</InventionName><ApplicationNumber>20</ApplicationNumber></PatentUtilityInfo>
    </items></body></response>"""

    def test_repeated_elements_become_a_list(self):
        parsed = kipris.parse_response(self.XML.encode(), "application/xml")
        items = kipris._find_first(parsed, "items")
        self.assertEqual(len(items["PatentUtilityInfo"]), 2)
        self.assertEqual(items["PatentUtilityInfo"][0]["InventionName"], "가")

    def test_total_count_found_regardless_of_nesting(self):
        parsed = kipris.parse_response(self.XML.encode(), "application/xml")
        self.assertEqual(kipris._find_first(parsed, "totalCount"), "2")

    def test_ok_result_code_passes(self):
        kipris.check_result_code(kipris.parse_response(self.XML.encode(), "application/xml"))

    def test_unregistered_api_error_carries_actionable_hint(self):
        xml = b"<response><header><resultCode>101</resultCode><resultMsg>Not Registerd</resultMsg></header></response>"
        with self.assertRaises(kipris.KiprisError) as ctx:
            kipris.check_result_code(kipris.parse_response(xml, "application/xml"))
        self.assertIn("101", str(ctx.exception))
        self.assertIn("not registered", str(ctx.exception).lower())

    def test_numeric_result_code_zero_is_a_success(self):
        """JSON responses carry resultCode as a number, and 0 means OK."""
        parsed = kipris.parse_response(b'{"resultCode": 0, "resultMsg": "OK"}',
                                       "application/json")
        kipris.check_result_code(parsed)

    def test_success_flag_n_is_a_failure(self):
        xml = b"<response><body><successYN>N</successYN><resultMsg>nope</resultMsg></body></response>"
        with self.assertRaises(kipris.KiprisError):
            kipris.check_result_code(kipris.parse_response(xml, "application/xml"))

    def test_unparseable_body_is_reported_with_a_sample(self):
        with self.assertRaises(kipris.KiprisError) as ctx:
            kipris.parse_response(b"<html>gateway timeout", "text/html")
        self.assertIn("XML", str(ctx.exception))


class CatalogTest(unittest.TestCase):
    """The catalog drives real URLs. A drifted or truncated build would produce
    silently wrong requests, so the invariants the script relies on are asserted."""

    @classmethod
    def setUpClass(cls):
        cls.index = json.loads((CATALOG / "index.json").read_text(encoding="utf-8"))

    def test_every_indexed_service_has_a_detail_file(self):
        for service in self.index["services"]:
            self.assertTrue((CATALOG / "services" / f"{service['id']}.json").is_file(),
                            f"missing detail file for {service['id']}")

    def test_operation_counts_agree_between_index_and_detail(self):
        for service in self.index["services"]:
            detail = json.loads((CATALOG / "services" / f"{service['id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(service["operation_count"], len(detail["operations"]), service["id"])

    def test_gateway_bases_are_present(self):
        for gateway in ("openapi", "kipo"):
            conf = self.index["gateways"][gateway]
            self.assertTrue(conf["base"].startswith("http"))
            self.assertIn(conf["auth_param"], ("accessKey", "ServiceKey"))

    def test_documented_operations_exist_in_the_catalog(self):
        """SKILL.md names these operations directly; if a rebuild drops one, the
        skill would send users to an operation the script then refuses."""
        expected = {
            "patent_utility": ["freeSearchInfo", "applicationNumberSearchInfo",
                               "applicantNameSearchInfo", "rightHolerSearchInfo",
                               "getAdvancedSearch", "getBibliographyDetailInfoSearch",
                               "getBibliographySumryInfoSearch"],
            "foreign_patent": ["freeSearch", "applicationNumberSearch", "applicantSearch",
                               "internationalOpenNumberSearch",
                               "internationalApplicationNumberSearch"],
        }
        for service_id, operations in expected.items():
            detail = json.loads((CATALOG / "services" / f"{service_id}.json").read_text(encoding="utf-8"))
            available = {o["id"] for o in detail["operations"]}
            for operation in operations:
                self.assertIn(operation, available, f"{service_id}.{operation}")

    def test_services_with_a_path_are_callable(self):
        """A path without a gateway cannot produce a URL; the pair must travel together."""
        for service in self.index["services"]:
            if service["service_path"]:
                self.assertIn(service["gateway"], ("openapi", "kipo", "both"), service["id"])

    def test_every_verified_operation_mapping_is_real(self):
        known = {}
        for service in self.index["services"]:
            detail = json.loads((CATALOG / "services" / f"{service['id']}.json").read_text(encoding="utf-8"))
            known[service["id"]] = {o["id"] for o in detail["operations"] if o["id"]}
        for service_id, operation in kipris.OPERATION_GATEWAY:
            self.assertIn(service_id, known, f"hardcoded mapping for unknown service {service_id}")
            self.assertIn(operation, known[service_id],
                          f"hardcoded mapping for unknown operation {service_id}.{operation}")


class CallCommandTest(unittest.TestCase):
    """cmd_call assembles the URL, the auth param and _meta from several sources,
    so the guarantees that matter are asserted through the command itself rather
    than through its helpers."""

    ENV = ("KIPRIS_API_KEY", "KIPRIS_ACCESS_KEY", "KIPRIS_SERVICE_KEY")

    def setUp(self):
        self._saved_env = {k: os.environ.pop(k, None) for k in self.ENV}
        os.environ["KIPRIS_API_KEY"] = "secret-key"
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (kipris.CONFIG_DIR, kipris.USAGE_FILE, kipris.USAGE_LOCK,
                       kipris._fetch, kipris.MIN_INTERVAL)
        kipris.CONFIG_DIR = Path(self._tmp.name)
        kipris.USAGE_FILE = Path(self._tmp.name) / "usage.json"
        kipris.USAGE_LOCK = Path(self._tmp.name) / "usage.lock"
        kipris.MIN_INTERVAL = 0
        self.requested = []

        def fake_fetch(url):
            self.requested.append(url)
            return ResponseParsingTest.XML.encode(), "application/xml", kipris._usage_bump()

        kipris._fetch = fake_fetch

    def tearDown(self):
        (kipris.CONFIG_DIR, kipris.USAGE_FILE, kipris.USAGE_LOCK,
         kipris._fetch, kipris.MIN_INTERVAL) = self._saved
        self._tmp.cleanup()
        for key, value in self._saved_env.items():
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)

    def _args(self, **overrides):
        base = dict(service="patent_utility", operation="freeSearchInfo", param=None,
                    gateway=None, service_path=None, raw=False, full=False, force=False)
        base.update(overrides)
        return argparse.Namespace(**base)

    def _run(self, **overrides):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(kipris.cmd_call(self._args(**overrides)), 0)
        return json.loads(buffer.getvalue())

    def test_total_count_is_the_scalar_not_its_wrapper(self):
        """The envelope nests totalCount inside <count>; matching the wrapper name
        would put a dict where the caller expects a number."""
        self.assertEqual(self._run()["_meta"]["total_count"], "2")

    def test_service_path_override_reaches_a_service_without_a_catalogued_path(self):
        """29 services have no confirmed ServicePath; --service-path is the only
        way to call them, so it has to survive the missing-path guard."""
        result = self._run(service="amendment_gazette",
                           operation="amendmentGazzetePatentBibliographicInfo",
                           service_path="myServicePath", gateway="openapi")
        self.assertIn("/myServicePath/amendmentGazzetePatentBibliographicInfo?",
                      self.requested[0])
        self.assertEqual(result["_meta"]["gateway"], "openapi")

    def test_missing_path_from_both_sources_still_refuses(self):
        with self.assertRaises(kipris.KiprisError) as ctx:
            self._run(service="amendment_gazette",
                      operation="amendmentGazzetePatentBibliographicInfo",
                      gateway="openapi")
        self.assertIn("--service-path", str(ctx.exception))

    def test_param_may_not_overwrite_the_auth_key(self):
        """It would replace the resolved key, which redaction still looks for, and
        the substituted credential would be printed in clear text in _meta.url."""
        with self.assertRaises(kipris.KiprisError) as ctx:
            self._run(param=["accessKey=leaked-key"])
        self.assertIn("accessKey", str(ctx.exception))
        self.assertNotIn("leaked-key", str(ctx.exception))
        self.assertEqual(self.requested, [])

    def test_resolved_key_is_redacted_in_the_reported_url(self):
        meta = self._run(param=["word=robot"])["_meta"]
        self.assertNotIn("secret-key", meta["url"])
        self.assertIn("***", meta["url"])


class FetchRetryTest(unittest.TestCase):
    """The 1,000 calls/month quota counts requests, not successes, so a retried
    call has to record every attempt that left the machine."""

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._payload

        @property
        def headers(self):
            return self

        def get_content_type(self):
            return "application/xml"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (kipris.USAGE_FILE, kipris.USAGE_LOCK,
                       kipris.urllib.request.urlopen, kipris.time)
        kipris.USAGE_FILE = Path(self._tmp.name) / "usage.json"
        kipris.USAGE_LOCK = Path(self._tmp.name) / "usage.lock"
        kipris.time = type("_NoSleep", (), {"sleep": staticmethod(lambda _: None)})

    def tearDown(self):
        (kipris.USAGE_FILE, kipris.USAGE_LOCK,
         kipris.urllib.request.urlopen, kipris.time) = self._saved
        self._tmp.cleanup()

    def _install(self, outcomes):
        self.attempts = []

        def fake_urlopen(request, timeout=None):
            self.attempts.append(request.full_url)
            outcome = outcomes[len(self.attempts) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        kipris.urllib.request.urlopen = fake_urlopen

    def test_every_attempt_of_a_retried_call_is_counted(self):
        self._install([urllib.error.URLError("reset"), urllib.error.URLError("reset"),
                       self._Response(b"<response><body/></response>")])
        _, _, usage = kipris._fetch("http://example.invalid/x")
        self.assertEqual(len(self.attempts), 3)
        self.assertEqual(usage["calls"], 3)
        self.assertEqual(json.loads(kipris.USAGE_FILE.read_text())["calls"], 3)

    def test_a_call_that_never_succeeds_still_spends_quota(self):
        self._install([urllib.error.URLError("reset")] * kipris.MAX_RETRIES)
        with self.assertRaises(kipris.KiprisError):
            kipris._fetch("http://example.invalid/x")
        self.assertEqual(json.loads(kipris.USAGE_FILE.read_text())["calls"],
                         kipris.MAX_RETRIES)


class DoctorCommandTest(unittest.TestCase):
    """doctor is the command a user runs when the setup is broken, so it has to
    survive the breakage it reports on."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (kipris.USAGE_FILE, kipris.USAGE_LOCK)
        kipris.USAGE_FILE = Path(self._tmp.name) / "usage.json"
        kipris.USAGE_LOCK = Path(self._tmp.name) / "usage.lock"

    def tearDown(self):
        (kipris.USAGE_FILE, kipris.USAGE_LOCK) = self._saved
        self._tmp.cleanup()

    def test_corrupt_usage_file_is_reported_not_raised(self):
        kipris.USAGE_FILE.write_text('{"month": "2026-01", "cal', encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(kipris.cmd_doctor(argparse.Namespace()), 0)
        self.assertIn("unreadable", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()

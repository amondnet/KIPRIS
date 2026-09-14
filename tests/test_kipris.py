"""Tests for the kipris skill client.

Standard library only, matching the script itself -- these have to run in the
same bare environments the skill does.
"""

import importlib.util
import json
import os
import tempfile
import unittest
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
        known = set()
        for service in self.index["services"]:
            detail = json.loads((CATALOG / "services" / f"{service['id']}.json").read_text(encoding="utf-8"))
            known.update(o["id"] for o in detail["operations"] if o["id"])
        for operation in kipris.OPERATION_GATEWAY:
            self.assertIn(operation, known, f"hardcoded mapping for unknown operation {operation}")


if __name__ == "__main__":
    unittest.main()

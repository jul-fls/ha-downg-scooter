"""Catch repository metadata mistakes before Hassfest and HACS do."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "downg_scooter"


class MetadataTests(unittest.TestCase):
    def test_manifest_keys_and_hacs_fields(self) -> None:
        manifest = json.loads(
            (INTEGRATION / "manifest.json").read_text(encoding="utf-8")
        )
        keys = list(manifest)
        self.assertEqual(keys[:2], ["domain", "name"])
        self.assertEqual(keys[2:], sorted(keys[2:]))
        self.assertEqual(manifest["domain"], "downg_scooter")
        self.assertTrue(manifest["documentation"].startswith("https://github.com/"))
        self.assertTrue(manifest["issue_tracker"].startswith("https://github.com/"))

        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs["name"], manifest["name"])
        self.assertRegex(hacs["homeassistant"], r"^\d+\.\d+\.\d+$")

    def test_default_and_translated_strings_have_the_same_entities(self) -> None:
        catalogs = [
            json.loads((INTEGRATION / path).read_text(encoding="utf-8"))
            for path in (
                "strings.json",
                "translations/en.json",
                "translations/fr.json",
            )
        ]
        expected = catalogs[0]["entity"]
        for catalog in catalogs[1:]:
            self.assertEqual(catalog["entity"].keys(), expected.keys())
            for platform, entities in expected.items():
                self.assertEqual(catalog["entity"][platform].keys(), entities.keys())

    def test_bluetooth_discovery_is_scooter_specific(self) -> None:
        manifest = json.loads(
            (INTEGRATION / "manifest.json").read_text(encoding="utf-8")
        )
        matchers = manifest["bluetooth"]

        self.assertTrue(
            any(matcher.get("local_name") == "MIScooter*" for matcher in matchers)
        )
        self.assertFalse(
            any(
                matcher.get("service_data_uuid")
                == "0000fe95-0000-1000-8000-00805f9b34fb"
                and len(matcher) == 2
                for matcher in matchers
            ),
            "The generic Xiaomi FE95 service must not match on its own",
        )


if __name__ == "__main__":
    unittest.main()

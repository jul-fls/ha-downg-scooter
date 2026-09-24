"""Ensure entity declarations and translation catalogs stay synchronized."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "downg_scooter"


class EntityTranslationTests(unittest.TestCase):
    def test_sensor_translation_keys_match_catalog(self) -> None:
        sensor_source = (INTEGRATION / "sensor.py").read_text(encoding="utf-8")
        strings = json.loads(
            (INTEGRATION / "strings.json").read_text(encoding="utf-8")
        )

        for translation_key in strings["entity"]["sensor"]:
            self.assertIn(f'"{translation_key}"', sensor_source)

    def test_switch_translation_keys_match_catalog(self) -> None:
        switch_source = (INTEGRATION / "switch.py").read_text(encoding="utf-8")
        strings = json.loads(
            (INTEGRATION / "strings.json").read_text(encoding="utf-8")
        )

        for translation_key in strings["entity"]["switch"]:
            self.assertIn(f'"{translation_key}"', switch_source)


if __name__ == "__main__":
    unittest.main()

"""Ensure entity declarations and translation catalogs stay synchronized."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "downg_scooter"


class EntityTranslationTests(unittest.TestCase):
    def test_entity_translation_keys_match_platform_sources(self) -> None:
        strings = json.loads(
            (INTEGRATION / "strings.json").read_text(encoding="utf-8")
        )
        for platform, entities in strings["entity"].items():
            source = (INTEGRATION / f"{platform}.py").read_text(encoding="utf-8")
            for translation_key in entities:
                if translation_key.startswith("battery_cell_") and translation_key.removeprefix("battery_cell_").isdigit():
                    self.assertIn('f"battery_cell_{index}"', source)
                else:
                    self.assertIn(f'"{translation_key}"', source)


if __name__ == "__main__":
    unittest.main()

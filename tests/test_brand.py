"""Validate local Home Assistant brand assets."""

from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components" / "downg_scooter" / "brand"


class BrandAssetTests(unittest.TestCase):
    def test_brand_png_dimensions_and_transparency(self) -> None:
        expected_sizes = {
            "icon.png": (256, 256),
            "icon@2x.png": (512, 512),
            "logo.png": (768, 256),
            "logo@2x.png": (1536, 512),
        }

        for filename, expected_size in expected_sizes.items():
            with self.subTest(filename=filename):
                data = (BRAND / filename).read_bytes()
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(data[12:16], b"IHDR")
                self.assertEqual(struct.unpack(">II", data[16:24]), expected_size)
                self.assertIn(data[25], (4, 6), "PNG must contain an alpha channel")


if __name__ == "__main__":
    unittest.main()

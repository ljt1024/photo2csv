import tempfile
import unittest
from pathlib import Path

import fix_csv_for_excel


class FixCsvForExcelTests(unittest.TestCase):
    def test_adds_utf8_bom_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "products.csv"
            original_text = "image_id,title\npdd0001,测试商品\n"
            csv_path.write_text(original_text, encoding="utf-8")

            result = fix_csv_for_excel.fix_csv_for_excel(csv_path)

            self.assertTrue(result.changed)
            self.assertEqual(result.source_encoding, "utf-8")
            self.assertIsNotNone(result.backup_path)
            self.assertTrue(result.backup_path.exists())
            self.assertTrue(csv_path.read_bytes().startswith(fix_csv_for_excel.UTF8_BOM))
            self.assertEqual(csv_path.read_text(encoding="utf-8-sig"), original_text)

    def test_keeps_existing_utf8_bom_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "products.csv"
            original_bytes = fix_csv_for_excel.UTF8_BOM + "image_id,title\npdd0001,测试商品\n".encode("utf-8")
            csv_path.write_bytes(original_bytes)

            result = fix_csv_for_excel.fix_csv_for_excel(csv_path)

            self.assertFalse(result.changed)
            self.assertEqual(result.source_encoding, "utf-8-sig")
            self.assertEqual(csv_path.read_bytes(), original_bytes)

    def test_converts_gb18030_to_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "products.csv"
            original_text = "image_id,title\npdd0001,中文商品\n"
            csv_path.write_bytes(original_text.encode("gb18030"))

            result = fix_csv_for_excel.fix_csv_for_excel(csv_path)

            self.assertTrue(result.changed)
            self.assertEqual(result.source_encoding, "gb18030")
            self.assertTrue(csv_path.read_bytes().startswith(fix_csv_for_excel.UTF8_BOM))
            self.assertEqual(csv_path.read_text(encoding="utf-8-sig"), original_text)


if __name__ == "__main__":
    unittest.main()

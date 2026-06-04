import csv
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import photo2csv


class Photo2CsvTests(unittest.TestCase):
    def test_resolve_output_paths_defaults_to_project_dir(self):
        args = type(
            "Args",
            (),
            {
                "output_dir": None,
                "csv": None,
                "images_dir": None,
                "template_csv": "",
            },
        )()

        csv_path, images_dir, template_csv = photo2csv.resolve_output_paths(args)

        self.assertEqual(csv_path, photo2csv.PROJECT_DIR / "product_data_20260525.csv")
        self.assertEqual(images_dir, photo2csv.PROJECT_DIR / "images")
        self.assertIsNone(template_csv)

    def test_seed_csv_from_template_copies_when_target_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_csv = Path(tmp) / "template.csv"
            target_csv = Path(tmp) / "out" / "product_data_20260525.csv"
            template_csv.write_text(",".join(photo2csv.EXPECTED_FIELDS) + "\n", encoding="utf-8")

            seeded = photo2csv.seed_csv_from_template(target_csv, template_csv)

            self.assertEqual(seeded, target_csv)
            self.assertTrue(target_csv.exists())
            self.assertEqual(target_csv.read_text(encoding="utf-8"), template_csv.read_text(encoding="utf-8"))

    def test_ensure_output_targets_creates_csv_and_images_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_csv = Path(tmp) / "template.csv"
            target_csv = Path(tmp) / "out" / "product_data_20260525.csv"
            images_dir = Path(tmp) / "out" / "images"
            template_csv.write_text(",".join(photo2csv.EXPECTED_FIELDS) + "\n", encoding="utf-8")

            seeded = photo2csv.ensure_output_targets(target_csv, images_dir, template_csv)

            self.assertEqual(seeded, target_csv)
            self.assertTrue(target_csv.exists())
            self.assertTrue(images_dir.is_dir())

    def test_load_env_file_does_not_override_existing_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("PHOTO2CSV_TEST_KEY=from_file\n", encoding="utf-8")

            os.environ["PHOTO2CSV_TEST_KEY"] = "existing"
            try:
                photo2csv.load_env_file(env_path)
                self.assertEqual(os.environ["PHOTO2CSV_TEST_KEY"], "existing")
            finally:
                os.environ.pop("PHOTO2CSV_TEST_KEY", None)

    def test_normalize_platform(self):
        self.assertEqual(photo2csv.normalize_platform("KS"), "ks")
        self.assertEqual(photo2csv.normalize_platform(" xhs "), "xhs")

        with self.assertRaises(photo2csv.AppError):
            photo2csv.normalize_platform("pdd-1")

    def test_next_image_number_reads_existing_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "products.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        ",".join(photo2csv.EXPECTED_FIELDS),
                        "pdd0001,pdd,服装鞋包,0,title,brand,,35.9,闪降价,shop,20260525,",
                        "pdd0020,pdd,食品饮料,1,title,brand,5斤,29.9,原价,shop,20260525,",
                        "ks0003,ks,食品饮料,0,title,brand,,19.9,原价,shop,20260525,",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(photo2csv.next_image_number(csv_path), 21)
            self.assertEqual(photo2csv.next_image_number(csv_path, "ks"), 4)
            self.assertEqual(photo2csv.next_image_number(csv_path, "dy"), 1)

    def test_image_id_uses_platform_prefix(self):
        self.assertEqual(photo2csv.image_id_for(4, "ks"), "ks0004")
        self.assertEqual(photo2csv.image_id_for(21, "dy"), "dy0021")

    def test_group_paths_requires_multiple_of_three(self):
        paths = [Path("1.png"), Path("2.png"), Path("3.png"), Path("4.png")]

        with self.assertRaises(photo2csv.AppError):
            photo2csv.group_paths(paths)

    def test_group_paths_by_dash_name(self):
        paths = [
            Path("test/2-3.jpg"),
            Path("test/1-2.jpg"),
            Path("test/1-1.jpg"),
            Path("test/2-1.jpg"),
            Path("test/1-3.jpg"),
            Path("test/2-2.jpg"),
        ]

        groups = photo2csv.group_paths(sorted(paths, key=photo2csv.natural_sort_key))

        self.assertEqual([path.name for path in groups[0]], ["1-1.jpg", "1-2.jpg", "1-3.jpg"])
        self.assertEqual([path.name for path in groups[1]], ["2-1.jpg", "2-2.jpg", "2-3.jpg"])

    def test_collect_images_from_zip_ignores_macos_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("test/1-1.jpg", b"fake")
                archive.writestr("test/1-2.jpg", b"fake")
                archive.writestr("test/1-3.jpg", b"fake")
                archive.writestr("__MACOSX/test/._1-1.jpg", b"metadata")

            collected = photo2csv.collect_images_from_zip(zip_path)
            try:
                self.assertEqual([path.name for path in collected.paths], ["1-1.jpg", "1-2.jpg", "1-3.jpg"])
                groups = photo2csv.group_paths(collected.paths)
                self.assertEqual(len(groups), 1)
            finally:
                collected.cleanup()

    def test_manual_json_result_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "result.json"
            json_path.write_text(
                json.dumps(
                    [
                        {
                            "category": "食品饮料",
                            "has_sku_info": "有",
                            "title": "某品牌大米5斤装",
                            "brand": "某品牌",
                            "sku_spec": "5斤",
                            "price": "￥29.90",
                            "price_type": "闪降价",
                            "shop_name": "某品牌旗舰店",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = photo2csv.load_manual_results(json_path)[0]

            self.assertEqual(result.category, "食品饮料")
            self.assertEqual(result.has_sku_info, 1)
            self.assertEqual(result.sku_spec, "5斤")
            self.assertEqual(result.price, "29.9")

    def test_pet_supplies_category_is_supported(self):
        result = photo2csv.RecognitionResult.from_mapping(
            {
                "category": "宠物用品",
                "has_sku_info": 0,
                "title": "宠物猫粮",
                "price": "39.9",
            }
        )

        self.assertEqual(result.category, "宠物用品")

    def test_parse_json_object_extracts_wrapped_response(self):
        data = photo2csv.parse_json_object(
            '识别结果如下：{"category":"食品饮料","has_sku_info":0,"title":"茶饮","price":"9.9"}'
        )

        self.assertEqual(data["category"], "食品饮料")
        self.assertEqual(data["price"], "9.9")

    def test_append_rows_preserves_expected_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "products.csv"
            row = {
                "image_id": "pdd0021",
                "platform": "pdd",
                "category": "食品饮料",
                "has_sku_info": "1",
                "title": "某品牌大米5斤装",
                "brand": "某品牌",
                "sku_spec": "5斤",
                "price": "29.9",
                "price_type": "闪降价",
                "shop_name": "某品牌旗舰店",
                "capture_time": "20260601",
                "remark": "",
            }

            photo2csv.append_rows(csv_path, [row], photo2csv.EXPECTED_FIELDS)

            with csv_path.open("r", encoding="utf-8", newline="") as file:
                reader = csv.DictReader(file)
                rows = list(reader)

            self.assertEqual(reader.fieldnames, list(photo2csv.EXPECTED_FIELDS))
            self.assertEqual(rows[0]["image_id"], "pdd0021")
            self.assertEqual(rows[0]["capture_time"], "20260601")


if __name__ == "__main__":
    unittest.main()

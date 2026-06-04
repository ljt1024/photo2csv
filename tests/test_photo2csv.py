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
            self.assertTrue(target_csv.read_bytes().startswith(photo2csv.UTF8_BOM))
            self.assertEqual(target_csv.read_text(encoding="utf-8-sig"), template_csv.read_text(encoding="utf-8"))

    def test_ensure_output_targets_creates_csv_and_images_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_csv = Path(tmp) / "template.csv"
            target_csv = Path(tmp) / "out" / "product_data_20260525.csv"
            images_dir = Path(tmp) / "out" / "images"
            template_csv.write_text(",".join(photo2csv.EXPECTED_FIELDS) + "\n", encoding="utf-8")

            seeded = photo2csv.ensure_output_targets(target_csv, images_dir, template_csv)

            self.assertEqual(seeded, target_csv)
            self.assertTrue(target_csv.exists())
            self.assertTrue(target_csv.read_bytes().startswith(photo2csv.UTF8_BOM))
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

    def test_image_to_data_url_uses_jpeg_mime_type_for_jpg(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "1-1.jpg"
            path.write_bytes(b"fake jpg bytes")

            data_url = photo2csv.image_to_data_url(path)

            self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))

    def test_prepare_groups_uses_jpg_destination(self):
        result = photo2csv.RecognitionResult(
            category="食品饮料",
            has_sku_info=0,
            title="测试商品",
            brand="",
            sku_spec="",
            price="9.9",
            price_type="原价",
            shop_name="测试店",
        )

        prepared = photo2csv.prepare_groups(
            [(Path("1-1.jpg"), Path("1-2.jpg"), Path("1-3.jpg"))],
            [result],
            csv_path=Path("missing.csv"),
            images_dir=Path("images"),
            platform="ks",
            capture_time="20260604",
            start_number=1,
        )

        self.assertEqual(prepared[0].destination_image, Path("images/ks0001.jpg"))

    def test_prepare_groups_preserves_png_destination(self):
        result = photo2csv.RecognitionResult(
            category="食品饮料",
            has_sku_info=0,
            title="测试商品",
            brand="",
            sku_spec="",
            price="9.9",
            price_type="原价",
            shop_name="测试店",
        )

        prepared = photo2csv.prepare_groups(
            [(Path("1-1.png"), Path("1-2.png"), Path("1-3.png"))],
            [result],
            csv_path=Path("missing.csv"),
            images_dir=Path("images"),
            platform="ks",
            capture_time="20260604",
            start_number=1,
        )

        self.assertEqual(prepared[0].destination_image, Path("images/ks0001.png"))

    def test_prepare_groups_normalizes_jpeg_destination_to_jpg(self):
        result = photo2csv.RecognitionResult(
            category="食品饮料",
            has_sku_info=0,
            title="测试商品",
            brand="",
            sku_spec="",
            price="9.9",
            price_type="原价",
            shop_name="测试店",
        )

        prepared = photo2csv.prepare_groups(
            [(Path("1-1.jpeg"), Path("1-2.jpeg"), Path("1-3.jpeg"))],
            [result],
            csv_path=Path("missing.csv"),
            images_dir=Path("images"),
            platform="ks",
            capture_time="20260604",
            start_number=1,
        )

        self.assertEqual(prepared[0].destination_image, Path("images/ks0001.jpg"))

    def test_save_first_image_copies_jpg_without_converting(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "1-1.jpg"
            destination = Path(tmp) / "images" / "ks0001.jpg"
            source.write_bytes(b"fake jpg bytes")

            photo2csv.save_first_image(source, destination, move=False)

            self.assertTrue(source.exists())
            self.assertEqual(destination.read_bytes(), b"fake jpg bytes")

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

    def test_manual_json_invalid_item_becomes_error_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "result.json"
            json_path.write_text(
                json.dumps(
                    [
                        {
                            "category": "不存在的品类",
                            "has_sku_info": 0,
                            "title": "坏数据",
                            "price": "9.9",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = photo2csv.load_manual_results(json_path)[0]

            self.assertEqual(result.title, photo2csv.ERROR_TITLE)
            self.assertIn("识别错误", result.remark)
            self.assertIn("不存在的品类", result.remark)

    def test_recognize_groups_keeps_order_when_one_group_fails(self):
        class FakeRecognizer:
            def __init__(self):
                self.calls = 0

            def recognize(self, _group):
                self.calls += 1
                if self.calls == 2:
                    raise photo2csv.AppError("第二组 JSON 错误")
                return photo2csv.RecognitionResult(
                    category="食品饮料",
                    has_sku_info=0,
                    title=f"成功{self.calls}",
                    brand="",
                    sku_spec="",
                    price="9.9",
                    price_type="原价",
                    shop_name="测试店",
                )

        groups = [
            (Path("1-1.jpg"), Path("1-2.jpg"), Path("1-3.jpg")),
            (Path("2-1.jpg"), Path("2-2.jpg"), Path("2-3.jpg")),
            (Path("3-1.jpg"), Path("3-2.jpg"), Path("3-3.jpg")),
        ]

        results = photo2csv.recognize_groups(groups, manual_results_path=None, recognizer=FakeRecognizer())

        self.assertEqual([result.title for result in results], ["成功1", photo2csv.ERROR_TITLE, "成功3"])
        self.assertIn("第二组 JSON 错误", results[1].remark)

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

            self.assertTrue(csv_path.read_bytes().startswith(photo2csv.UTF8_BOM))
            with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                rows = list(reader)

            self.assertEqual(reader.fieldnames, list(photo2csv.EXPECTED_FIELDS))
            self.assertEqual(rows[0]["image_id"], "pdd0021")
            self.assertEqual(rows[0]["capture_time"], "20260601")

    def test_append_rows_starts_new_line_when_existing_csv_lacks_final_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "products.csv"
            existing_row = "pdd0001,pdd,食品饮料,0,旧商品,旧品牌,,10,原价,旧店,20260601,"
            csv_path.write_text(
                ",".join(photo2csv.EXPECTED_FIELDS) + "\n" + existing_row,
                encoding="utf-8",
            )
            row = {
                "image_id": "pdd0002",
                "platform": "pdd",
                "category": "食品饮料",
                "has_sku_info": "0",
                "title": "新商品",
                "brand": "新品牌",
                "sku_spec": "",
                "price": "12.5",
                "price_type": "原价",
                "shop_name": "新店",
                "capture_time": "20260602",
                "remark": "",
            }

            photo2csv.append_rows(csv_path, [row], photo2csv.EXPECTED_FIELDS)

            self.assertTrue(csv_path.read_bytes().startswith(photo2csv.UTF8_BOM))
            content = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("\npdd0002,", content)
            self.assertTrue(content.endswith("\n"))
            with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                rows = list(reader)

            self.assertEqual([row["image_id"] for row in rows], ["pdd0001", "pdd0002"])

    def test_append_rows_keeps_single_bom_when_existing_csv_already_has_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "products.csv"
            csv_path.write_bytes(photo2csv.UTF8_BOM + (",".join(photo2csv.EXPECTED_FIELDS) + "\n").encode("utf-8"))
            row = {
                "image_id": "pdd0001",
                "platform": "pdd",
                "category": "食品饮料",
                "has_sku_info": "0",
                "title": "新商品",
                "brand": "新品牌",
                "sku_spec": "",
                "price": "12.5",
                "price_type": "原价",
                "shop_name": "新店",
                "capture_time": "20260602",
                "remark": "",
            }

            photo2csv.append_rows(csv_path, [row], photo2csv.EXPECTED_FIELDS)

            self.assertEqual(csv_path.read_bytes().count(photo2csv.UTF8_BOM), 1)


if __name__ == "__main__":
    unittest.main()

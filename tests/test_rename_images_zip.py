import unittest
from pathlib import Path

import rename_images_zip


class RenameImagesZipTests(unittest.TestCase):
    def test_archive_name_uses_custom_group_size(self):
        names = [
            rename_images_zip.archive_name(index, start_number=1, suffix=".jpg", group_size=2)
            for index in range(4)
        ]

        self.assertEqual(names, ["1-1.jpg", "1-2.jpg", "2-1.jpg", "2-2.jpg"])

    def test_build_archive_plan_uses_custom_group_size(self):
        images = [Path(f"img{index}.jpg") for index in range(1, 5)]

        planned = rename_images_zip.build_archive_plan(images, start_number=3, group_size=2)

        self.assertEqual(
            [arcname for _image, arcname in planned],
            ["3-1.jpg", "3-2.jpg", "4-1.jpg", "4-2.jpg"],
        )

    def test_validate_image_count_requires_multiple_of_group_size(self):
        images = [Path("1.jpg"), Path("2.jpg"), Path("3.jpg")]

        with self.assertRaises(rename_images_zip.RenameZipError):
            rename_images_zip.validate_image_count(images, group_size=2)


if __name__ == "__main__":
    unittest.main()

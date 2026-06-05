#!/usr/bin/env python3
"""Package images into a ZIP with grouped, renamed archive entries."""

import argparse
import platform
import sys
import zipfile
from collections import defaultdict
from pathlib import Path


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}
DEFAULT_GROUP_SIZE = 3


class RenameZipError(Exception):
    """User-facing error raised for invalid input or unsafe operations."""


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=f"将图片按每 {DEFAULT_GROUP_SIZE} 张一组重命名后写入 ZIP，不修改原始图片文件。"
    )
    parser.add_argument("folder", help="图片文件夹路径，只处理第一层文件")
    parser.add_argument(
        "--start",
        type=int,
        default=1,
        help="起始编号，默认 1",
    )
    parser.add_argument(
        "--sort",
        choices=("name", "created", "modified"),
        default="name",
        help="排序方式：name 文件名，created 创建时间，modified 修改时间；默认 name",
    )
    parser.add_argument(
        "--group-size",
        type=int,
        default=DEFAULT_GROUP_SIZE,
        help=f"每组图片数量，默认 {DEFAULT_GROUP_SIZE}；例如 2 会生成 1-1、1-2、2-1、2-2",
    )
    parser.add_argument(
        "--output",
        default="renamed_images.zip",
        help="输出 ZIP 文件路径，默认 ./renamed_images.zip",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已存在的 ZIP 文件",
    )
    return parser.parse_args(argv)


def validate_args(args):
    folder = Path(args.folder).expanduser()
    output = Path(args.output).expanduser()

    if args.start < 1:
        raise RenameZipError("--start 必须是大于等于 1 的整数。")
    validate_group_size(args.group_size)
    if not folder.exists():
        raise RenameZipError(f"图片文件夹不存在：{folder}")
    if not folder.is_dir():
        raise RenameZipError(f"指定路径不是文件夹：{folder}")
    if output.exists() and not args.overwrite:
        raise RenameZipError(f"输出文件已存在：{output}。如需覆盖，请添加 --overwrite。")
    if output.exists() and output.is_dir():
        raise RenameZipError(f"输出路径是文件夹，不能写入 ZIP：{output}")
    if not output.parent.exists():
        raise RenameZipError(f"输出目录不存在：{output.parent}")

    return folder, output


def find_images(folder):
    images = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not images:
        raise RenameZipError(f"未找到支持的图片文件：{folder}")
    return images


def validate_group_size(group_size):
    if group_size < 1:
        raise RenameZipError("--group-size 必须是大于等于 1 的整数。")


def validate_image_count(images, group_size):
    validate_group_size(group_size)
    if len(images) % group_size != 0:
        raise RenameZipError(f"图片数量必须是 {group_size} 的倍数；当前数量：{len(images)}。")


def created_time(path):
    system = platform.system()
    stat_result = path.stat()

    if system == "Darwin":
        if not hasattr(stat_result, "st_birthtime"):
            raise RenameZipError("当前 macOS/Python 环境无法读取文件创建时间 st_birthtime。")
        return stat_result.st_birthtime
    if system == "Windows":
        return stat_result.st_ctime

    raise RenameZipError(
        f"当前系统不支持可靠的创建时间排序：{system}。请改用 --sort name 或 --sort modified。"
    )


def ensure_unique_created_times(images):
    by_timestamp = defaultdict(list)
    for image in images:
        by_timestamp[created_time(image)].append(image)

    conflicts = {ts: paths for ts, paths in by_timestamp.items() if len(paths) > 1}
    if not conflicts:
        return

    lines = ["发现创建时间完全相同的图片，无法严格保证创建时间顺序："]
    for timestamp, paths in sorted(conflicts.items()):
        joined = ", ".join(path.name for path in sorted(paths, key=lambda item: item.name))
        lines.append(f"- {timestamp}: {joined}")
    raise RenameZipError("\n".join(lines))


def sort_images(images, sort_mode):
    if sort_mode == "name":
        return sorted(images, key=lambda path: (path.name.casefold(), path.name))
    if sort_mode == "modified":
        return sorted(
            images,
            key=lambda path: (path.stat().st_mtime, path.name.casefold(), path.name),
        )
    if sort_mode == "created":
        ensure_unique_created_times(images)
        return sorted(images, key=created_time)

    raise RenameZipError(f"未知排序方式：{sort_mode}")


def archive_name(index, start_number, suffix, group_size=DEFAULT_GROUP_SIZE):
    validate_group_size(group_size)
    group_number = start_number + (index // group_size)
    position = (index % group_size) + 1
    return f"{group_number}-{position}{suffix}"


def build_archive_plan(images, start_number, group_size=DEFAULT_GROUP_SIZE):
    validate_group_size(group_size)
    planned = []
    used_names = set()
    for index, image in enumerate(images):
        arcname = archive_name(index, start_number, image.suffix, group_size)
        if arcname in used_names:
            raise RenameZipError(f"ZIP 内文件名冲突：{arcname}")
        used_names.add(arcname)
        planned.append((image, arcname))
    return planned


def write_zip(planned_images, output):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image, arcname in planned_images:
            archive.write(image, arcname=arcname)


def run(argv):
    args = parse_args(argv)
    folder, output = validate_args(args)
    images = find_images(folder)
    validate_image_count(images, args.group_size)
    sorted_images = sort_images(images, args.sort)
    planned_images = build_archive_plan(sorted_images, args.start, args.group_size)
    write_zip(planned_images, output)

    print(f"已创建 ZIP：{output}")
    print(f"图片数量：{len(planned_images)}")
    print("ZIP 内文件：")
    for _, arcname in planned_images:
        print(f"- {arcname}")


def main():
    try:
        run(sys.argv[1:])
    except RenameZipError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"文件系统错误：{error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

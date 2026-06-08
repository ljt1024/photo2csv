#!/usr/bin/env python3
"""Fix CSV encoding so Excel can open Chinese text without garbling."""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = PROJECT_DIR / "product_data_20260525.csv"
UTF8_BOM = b"\xef\xbb\xbf"
UTF16_LE_BOM = b"\xff\xfe"
UTF16_BE_BOM = b"\xfe\xff"
AUTO_ENCODINGS = ("utf-8", "gb18030")


class CsvFixError(Exception):
    """User-facing error for CSV repair failures."""


@dataclass(frozen=True)
class FixResult:
    csv_path: Path
    output_path: Path
    source_encoding: str
    changed: bool
    backup_path: Path | None = None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 CSV 转成 UTF-8 with BOM，避免 Windows Excel 直接打开中文乱码。"
    )
    parser.add_argument(
        "csv",
        nargs="?",
        default=str(DEFAULT_CSV),
        help=f"要修复的 CSV，默认：{DEFAULT_CSV}",
    )
    parser.add_argument(
        "--output",
        help="输出到新文件；不传则原地修复。",
    )
    parser.add_argument(
        "--encoding",
        help="手动指定原始文件编码，例如 utf-8、gb18030、gbk；默认自动尝试。",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="原地修复时不生成 .bak 备份。",
    )
    return parser.parse_args(argv)


def decode_csv_content(data: bytes, encoding: str | None = None) -> tuple[str, str]:
    if encoding:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError as error:
            raise CsvFixError(f"无法按指定编码 {encoding} 解码 CSV：{error}") from error
        except LookupError as error:
            raise CsvFixError(f"未知编码：{encoding}") from error

    if data.startswith(UTF8_BOM):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith(UTF16_LE_BOM) or data.startswith(UTF16_BE_BOM):
        return data.decode("utf-16"), "utf-16"

    for candidate in AUTO_ENCODINGS:
        try:
            return data.decode(candidate), candidate
        except UnicodeDecodeError:
            continue

    raise CsvFixError("无法自动识别 CSV 编码，请使用 --encoding 手动指定。")


def backup_csv(csv_path: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = csv_path.with_name(f"{csv_path.name}.bak.{stamp}")
    shutil.copy2(csv_path, backup_path)
    return backup_path


def write_utf8_bom_csv(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned_text = text.removeprefix("\ufeff")
    path.write_bytes(UTF8_BOM + cleaned_text.encode("utf-8"))


def fix_csv_for_excel(
    csv_path: Path,
    *,
    output_path: Path | None = None,
    backup: bool = True,
    encoding: str | None = None,
) -> FixResult:
    csv_path = csv_path.expanduser()
    output_path = output_path.expanduser() if output_path else csv_path

    if not csv_path.exists():
        raise CsvFixError(f"CSV 文件不存在：{csv_path}")
    if csv_path.is_dir():
        raise CsvFixError(f"指定路径是文件夹，不是 CSV：{csv_path}")
    if output_path.exists() and output_path.is_dir():
        raise CsvFixError(f"输出路径是文件夹，不能写入 CSV：{output_path}")

    original = csv_path.read_bytes()
    text, source_encoding = decode_csv_content(original, encoding)
    fixed = UTF8_BOM + text.removeprefix("\ufeff").encode("utf-8")
    same_target = csv_path.resolve() == output_path.resolve()

    if same_target and original == fixed:
        return FixResult(
            csv_path=csv_path,
            output_path=output_path,
            source_encoding=source_encoding,
            changed=False,
        )

    backup_path = backup_csv(csv_path) if same_target and backup else None
    write_utf8_bom_csv(output_path, text)
    return FixResult(
        csv_path=csv_path,
        output_path=output_path,
        source_encoding=source_encoding,
        changed=True,
        backup_path=backup_path,
    )


def run(argv: list[str]) -> int:
    args = parse_args(argv)
    result = fix_csv_for_excel(
        Path(args.csv),
        output_path=Path(args.output) if args.output else None,
        backup=not args.no_backup,
        encoding=args.encoding,
    )

    if result.changed:
        print(f"已修复 CSV：{result.output_path}")
        print(f"原始编码：{result.source_encoding}")
        print("目标编码：utf-8-sig")
        if result.backup_path:
            print(f"备份文件：{result.backup_path}")
    else:
        print(f"无需修复，CSV 已是 UTF-8 with BOM：{result.csv_path}")

    return 0


def main() -> int:
    try:
        return run(sys.argv[1:])
    except CsvFixError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"文件系统错误：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

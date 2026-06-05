#!/usr/bin/env python3
"""
Process PDD product screenshot groups into the product CSV.

Usage examples:
  python photo2csv.py ./a.png ./b.png ./c.png
  python photo2csv.py --input-dir ./new_images --dry-run
  python photo2csv.py ./a.png ./b.png ./c.png --manual-json result.json
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import mimetypes
import os
import re
import shutil
import ssl
import sys
import time
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR
DEFAULT_TEMPLATE_CSV = Path("/Users/ljt/Downloads/product_data_20260525.csv")

load_env_file(PROJECT_DIR / ".env")

DEFAULT_CSV_NAME = "product_data_20260525.csv"
DEFAULT_IMAGES_DIR_NAME = "images"
DEFAULT_CSV = DEFAULT_OUTPUT_DIR / DEFAULT_CSV_NAME
DEFAULT_IMAGES_DIR = DEFAULT_OUTPUT_DIR / DEFAULT_IMAGES_DIR_NAME
DEFAULT_PLATFORM = "pdd"
PLATFORM_EXAMPLES = ("pdd", "ks", "dy", "jd", "xhs")
DEFAULT_GROUP_SIZE = 3
DEFAULT_MODEL = os.environ.get("QWEN_MODEL") or os.environ.get("DASHSCOPE_MODEL") or "qwen3-vl-plus"
DASHSCOPE_API_BASE = (
    os.environ.get("QWEN_API_BASE")
    or os.environ.get("DASHSCOPE_API_BASE")
    or "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

CATEGORIES = (
    "美妆护肤",
    "食品饮料",
    "个护家清",
    "母婴用品",
    "保健滋补",
    "数码家电",
    "家居百货",
    "服装鞋包",
    "宠物用品",
)

ERROR_TITLE = "识别失败"
MAX_ERROR_REMARK_LENGTH = 800
UTF8_BOM = b"\xef\xbb\xbf"

EXPECTED_FIELDS = (
    "image_id",
    "platform",
    "category",
    "has_sku_info",
    "title",
    "brand",
    "sku_spec",
    "price",
    "price_type",
    "shop_name",
    "capture_time",
    "remark",
)

SUPPORTED_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}

IMAGE_ID_RE = re.compile(r"^([a-zA-Z]+)(\d+)$")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
GROUPED_IMAGE_RE = re.compile(r"^(.+)[-_](\d+)$")
PLATFORM_RE = re.compile(r"^[a-z]+$")


class AppError(Exception):
    """Expected user-facing error."""


@dataclass(frozen=True)
class RecognitionResult:
    category: str
    has_sku_info: int
    title: str
    brand: str
    sku_spec: str
    price: str
    price_type: str
    shop_name: str
    remark: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "RecognitionResult":
        category = normalize_category(str(data.get("category", "")).strip())
        has_sku_info = normalize_has_sku_info(data.get("has_sku_info", 0))
        sku_spec = clean_text(data.get("sku_spec", ""))

        if not has_sku_info:
            sku_spec = ""

        return cls(
            category=category,
            has_sku_info=has_sku_info,
            title=clean_text(data.get("title", "")),
            brand=clean_text(data.get("brand", "")),
            sku_spec=sku_spec,
            price=normalize_price(data.get("price", "")),
            price_type=clean_text(data.get("price_type", "")),
            shop_name=clean_text(data.get("shop_name", "")),
            remark=clean_text(data.get("remark", "")),
        )

    def to_csv_row(
        self,
        *,
        image_id: str,
        platform: str,
        capture_time: str,
    ) -> dict[str, str]:
        return {
            "image_id": image_id,
            "platform": platform,
            "category": self.category,
            "has_sku_info": str(self.has_sku_info),
            "title": self.title,
            "brand": self.brand,
            "sku_spec": self.sku_spec,
            "price": self.price,
            "price_type": self.price_type,
            "shop_name": self.shop_name,
            "capture_time": capture_time,
            "remark": self.remark,
        }


def error_recognition_result(message: str) -> RecognitionResult:
    cleaned = clean_text(message)
    if len(cleaned) > MAX_ERROR_REMARK_LENGTH:
        cleaned = f"{cleaned[:MAX_ERROR_REMARK_LENGTH]}..."

    return RecognitionResult(
        category="",
        has_sku_info=0,
        title=ERROR_TITLE,
        brand="",
        sku_spec="",
        price="",
        price_type="",
        shop_name="",
        remark=f"识别错误：{cleaned}",
    )


@dataclass(frozen=True)
class PreparedGroup:
    image_id: str
    source_images: tuple[Path, ...]
    destination_image: Path
    result: RecognitionResult
    csv_row: dict[str, str]


@dataclass
class CollectedImages:
    paths: list[Path]
    temp_dir: tempfile.TemporaryDirectory | None = None

    def cleanup(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()
            self.temp_dir = None


class QwenVisionRecognizer:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        api_base: str = DASHSCOPE_API_BASE,
        timeout: int = 90,
        retries: int = 2,
        verify_ssl: bool = True,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.ssl_context = ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()

    def recognize(self, image_paths: Sequence[Path]) -> RecognitionResult:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "你是电商商品图片信息录入助手。只输出合法 JSON，"
                                "不要输出 Markdown，不要解释。"
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": build_recognition_prompt(len(image_paths))},
                        *[
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_to_data_url(path),
                                    "detail": "high",
                                },
                            }
                            for path in image_paths
                        ],
                    ],
                },
            ],
            "max_tokens": 1000,
        }

        data = self._post_json("/chat/completions", payload)
        try:
            content = extract_message_content(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError(f"千问返回格式异常：{json.dumps(data, ensure_ascii=False)}") from exc

        return RecognitionResult.from_mapping(parse_json_object(content))

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.api_base}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": "photo2csv/1.0",
        }

        last_error: str | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout, context=self.ssl_context) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                    break
            except urllib.error.URLError as exc:
                last_error = format_network_error(exc)
            except (ssl.SSLError, TimeoutError, OSError) as exc:
                last_error = format_network_error(exc)

            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))

        raise AppError(f"调用千问识别失败：{last_error}")


def format_network_error(exc: BaseException) -> str:
    message = str(exc)
    reason = getattr(exc, "reason", None)
    if reason is not None:
        message = str(reason)

    tips: list[str] = []
    lowered = message.lower()
    if "eof occurred in violation of protocol" in lowered or "ssl" in lowered:
        tips.append("这是 TLS/SSL 连接被提前断开的错误，常见于本机证书、代理/VPN、网络拦截或网关临时中断")
        tips.append("可先重试：--retries 5")
        tips.append("排查证书/代理时可临时加：--no-verify-ssl")
    elif "timed out" in lowered or "timeout" in lowered:
        tips.append("请求超时，可加大：--timeout 180 --retries 5")

    if tips:
        return f"{message}；{'；'.join(tips)}"
    return message


def build_recognition_prompt(image_count: int = DEFAULT_GROUP_SIZE) -> str:
    category_text = "、".join(CATEGORIES)
    return f"""
请从这组 {image_count} 张同一商品的拼多多商品图片中识别信息，并输出 JSON。

字段要求：
- category：必须严格取以下 {len(CATEGORIES)} 个值之一：{category_text}
- has_sku_info：图片中出现规格、颜色、尺寸、容量、重量、组合装等 SKU 选择信息时填 1，否则填 0
- title：商品标题，尽量保留图片中的标题文本
- brand：品牌名，无法确认则填空字符串
- sku_spec：SKU 规格，例如 "4斤10卷"、"10斤"；has_sku_info 为 0 时填空字符串
- price：只填数字，不要货币符号，例如 29.9
- price_type：价格类型，例如 闪降价、原价、限量价、大促价、券后、首件价、活动价；无法确认则填空字符串
- shop_name：店铺名称，无法确认则填空字符串
- remark：可选备注，默认空字符串

只输出如下结构的 JSON 对象，不要输出其他文字：
{{
  "category": "食品饮料",
  "has_sku_info": 1,
  "title": "商品标题",
  "brand": "品牌",
  "sku_spec": "5斤",
  "price": 29.9,
  "price_type": "闪降价",
  "shop_name": "店铺名称",
  "remark": ""
}}
""".strip()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def normalize_category(value: str) -> str:
    if value in CATEGORIES:
        return value

    for category in CATEGORIES:
        if category in value:
            return category

    raise AppError(f"品类必须是以下 {len(CATEGORIES)} 个之一：{'、'.join(CATEGORIES)}；实际得到：{value!r}")


def normalize_has_sku_info(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) else 0

    text = clean_text(value).lower()
    if text in {"1", "true", "yes", "y", "有", "是", "包含"}:
        return 1
    if text in {"0", "false", "no", "n", "无", "否", "不包含", ""}:
        return 0
    raise AppError(f"has_sku_info 只能是 0/1；实际得到：{value!r}")


def normalize_price(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (int, float, Decimal)):
        return format_decimal(Decimal(str(value)))

    text = clean_text(value).replace(",", "")
    if not text:
        return ""

    match = NUMBER_RE.search(text)
    if not match:
        raise AppError(f"价格无法解析为数字：{value!r}")

    return format_decimal(Decimal(match.group(0)))


def format_decimal(value: Decimal) -> str:
    try:
        normalized = value.normalize()
    except InvalidOperation as exc:
        raise AppError(f"价格无法解析为数字：{value!r}") from exc

    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        extracted = extract_first_json_object(stripped)
        if extracted is None:
            raise AppError(f"识别结果不是合法 JSON：{text}") from exc
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError as inner_exc:
            raise AppError(f"识别结果不是合法 JSON：{text}") from inner_exc

    if not isinstance(data, dict):
        raise AppError("识别结果必须是 JSON 对象")
    return data


def extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(clean_text(item.get("text", "")))
        return "\n".join(part for part in text_parts if part)

    raise TypeError(f"unsupported message content type: {type(content).__name__}")


def image_to_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(path))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = sniff_image_mime_type(path)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def sniff_image_mime_type(path: Path) -> str:
    header = path.read_bytes()[:16]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def load_manual_results(path: Path) -> list[RecognitionResult]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AppError(f"--manual-json 文件不是合法 JSON：{path}") from exc

    if isinstance(raw, dict) and "groups" in raw:
        raw = raw["groups"]
    elif isinstance(raw, dict):
        raw = [raw]

    if not isinstance(raw, list):
        raise AppError("--manual-json 必须是对象、对象数组，或包含 groups 数组的对象")

    results: list[RecognitionResult] = []
    for index, item in enumerate(raw, start=1):
        try:
            if not isinstance(item, dict):
                raise AppError(f"--manual-json 第 {index} 组不是 JSON 对象")
            results.append(RecognitionResult.from_mapping(item))
        except Exception as exc:
            results.append(error_recognition_result(f"--manual-json 第 {index} 组解析失败：{format_exception_message(exc)}"))
    return results


def collect_image_paths(args: argparse.Namespace) -> CollectedImages:
    input_zip = getattr(args, "input_zip", None)
    selected_inputs = sum(bool(value) for value in (args.input_dir, input_zip, args.images))
    if selected_inputs > 1:
        raise AppError("请使用 --input-dir、--input-zip 或直接传图片路径，三者不要同时使用")

    if not input_zip and len(args.images) == 1 and Path(args.images[0]).suffix.lower() == ".zip":
        input_zip = args.images[0]

    if args.input_dir:
        directory = Path(args.input_dir).expanduser()
        if not directory.is_dir():
            raise AppError(f"输入目录不存在：{directory}")
        return CollectedImages(collect_images_from_directory(directory))

    if input_zip:
        return collect_images_from_zip(Path(input_zip).expanduser())

    return CollectedImages([Path(path).expanduser() for path in args.images])


def collect_images_from_directory(directory: Path) -> list[Path]:
    paths = [path for path in directory.rglob("*") if is_real_image_file(path)]
    return sorted(paths, key=natural_sort_key)


def collect_images_from_zip(zip_path: Path) -> CollectedImages:
    if not zip_path.is_file():
        raise AppError(f"压缩包不存在：{zip_path}")
    if zip_path.suffix.lower() != ".zip":
        raise AppError(f"当前只支持 .zip 素材包：{zip_path}")

    temp_dir = tempfile.TemporaryDirectory(prefix="photo2csv-zip-")
    root = Path(temp_dir.name)
    paths: list[Path] = []

    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                if member.is_dir() or should_skip_zip_member(member.filename):
                    continue

                relative_path = safe_zip_member_path(member.filename)
                if relative_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                    continue

                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                paths.append(destination)
    except zipfile.BadZipFile as exc:
        temp_dir.cleanup()
        raise AppError(f"压缩包无法读取：{zip_path}") from exc
    except Exception:
        temp_dir.cleanup()
        raise

    if not paths:
        temp_dir.cleanup()
        raise AppError(f"压缩包中没有找到图片：{zip_path}")

    return CollectedImages(sorted(paths, key=natural_sort_key), temp_dir=temp_dir)


def is_real_image_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        and not path.name.startswith("._")
        and "__MACOSX" not in path.parts
    )


def should_skip_zip_member(filename: str) -> bool:
    parts = PurePosixPath(filename).parts
    name = parts[-1] if parts else ""
    return "__MACOSX" in parts or name.startswith("._") or name == ".DS_Store"


def safe_zip_member_path(filename: str) -> Path:
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts:
        raise AppError(f"压缩包包含不安全路径：{filename}")
    return Path(*path.parts)


def natural_sort_key(path: Path) -> list[Any]:
    parts = re.split(r"(\d+)", str(path).lower())
    return [int(part) if part.isdigit() else part for part in parts]


def group_paths(
    paths: Sequence[Path],
    *,
    mode: str = "auto",
    group_size: int = DEFAULT_GROUP_SIZE,
) -> list[tuple[Path, ...]]:
    if not paths:
        raise AppError("没有找到要处理的图片")
    validate_group_size(group_size)
    if mode not in {"auto", "name", "sequential"}:
        raise AppError(f"未知分组方式：{mode}")

    if mode in {"auto", "name"}:
        grouped = group_paths_by_name(paths, group_size=group_size)
        if grouped is not None:
            return grouped
        if mode == "name":
            raise AppError(f"按命名分组失败：图片名需要类似 {group_name_example(group_size)}")

    return group_paths_sequential(paths, group_size=group_size)


def group_paths_sequential(paths: Sequence[Path], *, group_size: int = DEFAULT_GROUP_SIZE) -> list[tuple[Path, ...]]:
    validate_group_size(group_size)
    if len(paths) % group_size != 0:
        raise AppError(f"图片数量必须是 {group_size} 的倍数；当前数量：{len(paths)}")

    groups: list[tuple[Path, ...]] = []
    for index in range(0, len(paths), group_size):
        group = tuple(paths[index : index + group_size])
        if len(group) != group_size:
            raise AppError("图片分组失败")
        groups.append(group)
    return groups


def group_paths_by_name(paths: Sequence[Path], *, group_size: int = DEFAULT_GROUP_SIZE) -> list[tuple[Path, ...]] | None:
    validate_group_size(group_size)
    grouped: dict[tuple[str, str], dict[int, Path]] = {}
    matched_count = 0

    for path in paths:
        match = GROUPED_IMAGE_RE.match(path.stem)
        if not match:
            return None

        group_name, image_index_text = match.groups()
        image_index = int(image_index_text)
        key = (str(path.parent), group_name)
        group = grouped.setdefault(key, {})
        if image_index in group:
            raise AppError(f"同一组里出现重复序号：{path.name}")
        group[image_index] = path
        matched_count += 1

    if not matched_count:
        return None

    result: list[tuple[Path, ...]] = []
    for (_parent, group_name), group in sorted(grouped.items(), key=lambda item: natural_group_key(item[0])):
        expected_indexes = set(range(1, group_size + 1))
        actual_indexes = set(group)
        if actual_indexes != expected_indexes:
            missing = sorted(expected_indexes - actual_indexes)
            extra = sorted(actual_indexes - expected_indexes)
            details = []
            if missing:
                details.append(f"缺少 {missing}")
            if extra:
                details.append(f"多出 {extra}")
            raise AppError(f"组 {group_name} 图片序号应为 {index_list_text(group_size)}，当前{'; '.join(details)}")
        result.append(tuple(group[index] for index in range(1, group_size + 1)))

    return result


def validate_group_size(group_size: int) -> None:
    if group_size < 1:
        raise AppError("--group-size 必须是大于等于 1 的整数")


def index_list_text(group_size: int) -> str:
    return "、".join(str(index) for index in range(1, group_size + 1))


def group_name_example(group_size: int) -> str:
    return "、".join(f"1-{index}.jpg" for index in range(1, group_size + 1))


def natural_group_key(key: tuple[str, str]) -> list[Any]:
    parent, group_name = key
    parts = re.split(r"(\d+)", f"{parent}/{group_name}".lower())
    return [int(part) if part.isdigit() else part for part in parts]


def validate_source_images(groups: Iterable[Sequence[Path]]) -> None:
    for group_index, group in enumerate(groups, start=1):
        for image_index, path in enumerate(group, start=1):
            if not path.is_file():
                raise AppError(f"第 {group_index} 组第 {image_index} 张图片不存在：{path}")
            if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                raise AppError(f"不支持的图片格式：{path}")


def read_fieldnames(csv_path: Path) -> list[str]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return list(EXPECTED_FIELDS)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        try:
            fieldnames = next(reader)
        except StopIteration:
            return list(EXPECTED_FIELDS)

    missing = [field for field in EXPECTED_FIELDS if field not in fieldnames]
    if missing:
        raise AppError(f"CSV 缺少必要字段：{', '.join(missing)}")
    return fieldnames


def next_image_number(csv_path: Path, platform: str = DEFAULT_PLATFORM) -> int:
    max_number = 0
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return 1

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            image_id = clean_text(row.get("image_id", ""))
            match = IMAGE_ID_RE.match(image_id)
            if not match:
                continue
            prefix, number_text = match.groups()
            if prefix.lower() != platform.lower():
                continue
            max_number = max(max_number, int(number_text))

    return max_number + 1


def image_id_for(number: int, platform: str = DEFAULT_PLATFORM) -> str:
    return f"{platform}{number:04d}"


def recognize_groups(
    groups: Sequence[Sequence[Path]],
    *,
    manual_results_path: Path | None,
    recognizer: QwenVisionRecognizer | None,
) -> list[RecognitionResult]:
    if manual_results_path:
        results = load_manual_results(manual_results_path)
        if len(results) < len(groups):
            for index in range(len(results) + 1, len(groups) + 1):
                results.append(error_recognition_result(f"--manual-json 缺少第 {index} 组识别结果"))
        elif len(results) > len(groups):
            print(f"警告：--manual-json 组数为 {len(results)}，图片组数为 {len(groups)}，多余结果已忽略", file=sys.stderr)
            results = results[: len(groups)]
        return results

    if recognizer is None:
        message = "缺少 DASHSCOPE_API_KEY。请设置千问 API Key，或使用 --manual-json 导入识别结果"
        print(f"警告：{message}；将为每组写入错误提示并继续保存图片", file=sys.stderr)
        return [error_recognition_result(message) for _ in groups]

    results: list[RecognitionResult] = []
    for index, group in enumerate(groups, start=1):
        group_names = ", ".join(path.name for path in group)
        print(f"正在识别第 {index}/{len(groups)} 组：{group_names}")
        try:
            results.append(recognizer.recognize(group))
        except Exception as exc:
            message = format_exception_message(exc)
            print(f"警告：第 {index}/{len(groups)} 组识别失败，已写入错误提示并继续：{message}", file=sys.stderr)
            results.append(error_recognition_result(message))
    return results


def format_exception_message(exc: BaseException) -> str:
    if isinstance(exc, AppError):
        return clean_text(str(exc))
    return clean_text(f"{type(exc).__name__}: {exc}")


def read_qwen_api_key() -> str:
    for name in ("DASHSCOPE_API_KEY", "QWEN_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def normalize_platform(value: str) -> str:
    platform = value.strip().lower()
    if not platform:
        raise AppError("平台标识不能为空")
    if not PLATFORM_RE.match(platform):
        examples = "/".join(PLATFORM_EXAMPLES)
        raise AppError(f"平台标识只能使用小写英文字母，例如 {examples}；实际输入：{value!r}")
    return platform


def prompt_platform() -> str:
    examples = "/".join(PLATFORM_EXAMPLES)
    while True:
        try:
            value = input(f"请输入平台标识（例如 {examples}）：")
        except (EOFError, KeyboardInterrupt) as exc:
            raise AppError(f"缺少平台标识。请使用 --platform {PLATFORM_EXAMPLES[0]}，或在交互终端输入平台名称") from exc

        try:
            return normalize_platform(value)
        except AppError as exc:
            print(f"错误：{exc}", file=sys.stderr)


def resolve_platform(args: argparse.Namespace) -> str:
    if args.platform:
        return normalize_platform(args.platform)
    if not sys.stdin.isatty():
        examples = "/".join(PLATFORM_EXAMPLES)
        raise AppError(f"缺少平台标识。请加参数 --platform，例如 --platform {PLATFORM_EXAMPLES[0]}；可选示例：{examples}")
    return prompt_platform()


def prepare_groups(
    groups: Sequence[tuple[Path, ...]],
    results: Sequence[RecognitionResult],
    *,
    csv_path: Path,
    images_dir: Path,
    platform: str,
    capture_time: str,
    start_number: int | None = None,
) -> list[PreparedGroup]:
    number = start_number if start_number is not None else next_image_number(csv_path, platform)
    prepared: list[PreparedGroup] = []

    if len(groups) != len(results):
        raise AppError(f"图片组数为 {len(groups)}，识别结果组数为 {len(results)}，二者不一致")

    for group, result in zip(groups, results):
        image_id = image_id_for(number, platform)
        destination = images_dir / f"{image_id}{saved_image_suffix(group[0])}"
        if destination.exists() and group[0].resolve() != destination.resolve():
            raise AppError(f"目标图片已存在，避免覆盖：{destination}")

        csv_row = result.to_csv_row(
            image_id=image_id,
            platform=platform,
            capture_time=capture_time,
        )
        prepared.append(
            PreparedGroup(
                image_id=image_id,
                source_images=group,
                destination_image=destination,
                result=result,
                csv_row=csv_row,
            )
        )
        number += 1

    return prepared


def backup_csv(csv_path: Path) -> Path | None:
    if not csv_path.exists():
        return None

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = csv_path.with_name(f"{csv_path.name}.bak.{stamp}")
    shutil.copy2(csv_path, backup_path)
    return backup_path


def save_first_images(prepared_groups: Sequence[PreparedGroup], *, move: bool) -> None:
    for prepared in prepared_groups:
        save_first_image(prepared.source_images[0], prepared.destination_image, move=move)


def saved_image_suffix(source: Path) -> str:
    suffix = source.suffix.lower()
    if suffix == ".jpeg":
        return ".jpg"
    return suffix


def save_first_image(source: Path, destination: Path, *, move: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source.resolve() == destination.resolve():
        return

    if move:
        shutil.move(str(source), str(destination))
    else:
        shutil.copy2(source, destination)


def append_rows(csv_path: Path, rows: Sequence[dict[str, str]], fieldnames: Sequence[str]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    should_write_header = should_write_csv_header(csv_path)
    ensure_utf8_bom(csv_path)
    if not should_write_header:
        ensure_trailing_newline(csv_path)

    with csv_path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        if should_write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def should_write_csv_header(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return True
    if path.stat().st_size == len(UTF8_BOM):
        return path.read_bytes() == UTF8_BOM
    return False


def ensure_utf8_bom(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        path.write_bytes(UTF8_BOM)
        return

    content = path.read_bytes()
    if content.startswith(UTF8_BOM):
        return
    path.write_bytes(UTF8_BOM + content)


def ensure_trailing_newline(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        return

    with path.open("rb+") as file:
        file.seek(-1, os.SEEK_END)
        if file.read(1) not in {b"\n", b"\r"}:
            file.write(b"\n")


def resolve_output_paths(args: argparse.Namespace) -> tuple[Path, Path, Path | None]:
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_OUTPUT_DIR
    csv_path = Path(args.csv).expanduser() if args.csv else output_dir / DEFAULT_CSV_NAME
    images_dir = Path(args.images_dir).expanduser() if args.images_dir else output_dir / DEFAULT_IMAGES_DIR_NAME
    template_csv = Path(args.template_csv).expanduser() if args.template_csv else None
    return csv_path, images_dir, template_csv


def choose_number_source_csv(csv_path: Path, template_csv: Path | None) -> Path:
    if csv_path.exists():
        return csv_path
    if template_csv and template_csv.exists():
        return template_csv
    return csv_path


def seed_csv_from_template(csv_path: Path, template_csv: Path | None) -> Path | None:
    if csv_path.exists() or not template_csv or not template_csv.exists():
        return None
    if csv_path.resolve() == template_csv.resolve():
        return None

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_csv, csv_path)
    ensure_utf8_bom(csv_path)
    return csv_path


def ensure_output_targets(csv_path: Path, images_dir: Path, template_csv: Path | None) -> Path | None:
    seeded_path = seed_csv_from_template(csv_path, template_csv)
    images_dir.mkdir(parents=True, exist_ok=True)
    return seeded_path


def process(args: argparse.Namespace) -> list[PreparedGroup]:
    csv_path, images_dir, template_csv = resolve_output_paths(args)
    group_size = args.group_size
    validate_group_size(group_size)
    platform = resolve_platform(args)
    capture_time = args.capture_time or dt.date.today().strftime("%Y%m%d")

    print(f"平台标识：{platform}")
    print(f"输出 CSV：{csv_path}")
    print(f"输出图片目录：{images_dir}")
    if args.dry_run:
        print("当前是 dry-run 预览模式，不会生成或修改文件。")
    else:
        seeded_path = ensure_output_targets(csv_path, images_dir, template_csv)
        if seeded_path:
            print(f"已从模板复制 CSV 到当前输出目录：{seeded_path}")

    collected = collect_image_paths(args)
    try:
        groups = group_paths(collected.paths, mode=args.group_mode, group_size=group_size)
        validate_source_images(groups)

        recognizer: QwenVisionRecognizer | None = None
        if not args.manual_json:
            api_key = read_qwen_api_key()
            if api_key:
                recognizer = QwenVisionRecognizer(
                    api_key=api_key,
                    model=args.model,
                    api_base=args.api_base,
                    timeout=args.timeout,
                    retries=args.retries,
                    verify_ssl=not args.no_verify_ssl,
                )

        results = recognize_groups(
            groups,
            manual_results_path=Path(args.manual_json).expanduser() if args.manual_json else None,
            recognizer=recognizer,
        )

        prepared = prepare_groups(
            groups,
            results,
            csv_path=csv_path,
            images_dir=images_dir,
            platform=platform,
            capture_time=capture_time,
            start_number=args.start_number or next_image_number(choose_number_source_csv(csv_path, template_csv), platform),
        )

        print_summary(prepared, csv_path=csv_path, dry_run=args.dry_run)

        if args.dry_run:
            return prepared

        fieldnames = read_fieldnames(csv_path)
        backup_path = None if args.no_backup else backup_csv(csv_path)
        if backup_path:
            print(f"已备份 CSV：{backup_path}")

        save_first_images(prepared, move=not args.copy_first_image)
        append_rows(csv_path, [item.csv_row for item in prepared], fieldnames)
        print(f"已追加 {len(prepared)} 行到 CSV：{csv_path}")
        print(f"已保存 {len(prepared)} 张首图到：{images_dir}")
        return prepared
    finally:
        collected.cleanup()


def print_summary(prepared: Sequence[PreparedGroup], *, csv_path: Path, dry_run: bool) -> None:
    action = "预览" if dry_run else "准备写入"
    print(f"\n{action} {len(prepared)} 组，目标 CSV：{csv_path}")
    for item in prepared:
        row = item.csv_row
        if row["remark"].startswith("识别错误："):
            print(f" - {item.image_id}: {ERROR_TITLE} | {row['remark']}")
            continue
        print(
            " - "
            f"{item.image_id}: {row['category']} | {row['title']} | "
            f"{row['price_type']} {row['price']} | SKU={row['sku_spec'] or '无'}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="识别同一商品的一组图片，追加到产品 CSV，并保存每组第一张图。",
    )
    parser.add_argument("images", nargs="*", help="图片路径或单个 .zip 素材包；图片数量必须是 --group-size 的倍数")
    parser.add_argument("--input-dir", help="从目录递归读取图片")
    parser.add_argument("--input-zip", help="从 .zip 素材包读取图片，例如 test.zip")
    parser.add_argument(
        "--group-mode",
        choices=("auto", "name", "sequential"),
        default="auto",
        help="图片分组方式：auto 自动识别 1-1/1-2/...；name 强制按命名；sequential 按排序和 --group-size 分组",
    )
    parser.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE, help=f"每组图片数量，默认 {DEFAULT_GROUP_SIZE}")
    parser.add_argument("--output-dir", help=f"输出目录，默认当前工程目录：{DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--csv", help=f"目标 CSV，默认：{DEFAULT_CSV}")
    parser.add_argument("--images-dir", help=f"图片保存目录，默认：{DEFAULT_IMAGES_DIR}")
    parser.add_argument(
        "--template-csv",
        default=str(DEFAULT_TEMPLATE_CSV),
        help=f"目标 CSV 不存在时用于初始化和续号的模板 CSV，默认：{DEFAULT_TEMPLATE_CSV}",
    )
    parser.add_argument("--platform", help="平台标识，例如 pdd、ks、dy、jd、xhs；不传时会提示输入")
    parser.add_argument("--capture-time", help="采集日期 YYYYMMDD，默认当天")
    parser.add_argument("--start-number", type=int, help="手动指定起始编号，例如 21")
    parser.add_argument("--manual-json", help="跳过模型识别，使用 JSON 文件中的识别结果")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"千问视觉模型，默认：{DEFAULT_MODEL}")
    parser.add_argument("--api-base", default=DASHSCOPE_API_BASE, help=f"千问兼容接口 API base，默认：{DASHSCOPE_API_BASE}")
    parser.add_argument("--timeout", type=int, default=90, help="千问请求超时时间，秒")
    parser.add_argument("--retries", type=int, default=2, help="千问请求重试次数")
    parser.add_argument("--no-verify-ssl", action="store_true", help="临时跳过 HTTPS 证书校验，用于排查本机证书或代理导致的 TLS 错误")
    parser.add_argument("--copy-first-image", action="store_true", help="复制首图而不是移动首图")
    parser.add_argument("--no-backup", action="store_true", help="写入前不备份 CSV")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不移动图片、不写 CSV")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        process(args)
    except AppError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

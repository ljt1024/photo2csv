# 产品图片识别入库工具

这个项目根据 `产品图片识别需求文档.md` 实现了一个命令行工具，用于：

- 默认每 3 张图片作为同一商品的一组，也可以用 `--group-size` 改成每组 2 张等
- 调用视觉模型识别标题、品牌、价格、店铺、SKU、品类等字段
- 运行时输入平台标识，例如 `pdd`、`ks`、`dy`、`jd`、`xhs`
- 自动生成 `平台标识 + 4位数字` 形式的 `image_id`，例如 `pdd0001`、`ks0001`
- 将每组第一张图片保存到当前工程目录的 `images/平台标识NNNN.jpg` 或 `.png`，格式跟随输入首图
- 追加数据到当前工程目录的 `product_data_20260525.csv`
- CSV 使用 UTF-8 带 BOM 编码，兼容 Excel 的「CSV UTF-8」格式，避免中文乱码
- 写入前自动备份 CSV

## 安装

```bash
pip install -r requirements.txt
```

首图会按输入格式保存：JPG/JPEG 输出 `.jpg`，PNG 输出 `.png`，不会强制转成 PNG。

## 使用千问视觉识别

```bash
export DASHSCOPE_API_KEY="你的百炼 API Key"
python photo2csv.py ./img1.jpg ./img2.jpg ./img3.jpg
```

也可以把 `DASHSCOPE_API_KEY=...` 放在项目根目录的 `.env` 文件中。脚本会自动读取 `.env`，并且 `.env` 已加入 `.gitignore`。

默认使用百炼兼容模式接口：

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

默认模型是 `qwen3-vl-plus`。也可以通过环境变量或参数改成其他千问视觉模型：

```bash
export QWEN_MODEL="qwen-vl-max"
python photo2csv.py ./img1.png ./img2.png ./img3.png
```

一次处理多组时，图片数量必须是 `--group-size` 的倍数，默认是 3：

```bash
python photo2csv.py ./1.png ./2.png ./3.png ./4.png ./5.png ./6.png
```

如果每组只有 2 张图片：

```bash
python photo2csv.py ./1-1.jpg ./1-2.jpg ./2-1.jpg ./2-2.jpg --group-size 2
```

也可以直接处理你这次给的 zip 素材包格式：

```text
test.zip
└── test/
    ├── 1-1.jpg
    ├── 1-2.jpg
    └── 1-3.jpg
```

运行：

```bash
python photo2csv.py /Users/ljt/Downloads/test.zip --dry-run
python photo2csv.py /Users/ljt/Downloads/test.zip
```

如果不传 `--platform`，脚本会先提示输入平台标识：

```text
请输入平台标识（例如 pdd/ks/dy/jd/xhs）：
```

也可以直接通过参数传入：

```bash
python photo2csv.py /Users/ljt/Downloads/test.zip --platform ks
```

默认输出在当前工程目录：

```text
photo2csv/
├── product_data_20260525.csv
└── images/
    └── ks0001.jpg
```

如果当前工程目录还没有 `product_data_20260525.csv`，脚本会先从 `/Users/ljt/Downloads/product_data_20260525.csv` 复制一份作为模板，再追加识别结果，这样编号会继续沿用已有数据。

如果某一组识别失败，脚本不会中断；首图仍会保存，CSV 对应行的 `title` 会写 `识别失败`，具体错误写入 `remark`，以保证图片和表格顺序一致。

多组素材按同样规则命名，例如 `2-1.jpg、2-2.jpg、2-3.jpg`。脚本会自动忽略 `__MACOSX` 和 `._*` 这类 macOS 压缩包元数据。

两张一组时，命名规则就是 `1-1.jpg、1-2.jpg、2-1.jpg、2-2.jpg`，运行 `photo2csv.py` 时加 `--group-size 2`。

也可以从目录递归读取，默认会自动识别 `1-1/1-2/1-3` 这种命名；如果没有这种命名，就按排序后每组 3 张分组：

```bash
python photo2csv.py --input-dir ./待处理图片
```

## 素材重命名打包

`rename_images_zip.py` 默认按每组 3 张生成 `1-1、1-2、1-3` 这类 ZIP 内文件名，不修改原始图片：

```bash
python rename_images_zip.py ./待处理图片 --output renamed_images.zip
```

如果每组是 2 张，添加 `--group-size 2`，会生成 `1-1、1-2、2-1、2-2`：

```bash
python rename_images_zip.py ./待处理图片 --group-size 2 --output renamed_images.zip
python photo2csv.py renamed_images.zip --group-size 2 --platform ks
```

## 先预览不写入

```bash
python photo2csv.py --input-dir ./待处理图片 --dry-run
```

`--dry-run` 不会移动图片，也不会追加 CSV。

## 没有千问 API Key 时手动导入识别结果

准备一个 JSON 文件，数组中每个对象对应一组图片：

```json
[
  {
    "category": "食品饮料",
    "has_sku_info": 1,
    "title": "某品牌大米5斤装",
    "brand": "某品牌",
    "sku_spec": "5斤",
    "price": 29.9,
    "price_type": "闪降价",
    "shop_name": "某品牌旗舰店",
    "remark": ""
  }
]
```

然后执行：

```bash
python photo2csv.py ./img1.png ./img2.png ./img3.png --manual-json result.json
```

## 常用参数

- `--output-dir`：输出目录，默认当前工程目录
- `--csv`：目标 CSV，默认当前工程目录的 `product_data_20260525.csv`
- `--images-dir`：首图保存目录，默认当前工程目录的 `images/`
- `--template-csv`：目标 CSV 不存在时用于初始化和续号的模板 CSV，默认 `/Users/ljt/Downloads/product_data_20260525.csv`
- `--input-zip`：读取 `.zip` 素材包，也可以直接把单个 zip 路径作为位置参数
- `--group-size`：每组图片数量，默认 `3`；例如两张一组用 `--group-size 2`
- `--group-mode`：分组方式，`auto` 自动识别命名，`name` 强制按 `1-1/1-2/...`，`sequential` 按排序和 `--group-size` 分组
- `--platform`：平台标识，例如 `pdd`、`ks`、`dy`、`jd`、`xhs`；不传时会提示输入
- `--capture-time`：采集日期，格式 `YYYYMMDD`，默认当天
- `--start-number`：手动指定起始编号，例如 `--start-number 21`
- `--copy-first-image`：复制第一张图片，而不是移动
- `--no-backup`：写入前不备份 CSV
- `--model`：指定千问视觉模型，默认读取 `QWEN_MODEL` 或 `DASHSCOPE_MODEL`，否则使用 `qwen3-vl-plus`
- `--api-base`：指定千问兼容接口地址，默认读取 `QWEN_API_BASE` 或 `DASHSCOPE_API_BASE`，否则使用百炼兼容接口
- `--timeout` / `--retries`：网络较慢时可加大，例如 `--timeout 180 --retries 5`
- `--no-verify-ssl`：临时跳过 HTTPS 证书校验，用于排查本机证书或代理导致的 TLS 错误

## 测试

```bash
python -m unittest discover -s tests
```

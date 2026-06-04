# 产品图片识别入库工具

这个项目根据 `产品图片识别需求文档.md` 实现了一个命令行工具，用于：

- 每 3 张图片作为同一商品的一组
- 调用视觉模型识别标题、品牌、价格、店铺、SKU、品类等字段
- 运行时输入平台标识，例如 `pdd`、`ks`、`dy`、`jd`、`xhs`
- 自动生成 `平台标识 + 4位数字` 形式的 `image_id`，例如 `pdd0001`、`ks0001`
- 将每组第一张图片保存到当前工程目录的 `images/平台标识NNNN.png`
- 追加数据到当前工程目录的 `product_data_20260525.csv`
- 写入前自动备份 CSV

## 安装

```bash
pip install -r requirements.txt
```

如果首图本身已经是 PNG，脚本不一定需要 Pillow；但遇到 JPG/WebP 等格式时，需要 Pillow 转成需求里的 `.png`。

## 使用千问视觉识别

```bash
export DASHSCOPE_API_KEY="你的百炼 API Key"
python photo2csv.py ./img1.png ./img2.png ./img3.png
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

一次处理多组时，图片数量必须是 3 的倍数：

```bash
python photo2csv.py ./1.png ./2.png ./3.png ./4.png ./5.png ./6.png
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
    └── ks0001.png
```

如果当前工程目录还没有 `product_data_20260525.csv`，脚本会先从 `/Users/ljt/Downloads/product_data_20260525.csv` 复制一份作为模板，再追加识别结果，这样编号会继续沿用已有数据。

多组素材按同样规则命名，例如 `2-1.jpg、2-2.jpg、2-3.jpg`。脚本会自动忽略 `__MACOSX` 和 `._*` 这类 macOS 压缩包元数据。

也可以从目录递归读取，默认会自动识别 `1-1/1-2/1-3` 这种命名；如果没有这种命名，就按排序后每 3 张分一组：

```bash
python photo2csv.py --input-dir ./待处理图片
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
- `--group-mode`：分组方式，`auto` 自动识别命名，`name` 强制按 `1-1/1-2/1-3`，`sequential` 按排序每 3 张
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

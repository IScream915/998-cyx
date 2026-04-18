# moduleCD 订阅+检测+发布端

订阅 `moduleA` 在 `tcp://localhost:5051` 发布的 `Frame` 消息，
对消息中的 `image`（base64 编码 jpg）直接调用 `coreDetector` 进行检测（不落盘二次保存），
再将结果发布到 `tcp://localhost:5053` 供 `moduleE` 订阅。

发布消息格式：
- 保留 `frame_id`
- 其余字段与 `coreDetector` 检测输出字段兼容（如 `success`、`image_size`、`traffic_signs`、`pedestrians`、`vehicles` 等）

## 启动

```bash
python3 moduleCD/mock_module_cd.py
```

## 可选参数

- `--endpoint`：订阅地址，默认 `tcp://localhost:5051`
- `--topic`：订阅 topic，默认 `Frame`
- `--publish_bind`：发布地址，默认 `tcp://*:5053`
- `--publish_topic`：发布 topic，默认 `Frame`
- `--timeout_ms`：接收超时，默认 `1000`
- `--sign-model`：交通标志模型路径
- `--scene-model`：场景模型路径
- `--conf`：置信度阈值，默认 `0.25`
- `--iou`：IoU 阈值，默认 `0.45`
- `--img-size`：推理尺寸，默认 `640`
- `--device`：推理设备（`cuda:0`/`cpu`）
- `--disable-ocr`：禁用数字类交通标志 OCR 主识别（默认启用 OCR）
- `--ocr-min-conf`：OCR 主识别置信度阈值，默认 `0.4`
- `--save-vis`：保存可视化检测图
- `--vis-dir`：可视化输出目录（与 `--save-vis` 配合，默认写入 `moduleCD/coreDetector/outputs`）

## OCR 启动检查

默认启用 OCR 时，`CoreDetector` 启动会执行 EasyOCR 自检（导入 + Reader 初始化）。
若缺少 `easyocr` 依赖，或首次模型下载失败，moduleCD 将直接启动失败并报错退出。

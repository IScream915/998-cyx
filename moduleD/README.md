# moduleD 订阅+检测+发布端

`moduleD/mock_module_d.py` 支持与 moduleB 一致的双输入模式：

- `zmq` 模式：订阅 moduleA 的 `Frame`（默认 `tcp://localhost:5051`）
- `local` 模式：读取 `frontend/assets/scenes/<scene>` 本地图片序列

检测后统一发布到 `tcp://*:5053`（topic 默认 `Frame`）。

## 启动

```bash
python3 moduleD/mock_module_d.py
```

默认启动能力：

- ZeroMQ 发布：`tcp://*:5053`（topic `Frame`）
- 控制接口：`http://127.0.0.1:5057`

## 控制接口

- `GET /state`
- `POST /mode`，参数：`{"mode":"zmq"|"local"}`
- `POST /scene`，参数：`{"scene":"<folder>"}`
- `POST /player`，参数：`{"action":"play"|"pause"|"reset"}`

## 发布字段

保留检测字段：

- `frame_id`
- `image_size`
- `traffic_signs` / `num_traffic_signs`
- `pedestrians` / `num_pedestrians`
- `vehicles` / `num_vehicles`
- `traffic_lights`（`light_color` + `confidence`）

新增：

- `source_mode`：`zmq` 或 `local`
- local 模式附带：`scene_folder`、`image_relpath`、`frame_index`、`frame_total`
- local 模式可选：`yolo_overlay_base64`（JPEG base64，无 `data:` 前缀）
- 若识别框叠加图生成失败，会降级为仅发布检测统计字段，不中断逐帧播放

## 常用参数

- `--endpoint`：订阅地址，默认 `tcp://localhost:5051`
- `--topic`：订阅 topic，默认 `Frame`
- `--publish_bind`：发布地址，默认 `tcp://*:5053`
- `--publish_topic`：发布 topic，默认 `Frame`
- `--publish_rate_hz`：发布限速，默认 `0`（不限制）
- `--timeout_ms`：接收超时，默认 `1000`
- `--reconnect_delay`：重连等待秒数，默认 `1.0`
- `--local_scenes_root`：本地场景根目录，默认 `frontend/assets/scenes`
- `--local_rate_hz`：local 播放速率，默认 `2.0`
- `--control_host`：控制接口地址，默认 `127.0.0.1`
- `--control_port`：控制接口端口，默认 `5057`
- `--sign-model` / `--scene-model` / `--conf` / `--iou` / `--img-size` / `--device`
- `--disable-ocr` / `--ocr-min-conf`
- `--save-vis` / `--vis-dir`

## 权重说明

- 默认加载顺序不变（优先 `best.pt` / `yolov8n.pt`）。
- `coreDetector/weights` 额外提供可选权重：`tsr_best.pt`、`carla_car_best.pt`（仅新增，不替换默认）。

## OCR 启动检查

默认启用 OCR 时，`CoreDetector` 启动会执行 EasyOCR 自检（导入 + Reader 初始化）。
若缺少 `easyocr` 依赖，或首次模型下载失败，moduleD 将直接启动失败并报错退出。

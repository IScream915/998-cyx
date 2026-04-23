# moduleB 场景识别与 ZeroMQ 服务

moduleB 当前提供三类能力：

1. 图片场景分类推理（`moduleB/inference.py`）
2. ZeroMQ 实时订阅 + 发布（`moduleB/zmq_service.py` / `moduleB/run.py`）
3. 本地图片流播放 + 控制接口（`moduleB/zmq_service.py`）

---

## 1) 快速启动（推荐）

```bash
python3 moduleB/run.py
```

或：

```bash
python3 -m moduleB.run
```

默认行为：

- ZMQ 输入模式：订阅 `tcp://localhost:5051` 的 `Frame`
- 将推理结果发布到 `tcp://*:5052` 的 `Frame`
- 控制接口监听 `http://127.0.0.1:5056`

---

## 2) 两种输入模式

### 2.1 `zmq` 模式（默认）

按原逻辑从 moduleA 订阅图像消息并推理后发布。

输入支持：

- 旧格式：`{"frame_id": ..., "image": "<base64>"}`
- 新格式：`frame_id + frames.top_camera.payload.Image.data + vehicle_states.ego.speed_mps`

### 2.2 `local` 模式（新增）

通过控制接口切换到本地图片流模式后，moduleB 会读取 `frontend/assets/scenes/<scene_folder>` 下图片并按速率逐帧推理后发布。

默认：

- 本地播放速率 `2 FPS`
- 本地速度字段 `speed=0.0 km/h`（可配置）

---

## 3) 控制接口（HTTP）

默认地址：`http://127.0.0.1:5056`

### `GET /state`

返回当前状态：

- `mode`：`zmq` / `local`
- `scene_folder`
- `frame_index` / `frame_total`
- `playing`
- `next_local_frame_id`
- `last_error`

### `POST /mode`

请求：

```json
{"mode": "local"}
```

或

```json
{"mode": "zmq"}
```

### `POST /scene`

请求：

```json
{"scene": "scene-1"}
```

会加载 `frontend/assets/scenes/scene-1` 下图片并重置到首帧。

### `POST /player`

请求：

```json
{"action": "play"}
```

`action` 支持：`play` / `pause` / `reset`

---

## 4) ZeroMQ 输出协议

发布 topic：默认 `Frame`

示例：

```json
{
  "frame_id": 12,
  "scene": "city street",
  "conference": 84.23,
  "confidence": 84.23,
  "speed": 23,
  "source_mode": "local",
  "scene_folder": "scene-1",
  "image_relpath": "assets/scenes/scene-1/frame-1.jpg",
  "frame_index": 0,
  "frame_total": 3
}
```

说明：

- 兼容字段 `conference` 保留
- 新增 `confidence`（同值）
- `source_mode=zmq` 时不附带本地播放字段

---

## 5) 服务参数（`moduleB/zmq_service.py`）

基础参数：

- `--endpoint`：订阅地址，默认 `tcp://localhost:5051`
- `--topic`：订阅 topic，默认 `Frame`
- `--publish_bind`：发布地址，默认 `tcp://*:5052`
- `--publish_topic`：发布 topic，默认 `Frame`
- `--publish_rate_hz`：发布限速(Hz)，默认 `0`（不限制）
- `--timeout_ms`：接收超时(ms)，默认 `1000`
- `--reconnect_delay`：重连等待(秒)，默认 `1.0`

模型参数：

- `--checkpoint`：默认 `moduleB/outputs/best_model.pth`
- `--model_size`：`0_5x/0_8x/1_0x/2_0x`，默认 `2_0x`
- `--num_classes`：默认 `7`
- `--img_size`：默认 `224`
- `--device`：`auto/cuda/cpu`，默认 `auto`

本地模式参数：

- `--local_scenes_root`：默认 `frontend/assets/scenes`
- `--local_rate_hz`：本地播放速率，默认 `2`
- `--local_speed_kmh`：本地模式输出速度，默认 `0.0`

控制接口参数：

- `--control_host`：默认 `127.0.0.1`
- `--control_port`：默认 `5056`

---

## 6) 离线推理脚本（`moduleB/inference.py`）

单图推理：

```bash
python3 moduleB/inference.py --image inference/test.jpg
```

批量推理：

```bash
python3 moduleB/inference.py --image_dir inference --output moduleB/outputs/result.csv
```

---

## 7) 相关文件

- 入口：`moduleB/run.py`
- 实时服务：`moduleB/zmq_service.py`
- 离线推理：`moduleB/inference.py`
- 模型定义：`moduleB/model/repghost.py`
- 默认权重：`moduleB/outputs/best_model.pth`

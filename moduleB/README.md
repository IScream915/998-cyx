# moduleB 场景识别与 ZeroMQ 服务

moduleB 提供两类能力：

1. 图片场景分类推理（`moduleB/inference.py`）
2. ZeroMQ 实时订阅服务（`moduleB/zmq_service.py` / `moduleB/run.py`）

---

## 1) 快速启动（ZeroMQ 实时服务）

推荐启动方式：

```bash
python3 moduleB/run.py
```

或：

```bash
python3 -m moduleB.run
```

默认行为：

- 订阅 `tcp://localhost:5051` 的 `Frame` 消息
- 从消息中读取图像并进行场景分类
- 将结果发布到 `tcp://*:5052` 的 `Frame` topic（供 moduleE/桥接服务订阅）

限制发布速率示例（2Hz）：

```bash
python3 moduleB/run.py --publish_rate_hz 2
```

---

## 2) ZeroMQ 输入协议

`moduleB/zmq_service.py` 支持两种输入结构（优先旧格式）：

### 旧格式

```json
{
  "frame_id": 1,
  "image": "/9j/4AAQSkZJRgABAQAAAQABAAD..."
}
```

### 新格式（推荐）

```json
{
  "frame_id": 1,
  "frames": {
    "top_camera": {
      "payload": {
        "Image": {
          "data": "/9j/4AAQSkZJRgABAQAAAQABAAD..."
        }
      }
    }
  },
  "vehicle_states": {
    "ego": {
      "speed_mps": 6.45
    }
  }
}
```

字段要求：

- `frame_id`：必须可转为整数
- 图像字段：旧格式用 `image`；新格式用 `frames.top_camera.payload.Image.data`
- 速度字段：`vehicle_states.ego.speed_mps`（m/s，服务内部换算为 km/h）

---

## 3) ZeroMQ 输出协议

服务会发布 JSON（topic 为 `Frame`）：

```json
{
  "frame_id": 1,
  "scene": "city street",
  "conference": 84.23,
  "speed": 23
}
```

说明：

- `conference` 为当前实现中的置信度字段名（代码即如此）
- `speed` 为 km/h（`round(speed_mps * 3.6)`）

---

## 4) 服务参数（`moduleB/zmq_service.py`）

- `--endpoint`：订阅地址，默认 `tcp://localhost:5051`
- `--topic`：订阅 topic，默认 `Frame`
- `--publish_bind`：发布地址，默认 `tcp://*:5052`
- `--publish_topic`：发布 topic，默认 `Frame`
- `--publish_rate_hz`：发布限速(Hz)，默认 `0`（不限制，来一帧发一帧）
- `--timeout_ms`：接收超时(ms)，默认 `1000`
- `--reconnect_delay`：重连等待(秒)，默认 `1.0`
- `--checkpoint`：模型权重，默认 `moduleB/outputs/best_model.pth`
- `--model_size`：`0_5x/0_8x/1_0x/2_0x`，默认 `2_0x`
- `--num_classes`：类别数，默认 `7`
- `--img_size`：输入尺寸，默认 `224`
- `--device`：`auto/cuda/cpu`，默认 `auto`

---

## 5) 离线推理脚本（`moduleB/inference.py`）

单图推理：

```bash
python3 moduleB/inference.py --image inference/test.jpg
```

批量推理：

```bash
python3 moduleB/inference.py --image_dir inference --output moduleB/outputs/result.csv
```

常用参数：

- `--checkpoint`：默认 `moduleB/outputs/best_model.pth`
- `--model_size`：默认 `2_0x`
- `--img_size`：默认 `224`
- `--device`：`auto/cuda/cpu`
- `--top_k`：默认 `3`

---

## 6) 相关文件

- 入口：`moduleB/run.py`
- 实时服务：`moduleB/zmq_service.py`
- 离线推理：`moduleB/inference.py`
- 模型定义：`moduleB/model/repghost.py`
- 默认权重：`moduleB/outputs/best_model.pth`

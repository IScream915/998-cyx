# moduleCD BSD Demo

这是一个完全不依赖 CARLA 的 BSD ZeroMQ 演示子项目。

它的目标是和 `moduleCD` 风格的消费者对齐：

- 输入：以 `demo/modulecd_bsd_demo/config.toml` 中的 `demo.zmq.input_addr` / `input_bind` 为准，topic 默认 `Frame`
- 输入 payload：UTF-8 JSON，左右/顶部相机图像使用 **JPG base64**
- 输出：以 `demo/modulecd_bsd_demo/config.toml` 中的 `demo.zmq.output_bind` / `output_endpoint` 为准，topic 默认 `Frame`
- 输出 payload：`moduleCD` 顶层字段兼容，并追加 `bsd` 扩展对象

默认内置权重已经同步为当前主仓库的 **mirror-view 主线版本**，也就是在 lane-projected 新标注数据上继续训练后的 `stage_c_laneprojected_continue`。

## 依赖

统一使用仓库根目录 `uv`：

```bash
uv sync --extra dev
```

不需要：

```bash
uv sync --extra carla
```

## 启动服务

```bash
uv run python demo/modulecd_bsd_demo/service.py
```

当前仓库提交的默认配置请直接看：

```toml
[demo.zmq]
input_addr = "tcp://192.168.31.157:5050"
input_bind = "tcp://*:5051"
output_bind = "tcp://*:5058"
output_endpoint = "tcp://localhost:5058"

[demo.frontend]
bind = "tcp://*:5059"
endpoint = "tcp://localhost:5059"
topic = "Frame"
```

含义分别是：

- `input_addr`：`service.py` 作为 SUB 连接的上游输入地址
- `input_bind`：`sample_publisher.py` 默认绑定的本地输入地址
- `output_bind`：`service.py` 作为 PUB 绑定的输出地址
- `output_endpoint`：`sample_subscriber.py` / `frontend/live_server.py` 默认连接的输出地址
- `demo.frontend.bind/endpoint`：`service.py` 额外发布给浏览器桥接层的本地专用流，单条消息里同时携带原图和 `moduleCD` 输出，避免前端再做双订阅配对

如果你要直接用仓库里的 `sample_publisher.py` 做本地闭环，请先把 `input_addr` 改成和 `input_bind` 同一个可连接地址，或者另写一份临时 config 再通过 `--config` 传入。

## 发送样例输入

```bash
uv run python demo/modulecd_bsd_demo/sample_publisher.py --count 5 --interval-ms 300
```

## 订阅输出结果

```bash
uv run python demo/modulecd_bsd_demo/sample_subscriber.py --count 5
```

推荐顺序：

1. 先启动 `service.py`
2. 再启动 `sample_subscriber.py`
3. 最后启动 `sample_publisher.py`

`sample_publisher.py` 默认会多发几帧并留出 warmup 时间，方便 PUB/SUB 完成握手。

若要联动前端 `module-c` 实时叠加页（在项目根目录），再启动：

```bash
python3 frontend/server.py --module_c_config moduleC/demo/modulecd_bsd_demo/config.toml
```

该服务会额外暴露：

- `GET /api/module-c/health`
- `GET /api/module-c/ws`（WebSocket）

`server.py` 内置的 moduleC bridge 会优先订阅 `demo.frontend.endpoint`。这条流由 `service.py` 在处理同一帧时直接发出，所以浏览器拿到的图像和检测结果天然同帧，不需要再在前端侧按 `frame_id` 拼接。

浏览器访问 moduleC 页面：

```text
http://127.0.0.1:4173/#/module-c
```

## 输入协议

消息采用 ZeroMQ `multipart`：

1. 第 1 帧：topic，固定 `Frame`
2. 第 2 帧：UTF-8 JSON

核心字段：

- `t_sync`
- `frame_id`
- `frames`
- `vehicle_states`
- `sync_meta`

其中 `frames` 默认读取：

- `left_camera`
- `right_camera`
- `top_camera`
- `imu`

左右相机为必需项。`top_camera`、`imu`、`vehicle_states` 可缺省。

图像字段固定要求：

```json
{
  "payload": {
    "Image": {
      "format": "jpeg",
      "data": "<base64 jpg string>"
    }
  }
}
```

## 输出协议

顶层字段固定包含：

- `frame_id`
- `image_size`
- `traffic_signs`
- `num_traffic_signs`
- `pedestrians`
- `num_pedestrians`
- `vehicles`
- `num_vehicles`
- `tracked_pedestrians`
- `bsd`

`traffic_signs` 当前固定为空数组，`num_traffic_signs` 固定为 `0`。

`bsd` 扩展对象中包含：

- `schema_version`
- `input_source`
- `ego`
- `system`
- `left`
- `right`
- `overview`

## 目录说明

- `service.py`：主服务
- `sample_publisher.py`：样例输入发送器
- `sample_subscriber.py`：结果订阅器
- `config.toml`：demo 默认配置
- `assets/`：演示 JPG
- `weights/`：演示用权重
- `scripts/`：启动辅助脚本

## 当前主线参数

这份 demo 现在默认对齐了主仓库当前的 mirror-view 主线：

- 参考相机外参：
  - 左：`loc=[1.05, -1.02, 1.22]`, `rot=[-3.5, -148.0, 0.0]`
  - 右：`loc=[1.05, 1.02, 1.22]`, `rot=[-3.5, 148.0, 0.0]`
- 参考 `fov = 72`
- 默认输入尺寸：`960x540`
- 默认盲区模板：
  - 左：`center_x=0.24`, `top_y_base=0.52`, `bot_half_w_base=0.22`, `top_half_w_base=0.09`
  - 右：`center_x=0.76`, `top_y_base=0.52`, `bot_half_w_base=0.22`, `top_half_w_base=0.09`

虽然 demo 不依赖 CARLA，但这些参数会影响模型和盲区模板对输入分布的假设。更完整的主线说明见 `../../docs/current_mainline.md`。

## 更换权重

当前默认读取：

- `demo/modulecd_bsd_demo/weights/bsd_demo.pt`
- `demo/modulecd_bsd_demo/weights/bsd_demo.json`

如果你后面训练了新的兼容权重，可以直接替换这两个文件；如果你想保留旧文件，也可以改 `config.toml` 里的 `detection.model_path`。

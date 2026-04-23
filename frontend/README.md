# 前端说明（frontend）

本目录是智能驾驶辅助系统前端演示工程，采用原生 `HTML/CSS/JS`。

## 启动方式

### 0) 安装依赖（首次）

```bash
pip install pyzmq websockets
```

### 1) 启动 moduleB（含控制接口）

```bash
python3 moduleB/run.py
```

默认会启动：

- ZeroMQ 发布：`tcp://*:5052`（topic `Frame`）
- 控制接口：`http://127.0.0.1:5056`

### 2) 启动 moduleC（含控制接口）

```bash
python3 moduleC/mock_module_c.py
```

默认会启动：

- ZeroMQ 发布：`tcp://*:5053`（topic `Frame`）
- 控制接口：`http://127.0.0.1:5057`

### 3) 启动前端静态服务（含 API 代理）

```bash
python3 frontend/server.py
```

默认监听：

- Host: `0.0.0.0`
- Port: `4173`

浏览器访问：

```text
http://127.0.0.1:4173
```

### 4) 启动桥接服务（全流程页实时）

```bash
python3 frontend/ws_bridge.py
```

默认监听：`ws://0.0.0.0:8765`。

---

## frontend/server.py 新增 API

`server.py` 现在除了静态资源，还提供以下同源 API。

### 场景目录 API

- `GET /api/scenes`
  - 返回 `frontend/assets/scenes` 下一级目录与帧数
- `GET /api/scenes/{scene}/frames`
  - 返回该目录图片列表（仅 `.jpg/.jpeg/.png`）

安全限制：

- 禁止 `..`、绝对路径和越界目录
- 仅允许白名单后缀

### moduleB/moduleC 控制代理 API

- `GET /api/module-b/state` -> 代理 `GET http://127.0.0.1:5056/state`
- `POST /api/module-b/mode` -> 代理 `/mode`
- `POST /api/module-b/scene` -> 代理 `/scene`
- `POST /api/module-b/player` -> 代理 `/player`
- `GET /api/module-c/state` -> 代理 `GET http://127.0.0.1:5057/state`
- `POST /api/module-c/mode` -> 代理 `/mode`
- `POST /api/module-c/scene` -> 代理 `/scene`
- `POST /api/module-c/player` -> 代理 `/player`

可通过参数改代理目标：

```bash
python3 frontend/server.py \
  --module_b_control_host 127.0.0.1 --module_b_control_port 5056 \
  --module_c_control_host 127.0.0.1 --module_c_control_port 5057
```

---

## 模块B展示页行为

`模块B展示` 页面已切换为“后端驱动本地图片流”：

1. 进入页面后自动调用 `POST /api/module-b/mode {"mode":"local"}`。
2. 场景下拉框会动态读取 `frontend/assets/scenes` 子目录。
3. 选择场景后调用 `POST /api/module-b/scene`。
4. 点击播放/暂停/重置分别调用 `POST /api/module-b/player`。
5. 页面通过 WebSocket `b_frame` 事件实时刷新图片与 `scene/confidence/speed`。
6. `source_mode=local` 时，若 `moduleB.heatmap_base64` 存在，会在右侧“热力图预留窗口”实时展示 Grad-CAM 叠加热图。

---

## 模块C展示页行为

`模块C展示` 页面已切换为“后端驱动本地图片流”：

1. 进入页面后自动调用 `POST /api/module-c/mode {"mode":"local"}`。
2. 场景下拉框会动态读取 `frontend/assets/scenes` 子目录。
3. 选择场景后调用 `POST /api/module-c/scene`。
4. 点击播放/暂停/重置分别调用 `POST /api/module-c/player`。
5. 页面通过 WebSocket `c_frame` 事件实时刷新图片与 `num_traffic_signs/num_pedestrians/num_vehicles`。
6. `source_mode=local` 时，若 `moduleC.yolo_overlay_base64` 存在，会在右侧“YOLO识别框预留窗口”实时展示识别框叠加图。
7. 若某帧识别框生成失败，该帧会降级为仅更新统计字段，页面回退占位态但播放不中断。

---

## 全流程页行为

`全流程展示` 页面 mount 时会调用：

```json
{"mode": "zmq"}
```

即自动把 moduleB 切回 A-ZMQ 输入模式。
即自动把 moduleC 切回 A-ZMQ 输入模式。

---

## ws_bridge.py 事件

保留：

- `ab_frame`
- `c_frame`
  - local 模式可带：`moduleC.scene_folder/image_relpath/frame_index/frame_total/yolo_overlay_base64`
- `e_frame`
- `status`

新增：

- `b_frame`（每条 moduleB 消息都推送，不依赖 A/B 配对）
  - 可选字段：`moduleB.heatmap_base64`（local 模式热力图）

---

## 目录结构（关键）

```text
frontend/
  server.py                  # 静态服务 + 场景API + moduleB/moduleC 控制代理
  ws_bridge.py               # A+B+C+E ZMQ -> WebSocket
  pages/
    fullflow/page.js         # 全流程页（进入时回切moduleB/moduleC到zmq）
    module-b/page.js         # 模块B页（本地模式 + 实时b_frame）
    module-c/page.js         # 模块C页（本地模式 + 实时c_frame）
  assets/
    scenes/                  # 本地图片流场景目录
```

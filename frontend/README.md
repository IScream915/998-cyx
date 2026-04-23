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

- ZeroMQ 发布：`tcp://*:5052`（topic `Frame`）
- 控制接口：`http://127.0.0.1:5056`

### 2) 启动 moduleD（含控制接口）

```bash
python3 moduleD/mock_module_d.py
```

- ZeroMQ 发布：`tcp://*:5053`（topic `Frame`）
- 控制接口：`http://127.0.0.1:5057`

### 3) 启动 moduleC（新版本）

```bash
cd moduleC
uv run python demo/modulecd_bsd_demo/service.py
```

- 输出：`tcp://*:5058`（topic `Frame`）
- browser-only 流：`tcp://*:5059`（topic `Frame`）

### 4) 启动前端统一服务（静态 + API 代理 + moduleC 实时桥接）

```bash
python3 frontend/server.py --module_c_config moduleC/demo/modulecd_bsd_demo/config.toml
```

默认监听：

- Host: `0.0.0.0`
- Port: `4173`

浏览器访问：

```text
http://127.0.0.1:4173
```

### 5) 启动全流程桥接（ab_frame / b_frame / d_frame / e_frame）

```bash
python3 frontend/ws_bridge.py
```

默认监听：`ws://0.0.0.0:8765`。

### 6) 启动 moduleE-demo（模块E独立展示仿真链路）

```bash
python3 moduleE/mock_module_e.py \
  --endpoints tcp://127.0.0.1:6062,tcp://127.0.0.1:6063 \
  --topic SimFrame \
  --publish_bind tcp://*:6064 \
  --publish_topic SimFrame \
  --control_host 127.0.0.1 \
  --control_port 5064
```

---

## frontend/server.py API

### 场景目录 API

- `GET /api/scenes`
- `GET /api/scenes/{scene}/frames`

### moduleB/moduleD 控制代理 API

- `GET /api/module-b/state`
- `POST /api/module-b/mode`
- `POST /api/module-b/scene`
- `POST /api/module-b/player`
- `GET /api/module-d/state`
- `POST /api/module-d/mode`
- `POST /api/module-d/scene`
- `POST /api/module-d/player`

### moduleC 实时 API（新增）

- `GET /api/module-c/health`
- `GET /api/module-c/ws`（WebSocket）

### moduleE 仿真 API（新增）

- `GET /api/module-e/state`
- `GET /api/module-e/ws`（WebSocket）
- `POST /api/module-e/simulate`
- `POST /api/module-e/reset`

可通过参数覆盖 moduleC bridge 配置：

- `--module_c_config`
- `--module_c_input_endpoint`
- `--module_c_output_endpoint`
- `--module_c_browser_endpoint`
- `--module_c_topic`
- `--module_c_merge_timeout_ms`
- `--module_c_push_fps`

可通过参数覆盖 moduleE 仿真网关配置：

- `--module_e_sim_b_bind`
- `--module_e_sim_d_bind`
- `--module_e_sim_output_endpoint`
- `--module_e_sim_topic`
- `--module_e_sim_start_frame_id`
- `--module_e_control_host`
- `--module_e_control_port`

---

## 页面行为

- `模块B展示`：本地图片流 + `b_frame` 实时渲染（含热力图）。
- `模块C展示`：加载 `pages/module-c`，通过 `/api/module-c/ws` 实时绘制左右双窗 tracker 叠加。
- `模块D展示`：本地图片流 + `d_frame` 实时渲染。
- `模块E展示`：通过下拉模板组装仿真 B/D 消息，触发 moduleE demo 输出并展示决策结果。
- `全流程展示`：继续走 A+B+D+E 链路，不受 moduleC 新接入影响。

---

## 目录结构（关键）

```text
frontend/
  server.py                  # 统一服务入口（静态 + API + moduleC bridge）
  ws_bridge.py               # A+B+D+E ZMQ -> WebSocket
  pages/
    fullflow/page.js
    module-b/page.js
    module-c/page.js     # 模块C新实时页（当前接入）
    module-d/page.js
  assets/
    scenes/
```

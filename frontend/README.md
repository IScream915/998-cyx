# 前端说明（frontend）

本目录是智能驾驶辅助系统的前端演示工程，采用原生 `HTML/CSS/JS` 实现。

## 启动方式

### 0) 安装实时桥接依赖（首次）

```bash
pip install pyzmq websockets
```

### 1) 推荐方式（无需手动指定目录路径）
在项目根目录执行：

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

注意：`0.0.0.0` 是服务绑定地址，不是浏览器访问地址。  
当服务监听 `0.0.0.0` 时，请使用 `127.0.0.1` 或 `localhost` 访问。

### 2) 自定义端口/地址

```bash
python3 frontend/server.py --host 0.0.0.0 --port 8080
```

说明：`server.py` 会自动定位自身所在目录并作为静态资源根目录，所以无论在本地还是部署到服务器，只要目录结构保持 `frontend/` 不变，都不需要手动写绝对路径。

### 3) 全流程页实时联动（A+B+C）

`全流程展示` 页面已改为实时模式，需要同时启动静态服务与桥接服务：

终端1（静态页面）：

```bash
python3 frontend/server.py --host 0.0.0.0 --port 4173
```

终端2（A+B+C -> WebSocket 桥接）：

```bash
python3 frontend/ws_bridge.py
```

桥接默认参数：

- A endpoint: `tcp://192.168.31.157:5050`
- A topic: `Frame`
- B endpoint: `tcp://localhost:5052`
- B topic: `Frame`
- C endpoint: `tcp://localhost:5053`
- C topic: `Frame`
- WS: `ws://0.0.0.0:8765`

前端会自动连接 `ws://<页面host>:8765`。

可选参数示例：

```bash
python3 frontend/ws_bridge.py \
  --a-endpoint tcp://192.168.31.157:5050 \
  --a-topic Frame \
  --b-endpoint tcp://localhost:5052 \
  --b-topic Frame \
  --c-endpoint tcp://localhost:5053 \
  --c-topic Frame \
  --ws-host 0.0.0.0 \
  --ws-port 8765 \
  --match-timeout-ms 1500
```

## 目录结构

```text
frontend/
  index.html                 # 单入口页面壳
  app.css                    # 全局布局与通用样式
  app.js                     # 路由与页面挂载调度
  server.py                  # 静态服务启动脚本（免手动目录）
  ws_bridge.py               # A+B+C ZMQ 到 WebSocket 的实时桥接
  README.md                  # 本说明文档
  shared/
    theme.css                # 主题变量（黑白灰科技风）
    layout.js                # 侧边栏/顶部栏/页面头部逻辑
    components.js            # 通用 UI 小组件与工具函数
  pages/
    fullflow/
      data.js                # 全流程 mock 场景与模块输出数据
      page.css               # 全流程页面样式
      page.js                # 全流程页面逻辑（WebSocket实时渲染、日志）
    module-b/
      data.js                # 模块B mock 数据
      page.css               # 模块B页面样式
      page.js                # 模块B页面逻辑（双窗、播放、输出）
    module-c/
      page.css               # 模块C占位页样式
      page.js                # 模块C占位页逻辑
    module-d/
      page.css               # 模块D占位页样式
      page.js                # 模块D占位页逻辑
    module-e/
      page.css               # 模块E占位页样式
      page.js                # 模块E占位页逻辑
  assets/
    scenes/
      scene-1/              # 场景1帧序列
      scene-2/              # 场景2帧序列
      scene-3/              # 场景3帧序列
```

## 当前页面说明

- `全流程展示`：实时接收 A+B 配对帧更新主画面与 `moduleB`，并实时接收 `moduleC` 输出更新 C 面板。
- `模块B展示`：左右双窗（左侧原始场景，右侧热力图占位），并展示 B 模块输出。
- `模块C/D/E展示`：当前为占位页，预留后续独立展示能力。

## 部署建议

- 生产环境可将 `frontend/` 目录交给 Nginx/Caddy 等静态服务器托管。
- 若临时演示，可直接使用：

```bash
python3 frontend/server.py --host 0.0.0.0 --port 4173
```

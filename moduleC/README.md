# modulecd_bsd_demo_app

这是一个**可单独运行**的 BSD ZMQ 演示项目。

它不依赖 CARLA，不依赖当前大仓库根环境，拥有：

- 单独的 `pyproject.toml`
- 单独的 `uv.lock`
- 单独的 `git` 仓库

当前项目内置的默认模型权重已经同步到主仓库的**新主线 mirror-view 版本**，也就是基于 lane-projected mask 继续训练得到的 **Stage C continuation** 权重。

## 功能

- 输入：`tcp://localhost:5051`，topic=`Frame`
- 输入 payload：UTF-8 JSON，包含多 sensor，图像为 **JPG base64**
- 输出：`tcp://*:5058`，topic=`Frame`
- 前端 browser-only 流：`tcp://*:5059`，topic=`Frame`
- 输出 payload：兼容 `moduleCD` 顶层字段，并包含 `bsd` 扩展对象

## 安装

```bash
cd moduleC
uv sync --extra dev
```

## 运行

启动服务：

```bash
uv run python demo/modulecd_bsd_demo/service.py
```

启动订阅器：

```bash
uv run python demo/modulecd_bsd_demo/sample_subscriber.py --count 1
```

发送样例输入：

```bash
uv run python demo/modulecd_bsd_demo/sample_publisher.py
```

一键 smoke：

```bash
bash demo/modulecd_bsd_demo/scripts/run_demo.sh
```

## 当前默认模型

默认权重文件：

- `demo/modulecd_bsd_demo/weights/bsd_demo.pt`
- `demo/modulecd_bsd_demo/weights/bsd_demo.json`

它们当前对应主仓库中的：

- `weights/stage_c_laneprojected_continue.pt`
- `weights/stage_c_laneprojected_continue.json`

这版主线同时对齐了：

- mirror-view 相机参考视角
- `960x540` 输入尺寸
- 新的默认盲区模板
- `bsd_remote_multimap_mirror_collect_v1_caronly` 这套训练/评估数据分布

更完整的主线说明见：

- `docs/current_mainline.md`

如果后面你重新训练了兼容结构的新权重，可以直接替换这两个文件，或者修改 `demo/modulecd_bsd_demo/config.toml` 里的 `detection.model_path`。

## 测试

```bash
uv run pytest tests/test_modulecd_bsd_demo_protocol.py tests/test_modulecd_bsd_demo_zmq.py -q
```

## 目录

- `demo/modulecd_bsd_demo/`：服务、publisher、subscriber、配置、资源
- `src/`：运行时算法最小依赖
- `training/`：检测模型结构定义
- `tests/`：协议和 roundtrip 测试

## 与前端联调

在项目根目录运行前端统一服务：

```bash
python3 frontend/server.py --module_c_config moduleC/demo/modulecd_bsd_demo/config.toml
```

然后访问：

```text
http://127.0.0.1:4173/#/module-c
```

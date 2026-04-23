# moduleC（迁移占位）

模块C的运行实现已整体迁移到 `moduleD/`，本目录仅保留迁移说明，不再承载可运行服务。

## 迁移说明

- 原 `moduleC` 的检测核心、双输入模式（`zmq/local`）、控制接口与发布逻辑已迁移为 `moduleD` 命名。
- 前端实时链路已切换到 `moduleD`：`/api/module-d/*` 与 `d_frame`。
- 端口保持不变：发布 `tcp://*:5053`，控制接口 `http://127.0.0.1:5057`。

## 现在应使用

```bash
python3 moduleD/mock_module_d.py
```

如需查看运行参数、控制接口与发布字段，请参考 `moduleD/README.md`。

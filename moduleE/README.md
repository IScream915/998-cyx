# moduleE 订阅端

用于同时订阅多个上游发布端并打印消息，默认同时订阅：
- `tcp://localhost:5052`（moduleB）
- `tcp://localhost:5053`（moduleCD）

## 启动

```bash
python3 moduleE/mock_module_e.py
```

## 可选参数

- `--endpoints`：订阅地址列表（逗号分隔），默认 `tcp://localhost:5052,tcp://localhost:5053`
- `--topic`：订阅 topic，默认 `Frame`
- `--timeout_ms`：接收超时，默认 `1000`

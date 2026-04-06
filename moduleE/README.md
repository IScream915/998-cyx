# moduleE 订阅端

用于订阅本项目在 `tcp://localhost:5052` 发布的结果消息并打印。

## 启动

```bash
python3 moduleE/mock_module_e.py
```

## 可选参数

- `--endpoint`：订阅地址，默认 `tcp://localhost:5052`
- `--topic`：订阅 topic，默认 `Frame`
- `--timeout_ms`：接收超时，默认 `1000`

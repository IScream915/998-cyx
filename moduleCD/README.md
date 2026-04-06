# moduleCD 订阅+发布端

用于订阅 `moduleA` 在 `tcp://localhost:5051` 发布的 `Frame` 消息，并转发到 `tcp://localhost:5053` 供 `moduleE` 订阅。

## 启动

```bash
python3 moduleCD/mock_module_cd.py
```

## 可选参数

- `--endpoint`：订阅地址，默认 `tcp://localhost:5051`
- `--topic`：订阅 topic，默认 `Frame`
- `--publish_bind`：发布地址，默认 `tcp://*:5053`
- `--publish_topic`：发布 topic，默认 `Frame`
- `--timeout_ms`：接收超时，默认 `1000`

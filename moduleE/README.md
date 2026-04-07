# moduleE 订阅端

用于同时订阅多个上游发布端，并且只处理同一 `frame_id` 下 B+CD 都到齐的消息。

默认同时订阅：
- `tcp://localhost:5052`（moduleB）
- `tcp://localhost:5053`（moduleCD）

## 启动

```bash
python3 moduleE/mock_module_e.py
```

## 处理规则

- 只在同一 `frame_id` 的 B 和 CD 消息都到齐时输出结果
- 任一 `frame_id` 在配对超时内未到齐会被丢弃（保证低延迟）

## 可选参数

- `--endpoints`：订阅地址列表（逗号分隔），默认 `tcp://localhost:5052,tcp://localhost:5053`
- `--topic`：订阅 topic，默认 `Frame`
- `--timeout_ms`：轮询等待时间，默认 `10`
- `--match_timeout_ms`：同一 frame_id 配对超时，默认 `450`

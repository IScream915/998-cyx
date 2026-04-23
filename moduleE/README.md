# moduleE 任务处理端

用于同时订阅多个上游发布端，并且只处理同一 `frame_id` 下 B+CD 都到齐的消息。
在对齐后会调用 `TrafficReminder` 引擎执行真实任务处理（语义匹配 + 规则仲裁 + 异步语音播报）。
处理结果会发布到 `tcp://localhost:5054` 的 `Frame` topic，供前端或其他模块订阅。

默认同时订阅：
- `tcp://localhost:5052`（moduleB）
- `tcp://localhost:5053`（moduleC）

## 启动

```bash
python3 moduleE/mock_module_e.py
```

## 处理规则

- 只在同一 `frame_id` 的 B 和 CD 消息都到齐时输出结果
- 任一 `frame_id` 在配对超时内未到齐会被丢弃（保证低延迟）
- 对齐成功后，将 B+CD 数据映射为 `perception_json` 并调用 `TrafficReminder` 处理

## 可选参数

- `--endpoints`：订阅地址列表（逗号分隔），默认 `tcp://localhost:5052,tcp://localhost:5053`
- `--topic`：订阅 topic，默认 `Frame`
- `--timeout_ms`：轮询等待时间，默认 `10`
- `--match_timeout_ms`：同一 frame_id 配对超时，默认 `1500`
- `--publish_bind`：结果发布地址，默认 `tcp://*:5054`
- `--publish_topic`：结果发布 topic，默认 `Frame`
- `--kb_path`：规则库 JSON 路径，默认 `moduleE/gb5768_rules.json`
- `--st_model`：句向量模型本地路径或模型名，默认 `moduleE/model/paraphrase-multilingual-MiniLM-L12-v2`
- `--default_speed`：B 未提供车速时的默认速度，默认 `60.0`

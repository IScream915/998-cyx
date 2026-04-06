# moduleA 模拟发布端

用于模拟模块A持续通过 ZeroMQ 在 `tcp://*:5051` 发布 JSON 消息，默认 topic 为 `Frame`。

## 启动

```bash
python3 moduleA/mock_module_a.py
```

默认每秒发送一次，消息格式：

```json
{
  "frame_id": 1,
  "image": "aaaa"
}
```

实际发送为 ZeroMQ 多帧消息：
- 第1帧：`Frame`（topic）
- 第2帧：上述 JSON 字符串

## 可选参数

- `--bind`：绑定地址，默认 `tcp://*:5051`
- `--interval`：发送间隔(秒)，默认 `1.0`
- `--start_frame_id`：起始帧号，默认 `1`
- `--image`：image 字段内容，默认 `aaaa`
- `--topic`：发布 topic，默认 `Frame`

示例：

```bash
python3 moduleA/mock_module_a.py --interval 0.2 --image aaaa
```

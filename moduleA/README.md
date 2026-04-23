# moduleA 模拟发布端

用于模拟模块A持续通过 ZeroMQ 在 `tcp://*:5051` 发布 JSON 消息，默认 topic 为 `Frame`。
当前默认以 `moduleA/pub_example.json` 作为发布模板，并在每次发送时递增 `frame_id`。

## 启动

```bash
python3 moduleA/mock_module_a.py --image_path inference/35a6a1aa-5cb6907b.jpg
```

推荐（模块方式）：

```bash
python3 -m moduleA.mock_module_a --image_path inference/35a6a1aa-5cb6907b.jpg
```

默认每 `0.5` 秒发送一次，消息格式示意：

```json
{
  "...": "来自 moduleA/pub_example.json 的完整结构",
  "frame_id": 22489
}
```

说明：
- 发送前会把 `--image_path` 编码为 base64，并写入模板中各相机 `payload.Image.data`。
- 每次发送时，会把模板内所有 `frame_id` 字段整体递增（保持相对差值）。

实际发送为 ZeroMQ 多帧消息：
- 第1帧：`Frame`（topic）
- 第2帧：上述 JSON 字符串

## 可选参数

- `--bind`：绑定地址，默认 `tcp://*:5051`
- `--interval`：发送间隔(秒)，默认 `0.5`
- `--start_frame_id`：起始帧号，默认使用模板中的 `frame_id`
- `--image_path`：用于编码的 `.jpg/.jpeg` 文件路径（必填）
- `--template_path`：模板 JSON 路径，默认 `moduleA/pub_example.json`
- `--topic`：发布 topic，默认 `Frame`

示例：

```bash
python3 moduleA/mock_module_a.py \
  --image_path inference/35a6a1aa-5cb6907b.jpg \
  --interval 0.2 \
  --start_frame_id 30000
```

## 📊 模型推理

训练完成后，您可以使用 `moduleB/inference.py` 脚本对图片进行分类预测。

### 方法1: 使用推理脚本

#### 单张图片推理
```bash
# 基本用法（使用默认模型路径）
python moduleB/inference.py --image path/to/your/image.jpg

# 指定模型大小和设备
python moduleB/inference.py --image your_image.jpg --model_size 0_5x --device cpu
```

#### 批量图片预测
```bash
# 对整个目录进行预测
python moduleB/inference.py --image_dir path/to/images/ --output results.csv

# 指定Top-k结果
python moduleB/inference.py --image_dir images/ --output results.csv --top_k 5

# 使用不同的图像尺寸
python moduleB/inference.py --image_dir images/ --img_size 128 --output results.csv
```

#### 推理参数说明
- `--checkpoint`: 模型检查点路径（默认：`moduleB/outputs/best_model.pth`）
- `--image`: 单张图片路径
- `--image_dir`: 图片目录路径（批量预测）
- `--model_size`: 模型大小 0_5x/0_8x/1_0x (默认: 0_5x)
- `--num_classes`: 类别数量 (默认: 7)
- `--img_size`: 输入图像大小 (默认: 224)
- `--device`: 推理设备 auto/cuda/cpu (默认: auto)
- `--output`: 结果输出文件路径（CSV格式）
- `--top_k`: 显示top-k预测结果 (默认: 3)

### 推理输出示例

```
使用设备: cpu
加载模型: moduleB/outputs/best_model.pth
模型大小: 0_5x
模型参数总数: 1,041,935
验证准确率: 53.85%

预测图片: inference/b1cd1e94-26dd524f.jpg

预测结果:
类别: highway
置信度: 53.82%

Top-3 预测:
  1. highway: 53.82%
  2. city street: 21.31%
  3. unknown: 11.09%
```

## 🔌 moduleB ZeroMQ 服务输入格式

`moduleB/zmq_service.py` 当前支持两种输入协议（优先匹配旧格式）：

### 旧格式（兼容保留）
```json
{
  "frame_id": 1,
  "image": "/9j/4AAQSkZJRgABAQAAAQABAAD..."
}
```

### 新格式（moduleA 最新结构）
```json
{
  "frame_id": 1,
  "frames": {
    "top_camera": {
      "payload": {
        "Image": {
          "data": "/9j/4AAQSkZJRgABAQAAAQABAAD..."
        }
      }
    }
  }
}
```

字段映射规则：
- `frame_id`：取消息顶层 `frame_id`（必须可转为整数）
- `image`：取 `frames.top_camera.payload.Image.data`（必须为非空 base64 字符串）

非法消息会记录明确字段路径并跳过，服务保持持续运行。

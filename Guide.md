## 📊 模型推理

训练完成后，您可以使用 `inference.py` 脚本对图片进行分类预测。

### 方法1: 使用推理脚本

#### 单张图片推理
```bash
# 基本用法（使用默认模型路径）
python inference.py --image path/to/your/image.jpg

# 指定模型大小和设备
python inference.py --image your_image.jpg --model_size 0_5x --device cpu
```

#### 批量图片预测
```bash
# 对整个目录进行预测
python inference.py --image_dir path/to/images/ --output results.csv

# 指定Top-k结果
python inference.py --image_dir images/ --output results.csv --top_k 5

# 使用不同的图像尺寸
python inference.py --image_dir images/ --img_size 128 --output results.csv
```

#### 推理参数说明
- `--checkpoint`: 模型检查点路径 (默认: outputs/best_model.pth)
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
加载模型: outputs/best_model.pth
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

# RepGhost 简单训练脚本使用说明

这个简化版的训练脚本专门用于在您的自定义数据集上微调RepGhost模型，已针对MacBook Air M3芯片优化。

## 数据集结构

您的数据集应该按照以下结构组织：
```
dataset/
├── train/
│   ├── city street/
│   ├── gas stations/
│   ├── highway/
│   ├── parking lot/
│   ├── residential/
│   ├── tunnel/
│   └── unknown/
└── val/
    ├── city street/
    ├── gas stations/
    ├── highway/
    ├── parking lot/
    ├── residential/
    ├── tunnel/
    └── unknown/
```

## MacBook Air M3 优化配置

### M3芯片特性
- 8核CPU（4个性能核心 + 4个效率核心）
- 8核GPU
- 统一内存架构
- 支持MPS（Metal Performance Shaders）加速

### 推荐的默认参数（已针对M3优化）
```bash
python simple_train.py
```

默认参数：
- 训练数据目录: `dataset/train`
- 验证数据目录: `dataset/val`
- 类别数量: 7
- 模型大小: 0_5x
- 批次大小: 16（为M3优化的小批次）
- 训练轮数: 100
- 学习率: 0.001
- 输出目录: `outputs/train`
- 设备: `cpu`（可改为`mps`使用GPU加速）

## 🚀 快速开始

### 方法1: 使用快速启动器（推荐新手）
```bash
python train.py
```
这将启动交互式界面，让您选择：
- 快速测试
- 标准训练
- 完整训练
- 自定义训练
- 推理测试

### 方法2: 直接使用训练脚本

#### M3专用训练命令

##### 1. CPU训练（推荐，稳定）
```bash
# 默认CPU训练
python simple_train.py

# 调整批次大小优化性能
python simple_train.py --batch_size 8 --epochs 50
```

##### 2. MPS加速训练（实验性）
```bash
# 使用MPS GPU加速（需要较新版本的PyTorch）
python simple_train.py --device mps --batch_size 8

# 如果MPS不稳定，可以改用CPU
python simple_train.py --device cpu
```

##### 3. 自定义参数训练
```bash
# 使用更大的模型（增加训练时间）
python simple_train.py --model_size 0_8x --epochs 50 --lr 0.0005

# 快速测试训练
python simple_train.py --epochs 10 --batch_size 8 --img_size 128

# 指定不同的输出目录
python simple_train.py --output_dir ./m3_training_output
```

## 📊 模型推理

### M3性能优化建议

#### 内存管理
- **批次大小**: 推荐使用8-16，避免内存不足
- **工作进程**: 设置为0，避免多进程开销
- **图像大小**: 可以使用更小的尺寸（如128x128）进行快速测试

#### 训练策略
- **小模型优先**: 从0_5x模型开始，稳定后再尝试更大的模型
- **渐进式训练**: 先用少量epoch测试，确认正常后再完整训练
- **监控资源**: 使用Activity Monitor监控CPU和内存使用情况

#### 常见问题解决
- **内存不足**: 减小batch_size到8或4
- **训练过慢**: 检查是否有其他程序占用CPU资源
- **MPS错误**: 改用CPU训练（`--device cpu`）

## 完整参数说明

### 数据集参数
- `--train_dir`: 训练数据目录路径 (默认: 'dataset/train')
- `--val_dir`: 验证数据目录路径 (默认: 'dataset/val')
- `--num_classes`: 类别数量 (默认: 7)

### 模型参数
- `--model_size`: 模型大小，可选 '0_5x', '0_8x', '1_0x' (默认: '0_5x')
- `--pretrained`: 是否使用预训练权重 (默认: False)

### 训练参数
- `--batch_size`: 批次大小 (默认: 16，为M3优化)
- `--epochs`: 训练轮数 (默认: 100)
- `--lr`: 学习率 (默认: 0.001)
- `--weight_decay`: 权重衰减 (默认: 1e-4)
- `--img_size`: 输入图像大小 (默认: 224)

### 输出参数
- `--output_dir`: 模型保存目录 (默认: 'outputs/train')
- `--save_interval`: 模型保存间隔轮数 (默认: 10)
- `--log_interval`: 日志打印间隔批次 (默认: 20)

### 其他参数
- `--num_workers`: 数据加载器工作进程数 (默认: 0，M3优化)
- `--device`: 训练设备，可选 'auto', 'cuda', 'cpu', 'mps' (默认: 'cpu')
- `--seed`: 随机种子 (默认: 42)

## 输出文件

训练完成后，所有文件都会保存在 `outputs/train` 目录下：

### 模型文件
- `latest_checkpoint.pth`: 最新的模型检查点
- `best_model.pth`: 验证准确率最高的模型
- `checkpoint_epoch_X.pth`: 每隔指定轮数保存的检查点

### 训练记录
- `train_log_YYYYMMDD_HHMMSS.log`: 详细的训练日志文件
- `training_history.csv`: 每个epoch的训练和验证结果（CSV格式）
- `training_summary.txt`: 训练完成后的摘要信息
- `training_args.json`: 训练参数配置
- `class_names.txt`: 类别名称列表

### 文件说明
- **CSV文件**: 包含epoch、训练损失、训练准确率、验证损失、验证准确率、学习率等
- **日志文件**: 包含完整的训练过程日志，便于问题排查
- **摘要文件**: 包含最终结果、训练时间等关键信息

## 使用示例

### 场景1: 快速测试
```bash
# 使用小模型快速测试训练流程
python simple_train.py --model_size 0_5x --epochs 10 --batch_size 16
```

### 场景2: 完整训练
```bash
# 使用大模型进行完整训练
python simple_train.py \
    --model_size 1_0x \
    --epochs 200 \
    --batch_size 64 \
    --lr 0.0001 \
    --output_dir ./full_training_output
```

### 场景3: CPU训练
```bash
# 在CPU上训练（速度较慢，适合小数据集）
python simple_train.py --device cpu --batch_size 8 --num_workers 0
```

## 训练技巧

1. **学习率调整**: 如果损失下降缓慢，可以尝试增加学习率；如果损失震荡，可以降低学习率

2. **批次大小**: 根据GPU内存调整，批次越大训练越稳定，但占用更多内存

3. **数据增强**: 脚本中已包含基本的数据增强（水平翻转、旋转、颜色抖动）

4. **早停**: 观察验证集准确率，如果连续多个epoch不再提升，可以考虑停止训练

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

### 方法2: 使用快速启动器
```bash
python train.py
# 选择 "5. 仅推理测试"
```

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

### 方法3: Python代码推理

如果您想在自己的Python代码中使用模型，可以参考以下示例：

```python
import torch
from model.repghost import repghostnet_0_5x
from torchvision import transforms
from PIL import Image

# 加载模型
model = repghostnet_0_5x(num_classes=7)
checkpoint = torch.load('outputs/best_model.pth', weights_only=False)
if 'model_state_dict' in checkpoint:
    model.load_state_dict(checkpoint['model_state_dict'])
else:
    model.load_state_dict(checkpoint)
model.eval()

# 图像预处理
transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
])

# 类别映射
class_names = ['city street', 'gas stations', 'highway', 'parking lot', 'residential', 'tunnel', 'unknown']

# 推理
image = Image.open('your_image.jpg').convert('RGB')
image_tensor = transform(image).unsqueeze(0)

with torch.no_grad():
    output = model(image_tensor)
    probabilities = torch.nn.functional.softmax(output, dim=1)
    confidence, predicted = torch.max(probabilities, 1)

print(f'预测类别: {class_names[predicted.item()]}')
print(f'置信度: {confidence.item()*100:.2f}%')
```

## 常见问题

1. **内存不足**: 减小batch_size或使用更小的模型
2. **训练慢**: 确保使用GPU训练，增加num_workers
3. **过拟合**: 使用更小的学习率，增加数据增强，或使用dropout

## 性能建议

- 对于较大的数据集，建议使用 `model_size 1_0x` 获得更好的性能
- GPU内存足够时，使用更大的 `batch_size` 可以加速训练
- 根据验证集性能调整 `lr` 和 `weight_decay` 参数

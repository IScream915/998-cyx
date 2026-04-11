import torch
import torch.nn.functional as F
from PIL import Image
import argparse
import os
import sys
import time
from pathlib import Path

# 兼容以脚本方式启动: python3 moduleB/inference.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moduleB.model.repghost import repghostnet_0_5x, repghostnet_0_8x, repghostnet_1_0x, repghostnet_2_0x

MODULE_B_DIR = Path(__file__).resolve().parent
DEFAULT_CHECKPOINT = str((MODULE_B_DIR / "outputs" / "best_model.pth").resolve())


def load_model(checkpoint_path, model_size='2_0x', num_classes=7, device='cuda'):
    """加载训练好的模型"""
    # 创建模型
    if model_size == '0_5x':
        model = repghostnet_0_5x(num_classes=num_classes)
    elif model_size == '0_8x':
        model = repghostnet_0_8x(num_classes=num_classes)
    elif model_size == '1_0x':
        model = repghostnet_1_0x(num_classes=num_classes)
    elif model_size == '2_0x':
        model = repghostnet_2_0x(num_classes=num_classes)
    else:
        raise ValueError(f'不支持的模型大小: {model_size}')

    # 加载权重（兼容PyTorch 2.6+的安全设置）
    try:
        # 首先尝试使用安全加载
        checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=True)
    except:
        try:
            # 如果安全加载失败，尝试非安全加载（仅当信任文件来源时）
            print("警告: 使用非安全模式加载检查点文件")
            checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
        except Exception as e:
            print(f"加载检查点失败: {str(e)}")
            return None

    if 'model_state_dict' in checkpoint:
        model.load_state_dict(checkpoint['model_state_dict'])
    else:
        model.load_state_dict(checkpoint)

    model.to(device)
    model.eval()

    # 打印模型信息
    total_params = sum(p.numel() for p in model.parameters())
    print(f'模型大小: {model_size}')
    print(f'模型参数总数: {total_params:,}')
    print(f'验证准确率: {checkpoint.get("acc", "未知"):.2f}%' if isinstance(checkpoint, dict) and 'acc' in checkpoint else '')

    return model


def preprocess_image(image_path, img_size=224):
    """图像预处理"""
    image = Image.open(image_path).convert('RGB')
    return preprocess_pil_image(image, img_size)


def preprocess_pil_image(image, img_size=224):
    """PIL图像预处理"""
    image = image.convert('RGB')
    image = image.resize((img_size, img_size), Image.BILINEAR)

    # 避免 torchvision->numpy 路径导致的兼容性异常，直接用 torch 处理像素
    byte_tensor = torch.ByteTensor(torch.ByteStorage.from_buffer(image.tobytes()))
    image_tensor = byte_tensor.view(img_size, img_size, 3).permute(2, 0, 1).contiguous()
    image_tensor = image_tensor.float().div(255.0)

    mean = torch.tensor([0.485, 0.456, 0.406], dtype=image_tensor.dtype).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], dtype=image_tensor.dtype).view(3, 1, 1)
    image_tensor = (image_tensor - mean) / std
    image_tensor = image_tensor.unsqueeze(0)
    return image_tensor, image


def predict(model, image_tensor, device, class_names):
    """模型推理"""
    image_tensor = image_tensor.to(device)

    with torch.no_grad():
        outputs = model(image_tensor)
        probabilities = F.softmax(outputs, dim=1)
        confidence, predicted = torch.max(probabilities, 1)

        # 获取所有类别的概率
        all_probs = probabilities.cpu().numpy()[0]
        class_probs = [(class_names[i], prob * 100) for i, prob in enumerate(all_probs)]
        class_probs.sort(key=lambda x: x[1], reverse=True)

        return class_names[predicted.item()], confidence.item() * 100, class_probs


def batch_predict(model, image_dir, device, class_names, img_size=224, top_k=3):
    """批量预测一个目录中的所有图像"""
    supported_formats = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff']
    results = []

    for filename in os.listdir(image_dir):
        if any(filename.lower().endswith(ext) for ext in supported_formats):
            image_path = os.path.join(image_dir, filename)
            try:
                image_tensor, original_image = preprocess_image(image_path, img_size)
                pred_class, confidence, all_probs = predict(model, image_tensor, device, class_names)

                result = {
                    'filename': filename,
                    'predicted_class': pred_class,
                    'confidence': confidence,
                    'top_k': all_probs[:top_k]
                }
                results.append(result)

                print(f'图片: {filename}')
                print(f'预测类别: {pred_class} (置信度: {confidence:.2f}%)')
                print(f'Top-{top_k} 预测:')
                for i, (class_name, prob) in enumerate(all_probs[:top_k], 1):
                    print(f'  {i}. {class_name}: {prob:.2f}%')
                print('-' * 50)

            except Exception as e:
                print(f'处理图片 {filename} 时出错: {str(e)}')

    return results


def main():
    parser = argparse.ArgumentParser(description='RepGhost 模型推理')

    # 模型参数
    parser.add_argument('--checkpoint', type=str, default=DEFAULT_CHECKPOINT,
                        help='模型检查点路径')
    parser.add_argument('--model_size', type=str, default='2_0x',
                        choices=['0_5x', '0_8x', '1_0x', '2_0x'],
                        help='模型大小')
    parser.add_argument('--num_classes', type=int, default=7,
                        help='类别数量')

    # 输入参数
    parser.add_argument('--image', type=str,
                        help='单张图片路径')
    parser.add_argument('--image_dir', type=str,
                        help='图片目录路径（批量预测）')
    parser.add_argument('--img_size', type=int, default=224,
                        help='输入图像大小')

    # 输出参数
    parser.add_argument('--output', type=str,
                        help='结果输出文件路径（CSV格式）')
    parser.add_argument('--top_k', type=int, default=3,
                        help='显示top-k预测结果')

    # 其他参数
    parser.add_argument('--device', type=str, default='auto',
                        choices=['auto', 'cuda', 'cpu'],
                        help='推理设备')

    args = parser.parse_args()

    # 设置设备
    if args.device == 'auto':
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    else:
        device = torch.device(args.device)

    print(f'使用设备: {device}')

    # 检查输入
    if not args.image and not args.image_dir:
        raise ValueError('必须指定 --image 或 --image_dir 参数')
    if args.image and args.image_dir:
        raise ValueError('不能同时指定 --image 和 --image_dir 参数')

    # 类别名称
    class_names = ['city street', 'gas stations', 'highway', 'parking lot',
                   'residential', 'tunnel', 'unknown']

    # 加载模型
    print(f'加载模型: {args.checkpoint}')
    model = load_model(args.checkpoint, args.model_size, args.num_classes, device)

    if model is None:
        print("模型加载失败，请检查检查点文件")
        return

    # 进行推理
    results = []

    if args.image:
        # 单张图片推理
        print(f'\\n预测图片: {args.image}')
        image_tensor, original_image = preprocess_image(args.image, args.img_size)
        pred_class, confidence, all_probs = predict(model, image_tensor, device, class_names)

        print(f'\\n预测结果:')
        print(f'类别: {pred_class}')
        print(f'置信度: {confidence:.2f}%')
        print(f'\\nTop-{args.top_k} 预测:')
        for i, (class_name, prob) in enumerate(all_probs[:args.top_k], 1):
            print(f'  {i}. {class_name}: {prob:.2f}%')

        results.append({
            'filename': os.path.basename(args.image),
            'predicted_class': pred_class,
            'confidence': confidence,
            'top_k': all_probs[:args.top_k]
        })

    else:
        # 批量预测
        print(f'\\n批量预测目录: {args.image_dir}')
        start_time = time.time()
        results = batch_predict(model, args.image_dir, device, class_names,
                               args.img_size, args.top_k)
        elapsed_time = time.time() - start_time

        # 打印耗时信息
        print(f'\\n批量预测完成!')
        print(f'处理图片数: {len(results)}')
        print(f'总耗时: {elapsed_time:.2f} 秒')
        if len(results) > 0:
            print(f'平均每张图片: {elapsed_time / len(results):.3f} 秒')

        # 默认在输入目录下保存结果
        if not args.output:
            args.output = os.path.join(args.image_dir, 'result.csv')

    # 保存结果（仅在指定了输出文件或批量预测时）
    if results and args.output:
        import csv
        with open(args.output, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['filename', 'predicted_class', 'confidence']
            for i in range(args.top_k):
                fieldnames.extend([f'top_{i+1}_class', f'top_{i+1}_confidence'])

            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for result in results:
                row = {
                    'filename': result['filename'],
                    'predicted_class': result['predicted_class'],
                    'confidence': result['confidence']
                }

                for i, (class_name, prob) in enumerate(result['top_k']):
                    row[f'top_{i+1}_class'] = class_name
                    row[f'top_{i+1}_confidence'] = prob

                writer.writerow(row)

        print(f'\\n结果已保存到: {args.output}')


if __name__ == '__main__':
    main()

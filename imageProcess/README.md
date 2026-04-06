# imageProcess 编解码接口

该目录提供 `.jpg/.jpeg` 与 JSON `image` 字段（base64 字符串）之间的转换接口。

## 接口

- `encode_jpg_file_to_base64(image_path)`：读取 jpg/jpeg 文件并编码为 base64 字符串
- `encode_jpg_bytes_to_base64(jpg_bytes)`：将 jpg 字节编码为 base64 字符串
- `decode_base64_to_jpg_bytes(image_base64)`：将 base64 字符串解码为 jpg 字节（含 JPEG 校验）
- `decode_base64_to_pil_image(image_base64)`：将 base64 字符串解码为 `PIL.Image`
- `save_jpg_bytes(jpg_bytes, output_path)`：把 jpg 字节保存为文件

## 最小示例

```python
from imageProcess.codec import encode_jpg_file_to_base64, decode_base64_to_jpg_bytes

image_b64 = encode_jpg_file_to_base64("inference/35a6a1aa-5cb6907b.jpg")
jpg_bytes = decode_base64_to_jpg_bytes(image_b64)
```

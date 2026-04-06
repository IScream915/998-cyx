import base64
from io import BytesIO
from pathlib import Path
from typing import Union


class ImageCodecError(ValueError):
    """图像编解码异常。"""


def _is_jpeg_bytes(data: bytes) -> bool:
    return len(data) >= 4 and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"


def encode_jpg_bytes_to_base64(jpg_bytes: bytes) -> str:
    if not isinstance(jpg_bytes, (bytes, bytearray)):
        raise ImageCodecError("jpg_bytes 必须是 bytes")
    if len(jpg_bytes) == 0:
        raise ImageCodecError("jpg_bytes 不能为空")
    return base64.b64encode(bytes(jpg_bytes)).decode("ascii")


def encode_jpg_file_to_base64(image_path: Union[str, Path]) -> str:
    path = Path(image_path)
    if not path.exists() or not path.is_file():
        raise ImageCodecError(f"文件不存在: {path}")
    if path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ImageCodecError("仅支持 .jpg/.jpeg 文件")
    return encode_jpg_bytes_to_base64(path.read_bytes())


def decode_base64_to_jpg_bytes(image_base64: str) -> bytes:
    if not isinstance(image_base64, str) or not image_base64.strip():
        raise ImageCodecError("image_base64 必须是非空字符串")

    try:
        jpg_bytes = base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        raise ImageCodecError("image 字段不是合法 base64") from exc

    if len(jpg_bytes) == 0:
        raise ImageCodecError("解码后字节为空")

    if not _is_jpeg_bytes(jpg_bytes):
        raise ImageCodecError("解码后不是 JPEG 图像")

    return jpg_bytes


def decode_base64_to_pil_image(image_base64: str):
    try:
        from PIL import Image
    except Exception as exc:
        raise ImageCodecError("需要安装 Pillow 才能解码为 PIL.Image") from exc

    jpg_bytes = decode_base64_to_jpg_bytes(image_base64)
    return Image.open(BytesIO(jpg_bytes)).convert("RGB")


def save_jpg_bytes(jpg_bytes: bytes, output_path: Union[str, Path]) -> Path:
    path = Path(output_path)
    if path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ImageCodecError("输出文件必须是 .jpg/.jpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpg_bytes)
    return path

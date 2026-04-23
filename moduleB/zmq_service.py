import argparse
import base64
import io
import json
import logging
import math
import os
import re
import signal
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

# 兼容 macOS 下 OpenMP 重复加载导致的进程中止问题
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 兼容以脚本方式启动: python3 moduleB/zmq_service.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import torch
import zmq
import numpy as np
from PIL import Image
import torch.nn.functional as F

from imageProcess.codec import decode_base64_to_pil_image
from moduleB.inference import load_model, predict, preprocess_pil_image

MODULE_B_DIR = Path(__file__).resolve().parent
DEFAULT_CHECKPOINT = str((MODULE_B_DIR / "outputs" / "best_model.pth").resolve())
DEFAULT_LOCAL_SCENES_ROOT = str((PROJECT_ROOT / "frontend" / "assets" / "scenes").resolve())
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _natural_sort_key(text: str) -> list[Any]:
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", text)]


def _parse_frame_id(value: Any, field_path: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_path} 必须可转换为整数，当前值: {value!r}") from exc


def _parse_base64_image(value: Any, field_path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_path} 必须是非空字符串(base64)")
    return value


def _extract_frame_and_image(payload: dict[str, Any]) -> tuple[int, str]:
    # 兼容旧格式: {"frame_id": ..., "image": ...}
    if "frame_id" in payload and "image" in payload:
        frame_id = _parse_frame_id(payload["frame_id"], "frame_id")
        image_b64 = _parse_base64_image(payload["image"], "image")
        return frame_id, image_b64

    # 新格式: {"frame_id": ..., "frames": {"top_camera": {"payload": {"Image": {"data": ...}}}}}
    if "frame_id" not in payload:
        raise ValueError("消息缺少 frame_id 字段")

    frame_id = _parse_frame_id(payload["frame_id"], "frame_id")

    frames = payload.get("frames")
    if not isinstance(frames, dict):
        raise ValueError("消息缺少或非法字段: frames")

    top_camera = frames.get("top_camera")
    if not isinstance(top_camera, dict):
        raise ValueError("消息缺少或非法字段: frames.top_camera")

    top_payload = top_camera.get("payload")
    if not isinstance(top_payload, dict):
        raise ValueError("消息缺少或非法字段: frames.top_camera.payload")

    image_obj = top_payload.get("Image")
    if not isinstance(image_obj, dict):
        raise ValueError("消息缺少或非法字段: frames.top_camera.payload.Image")

    image_b64 = _parse_base64_image(image_obj.get("data"), "frames.top_camera.payload.Image.data")
    return frame_id, image_b64


def _extract_speed_kmh(payload: dict[str, Any]) -> int:
    vehicle_states = payload.get("vehicle_states")
    if not isinstance(vehicle_states, dict):
        raise ValueError("消息缺少或非法字段: vehicle_states")

    ego = vehicle_states.get("ego")
    if not isinstance(ego, dict):
        raise ValueError("消息缺少或非法字段: vehicle_states.ego")

    if "speed_mps" not in ego:
        raise ValueError("消息缺少字段: vehicle_states.ego.speed_mps")

    raw_speed_mps = ego.get("speed_mps")
    try:
        speed_mps = float(raw_speed_mps)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"vehicle_states.ego.speed_mps 必须可转换为数值，当前值: {raw_speed_mps!r}") from exc

    if not math.isfinite(speed_mps):
        raise ValueError(f"vehicle_states.ego.speed_mps 必须是有限数值，当前值: {raw_speed_mps!r}")
    if speed_mps < 0:
        raise ValueError(f"vehicle_states.ego.speed_mps 不能为负值，当前值: {speed_mps}")

    # 模块A提供的是 m/s，模块B输出 speed 统一用 km/h
    return int(round(speed_mps * 3.6))


def _decode_frame(frame: bytes) -> str:
    return frame.decode("utf-8", errors="replace").strip()


def _parse_json_message(frames: list[bytes], subscribed_topic: str) -> tuple[Optional[str], dict[str, Any]]:
    if not frames:
        raise ValueError("空消息帧")

    topic: Optional[str] = None
    payload_text: str

    if len(frames) == 1:
        payload_text = _decode_frame(frames[0])
        if subscribed_topic and payload_text.startswith(subscribed_topic + " "):
            payload_text = payload_text[len(subscribed_topic) + 1 :]
            topic = subscribed_topic
    else:
        topic = _decode_frame(frames[0])
        payload_text = _decode_frame(frames[-1])

    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("JSON 顶层必须为对象")

    return topic, payload


def _resolve_scene_dir(scenes_root: Path, scene_folder: str) -> Path:
    if not isinstance(scene_folder, str) or not scene_folder.strip():
        raise ValueError("scene 不能为空")
    clean = scene_folder.strip()
    if "/" in clean or "\\" in clean or clean in (".", ".."):
        raise ValueError("scene 非法")

    root = scenes_root.resolve()
    scene_dir = (root / clean).resolve()
    if scene_dir.parent != root:
        raise ValueError("scene 越界")
    if not scene_dir.is_dir():
        raise FileNotFoundError(f"场景目录不存在: {clean}")
    return scene_dir


def _collect_scene_images(scene_dir: Path) -> list[Path]:
    images = [
        item
        for item in scene_dir.iterdir()
        if item.is_file() and item.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    ]
    images.sort(key=lambda p: _natural_sort_key(p.name))
    return images


def _to_public_relpath(path: Path, scenes_root: Path, scene_folder: str) -> str:
    # 默认目录下输出 assets/scenes/<scene>/<file>，便于前端直接作为静态资源路径使用。
    root = scenes_root.resolve()
    try:
        rel_to_scenes = path.resolve().relative_to(root)
        return f"assets/scenes/{rel_to_scenes.as_posix()}"
    except Exception:
        return f"assets/scenes/{scene_folder}/{path.name}"


def _encode_pil_image_to_base64(image: Image.Image, fmt: str = "JPEG", quality: int = 85) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _apply_jet_colormap(cam_map: np.ndarray) -> np.ndarray:
    """将 [0,1] CAM 映射为 RGB 热力图（近似 JET）。"""
    x = np.clip(cam_map.astype(np.float32), 0.0, 1.0)

    r = np.clip(1.5 - np.abs(4.0 * x - 3.0), 0.0, 1.0)
    g = np.clip(1.5 - np.abs(4.0 * x - 2.0), 0.0, 1.0)
    b = np.clip(1.5 - np.abs(4.0 * x - 1.0), 0.0, 1.0)

    rgb = np.stack([r, g, b], axis=-1)
    return (rgb * 255.0).astype(np.uint8)


class GradCamGenerator:
    """基于 model.blocks 输出特征的 Grad-CAM 生成器。"""

    def __init__(self, model: torch.nn.Module, target_module: torch.nn.Module, device: torch.device) -> None:
        self.model = model
        self.device = device

        self._activations: Optional[torch.Tensor] = None
        self._gradients: Optional[torch.Tensor] = None

        self._forward_handle = target_module.register_forward_hook(self._forward_hook)
        self._backward_handle = target_module.register_full_backward_hook(self._backward_hook)

    def _forward_hook(self, _module: torch.nn.Module, _inputs: Any, output: torch.Tensor) -> None:
        self._activations = output

    def _backward_hook(
        self,
        _module: torch.nn.Module,
        _grad_input: tuple[torch.Tensor, ...],
        grad_output: tuple[torch.Tensor, ...],
    ) -> None:
        if grad_output and grad_output[0] is not None:
            self._gradients = grad_output[0]

    def _build_overlay(self, image: Image.Image) -> Image.Image:
        if self._activations is None or self._gradients is None:
            raise RuntimeError("Grad-CAM 特征或梯度为空")

        # [1, C, H, W]
        activations = self._activations
        gradients = self._gradients

        weights = gradients.mean(dim=(2, 3), keepdim=True)
        raw_cam = (weights * activations).sum(dim=1, keepdim=True)
        cam = F.relu(raw_cam)

        if cam.max().item() <= 0:
            # 兜底策略：正响应全零时使用绝对值，避免整帧热力图缺失
            cam = raw_cam.abs()
            if cam.max().item() <= 0:
                raise RuntimeError("Grad-CAM 响应全为0")

        cam = F.interpolate(
            cam,
            size=(image.height, image.width),
            mode="bilinear",
            align_corners=False,
        )
        cam = cam[0, 0]
        cam = cam - cam.min()
        cam = cam / (cam.max() + 1e-8)
        cam_np = cam.detach().cpu().numpy()

        heatmap_rgb = _apply_jet_colormap(cam_np)
        heatmap_img = Image.fromarray(heatmap_rgb, mode="RGB")
        base = image.convert("RGB")
        return Image.blend(base, heatmap_img, alpha=0.45)

    def predict_with_gradcam(
        self,
        image_tensor: torch.Tensor,
        original_image: Image.Image,
        class_names: list[str],
    ) -> tuple[str, float, str]:
        self._activations = None
        self._gradients = None

        input_tensor = image_tensor.to(self.device)
        self.model.zero_grad(set_to_none=True)

        logits = self.model(input_tensor)
        probabilities = F.softmax(logits, dim=1)
        confidence, predicted = torch.max(probabilities, 1)
        class_idx = int(predicted.item())

        score = logits[:, class_idx].sum()
        score.backward()

        overlay = self._build_overlay(original_image)
        heatmap_b64 = _encode_pil_image_to_base64(overlay, fmt="JPEG", quality=85)

        scene = class_names[class_idx]
        confidence_pct = float(confidence.item() * 100.0)
        return scene, confidence_pct, heatmap_b64


class ModuleBRuntimeState:
    def __init__(self, scenes_root: Path) -> None:
        self._lock = threading.Lock()
        self._scenes_root = scenes_root

        self._mode = "zmq"
        self._scene_folder: Optional[str] = None
        self._frame_paths: list[Path] = []
        self._frame_relpaths: list[str] = []
        self._frame_index = 0
        self._playing = False
        self._next_local_frame_id = 1
        self._force_emit_current = False
        self._next_emit_monotonic = 0.0
        self._last_error = ""

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "scene_folder": self._scene_folder,
            "frame_index": self._frame_index,
            "frame_total": len(self._frame_paths),
            "playing": self._playing,
            "next_local_frame_id": self._next_local_frame_id,
            "last_error": self._last_error,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    def get_mode(self) -> str:
        with self._lock:
            return self._mode

    def set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

    def clear_error(self) -> None:
        with self._lock:
            self._last_error = ""

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in ("zmq", "local"):
            raise ValueError("mode 仅支持 zmq/local")

        with self._lock:
            self._mode = mode
            if mode == "zmq":
                self._playing = False
                self._force_emit_current = False
            else:
                self._playing = False
                if self._frame_paths:
                    self._force_emit_current = True
            return self._snapshot_unlocked()

    def set_scene(self, scene_folder: str) -> dict[str, Any]:
        scene_dir = _resolve_scene_dir(self._scenes_root, scene_folder)
        frame_paths = _collect_scene_images(scene_dir)
        if not frame_paths:
            raise ValueError(f"场景目录下没有可播放图片: {scene_folder}")

        frame_relpaths = [_to_public_relpath(path, self._scenes_root, scene_folder) for path in frame_paths]

        with self._lock:
            self._scene_folder = scene_folder
            self._frame_paths = frame_paths
            self._frame_relpaths = frame_relpaths
            self._frame_index = 0
            self._playing = False
            self._next_local_frame_id = 1
            self._force_emit_current = True
            self._next_emit_monotonic = 0.0
            self._last_error = ""
            return self._snapshot_unlocked()

    def player_action(self, action: str) -> dict[str, Any]:
        if action not in ("play", "pause", "reset"):
            raise ValueError("action 仅支持 play/pause/reset")

        with self._lock:
            if action == "play":
                if not self._frame_paths:
                    raise ValueError("当前未选择场景或场景无图片")
                if self._frame_index >= len(self._frame_paths) - 1:
                    self._frame_index = 0
                    self._next_local_frame_id = 1
                elif self._frame_index == 0 and self._next_local_frame_id > 1 and len(self._frame_paths) > 1:
                    # 首帧已预览过时，播放从下一帧开始，避免重复展示首帧。
                    self._frame_index = 1
                self._playing = True
                self._force_emit_current = False
                self._next_emit_monotonic = 0.0
            elif action == "pause":
                self._playing = False
            else:  # reset
                self._frame_index = 0
                self._next_local_frame_id = 1
                self._playing = False
                if self._frame_paths:
                    self._force_emit_current = True
                self._next_emit_monotonic = 0.0

            return self._snapshot_unlocked()

    def acquire_local_emit(self, now_monotonic: float, interval_sec: float) -> Optional[dict[str, Any]]:
        with self._lock:
            if self._mode != "local":
                return None
            if not self._frame_paths:
                return None

            should_emit = False
            advance_after_emit = False

            if self._force_emit_current:
                self._force_emit_current = False
                should_emit = True
                if self._playing:
                    self._next_emit_monotonic = now_monotonic + interval_sec
            elif self._playing and now_monotonic >= self._next_emit_monotonic:
                should_emit = True
                advance_after_emit = True
                self._next_emit_monotonic = now_monotonic + interval_sec

            if not should_emit:
                return None

            emit_index = self._frame_index
            emit_path = self._frame_paths[emit_index]
            emit_relpath = self._frame_relpaths[emit_index]
            emit_total = len(self._frame_paths)
            emit_frame_id = self._next_local_frame_id
            self._next_local_frame_id += 1

            if advance_after_emit:
                if self._frame_index < emit_total - 1:
                    self._frame_index += 1
                else:
                    self._playing = False

            return {
                "frame_id": emit_frame_id,
                "scene_folder": self._scene_folder,
                "image_path": emit_path,
                "image_relpath": emit_relpath,
                "frame_index": emit_index,
                "frame_total": emit_total,
                "playing": self._playing,
            }


def _create_control_handler(runtime_state: ModuleBRuntimeState):
    class ModuleBControlHandler(BaseHTTPRequestHandler):
        server_version = "ModuleBControl/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            logging.info("[control] " + format, *args)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_json_body(self) -> dict[str, Any]:
            raw_len = self.headers.get("Content-Length", "0")
            try:
                body_len = int(raw_len)
            except ValueError as exc:
                raise ValueError("Content-Length 非法") from exc

            if body_len <= 0:
                return {}

            raw = self.rfile.read(body_len)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise ValueError("请求体不是合法JSON") from exc

            if not isinstance(payload, dict):
                raise ValueError("请求体JSON顶层必须是对象")
            return payload

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/state":
                self._send_json(HTTPStatus.OK, {"ok": True, "state": runtime_state.snapshot()})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path

            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            try:
                if path == "/mode":
                    mode = payload.get("mode")
                    if not isinstance(mode, str):
                        raise ValueError("mode 必须是字符串")
                    state = runtime_state.set_mode(mode)
                    self._send_json(HTTPStatus.OK, {"ok": True, "state": state})
                    return

                if path == "/scene":
                    scene = payload.get("scene")
                    if not isinstance(scene, str):
                        raise ValueError("scene 必须是字符串")
                    state = runtime_state.set_scene(scene)
                    self._send_json(HTTPStatus.OK, {"ok": True, "state": state})
                    return

                if path == "/player":
                    action = payload.get("action", payload.get("command"))
                    if not isinstance(action, str):
                        raise ValueError("action 必须是字符串")
                    state = runtime_state.player_action(action)
                    self._send_json(HTTPStatus.OK, {"ok": True, "state": state})
                    return

                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
            except FileNotFoundError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except Exception as exc:
                logging.exception("控制接口处理失败: %s", exc)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "服务内部错误"})

    return ModuleBControlHandler


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ZeroMQ JSON 订阅服务")
    parser.add_argument("--endpoint", default="tcp://localhost:5051", help="订阅地址")
    parser.add_argument("--topic", default="Frame", help="订阅 topic，默认 Frame")
    parser.add_argument("--publish_bind", default="tcp://*:5052", help="发布地址，供 moduleE 订阅")
    parser.add_argument("--publish_topic", default="Frame", help="发布 topic，默认 Frame")
    parser.add_argument(
        "--publish_rate_hz",
        type=float,
        default=0.0,
        help="发布限速(Hz)，<=0 表示不限制",
    )
    parser.add_argument("--timeout_ms", type=int, default=1000, help="接收超时(ms)")
    parser.add_argument("--reconnect_delay", type=float, default=1.0, help="重连等待(秒)")
    parser.add_argument("--checkpoint", type=str, default=DEFAULT_CHECKPOINT, help="模型检查点路径")
    parser.add_argument(
        "--model_size",
        type=str,
        default="2_0x",
        choices=["0_5x", "0_8x", "1_0x", "2_0x"],
        help="模型大小",
    )
    parser.add_argument("--num_classes", type=int, default=7, help="类别数量")
    parser.add_argument("--img_size", type=int, default=224, help="输入图像大小")
    parser.add_argument("--device", type=str, default="auto", choices=["auto", "cuda", "cpu"], help="推理设备")

    parser.add_argument(
        "--local_scenes_root",
        type=str,
        default=DEFAULT_LOCAL_SCENES_ROOT,
        help="本地场景根目录，默认 frontend/assets/scenes",
    )
    parser.add_argument("--local_rate_hz", type=float, default=2.0, help="本地模式播放速率(Hz)")
    parser.add_argument("--local_speed_kmh", type=float, default=0.0, help="本地模式输出速度(km/h)")

    parser.add_argument("--control_host", default="127.0.0.1", help="控制接口监听地址")
    parser.add_argument("--control_port", type=int, default=5056, help="控制接口监听端口")
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.publish_rate_hz < 0:
        parser.error("--publish_rate_hz 不能为负数")
    if args.local_rate_hz <= 0:
        parser.error("--local_rate_hz 必须大于0")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    scenes_root = Path(args.local_scenes_root).resolve()
    if not scenes_root.is_dir():
        parser.error(f"--local_scenes_root 目录不存在: {scenes_root}")

    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    class_names = ["city street", "gas stations", "highway", "parking lot", "residential", "tunnel", "unknown"]

    logging.info("加载模型: %s", args.checkpoint)
    model = load_model(
        checkpoint_path=args.checkpoint,
        model_size=args.model_size,
        num_classes=args.num_classes,
        device=device,
    )
    if model is None:
        raise RuntimeError("模型加载失败，请检查检查点文件")

    gradcam_generator: Optional[GradCamGenerator] = None
    if hasattr(model, "blocks"):
        try:
            gradcam_generator = GradCamGenerator(model, getattr(model, "blocks"), device)
            logging.info("Grad-CAM 已启用，目标层: model.blocks（仅local模式）")
        except Exception as exc:
            logging.warning("Grad-CAM 初始化失败，将跳过热力图: %s", exc)
    else:
        logging.warning("模型缺少 blocks 层，Grad-CAM 将不可用")

    runtime_state = ModuleBRuntimeState(scenes_root=scenes_root)

    control_handler = _create_control_handler(runtime_state)
    control_server = ThreadingHTTPServer((args.control_host, args.control_port), control_handler)
    control_thread = threading.Thread(target=control_server.serve_forever, daemon=True)
    control_thread.start()
    logging.info("控制接口已启动: http://%s:%d", args.control_host, args.control_port)

    subscribe_context = zmq.Context()

    def create_subscriber() -> zmq.Socket:
        subscriber = subscribe_context.socket(zmq.SUB)
        subscriber.setsockopt(zmq.RCVTIMEO, args.timeout_ms)
        subscriber.setsockopt_string(zmq.SUBSCRIBE, args.topic)
        subscriber.connect(args.endpoint)
        logging.info("已连接到 %s, topic=%r", args.endpoint, args.topic)
        return subscriber

    subscriber = create_subscriber()

    publish_context = zmq.Context()
    publisher = publish_context.socket(zmq.PUB)
    publisher.bind(args.publish_bind)
    logging.info("已启动发布端: %s, topic=%r", args.publish_bind, args.publish_topic)

    min_publish_interval = 1.0 / args.publish_rate_hz if args.publish_rate_hz > 0 else 0.0
    if min_publish_interval > 0:
        logging.info(
            "发布限速已启用: %.3f Hz (最小间隔 %.3f 秒)",
            args.publish_rate_hz,
            min_publish_interval,
        )

    running = True
    last_publish_at: Optional[float] = None
    local_emit_interval = 1.0 / args.local_rate_hz

    def handle_signal(signum: int, _frame: Any) -> None:
        nonlocal running
        logging.info("收到信号 %s，准备停止 moduleB 服务", signum)
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    def publish_result(
        *,
        frame_id: int,
        scene: str,
        confidence: float,
        speed: float,
        source_mode: str,
        scene_folder: Optional[str] = None,
        image_relpath: Optional[str] = None,
        frame_index: Optional[int] = None,
        frame_total: Optional[int] = None,
        heatmap_base64: Optional[str] = None,
    ) -> None:
        nonlocal last_publish_at

        result: dict[str, Any] = {
            "frame_id": frame_id,
            "scene": scene,
            "conference": confidence,
            "confidence": confidence,
            "speed": speed,
            "source_mode": source_mode,
        }

        if source_mode == "local":
            result["scene_folder"] = scene_folder
            result["image_relpath"] = image_relpath
            result["frame_index"] = frame_index
            result["frame_total"] = frame_total
            if isinstance(heatmap_base64, str) and heatmap_base64:
                result["heatmap_base64"] = heatmap_base64

        result_json = json.dumps(result, ensure_ascii=False)

        if min_publish_interval > 0 and last_publish_at is not None:
            elapsed = time.monotonic() - last_publish_at
            if elapsed < min_publish_interval:
                time.sleep(min_publish_interval - elapsed)

        publisher.send_multipart([args.publish_topic.encode("utf-8"), result_json.encode("utf-8")])
        last_publish_at = time.monotonic()

        print(result_json)
        sys.stdout.flush()

    try:
        while running:
            mode = runtime_state.get_mode()

            if mode == "zmq":
                try:
                    frames = subscriber.recv_multipart()
                    _topic, payload = _parse_json_message(frames, args.topic)
                    frame_id, image_b64 = _extract_frame_and_image(payload)
                    speed = _extract_speed_kmh(payload)
                    image = decode_base64_to_pil_image(image_b64)
                    image_tensor, _ = preprocess_pil_image(image, args.img_size)
                    scene, confidence, _ = predict(model, image_tensor, device, class_names)
                    publish_result(
                        frame_id=frame_id,
                        scene=scene,
                        confidence=float(confidence),
                        speed=float(speed),
                        source_mode="zmq",
                    )
                    runtime_state.clear_error()
                except zmq.Again:
                    continue
                except (json.JSONDecodeError, ValueError) as exc:
                    runtime_state.set_error(str(exc))
                    logging.warning("收到非法 ZMQ 消息，已跳过: %s", exc)
                except zmq.ZMQError as exc:
                    runtime_state.set_error(f"ZMQ异常: {exc}")
                    logging.error("ZMQ 异常，准备重连: %s", exc)
                    try:
                        subscriber.close(linger=0)
                    except Exception:
                        pass
                    time.sleep(args.reconnect_delay)
                    subscriber = create_subscriber()
                except Exception as exc:
                    runtime_state.set_error(str(exc))
                    logging.exception("ZMQ 模式处理异常: %s", exc)
                continue

            local_task = runtime_state.acquire_local_emit(time.monotonic(), local_emit_interval)
            if local_task is None:
                time.sleep(0.01)
                continue

            try:
                image = Image.open(local_task["image_path"]).convert("RGB")

                image_tensor, _ = preprocess_pil_image(image, args.img_size)
                heatmap_base64: Optional[str] = None
                if gradcam_generator is not None:
                    try:
                        scene, confidence, heatmap_base64 = gradcam_generator.predict_with_gradcam(
                            image_tensor=image_tensor,
                            original_image=image,
                            class_names=class_names,
                        )
                    except Exception as exc:
                        logging.warning("frame_id=%s 热力图生成失败，降级为普通推理: %s", local_task["frame_id"], exc)
                        scene, confidence, _ = predict(model, image_tensor, device, class_names)
                else:
                    scene, confidence, _ = predict(model, image_tensor, device, class_names)

                publish_result(
                    frame_id=int(local_task["frame_id"]),
                    scene=scene,
                    confidence=float(confidence),
                    speed=float(args.local_speed_kmh),
                    source_mode="local",
                    scene_folder=local_task.get("scene_folder"),
                    image_relpath=local_task.get("image_relpath"),
                    frame_index=local_task.get("frame_index"),
                    frame_total=local_task.get("frame_total"),
                    heatmap_base64=heatmap_base64,
                )
                runtime_state.clear_error()
            except Exception as exc:
                runtime_state.set_error(str(exc))
                logging.exception("本地模式处理异常: %s", exc)
                time.sleep(0.05)
    finally:
        try:
            control_server.shutdown()
        except Exception:
            pass
        try:
            control_server.server_close()
        except Exception:
            pass

        try:
            subscriber.close(linger=0)
        except Exception:
            pass
        try:
            subscribe_context.term()
        except Exception:
            pass

        try:
            publisher.close(linger=0)
        except Exception:
            pass
        try:
            publish_context.term()
        except Exception:
            pass


if __name__ == "__main__":
    main()

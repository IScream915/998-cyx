import argparse
import base64
import io
import json
import logging
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

import zmq
from PIL import Image

# 兼容以脚本方式运行: python3 moduleC/mock_module_c.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moduleC.coreDetector import CoreDetector
from moduleC.coreDetector.traffic_sign_map import TRAFFIC_SIGN

DEFAULT_LOCAL_SCENES_ROOT = str((PROJECT_ROOT / "frontend" / "assets" / "scenes").resolve())
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _decode_frame(frame: bytes) -> str:
    return frame.decode("utf-8", errors="replace").strip()


def _parse_json_message(frames: list[bytes], subscribed_topic: str) -> tuple[str, dict[str, Any]]:
    if not frames:
        raise ValueError("收到空消息帧")

    topic = subscribed_topic
    payload_text = ""

    if len(frames) == 1:
        payload_text = _decode_frame(frames[0])
        if subscribed_topic and payload_text.startswith(subscribed_topic + " "):
            payload_text = payload_text[len(subscribed_topic) + 1 :].strip()
    else:
        topic = _decode_frame(frames[0]) or subscribed_topic
        payload_candidates = [_decode_frame(frame) for frame in frames[1:]]
        for candidate in reversed(payload_candidates):
            if candidate:
                payload_text = candidate
                break

    if not payload_text:
        raise ValueError("消息 payload 为空，无法解析 JSON")

    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("JSON 顶层必须为对象")

    return topic, payload


def _extract_frame_and_image(payload: dict[str, Any]) -> tuple[int, str]:
    # 兼容旧格式: {"frame_id": ..., "image": ...}
    if "frame_id" in payload and "image" in payload:
        frame_id = int(payload["frame_id"])
        image = payload["image"]
        if not isinstance(image, str) or not image.strip():
            raise ValueError("消息缺少或非法字段: image")
        return frame_id, image

    if "frame_id" not in payload:
        raise ValueError("消息缺少 frame_id 字段")

    frame_id = int(payload["frame_id"])

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

    image_data = image_obj.get("data")
    if not isinstance(image_data, str) or not image_data.strip():
        raise ValueError("消息缺少或非法字段: frames.top_camera.payload.Image.data")

    return frame_id, image_data


def _slim_detections(items: Any, include_class_name: bool = False) -> list[dict[str, Any]]:
    slim: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return slim

    for item in items:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {
            "bbox": item.get("bbox", []),
            "confidence": item.get("confidence", 0.0),
        }
        if include_class_name:
            sign_key = item.get("class_name", "")
            if sign_key in TRAFFIC_SIGN:
                row["class_name"] = TRAFFIC_SIGN[sign_key].get("name", sign_key)
            else:
                row["class_name"] = sign_key
        slim.append(row)
    return slim


def _natural_sort_key(text: str) -> list[Any]:
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", text)]


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
    root = scenes_root.resolve()
    try:
        rel_to_scenes = path.resolve().relative_to(root)
        return f"assets/scenes/{rel_to_scenes.as_posix()}"
    except Exception:
        return f"assets/scenes/{scene_folder}/{path.name}"


def _encode_image_file_to_jpeg_base64(image_path: Path) -> str:
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=90)
        return base64.b64encode(buffer.getvalue()).decode("ascii")


class ModuleCRuntimeState:
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
                    self._frame_index = 1
                self._playing = True
                self._force_emit_current = False
                self._next_emit_monotonic = 0.0
            elif action == "pause":
                self._playing = False
            else:
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


def _create_control_handler(runtime_state: ModuleCRuntimeState):
    class ModuleCControlHandler(BaseHTTPRequestHandler):
        server_version = "ModuleCControl/1.0"
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

    return ModuleCControlHandler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模块C：订阅A并调用CoreDetector后发布")
    parser.add_argument("--endpoint", default="tcp://localhost:5051", help="订阅地址")
    parser.add_argument("--topic", default="Frame", help="订阅 topic")
    parser.add_argument("--publish_bind", default="tcp://*:5053", help="发布地址，供 moduleE/ws_bridge 订阅")
    parser.add_argument("--publish_topic", default="Frame", help="发布 topic")
    parser.add_argument("--publish_rate_hz", type=float, default=0.0, help="发布限速(Hz)，<=0表示不限制")
    parser.add_argument("--timeout_ms", type=int, default=1000, help="接收超时(ms)")
    parser.add_argument("--reconnect_delay", type=float, default=1.0, help="重连等待(秒)")

    parser.add_argument("--sign-model", default=None, help="CoreDetector 交通标志模型路径")
    parser.add_argument("--scene-model", default=None, help="CoreDetector 场景模型路径")
    parser.add_argument("--conf", type=float, default=0.25, help="CoreDetector 置信度阈值")
    parser.add_argument("--iou", type=float, default=0.45, help="CoreDetector IoU 阈值")
    parser.add_argument("--img-size", type=int, default=640, help="CoreDetector 推理尺寸")
    parser.add_argument("--device", default=None, help="CoreDetector 推理设备: cuda:0/cpu")
    parser.add_argument("--num_threads", type=int, default=4, help="CoreDetector torch/OpenMP 线程数")
    parser.add_argument("--num_interop_threads", type=int, default=1, help="CoreDetector torch interop 线程数")
    parser.add_argument(
        "--disable_parallel_infer",
        action="store_true",
        help="禁用双模型并行推理（默认开启并行）",
    )
    parser.add_argument("--disable-ocr", action="store_true", help="禁用数字类交通标志 OCR 主识别")
    parser.add_argument("--ocr-min-conf", type=float, default=0.4, help="OCR 主识别最低置信度阈值")
    parser.add_argument("--save-vis", action="store_true", help="是否保存检测可视化图片")
    parser.add_argument("--vis-dir", default=None, help="可视化输出目录（需配合 --save-vis）")

    parser.add_argument(
        "--local_scenes_root",
        type=str,
        default=DEFAULT_LOCAL_SCENES_ROOT,
        help="本地场景根目录，默认 frontend/assets/scenes",
    )
    parser.add_argument("--local_rate_hz", type=float, default=2.0, help="本地模式播放速率(Hz)")

    parser.add_argument("--control_host", default="127.0.0.1", help="控制接口监听地址")
    parser.add_argument("--control_port", type=int, default=5057, help="控制接口监听端口")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.publish_rate_hz < 0:
        raise ValueError("--publish_rate_hz 不能为负数")
    if args.local_rate_hz <= 0:
        raise ValueError("--local_rate_hz 必须大于0")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    scenes_root = Path(args.local_scenes_root).resolve()
    if not scenes_root.is_dir():
        raise FileNotFoundError(f"--local_scenes_root 目录不存在: {scenes_root}")

    detector = CoreDetector(
        sign_model_path=args.sign_model,
        scene_model_path=args.scene_model,
        conf=args.conf,
        iou=args.iou,
        img_size=args.img_size,
        device=args.device,
        num_threads=args.num_threads,
        num_interop_threads=args.num_interop_threads,
        enable_parallel_infer=not args.disable_parallel_infer,
        enable_ocr=not args.disable_ocr,
        ocr_min_conf=args.ocr_min_conf,
    )

    vis_dir = Path(args.vis_dir).resolve() if args.vis_dir else detector.output_dir
    if args.save_vis:
        vis_dir.mkdir(parents=True, exist_ok=True)

    runtime_state = ModuleCRuntimeState(scenes_root=scenes_root)
    control_handler = _create_control_handler(runtime_state)
    control_server = ThreadingHTTPServer((args.control_host, args.control_port), control_handler)
    control_thread = threading.Thread(target=control_server.serve_forever, daemon=True)
    control_thread.start()
    logging.info("控制接口已启动: http://%s:%d", args.control_host, args.control_port)

    ctx = zmq.Context()

    def create_subscriber() -> zmq.Socket:
        socket = ctx.socket(zmq.SUB)
        socket.setsockopt(zmq.RCVTIMEO, args.timeout_ms)
        socket.setsockopt_string(zmq.SUBSCRIBE, args.topic)
        socket.connect(args.endpoint)
        logging.info("[moduleC] SUB 已连接: %s, topic=%s", args.endpoint, args.topic)
        return socket

    socket = create_subscriber()

    publisher = ctx.socket(zmq.PUB)
    publisher.bind(args.publish_bind)
    logging.info("[moduleC] PUB 已启动: %s, topic=%s", args.publish_bind, args.publish_topic)
    logging.info("[moduleC] CoreDetector 已加载")
    logging.info(
        "[moduleC] 推理配置: num_threads=%s, num_interop_threads=%s, parallel_infer=%s, ocr_enabled=%s, ocr_min_conf=%.2f",
        args.num_threads,
        args.num_interop_threads,
        not args.disable_parallel_infer,
        not args.disable_ocr,
        args.ocr_min_conf,
    )

    running = True
    last_publish_at: Optional[float] = None
    min_publish_interval = 1.0 / args.publish_rate_hz if args.publish_rate_hz > 0 else 0.0
    local_emit_interval = 1.0 / args.local_rate_hz

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    def publish_payload(payload: dict[str, Any]) -> None:
        nonlocal last_publish_at

        if min_publish_interval > 0 and last_publish_at is not None:
            elapsed = time.monotonic() - last_publish_at
            if elapsed < min_publish_interval:
                time.sleep(min_publish_interval - elapsed)

        output_text = json.dumps(payload, ensure_ascii=False)
        publisher.send_multipart([args.publish_topic.encode("utf-8"), output_text.encode("utf-8")])
        last_publish_at = time.monotonic()
        print(output_text)
        sys.stdout.flush()

    def build_output_payload(
        *,
        frame_id: int,
        detect_result: dict[str, Any],
        source_mode: str,
        scene_folder: Optional[str] = None,
        image_relpath: Optional[str] = None,
        frame_index: Optional[int] = None,
        frame_total: Optional[int] = None,
        yolo_overlay_base64: Optional[str] = None,
    ) -> dict[str, Any]:
        traffic_signs = _slim_detections(detect_result.get("traffic_signs", []), include_class_name=True)
        pedestrians = _slim_detections(detect_result.get("pedestrians", []))
        vehicles = _slim_detections(detect_result.get("vehicles", []))

        payload: dict[str, Any] = {
            "frame_id": frame_id,
            "source_mode": source_mode,
            "image_size": detect_result.get("image_size", {}),
            "traffic_signs": traffic_signs,
            "num_traffic_signs": len(traffic_signs),
            "pedestrians": pedestrians,
            "num_pedestrians": len(pedestrians),
            "vehicles": vehicles,
            "num_vehicles": len(vehicles),
            "tracked_pedestrians": bool(detect_result.get("tracked_pedestrians", False)),
        }

        if source_mode == "local":
            payload["scene_folder"] = scene_folder
            payload["image_relpath"] = image_relpath
            payload["frame_index"] = frame_index
            payload["frame_total"] = frame_total
            if isinstance(yolo_overlay_base64, str) and yolo_overlay_base64:
                payload["yolo_overlay_base64"] = yolo_overlay_base64

        return payload

    def run_detect(
        *,
        frame_id: int,
        image_b64: str,
        source_mode: str,
        scene_folder: Optional[str] = None,
        image_relpath: Optional[str] = None,
        frame_index: Optional[int] = None,
        frame_total: Optional[int] = None,
        vis_output_path: Optional[str] = None,
    ) -> None:
        request_overlay = source_mode == "local"
        detect_result = detector.detect_base64(
            image_b64,
            save_visualization=args.save_vis,
            vis_output_path=vis_output_path,
            return_visualization_base64=request_overlay,
        )
        yolo_overlay_base64: Optional[str] = None
        if request_overlay:
            overlay_value = detect_result.get("visualization_base64")
            if isinstance(overlay_value, str) and overlay_value:
                yolo_overlay_base64 = overlay_value
            else:
                overlay_error = detect_result.get("visualization_error")
                if isinstance(overlay_error, str) and overlay_error:
                    logging.warning(
                        "frame_id=%s YOLO识别框生成失败，降级仅推送统计: %s",
                        frame_id,
                        overlay_error,
                    )
        payload = build_output_payload(
            frame_id=frame_id,
            detect_result=detect_result,
            source_mode=source_mode,
            scene_folder=scene_folder,
            image_relpath=image_relpath,
            frame_index=frame_index,
            frame_total=frame_total,
            yolo_overlay_base64=yolo_overlay_base64,
        )
        publish_payload(payload)
        runtime_state.clear_error()

    try:
        while running:
            mode = runtime_state.get_mode()

            if mode == "zmq":
                try:
                    frames = socket.recv_multipart()
                    _topic, payload = _parse_json_message(frames, args.topic)
                    frame_id, image_b64 = _extract_frame_and_image(payload)

                    vis_out = None
                    if args.save_vis:
                        vis_out = str((vis_dir / f"frame_{frame_id}_detected.jpg").resolve())

                    run_detect(
                        frame_id=frame_id,
                        image_b64=image_b64,
                        source_mode="zmq",
                        vis_output_path=vis_out,
                    )
                except zmq.Again:
                    continue
                except (json.JSONDecodeError, ValueError) as exc:
                    runtime_state.set_error(str(exc))
                    logging.warning("收到非法 ZMQ 消息，已跳过: %s", exc)
                except zmq.ZMQError as exc:
                    runtime_state.set_error(f"ZMQ异常: {exc}")
                    logging.error("ZMQ 异常，准备重连: %s", exc)
                    try:
                        socket.close(linger=0)
                    except Exception:
                        pass
                    time.sleep(args.reconnect_delay)
                    socket = create_subscriber()
                except Exception as exc:
                    runtime_state.set_error(str(exc))
                    logging.exception("ZMQ 模式处理异常: %s", exc)
                continue

            local_task = runtime_state.acquire_local_emit(time.monotonic(), local_emit_interval)
            if local_task is None:
                time.sleep(0.01)
                continue

            try:
                image_b64 = _encode_image_file_to_jpeg_base64(Path(local_task["image_path"]))

                vis_out = None
                if args.save_vis:
                    vis_out = str((vis_dir / f"local_frame_{local_task['frame_id']}_detected.jpg").resolve())

                run_detect(
                    frame_id=int(local_task["frame_id"]),
                    image_b64=image_b64,
                    source_mode="local",
                    scene_folder=local_task.get("scene_folder"),
                    image_relpath=local_task.get("image_relpath"),
                    frame_index=local_task.get("frame_index"),
                    frame_total=local_task.get("frame_total"),
                    vis_output_path=vis_out,
                )
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

        detector.close()

        try:
            socket.close(linger=0)
        except Exception:
            pass

        try:
            publisher.close(linger=0)
        except Exception:
            pass

        ctx.term()
        logging.info("[moduleC] 已停止")


if __name__ == "__main__":
    main()

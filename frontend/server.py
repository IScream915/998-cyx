#!/usr/bin/env python3
"""前端静态资源启动脚本（统一入口）。

特性：
- 无需手动指定 --directory
- 可从任意工作目录启动
- 默认监听 0.0.0.0，便于本地/服务器访问
- 提供场景目录查询 API
- 代理 moduleB/moduleD 控制接口，供前端同源调用
- 内置 moduleC 实时桥接（/api/module-c/health + /api/module-c/ws）
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import queue
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_MODULE_C_CONFIG_PATH = PROJECT_ROOT / "moduleC" / "demo" / "modulecd_bsd_demo" / "config.toml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 frontend 静态页面服务（含 moduleC 实时桥接）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=4173, help="监听端口，默认 4173")
    parser.add_argument("--module_b_control_host", default="127.0.0.1", help="moduleB控制接口地址")
    parser.add_argument("--module_b_control_port", type=int, default=5056, help="moduleB控制接口端口")
    parser.add_argument("--module_d_control_host", default="127.0.0.1", help="moduleD控制接口地址")
    parser.add_argument("--module_d_control_port", type=int, default=5057, help="moduleD控制接口端口")

    # moduleC bridge参数（保留 live_server 历史参数别名）
    parser.add_argument(
        "--module_c_config",
        "--config",
        dest="module_c_config",
        default=str(DEFAULT_MODULE_C_CONFIG_PATH),
        help="moduleC demo config.toml 路径",
    )
    parser.add_argument(
        "--module_c_input_endpoint",
        "--input-endpoint",
        dest="module_c_input_endpoint",
        default=None,
        help="覆盖 moduleC 输入订阅地址",
    )
    parser.add_argument(
        "--module_c_output_endpoint",
        "--output-endpoint",
        dest="module_c_output_endpoint",
        default=None,
        help="覆盖 moduleC 输出订阅地址",
    )
    parser.add_argument(
        "--module_c_browser_endpoint",
        "--browser-endpoint",
        dest="module_c_browser_endpoint",
        default=None,
        help="覆盖 moduleC browser-only 订阅地址",
    )
    parser.add_argument(
        "--module_c_topic",
        "--topic",
        dest="module_c_topic",
        default=None,
        help="覆盖 moduleC 订阅 topic",
    )
    parser.add_argument(
        "--module_c_merge_timeout_ms",
        "--merge-timeout-ms",
        dest="module_c_merge_timeout_ms",
        type=int,
        default=1000,
        help="moduleC 输入/输出配对超时（毫秒）",
    )
    parser.add_argument(
        "--module_c_push_fps",
        "--push-fps",
        dest="module_c_push_fps",
        type=float,
        default=5.0,
        help="moduleC WebSocket 最大推送帧率",
    )
    parser.add_argument(
        "--module_e_sim_b_bind",
        default="tcp://127.0.0.1:6062",
        help="moduleE仿真B输入发布地址（frontend bind）",
    )
    parser.add_argument(
        "--module_e_sim_d_bind",
        default="tcp://127.0.0.1:6063",
        help="moduleE仿真D输入发布地址（frontend bind）",
    )
    parser.add_argument(
        "--module_e_sim_output_endpoint",
        default="tcp://127.0.0.1:6064",
        help="moduleE仿真输出订阅地址（frontend connect）",
    )
    parser.add_argument(
        "--module_e_sim_topic",
        default="SimFrame",
        help="moduleE仿真输入输出topic",
    )
    parser.add_argument(
        "--module_e_sim_start_frame_id",
        type=int,
        default=10001,
        help="moduleE仿真起始frame_id",
    )
    parser.add_argument(
        "--module_e_control_host",
        default="127.0.0.1",
        help="moduleE-demo控制接口地址",
    )
    parser.add_argument(
        "--module_e_control_port",
        type=int,
        default=5064,
        help="moduleE-demo控制接口端口",
    )
    return parser


def resolve_lan_ip() -> str | None:
    """尽量获取当前机器局域网 IP，用于提示同网段访问地址。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 不会真正建立外部连接，仅用于触发本机路由选择
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def _natural_sort_key(text: str) -> list[Any]:
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", text)]


def _load_module_c_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with config_path.open("rb") as fh:
        return tomllib.load(fh)


def _require_zmq() -> None:
    try:
        import zmq  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover - environment specific
        raise RuntimeError("moduleC bridge 依赖 pyzmq，请先安装: pip install pyzmq") from exc


def _recv_topic_and_payload(frames: list[bytes], default_topic: str) -> tuple[str, bytes]:
    if len(frames) >= 2:
        topic = frames[0].decode("utf-8", errors="replace").strip() or default_topic
        payload = frames[-1]
    elif len(frames) == 1:
        single = frames[0]
        text = single.decode("utf-8", errors="replace")
        stripped = text.lstrip()
        if stripped.startswith("{"):
            topic = default_topic
            payload = single
        else:
            head, sep, tail = stripped.partition(" ")
            if sep and tail.lstrip().startswith("{"):
                topic = head.strip() or default_topic
                payload = tail.lstrip().encode("utf-8")
            else:
                topic = default_topic
                payload = single
    else:
        raise ValueError("Empty ZMQ message.")
    return topic, payload


def _bind_to_local_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    if not endpoint.startswith("tcp://"):
        return endpoint
    prefix = "tcp://"
    host_port = endpoint[len(prefix) :]
    if ":" not in host_port:
        return endpoint
    host, port = host_port.rsplit(":", 1)
    if host in {"*", "0.0.0.0", ""}:
        host = "127.0.0.1"
    return f"{prefix}{host}:{port}"


def _unique_strings(items: list[str | None]) -> list[str]:
    ordered: list[str] = []
    for item in items:
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def _resolve_input_endpoints(zmq_cfg: dict[str, Any], override: str | None) -> list[str]:
    if override:
        return [override]
    return _unique_strings(
        [
            str(zmq_cfg.get("input_addr", "")).strip() or None,
            _bind_to_local_endpoint(str(zmq_cfg.get("input_bind", "")).strip() or None),
        ]
    )


def _resolve_output_endpoint(zmq_cfg: dict[str, Any], override: str | None) -> str:
    if override:
        return override
    candidates = _unique_strings(
        [
            str(zmq_cfg.get("output_endpoint", "")).strip() or None,
            _bind_to_local_endpoint(str(zmq_cfg.get("output_bind", "")).strip() or None),
        ]
    )
    if not candidates:
        raise ValueError("moduleC config 中未找到可用 output endpoint")
    return candidates[0]


def _resolve_browser_endpoint(frontend_cfg: dict[str, Any], override: str | None) -> str | None:
    if override:
        return override
    candidates = _unique_strings(
        [
            str(frontend_cfg.get("endpoint", "")).strip() or None,
            _bind_to_local_endpoint(str(frontend_cfg.get("bind", "")).strip() or None),
        ]
    )
    return candidates[0] if candidates else None


MODULE_E_SIM_PARAM_KEYS = {"scene", "speed", "limit_speed", "num_pedestrians", "num_vehicles"}
MODULE_E_SIM_LIMIT_SPEEDS = {20, 40, 60, 80, 100, 120}
MODULE_E_SIM_TEMPLATES: dict[str, dict[str, Any]] = {
    "p0_blind_spot": {
        "label": "P0盲区高危",
        "scene_choices": {"city street", "highway", "tunnel", "residential", "unknown"},
        "defaults": {
            "scene": "city street",
            "speed": 38.0,
            "limit_speed": 60,
            "num_pedestrians": 1,
            "num_vehicles": 4,
        },
    },
    "p1_overspeed": {
        "label": "P1超速提醒",
        "scene_choices": {"city street", "highway", "unknown"},
        "defaults": {
            "scene": "highway",
            "speed": 98.0,
            "limit_speed": 80,
            "num_pedestrians": 0,
            "num_vehicles": 10,
        },
    },
    "p2_warning": {
        "label": "P2普通预警",
        "scene_choices": {"city street", "highway", "residential", "unknown"},
        "defaults": {
            "scene": "city street",
            "speed": 46.0,
            "limit_speed": 60,
            "num_pedestrians": 2,
            "num_vehicles": 7,
        },
    },
    "p3_silent": {
        "label": "P3静默建议",
        "scene_choices": {"city street", "highway", "unknown"},
        "defaults": {
            "scene": "highway",
            "speed": 78.0,
            "limit_speed": 80,
            "num_pedestrians": 0,
            "num_vehicles": 6,
        },
    },
}


def _parse_module_e_positive_int(value: Any, *, field_name: str, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是整数") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} 不能为负数")
    if parsed > max_value:
        raise ValueError(f"{field_name} 超出允许范围(0~{max_value})")
    return parsed


def _parse_module_e_speed(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("speed 必须是数字") from exc
    if parsed < 0 or parsed > 220:
        raise ValueError("speed 超出允许范围(0~220)")
    return parsed


def _normalize_module_e_simulate_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    template_id = payload.get("template_id")
    if not isinstance(template_id, str) or not template_id.strip():
        raise ValueError("template_id 不能为空")
    template_id = template_id.strip()
    template = MODULE_E_SIM_TEMPLATES.get(template_id)
    if template is None:
        raise ValueError(f"template_id 不支持: {template_id}")

    raw_params = payload.get("params", {})
    if raw_params is None:
        raw_params = {}
    if not isinstance(raw_params, dict):
        raise ValueError("params 必须是对象")

    unknown_keys = sorted([key for key in raw_params.keys() if key not in MODULE_E_SIM_PARAM_KEYS])
    if unknown_keys:
        raise ValueError(f"存在不支持的参数: {', '.join(unknown_keys)}")

    defaults = dict(template["defaults"])
    scene_val = raw_params.get("scene", defaults["scene"])
    if not isinstance(scene_val, str) or not scene_val.strip():
        raise ValueError("scene 必须是非空字符串")
    scene = scene_val.strip()
    allowed_scenes = set(template["scene_choices"])
    if scene not in allowed_scenes:
        raise ValueError(f"scene 不支持: {scene}，可选: {', '.join(sorted(allowed_scenes))}")

    speed = _parse_module_e_speed(raw_params.get("speed", defaults["speed"]))
    limit_speed = _parse_module_e_positive_int(
        raw_params.get("limit_speed", defaults["limit_speed"]),
        field_name="limit_speed",
        max_value=160,
    )
    if limit_speed not in MODULE_E_SIM_LIMIT_SPEEDS:
        raise ValueError("limit_speed 仅支持 20/40/60/80/100/120")

    num_pedestrians = _parse_module_e_positive_int(
        raw_params.get("num_pedestrians", defaults["num_pedestrians"]),
        field_name="num_pedestrians",
        max_value=20,
    )
    num_vehicles = _parse_module_e_positive_int(
        raw_params.get("num_vehicles", defaults["num_vehicles"]),
        field_name="num_vehicles",
        max_value=60,
    )

    normalized = {
        "scene": scene,
        "speed": speed,
        "limit_speed": limit_speed,
        "num_pedestrians": num_pedestrians,
        "num_vehicles": num_vehicles,
    }
    return template_id, normalized


def _build_module_e_sim_messages(
    *,
    frame_id: int,
    template_id: str,
    normalized_params: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    scene = str(normalized_params["scene"])
    speed = float(normalized_params["speed"])
    limit_speed = int(normalized_params["limit_speed"])
    num_pedestrians = int(normalized_params["num_pedestrians"])
    num_vehicles = int(normalized_params["num_vehicles"])

    b_payload: dict[str, Any] = {
        "frame_id": frame_id,
        "scene": scene,
        "speed": speed,
        "confidence": 0.95,
        "source_mode": "module_e_sim",
    }
    d_payload: dict[str, Any] = {
        "frame_id": frame_id,
        "num_pedestrians": num_pedestrians,
        "num_vehicles": num_vehicles,
        "source_mode": "module_e_sim",
    }

    if template_id == "p0_blind_spot":
        d_payload.update(
            {
                "num_traffic_signs": 0,
                "traffic_signs": [],
                "tracked_pedestrians": {"risk_level": "HIGH", "in_blind_spot": True},
            }
        )
    elif template_id == "p1_overspeed":
        d_payload.update(
            {
                "num_traffic_signs": 1,
                "traffic_signs": [{"class_name": f"Speed Limit {limit_speed} km/h", "confidence": 0.93}],
                "tracked_pedestrians": False,
            }
        )
    elif template_id == "p2_warning":
        d_payload.update(
            {
                "num_traffic_signs": 1,
                "traffic_signs": [{"class_name": "前方施工", "confidence": 0.91}],
                "tracked_pedestrians": False,
            }
        )
    else:  # p3_silent
        d_payload.update(
            {
                "num_traffic_signs": 1,
                "traffic_signs": [{"class_name": f"限速{limit_speed}", "confidence": 0.92}],
                "tracked_pedestrians": False,
            }
        )
    return b_payload, d_payload


def _extract_image_payload(sensor_payload: Any) -> dict[str, Any] | None:
    if not isinstance(sensor_payload, dict):
        return None
    payload = sensor_payload.get("payload", sensor_payload)
    if not isinstance(payload, dict):
        return None
    image_payload = payload.get("Image")
    if isinstance(image_payload, dict):
        return image_payload
    image_payload = payload.get("image")
    if isinstance(image_payload, dict):
        return image_payload
    return None


def _parse_positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _extract_camera_frame(
    sensor_payload: Any,
    *,
    fallback_sensor_id: str,
) -> dict[str, object] | None:
    image_payload = _extract_image_payload(sensor_payload)
    if image_payload is None:
        return None
    image_data = image_payload.get("data")
    if not isinstance(image_data, str) or not image_data.strip():
        return None
    sensor_id = fallback_sensor_id
    if isinstance(sensor_payload, dict) and isinstance(sensor_payload.get("sensor_id"), str):
        sensor_id = str(sensor_payload["sensor_id"])
    return {
        "src": f"data:image/jpeg;base64,{image_data.strip()}",
        "width": _parse_positive_int(image_payload.get("width"), 0),
        "height": _parse_positive_int(image_payload.get("height"), 0),
        "sensor_id": sensor_id,
    }


def _extract_input_frame(
    payload: dict[str, Any],
    *,
    left_sensor_id: str,
    right_sensor_id: str,
) -> dict[str, object] | None:
    frames = payload.get("frames")
    if not isinstance(frames, dict):
        return None
    left = _extract_camera_frame(frames.get(left_sensor_id), fallback_sensor_id=left_sensor_id)
    right = _extract_camera_frame(frames.get(right_sensor_id), fallback_sensor_id=right_sensor_id)
    if left is None or right is None:
        return None
    try:
        frame_id = int(payload.get("frame_id"))
    except (TypeError, ValueError):
        return None
    try:
        t_sync = float(payload.get("t_sync", 0.0))
    except (TypeError, ValueError):
        t_sync = 0.0
    return {
        "frame_id": frame_id,
        "t_sync": t_sync,
        "cameras": {
            "left": left,
            "right": right,
        },
    }


def _fill_camera_size(camera_payload: dict[str, Any], image_size: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(camera_payload)
    if _parse_positive_int(normalized.get("width"), 0) <= 0:
        normalized["width"] = _parse_positive_int(image_size.get("width"), 0)
    if _parse_positive_int(normalized.get("height"), 0) <= 0:
        normalized["height"] = _parse_positive_int(image_size.get("height"), 0)
    return normalized


def _websocket_accept_value(key: str) -> str:
    digest = hashlib.sha1(f"{key}{WEBSOCKET_GUID}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("utf-8")


def _encode_websocket_text_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    header = bytearray()
    header.append(0x81)
    payload_len = len(payload)
    if payload_len < 126:
        header.append(payload_len)
    elif payload_len <= 0xFFFF:
        header.append(126)
        header.extend(struct.pack("!H", payload_len))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", payload_len))
    return bytes(header) + payload


@dataclass(frozen=True)
class _PendingFrame:
    received_at: float
    payload: dict[str, Any]


class _FrameMerger:
    def __init__(self, merge_timeout_s: float) -> None:
        self.merge_timeout_s = max(0.05, float(merge_timeout_s))
        self._lock = threading.Lock()
        self._pending_input: dict[int, _PendingFrame] = {}
        self._pending_output: dict[int, _PendingFrame] = {}
        self.latest_input_frame_id: int | None = None
        self.latest_output_frame_id: int | None = None
        self.latest_merged_frame_id: int | None = None
        self.dropped_input = 0
        self.dropped_output = 0

    def add_input(self, frame_payload: dict[str, Any]) -> dict[str, Any] | None:
        frame_id = int(frame_payload["frame_id"])
        now = time.monotonic()
        with self._lock:
            self.latest_input_frame_id = frame_id
            self._pending_input[frame_id] = _PendingFrame(now, frame_payload)
            self._expire_locked(now)
            return self._merge_locked(frame_id)

    def add_output(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            frame_id = int(payload["frame_id"])
        except (KeyError, TypeError, ValueError):
            return None
        now = time.monotonic()
        with self._lock:
            self.latest_output_frame_id = frame_id
            self._pending_output[frame_id] = _PendingFrame(now, payload)
            self._expire_locked(now)
            return self._merge_locked(frame_id)

    def snapshot(self) -> dict[str, int | None]:
        now = time.monotonic()
        with self._lock:
            self._expire_locked(now)
            return {
                "last_input_frame_id": self.latest_input_frame_id,
                "last_output_frame_id": self.latest_output_frame_id,
                "last_merged_frame_id": self.latest_merged_frame_id,
                "pending_input": len(self._pending_input),
                "pending_output": len(self._pending_output),
                "dropped_input": self.dropped_input,
                "dropped_output": self.dropped_output,
            }

    def _merge_locked(self, frame_id: int) -> dict[str, Any] | None:
        if frame_id not in self._pending_input or frame_id not in self._pending_output:
            return None
        input_payload = self._pending_input.pop(frame_id).payload
        output_payload = self._pending_output.pop(frame_id).payload
        image_size = output_payload.get("image_size")
        image_size_dict = image_size if isinstance(image_size, dict) else {}
        cameras = input_payload.get("cameras", {})
        merged_payload = {
            "frame_id": frame_id,
            "t_sync": float(input_payload.get("t_sync", 0.0)),
            "cameras": {
                "left": _fill_camera_size(dict(cameras.get("left", {})), image_size_dict),
                "right": _fill_camera_size(dict(cameras.get("right", {})), image_size_dict),
            },
            "moduleCD": output_payload,
        }
        self.latest_merged_frame_id = frame_id
        return merged_payload

    def _expire_locked(self, now: float) -> None:
        expired_inputs = [
            frame_id
            for frame_id, pending in self._pending_input.items()
            if now - pending.received_at > self.merge_timeout_s
        ]
        for frame_id in expired_inputs:
            del self._pending_input[frame_id]
            self.dropped_input += 1

        expired_outputs = [
            frame_id
            for frame_id, pending in self._pending_output.items()
            if now - pending.received_at > self.merge_timeout_s
        ]
        for frame_id in expired_outputs:
            del self._pending_output[frame_id]
            self.dropped_output += 1


class _FrameBroadcaster:
    def __init__(self, push_fps: float) -> None:
        self.push_interval_s = 0.0 if push_fps <= 0 else 1.0 / float(push_fps)
        self._condition = threading.Condition()
        self._latest_payload: dict[str, Any] | None = None
        self._latest_version = 0
        self._published_version = 0
        self._clients: set[queue.Queue[dict[str, Any]]] = set()
        self._stop_requested = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="module-c-websocket-fanout", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def submit(self, payload: dict[str, Any]) -> None:
        with self._condition:
            self._latest_payload = payload
            self._latest_version += 1
            self._condition.notify_all()

    def register_client(self) -> queue.Queue[dict[str, Any]]:
        client_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._condition:
            self._clients.add(client_queue)
            if self._latest_payload is not None:
                client_queue.put_nowait(self._latest_payload)
        return client_queue

    def unregister_client(self, client_queue: queue.Queue[dict[str, Any]]) -> None:
        with self._condition:
            self._clients.discard(client_queue)

    def client_count(self) -> int:
        with self._condition:
            return len(self._clients)

    def _run(self) -> None:
        next_allowed = 0.0
        while True:
            with self._condition:
                while not self._stop_requested and self._latest_version == self._published_version:
                    self._condition.wait(timeout=1.0)
                if self._stop_requested:
                    return

                wait_s = max(0.0, next_allowed - time.monotonic())
                if wait_s > 0:
                    self._condition.wait(timeout=wait_s)
                    continue

                payload = self._latest_payload
                self._published_version = self._latest_version
                next_allowed = time.monotonic() + self.push_interval_s
                clients = list(self._clients)

            if payload is None:
                continue
            for client_queue in clients:
                if client_queue.full():
                    try:
                        client_queue.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    client_queue.put_nowait(payload)
                except queue.Full:
                    continue


class _ModuleCLiveBridge:
    def __init__(
        self,
        *,
        browser_endpoint: str | None,
        browser_topic: str | None,
        input_endpoints: list[str],
        output_endpoint: str,
        input_topic: str,
        output_topic: str,
        merge_timeout_s: float,
        push_fps: float,
        left_sensor_id: str,
        right_sensor_id: str,
    ) -> None:
        self.browser_endpoint = browser_endpoint
        self.browser_topic = browser_topic
        self.input_endpoints = input_endpoints
        self.output_endpoint = output_endpoint
        self.input_topic = input_topic
        self.output_topic = output_topic
        self.left_sensor_id = left_sensor_id
        self.right_sensor_id = right_sensor_id
        self._stop_event = threading.Event()
        self._merger = _FrameMerger(merge_timeout_s=merge_timeout_s)
        self._broadcaster = _FrameBroadcaster(push_fps=push_fps)
        self._threads: list[threading.Thread] = []
        self._latest_browser_frame_id: int | None = None

    def start(self) -> None:
        _require_zmq()
        self._broadcaster.start()
        if self.browser_endpoint:
            self._threads = [
                threading.Thread(
                    target=self._consume_browser_stream,
                    name="module-c-browser-bridge",
                    daemon=True,
                )
            ]
        else:
            self._threads = [
                threading.Thread(target=self._consume_input_stream, name="module-c-input-bridge", daemon=True),
                threading.Thread(target=self._consume_output_stream, name="module-c-output-bridge", daemon=True),
            ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=3.0)
        self._broadcaster.stop()

    def register_client(self) -> queue.Queue[dict[str, Any]]:
        return self._broadcaster.register_client()

    def unregister_client(self, client_queue: queue.Queue[dict[str, Any]]) -> None:
        self._broadcaster.unregister_client(client_queue)

    def health_snapshot(self) -> dict[str, Any]:
        if self.browser_endpoint:
            snapshot = {
                "last_input_frame_id": self._latest_browser_frame_id,
                "last_output_frame_id": self._latest_browser_frame_id,
                "last_merged_frame_id": self._latest_browser_frame_id,
                "pending_input": 0,
                "pending_output": 0,
                "dropped_input": 0,
                "dropped_output": 0,
            }
        else:
            snapshot = self._merger.snapshot()
        snapshot.update(
            {
                "client_count": self._broadcaster.client_count(),
                "mode": "browser_stream" if self.browser_endpoint else "dual_sub_merge",
                "browser_endpoint": self.browser_endpoint,
                "browser_topic": self.browser_topic,
                "input_endpoints": list(self.input_endpoints),
                "output_endpoint": self.output_endpoint,
                "input_topic": self.input_topic,
                "output_topic": self.output_topic,
            }
        )
        return snapshot

    def _build_subscriber(self, endpoints: list[str], topic: str) -> tuple[Any, Any]:
        import zmq

        ctx = zmq.Context()
        socket_obj = ctx.socket(zmq.SUB)
        socket_obj.setsockopt(zmq.RCVTIMEO, 250)
        socket_obj.setsockopt_string(zmq.SUBSCRIBE, topic)
        for endpoint in endpoints:
            socket_obj.connect(endpoint)
        return ctx, socket_obj

    def _consume_input_stream(self) -> None:
        import zmq

        ctx, socket_obj = self._build_subscriber(self.input_endpoints, self.input_topic)
        try:
            while not self._stop_event.is_set():
                try:
                    frames = socket_obj.recv_multipart()
                except zmq.Again:
                    continue
                try:
                    topic, payload_bytes = _recv_topic_and_payload(frames, self.input_topic)
                    if topic != self.input_topic:
                        continue
                    payload = json.loads(payload_bytes.decode("utf-8"))
                    input_frame = _extract_input_frame(
                        payload,
                        left_sensor_id=self.left_sensor_id,
                        right_sensor_id=self.right_sensor_id,
                    )
                except Exception as exc:
                    print(f"[frontend] module-c input_parse_failed: {type(exc).__name__}: {exc}")
                    continue
                if input_frame is None:
                    continue
                merged = self._merger.add_input(input_frame)
                if merged is not None:
                    self._broadcaster.submit(merged)
        finally:
            socket_obj.close(linger=0)
            ctx.term()

    def _consume_browser_stream(self) -> None:
        import zmq

        topic = str(self.browser_topic or self.output_topic)
        ctx, socket_obj = self._build_subscriber([self.browser_endpoint], topic)
        try:
            while not self._stop_event.is_set():
                try:
                    frames = socket_obj.recv_multipart()
                except zmq.Again:
                    continue
                try:
                    current_topic, payload_bytes = _recv_topic_and_payload(frames, topic)
                    if current_topic != topic:
                        continue
                    payload = json.loads(payload_bytes.decode("utf-8"))
                    frame_id = int(payload["frame_id"])
                except Exception as exc:
                    print(f"[frontend] module-c browser_parse_failed: {type(exc).__name__}: {exc}")
                    continue
                self._latest_browser_frame_id = frame_id
                self._broadcaster.submit(payload)
        finally:
            socket_obj.close(linger=0)
            ctx.term()

    def _consume_output_stream(self) -> None:
        import zmq

        ctx, socket_obj = self._build_subscriber([self.output_endpoint], self.output_topic)
        try:
            while not self._stop_event.is_set():
                try:
                    frames = socket_obj.recv_multipart()
                except zmq.Again:
                    continue
                try:
                    topic, payload_bytes = _recv_topic_and_payload(frames, self.output_topic)
                    if topic != self.output_topic:
                        continue
                    payload = json.loads(payload_bytes.decode("utf-8"))
                except Exception as exc:
                    print(f"[frontend] module-c output_parse_failed: {type(exc).__name__}: {exc}")
                    continue
                merged = self._merger.add_output(payload)
                if merged is not None:
                    self._broadcaster.submit(merged)
        finally:
            socket_obj.close(linger=0)
            ctx.term()


def _build_module_c_bridge_from_args(args: argparse.Namespace) -> _ModuleCLiveBridge:
    config = _load_module_c_config(args.module_c_config)
    demo_cfg = config.get("demo", {})
    zmq_cfg = demo_cfg.get("zmq", {})
    frontend_cfg = demo_cfg.get("frontend", {})
    sensors_cfg = demo_cfg.get("sensors", {})

    input_topic = str(args.module_c_topic or zmq_cfg.get("input_topic") or "Frame")
    output_topic = str(args.module_c_topic or zmq_cfg.get("output_topic") or input_topic)

    return _ModuleCLiveBridge(
        browser_endpoint=_resolve_browser_endpoint(frontend_cfg, args.module_c_browser_endpoint),
        browser_topic=str(frontend_cfg.get("topic", output_topic) or output_topic),
        input_endpoints=_resolve_input_endpoints(zmq_cfg, args.module_c_input_endpoint),
        output_endpoint=_resolve_output_endpoint(zmq_cfg, args.module_c_output_endpoint),
        input_topic=input_topic,
        output_topic=output_topic,
        merge_timeout_s=max(0.05, float(args.module_c_merge_timeout_ms) / 1000.0),
        push_fps=max(0.1, float(args.module_c_push_fps)),
        left_sensor_id=str(sensors_cfg.get("left_camera_sensor_id", "left_camera")),
        right_sensor_id=str(sensors_cfg.get("right_camera_sensor_id", "right_camera")),
    )


class _ModuleESimGateway:
    def __init__(
        self,
        *,
        sim_b_bind: str,
        sim_d_bind: str,
        sim_output_endpoint: str,
        sim_topic: str,
        start_frame_id: int,
        control_host: str,
        control_port: int,
    ) -> None:
        self.sim_b_bind = sim_b_bind
        self.sim_d_bind = sim_d_bind
        self.sim_output_endpoint = sim_output_endpoint
        self.sim_topic = sim_topic
        self.control_host = control_host
        self.control_port = int(control_port)
        self._next_frame_id = max(1, int(start_frame_id))

        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._broadcaster = _FrameBroadcaster(push_fps=10.0)

        self._ctx: Any = None
        self._socket_b: Any = None
        self._socket_d: Any = None
        self._socket_e: Any = None

        self.published_count = 0
        self.received_count = 0
        self.invalid_output_count = 0
        self.last_frame_id: int | None = None
        self.last_input: dict[str, Any] | None = None
        self.last_output: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_reset_at: float | None = None

    def start(self) -> None:
        _require_zmq()
        import zmq

        self._ctx = zmq.Context()
        self._socket_b = self._ctx.socket(zmq.PUB)
        self._socket_d = self._ctx.socket(zmq.PUB)
        self._socket_e = self._ctx.socket(zmq.SUB)

        self._socket_b.bind(self.sim_b_bind)
        self._socket_d.bind(self.sim_d_bind)
        self._socket_e.setsockopt(zmq.RCVTIMEO, 250)
        self._socket_e.setsockopt_string(zmq.SUBSCRIBE, self.sim_topic)
        self._socket_e.connect(self.sim_output_endpoint)

        self._broadcaster.start()
        self._thread = threading.Thread(
            target=self._consume_output_stream,
            name="module-e-sim-output-consumer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._broadcaster.stop()
        for socket_obj in (self._socket_b, self._socket_d, self._socket_e):
            if socket_obj is None:
                continue
            try:
                socket_obj.close(linger=0)
            except Exception:
                pass
        self._socket_b = None
        self._socket_d = None
        self._socket_e = None
        if self._ctx is not None:
            try:
                self._ctx.term()
            except Exception:
                pass
            self._ctx = None

    def register_client(self) -> queue.Queue[dict[str, Any]]:
        return self._broadcaster.register_client()

    def unregister_client(self, client_queue: queue.Queue[dict[str, Any]]) -> None:
        self._broadcaster.unregister_client(client_queue)

    def simulate(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        if self._socket_b is None or self._socket_d is None:
            raise RuntimeError("moduleE 仿真发布器未启动")
        demo_state, demo_error = self._call_demo_api(method="GET", path="/state")
        if demo_state is None:
            raise RuntimeError(demo_error or "moduleE-demo 不可用，无法触发仿真")

        template_id, normalized_params = _normalize_module_e_simulate_payload(raw_payload)
        with self._state_lock:
            frame_id = self._next_frame_id
            self._next_frame_id += 1

        b_payload, d_payload = _build_module_e_sim_messages(
            frame_id=frame_id,
            template_id=template_id,
            normalized_params=normalized_params,
        )
        topic_bytes = self.sim_topic.encode("utf-8")
        b_raw = json.dumps(b_payload, ensure_ascii=False).encode("utf-8")
        d_raw = json.dumps(d_payload, ensure_ascii=False).encode("utf-8")
        with self._send_lock:
            self._socket_b.send_multipart([topic_bytes, b_raw])
            self._socket_d.send_multipart([topic_bytes, d_raw])

        with self._state_lock:
            self.published_count += 1
            self.last_frame_id = frame_id
            self.last_input = {
                "template_id": template_id,
                "params": dict(normalized_params),
                "frame_id": frame_id,
                "b_payload": b_payload,
                "d_payload": d_payload,
            }

        return {
            "ok": True,
            "frame_id": frame_id,
            "template_id": template_id,
            "topic": self.sim_topic,
            "params": dict(normalized_params),
        }

    def _call_demo_api(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        target_url = f"http://{self.control_host}:{self.control_port}{path}"
        headers = {"Accept": "application/json"}
        req_data: bytes | None = None
        if payload is not None:
            req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(target_url, method=method, headers=headers, data=req_data)
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                raw = resp.read()
                try:
                    decoded = json.loads(raw.decode("utf-8"))
                except Exception:
                    return None, "moduleE-demo 返回了非JSON响应"
                if not isinstance(decoded, dict):
                    return None, "moduleE-demo 返回JSON格式非法"
                if decoded.get("ok") is False:
                    return None, str(decoded.get("error") or "moduleE-demo 返回失败状态")
                return decoded, None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return None, f"moduleE-demo HTTP {exc.code}: {raw or 'request failed'}"
        except Exception as exc:
            return None, f"moduleE-demo 不可用: {exc}"

    def reset_remote(self) -> dict[str, Any]:
        payload, error = self._call_demo_api(method="POST", path="/reset", payload={})
        if payload is None:
            raise RuntimeError(error or "moduleE-demo reset 失败")
        reset_at = payload.get("reset_at")
        reset_ts = None
        if isinstance(reset_at, (int, float)):
            reset_ts = float(reset_at)
        if reset_ts is None:
            reset_ts = time.time()
        with self._state_lock:
            self.last_reset_at = reset_ts
        return {"ok": True, "reset_at": reset_ts}

    def demo_ready(self) -> tuple[bool, str]:
        payload, error = self._call_demo_api(method="GET", path="/state")
        if payload is not None:
            return True, ""
        return False, str(error or "moduleE-demo 不可用")

    def health_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            snapshot: dict[str, Any] = {
                "sim_topic": self.sim_topic,
                "sim_b_bind": self.sim_b_bind,
                "sim_d_bind": self.sim_d_bind,
                "sim_output_endpoint": self.sim_output_endpoint,
                "published_count": self.published_count,
                "received_count": self.received_count,
                "invalid_output_count": self.invalid_output_count,
                "last_frame_id": self.last_frame_id,
                "last_input": self.last_input,
                "last_output": self.last_output,
                "last_error": self.last_error,
                "last_reset_at": self.last_reset_at,
                "next_frame_id": self._next_frame_id,
                "client_count": self._broadcaster.client_count(),
                "templates": [
                    {
                        "template_id": template_id,
                        "label": str(template.get("label", template_id)),
                        "scene_choices": sorted(list(template.get("scene_choices", []))),
                        "defaults": dict(template.get("defaults", {})),
                    }
                    for template_id, template in MODULE_E_SIM_TEMPLATES.items()
                ],
            }
        demo_state, demo_error = self._call_demo_api(method="GET", path="/state")
        snapshot["demo_connected"] = demo_state is not None
        snapshot["demo_state"] = demo_state
        if demo_error:
            snapshot["demo_error"] = demo_error
        return snapshot

    def _consume_output_stream(self) -> None:
        import zmq

        if self._socket_e is None:
            return
        while not self._stop_event.is_set():
            try:
                frames = self._socket_e.recv_multipart()
            except zmq.Again:
                continue
            except Exception as exc:
                with self._state_lock:
                    self.last_error = f"moduleE仿真输出接收失败: {type(exc).__name__}: {exc}"
                continue

            try:
                topic, payload_bytes = _recv_topic_and_payload(frames, self.sim_topic)
                if topic != self.sim_topic:
                    continue
                payload = json.loads(payload_bytes.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("payload 顶层必须是对象")
                frame_id = int(payload.get("frame_id"))
                event_payload = {
                    "event": "e_frame",
                    "frame_id": frame_id,
                    "moduleE": payload,
                    "ts": time.time(),
                }
            except Exception as exc:
                with self._state_lock:
                    self.invalid_output_count += 1
                    self.last_error = f"moduleE仿真输出解析失败: {type(exc).__name__}: {exc}"
                continue

            with self._state_lock:
                self.received_count += 1
                self.last_output = payload
                self.last_frame_id = frame_id
            self._broadcaster.submit(event_payload)


def _build_module_e_gateway_from_args(args: argparse.Namespace) -> _ModuleESimGateway:
    return _ModuleESimGateway(
        sim_b_bind=str(args.module_e_sim_b_bind),
        sim_d_bind=str(args.module_e_sim_d_bind),
        sim_output_endpoint=str(args.module_e_sim_output_endpoint),
        sim_topic=str(args.module_e_sim_topic or "SimFrame"),
        start_frame_id=max(1, int(args.module_e_sim_start_frame_id)),
        control_host=str(args.module_e_control_host),
        control_port=int(args.module_e_control_port),
    )


def _build_handler(
    frontend_dir: Path,
    scenes_root: Path,
    module_b_control_host: str,
    module_b_control_port: int,
    module_d_control_host: str,
    module_d_control_port: int,
    module_c_bridge: _ModuleCLiveBridge,
    module_e_gateway: _ModuleESimGateway,
):
    class FrontendHandler(SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(frontend_dir), **kwargs)

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

        def _resolve_scene(self, scene_name: str) -> Path:
            if not re.fullmatch(r"[A-Za-z0-9._-]+", scene_name):
                raise ValueError("scene 名称非法")

            root = scenes_root.resolve()
            scene_dir = (root / scene_name).resolve()
            if scene_dir.parent != root:
                raise ValueError("scene 越界")
            if not scene_dir.is_dir():
                raise FileNotFoundError("scene 不存在")
            return scene_dir

        def _list_scene_names(self) -> list[str]:
            if not scenes_root.is_dir():
                return []
            names = [item.name for item in scenes_root.iterdir() if item.is_dir()]
            names.sort(key=_natural_sort_key)
            return names

        def _list_scene_frames(self, scene_name: str) -> list[str]:
            scene_dir = self._resolve_scene(scene_name)
            images = [
                item
                for item in scene_dir.iterdir()
                if item.is_file() and item.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
            ]
            images.sort(key=lambda p: _natural_sort_key(p.name))

            frames: list[str] = []
            for image in images:
                try:
                    rel = image.resolve().relative_to(frontend_dir.resolve()).as_posix()
                except Exception:
                    rel = f"assets/scenes/{scene_name}/{image.name}"
                frames.append(rel)
            return frames

        def _proxy_module_control(
            self,
            *,
            module_name: str,
            host: str,
            port: int,
            method: str,
            target_path: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            target_url = f"http://{host}:{port}{target_path}"
            req_data: bytes | None = None
            headers = {"Accept": "application/json"}
            if payload is not None:
                req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"

            req = urllib.request.Request(target_url, method=method, data=req_data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=4) as resp:
                    raw = resp.read()
                    try:
                        decoded = json.loads(raw.decode("utf-8"))
                    except Exception:
                        decoded = {"ok": False, "error": f"{module_name} 返回了非JSON响应"}
                    status = int(resp.getcode() or 200)
                    self._send_json(status, decoded if isinstance(decoded, dict) else {"ok": True, "data": decoded})
                    return
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    decoded = json.loads(raw.decode("utf-8"))
                except Exception:
                    decoded = {"ok": False, "error": raw.decode("utf-8", errors="replace") or f"{module_name} 请求失败"}
                self._send_json(exc.code, decoded if isinstance(decoded, dict) else {"ok": False, "error": str(decoded)})
                return
            except Exception as exc:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"ok": False, "error": f"{module_name} 控制服务不可用: {exc}"},
                )
                return

        def _handle_module_c_health(self) -> None:
            payload = json.dumps(module_c_bridge.health_snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _handle_module_c_websocket(self) -> None:
            upgrade = str(self.headers.get("Upgrade", "")).strip().lower()
            version = str(self.headers.get("Sec-WebSocket-Version", "")).strip()
            key = str(self.headers.get("Sec-WebSocket-Key", "")).strip()
            if upgrade != "websocket" or not key or version != "13":
                self.send_error(HTTPStatus.BAD_REQUEST, "Expected a WebSocket upgrade request.")
                return

            client_queue = module_c_bridge.register_client()
            self.close_connection = True
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", _websocket_accept_value(key))
            self.end_headers()
            try:
                while True:
                    try:
                        payload = client_queue.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    event_text = json.dumps(payload, ensure_ascii=False)
                    self.connection.sendall(_encode_websocket_text_frame(event_text))
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                module_c_bridge.unregister_client(client_queue)

        def _handle_module_e_websocket(self) -> None:
            ready, reason = module_e_gateway.demo_ready()
            if not ready:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": reason})
                return
            upgrade = str(self.headers.get("Upgrade", "")).strip().lower()
            version = str(self.headers.get("Sec-WebSocket-Version", "")).strip()
            key = str(self.headers.get("Sec-WebSocket-Key", "")).strip()
            if upgrade != "websocket" or not key or version != "13":
                self.send_error(HTTPStatus.BAD_REQUEST, "Expected a WebSocket upgrade request.")
                return

            client_queue = module_e_gateway.register_client()
            self.close_connection = True
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", _websocket_accept_value(key))
            self.end_headers()
            try:
                while True:
                    try:
                        payload = client_queue.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    event_text = json.dumps(payload, ensure_ascii=False)
                    self.connection.sendall(_encode_websocket_text_frame(event_text))
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                module_e_gateway.unregister_client(client_queue)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/api/module-c/health":
                self._handle_module_c_health()
                return
            if path == "/api/module-c/ws":
                self._handle_module_c_websocket()
                return
            if path == "/api/module-e/ws":
                self._handle_module_e_websocket()
                return
            if path == "/api/module-e/state":
                self._send_json(HTTPStatus.OK, {"ok": True, "state": module_e_gateway.health_snapshot()})
                return

            if path == "/api/scenes":
                scenes = []
                for scene_name in self._list_scene_names():
                    frame_count = len(self._list_scene_frames(scene_name))
                    scenes.append({"name": scene_name, "frame_count": frame_count})
                self._send_json(HTTPStatus.OK, {"ok": True, "scenes": scenes})
                return

            m = re.fullmatch(r"/api/scenes/([^/]+)/frames", path)
            if m:
                scene_name = urllib.parse.unquote(m.group(1))
                try:
                    frames = self._list_scene_frames(scene_name)
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "scene": scene_name,
                            "frame_count": len(frames),
                            "frames": frames,
                        },
                    )
                except FileNotFoundError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "scene 不存在"})
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            if path == "/api/module-b/state":
                self._proxy_module_control(
                    module_name="moduleB",
                    host=module_b_control_host,
                    port=module_b_control_port,
                    method="GET",
                    target_path="/state",
                )
                return

            if path == "/api/module-d/state":
                self._proxy_module_control(
                    module_name="moduleD",
                    host=module_d_control_host,
                    port=module_d_control_port,
                    method="GET",
                    target_path="/state",
                )
                return

            super().do_GET()

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path not in {
                "/api/module-b/mode",
                "/api/module-b/scene",
                "/api/module-b/player",
                "/api/module-d/mode",
                "/api/module-d/scene",
                "/api/module-d/player",
                "/api/module-e/simulate",
                "/api/module-e/reset",
            }:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
                return

            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            if path == "/api/module-b/mode":
                self._proxy_module_control(
                    module_name="moduleB",
                    host=module_b_control_host,
                    port=module_b_control_port,
                    method="POST",
                    target_path="/mode",
                    payload=payload,
                )
                return
            if path == "/api/module-b/scene":
                self._proxy_module_control(
                    module_name="moduleB",
                    host=module_b_control_host,
                    port=module_b_control_port,
                    method="POST",
                    target_path="/scene",
                    payload=payload,
                )
                return
            if path == "/api/module-b/player":
                self._proxy_module_control(
                    module_name="moduleB",
                    host=module_b_control_host,
                    port=module_b_control_port,
                    method="POST",
                    target_path="/player",
                    payload=payload,
                )
                return

            if path == "/api/module-d/mode":
                self._proxy_module_control(
                    module_name="moduleD",
                    host=module_d_control_host,
                    port=module_d_control_port,
                    method="POST",
                    target_path="/mode",
                    payload=payload,
                )
                return
            if path == "/api/module-d/scene":
                self._proxy_module_control(
                    module_name="moduleD",
                    host=module_d_control_host,
                    port=module_d_control_port,
                    method="POST",
                    target_path="/scene",
                    payload=payload,
                )
                return
            if path == "/api/module-d/player":
                self._proxy_module_control(
                    module_name="moduleD",
                    host=module_d_control_host,
                    port=module_d_control_port,
                    method="POST",
                    target_path="/player",
                    payload=payload,
                )
                return
            if path == "/api/module-e/simulate":
                try:
                    result = module_e_gateway.simulate(payload)
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                    return
                except RuntimeError as exc:
                    self._send_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
                    return
                except Exception as exc:
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                    return
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/api/module-e/reset":
                try:
                    result = module_e_gateway.reset_remote()
                except RuntimeError as exc:
                    self._send_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
                    return
                except Exception as exc:
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                    return
                self._send_json(HTTPStatus.OK, result)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})

    return FrontendHandler


def main() -> None:
    args = build_parser().parse_args()

    frontend_dir = Path(__file__).resolve().parent
    scenes_root = (frontend_dir / "assets" / "scenes").resolve()

    module_c_bridge = _build_module_c_bridge_from_args(args)
    module_c_bridge.start()
    module_e_gateway = _build_module_e_gateway_from_args(args)
    module_e_gateway.start()

    handler = _build_handler(
        frontend_dir=frontend_dir,
        scenes_root=scenes_root,
        module_b_control_host=args.module_b_control_host,
        module_b_control_port=args.module_b_control_port,
        module_d_control_host=args.module_d_control_host,
        module_d_control_port=args.module_d_control_port,
        module_c_bridge=module_c_bridge,
        module_e_gateway=module_e_gateway,
    )

    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"[frontend] 静态服务目录: {frontend_dir}")
    print(f"[frontend] 场景目录API: {scenes_root}")
    print(
        f"[frontend] moduleB 控制代理 -> http://{args.module_b_control_host}:{args.module_b_control_port}"
    )
    print(
        f"[frontend] moduleD 控制代理 -> http://{args.module_d_control_host}:{args.module_d_control_port}"
    )
    print(f"[frontend] moduleC 配置: {args.module_c_config}")
    if module_c_bridge.browser_endpoint:
        print(f"[frontend] moduleC browser流: {module_c_bridge.browser_endpoint}")
    print(f"[frontend] moduleC input: {', '.join(module_c_bridge.input_endpoints)}")
    print(f"[frontend] moduleC output: {module_c_bridge.output_endpoint}")
    print("[frontend] moduleC API: GET /api/module-c/health, GET /api/module-c/ws")
    print(f"[frontend] moduleE sim topic: {module_e_gateway.sim_topic}")
    print(f"[frontend] moduleE sim PUB(B): {module_e_gateway.sim_b_bind}")
    print(f"[frontend] moduleE sim PUB(D): {module_e_gateway.sim_d_bind}")
    print(f"[frontend] moduleE sim SUB(E): {module_e_gateway.sim_output_endpoint}")
    print(f"[frontend] moduleE demo control: http://{module_e_gateway.control_host}:{module_e_gateway.control_port}")
    print("[frontend] moduleE API: GET /api/module-e/state, GET /api/module-e/ws, POST /api/module-e/simulate, POST /api/module-e/reset")

    if args.host in ("0.0.0.0", "::"):
        print(f"[frontend] 绑定地址: http://{args.host}:{args.port}")
        print(f"[frontend] 本机访问: http://127.0.0.1:{args.port}")
        print(f"[frontend] 本机访问: http://localhost:{args.port}")
        lan_ip = resolve_lan_ip()
        if lan_ip and lan_ip not in ("127.0.0.1", "0.0.0.0"):
            print(f"[frontend] 局域网访问: http://{lan_ip}:{args.port}")
        print("[frontend] 提示: 浏览器不要直接访问 0.0.0.0")
    else:
        print(f"[frontend] 访问地址: http://{args.host}:{args.port}")
    print("[frontend] 按 Ctrl+C 停止")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        module_c_bridge.stop()
        module_e_gateway.stop()
        print("[frontend] 已停止")


if __name__ == "__main__":
    main()

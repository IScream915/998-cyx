import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import zmq

# 兼容两种启动方式：
# 1) python3 moduleE/mock_module_e.py
# 2) python3 -m moduleE.mock_module_e
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_DIR = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from TrafficReminder import FusionDecisionEngine


def source_label(endpoint: str) -> str:
    if endpoint.endswith(":5052"):
        return "B"
    if endpoint.endswith(":5053"):
        return "CD"
    return endpoint


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_non_negative_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _extract_speed(b_payload: Dict[str, Any], default_speed: float) -> float:
    # B 侧若未携带车速，则使用默认值
    if "speed" in b_payload:
        return _to_float(b_payload.get("speed"), default_speed)
    return default_speed


def _extract_detected_signs(cd_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    signs: List[Dict[str, Any]] = []

    # 兼容已标准化结构
    if isinstance(cd_payload.get("detected_signs"), list):
        for item in cd_payload["detected_signs"]:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or item.get("class_name")
            if not content:
                continue
            signs.append(
                {
                    "content": str(content),
                    "confidence": _to_float(item.get("confidence"), 0.0),
                }
            )

    # 兼容 CoreDetector 输出结构: traffic_signs
    elif isinstance(cd_payload.get("traffic_signs"), list):
        for item in cd_payload["traffic_signs"]:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or item.get("class_name")
            if not content:
                continue
            signs.append(
                {
                    "content": str(content),
                    "confidence": _to_float(item.get("confidence"), 0.0),
                }
            )

    # 兼容单值字段
    elif cd_payload.get("sign_text"):
        signs.append(
            {
                "content": str(cd_payload["sign_text"]),
                "confidence": _to_float(cd_payload.get("confidence"), 0.0),
            }
        )

    return signs


def _build_perception(frame_id: int, b_payload: Dict[str, Any], cd_payload: Dict[str, Any]) -> Dict[str, Any]:
    if "num_pedestrians" in cd_payload:
        num_pedestrians = _to_non_negative_int(cd_payload.get("num_pedestrians"), 0)
    else:
        pedestrians = cd_payload.get("pedestrians")
        num_pedestrians = len(pedestrians) if isinstance(pedestrians, list) else 0

    if "num_vehicles" in cd_payload:
        num_vehicles = _to_non_negative_int(cd_payload.get("num_vehicles"), 0)
    else:
        vehicles = cd_payload.get("vehicles")
        num_vehicles = len(vehicles) if isinstance(vehicles, list) else 0

    perception: Dict[str, Any] = {
        "frame_id": frame_id,
        "scene": b_payload.get("scene", "unknown") or "unknown",
        "detected_signs": _extract_detected_signs(cd_payload),
        "num_pedestrians": num_pedestrians,
        "num_vehicles": num_vehicles,
    }

    # tracked_pedestrians 兼容处理：
    # 1) dict: 直接透传
    # 2) True: 视为 HIGH + blind spot（临时规则）
    tracked_val = cd_payload.get("tracked_pedestrians")
    if isinstance(tracked_val, dict):
        perception["tracked_pedestrians"] = tracked_val
    elif tracked_val is True:
        perception["tracked_pedestrians"] = {
            "risk_level": "HIGH",
            "in_blind_spot": True,
        }

    return perception


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模块E：B+CD 对齐后调用 TrafficReminder 进行真实任务处理")
    parser.add_argument(
        "--endpoints",
        default="tcp://localhost:5052,tcp://localhost:5053",
        help="订阅地址列表，逗号分隔",
    )
    parser.add_argument("--topic", default="Frame", help="订阅 topic")
    parser.add_argument("--timeout_ms", type=int, default=10, help="轮询等待(ms)")
    parser.add_argument("--match_timeout_ms", type=int, default=1500, help="同一 frame_id 配对超时(ms)")
    parser.add_argument("--publish_bind", default="tcp://*:5054", help="处理结果发布地址")
    parser.add_argument("--publish_topic", default="Frame", help="处理结果发布 topic")

    module_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--kb_path",
        default=str((module_dir / "gb5768_rules.json").resolve()),
        help="GB5768 规则库路径",
    )
    parser.add_argument(
        "--st_model",
        default=str((module_dir / "model" / "paraphrase-multilingual-MiniLM-L12-v2").resolve()),
        help="sentence-transformers 模型路径或模型名",
    )
    parser.add_argument("--default_speed", type=float, default=60.0, help="默认车速(km/h)")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    endpoints = [item.strip() for item in args.endpoints.split(",") if item.strip()]
    if not endpoints:
        raise ValueError("endpoints 不能为空")

    model_ref = args.st_model
    if Path(args.st_model).exists():
        model_ref = str(Path(args.st_model).resolve())
    else:
        print(f"[moduleE] 警告: 本地模型不存在，按模型名加载: {args.st_model}")

    engine = FusionDecisionEngine(model_name=model_ref, kb_path=args.kb_path)

    ctx = zmq.Context()
    poller = zmq.Poller()
    sockets = []
    socket_to_endpoint: Dict[Any, str] = {}
    for endpoint in endpoints:
        socket = ctx.socket(zmq.SUB)
        socket.setsockopt_string(zmq.SUBSCRIBE, args.topic)
        socket.connect(endpoint)
        poller.register(socket, zmq.POLLIN)
        sockets.append(socket)
        socket_to_endpoint[socket] = endpoint
    publisher = ctx.socket(zmq.PUB)
    publisher.bind(args.publish_bind)

    running = True

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print(f"[moduleE] SUB 已连接: {', '.join(endpoints)}, topic={args.topic}")
    print(f"[moduleE] PUB 已启动: {args.publish_bind}, topic={args.publish_topic}")
    print("[moduleE] B+CD 按 frame_id 对齐后将调用 TrafficReminder 引擎处理")
    print("[moduleE] 按 Ctrl+C 停止")

    pending: Dict[int, Dict[str, Any]] = {}
    dropped_timeout = 0
    last_stat_log_ts = time.monotonic()

    try:
        while running:
            events = dict(poller.poll(args.timeout_ms))
            now = time.monotonic()

            for socket in sockets:
                if socket not in events:
                    continue

                frames = socket.recv_multipart()
                endpoint = socket_to_endpoint[socket]
                label = source_label(endpoint)
                if label not in ("B", "CD"):
                    continue

                if len(frames) >= 2:
                    payload_text = frames[-1].decode("utf-8", errors="replace").strip()
                else:
                    payload_text = frames[0].decode("utf-8", errors="replace").strip()

                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue

                if "frame_id" not in payload:
                    continue

                try:
                    frame_id = int(payload["frame_id"])
                except (TypeError, ValueError):
                    continue

                if frame_id not in pending:
                    pending[frame_id] = {"first_ts": now, "B": None, "CD": None}

                pending[frame_id][label] = payload
                entry = pending[frame_id]

                if entry["B"] is not None and entry["CD"] is not None:
                    b_payload: Dict[str, Any] = entry["B"]
                    cd_payload: Dict[str, Any] = entry["CD"]

                    speed = _extract_speed(b_payload, args.default_speed)
                    perception = _build_perception(frame_id, b_payload, cd_payload)

                    try:
                        engine.update_telematics({"speed": speed})
                        engine.update_perception(perception)

                        result = {
                            "frame_id": frame_id,
                            "status": "processed",
                            "scene": perception.get("scene", "unknown"),
                            "speed": speed,
                            "detected_signs": perception.get("detected_signs", []),
                        }
                    except Exception as exc:
                        result = {
                            "frame_id": frame_id,
                            "status": "process_error",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }

                    result_text = json.dumps(result, ensure_ascii=False)
                    publisher.send_multipart(
                        [args.publish_topic.encode("utf-8"), result_text.encode("utf-8")]
                    )
                    print(result_text)
                    del pending[frame_id]

            # 超时未配齐的 frame_id 直接丢弃，保证低延迟
            expire_before = now - (args.match_timeout_ms / 1000.0)
            expired_ids = [
                frame_id
                for frame_id, entry in pending.items()
                if entry["first_ts"] < expire_before
            ]
            for frame_id in expired_ids:
                del pending[frame_id]
                dropped_timeout += 1

            # 低频输出统计，便于定位“无输出”是否由超时丢弃导致
            if dropped_timeout > 0 and (now - last_stat_log_ts) >= 1.0:
                print(
                    f"[moduleE] 配对超时丢弃累计: {dropped_timeout}, "
                    f"当前待配对: {len(pending)}, match_timeout_ms={args.match_timeout_ms}"
                )
                last_stat_log_ts = now
    finally:
        for socket in sockets:
            poller.unregister(socket)
            socket.close(linger=0)
        publisher.close(linger=0)
        ctx.term()
        engine.shutdown()
        print("[moduleE] 已停止")


if __name__ == "__main__":
    main()

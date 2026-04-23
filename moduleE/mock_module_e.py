import argparse
import json
import signal
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def _extract_detected_signs(d_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    signs: List[Dict[str, Any]] = []

    # 兼容已标准化结构
    if isinstance(d_payload.get("detected_signs"), list):
        for item in d_payload["detected_signs"]:
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
    elif isinstance(d_payload.get("traffic_signs"), list):
        for item in d_payload["traffic_signs"]:
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
    elif d_payload.get("sign_text"):
        signs.append(
            {
                "content": str(d_payload["sign_text"]),
                "confidence": _to_float(d_payload.get("confidence"), 0.0),
            }
        )

    return signs


def _build_perception(frame_id: int, b_payload: Dict[str, Any], d_payload: Dict[str, Any]) -> Dict[str, Any]:
    if "num_pedestrians" in d_payload:
        num_pedestrians = _to_non_negative_int(d_payload.get("num_pedestrians"), 0)
    else:
        pedestrians = d_payload.get("pedestrians")
        num_pedestrians = len(pedestrians) if isinstance(pedestrians, list) else 0

    if "num_vehicles" in d_payload:
        num_vehicles = _to_non_negative_int(d_payload.get("num_vehicles"), 0)
    else:
        vehicles = d_payload.get("vehicles")
        num_vehicles = len(vehicles) if isinstance(vehicles, list) else 0

    perception: Dict[str, Any] = {
        "frame_id": frame_id,
        "scene": b_payload.get("scene", "unknown") or "unknown",
        "detected_signs": _extract_detected_signs(d_payload),
        "num_pedestrians": num_pedestrians,
        "num_vehicles": num_vehicles,
    }

    # tracked_pedestrians 兼容处理：
    # 1) dict: 直接透传
    # 2) True: 视为 HIGH + blind spot（临时规则）
    tracked_val = d_payload.get("tracked_pedestrians")
    if isinstance(tracked_val, dict):
        perception["tracked_pedestrians"] = tracked_val
    elif tracked_val is True:
        perception["tracked_pedestrians"] = {
            "risk_level": "HIGH",
            "in_blind_spot": True,
        }

    return perception


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模块E：B+D 对齐后调用 TrafficReminder 进行真实任务处理")
    parser.add_argument(
        "--endpoints",
        default="tcp://localhost:5052,tcp://localhost:5053",
        help="订阅地址列表，逗号分隔（第1个视为B输入，第2个视为D输入）",
    )
    parser.add_argument("--topic", default="Frame", help="订阅 topic")
    parser.add_argument("--timeout_ms", type=int, default=10, help="轮询等待(ms)")
    parser.add_argument("--match_timeout_ms", type=int, default=1500, help="同一 frame_id 配对超时(ms)")
    parser.add_argument(
        "--fallback_mode",
        type=str,
        default="auto",
        choices=["auto", "frame_match", "latest"],
        help="融合模式: auto=先配对后自动退化, frame_match=始终配对, latest=始终取最新",
    )
    parser.add_argument(
        "--fallback_drop_threshold",
        type=int,
        default=8,
        help="auto模式下触发退化的配对超时丢弃阈值(累计)",
    )
    parser.add_argument(
        "--fallback_mismatch_streak",
        type=int,
        default=20,
        help="auto模式下触发退化的连续frame_id不一致阈值",
    )
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
    parser.add_argument("--control_host", default="127.0.0.1", help="moduleE控制接口监听地址")
    parser.add_argument("--control_port", type=int, default=5064, help="moduleE控制接口监听端口")
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
    socket_to_label: Dict[Any, str] = {}
    for index, endpoint in enumerate(endpoints):
        socket = ctx.socket(zmq.SUB)
        socket.setsockopt_string(zmq.SUBSCRIBE, args.topic)
        socket.connect(endpoint)
        poller.register(socket, zmq.POLLIN)
        sockets.append(socket)
        role = "B" if index == 0 else "D" if index == 1 else f"EXTRA_{index}"
        socket_to_label[socket] = role
    publisher = ctx.socket(zmq.PUB)
    publisher.bind(args.publish_bind)

    running = True
    control_lock = threading.Lock()
    control_state: Dict[str, Any] = {
        "processed_count": 0,
        "process_error_count": 0,
        "last_emit_ts": None,
        "last_result": None,
        "last_reset_at": None,
    }
    control_server: ThreadingHTTPServer | None = None
    control_thread: threading.Thread | None = None

    def snapshot_state() -> Dict[str, Any]:
        with control_lock:
            state_copy = dict(control_state)
        state_copy.update(
            {
                "mode": effective_mode,
                "dropped_timeout": dropped_timeout,
                "mismatch_streak": mismatch_streak,
                "pending_count": len(pending),
                "latest_frame_ids": dict(latest_frame_ids),
                "endpoints": list(endpoints),
                "topic": args.topic,
                "publish_bind": args.publish_bind,
                "publish_topic": args.publish_topic,
            }
        )
        state_copy["engine"] = engine.get_runtime_state()
        return {"ok": True, "state": state_copy}

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    class ControlHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/state":
                self._send_json(HTTPStatus.OK, snapshot_state())
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/reset":
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
                return
            reset_result = engine.reset_runtime_state()
            reset_at = float(reset_result.get("reset_at", time.time()))
            with control_lock:
                control_state["last_reset_at"] = reset_at
                control_state["last_result"] = None
            self._send_json(HTTPStatus.OK, {"ok": True, "reset_at": reset_at})

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    role_pairs = []
    for idx, endpoint in enumerate(endpoints):
        role = "B" if idx == 0 else "D" if idx == 1 else "EXTRA"
        role_pairs.append(f"{endpoint}={role}")
    print(f"[moduleE] SUB 已连接: {', '.join(endpoints)}, topic={args.topic}")
    print(f"[moduleE] SUB 端点角色映射: {', '.join(role_pairs)}")
    print(f"[moduleE] PUB 已启动: {args.publish_bind}, topic={args.publish_topic}")
    print(
        "[moduleE] 融合模式: "
        f"{args.fallback_mode} "
        f"(drop阈值={args.fallback_drop_threshold}, "
        f"mismatch阈值={args.fallback_mismatch_streak})"
    )
    print(f"[moduleE] 控制接口: http://{args.control_host}:{args.control_port} (/state, /reset)")
    print("[moduleE] 按 Ctrl+C 停止")

    pending: Dict[int, Dict[str, Any]] = {}
    latest_payloads: Dict[str, Dict[str, Any]] = {"B": {}, "D": {}}
    latest_frame_ids: Dict[str, int] = {}
    dropped_timeout = 0
    mismatch_streak = 0
    effective_mode = "frame_match" if args.fallback_mode in ("auto", "frame_match") else "latest"
    last_stat_log_ts = time.monotonic()

    def emit_result(frame_id: int, b_payload: Dict[str, Any], d_payload: Dict[str, Any]) -> None:
        speed = _extract_speed(b_payload, args.default_speed)
        perception = _build_perception(frame_id, b_payload, d_payload)

        try:
            engine.update_telematics({"speed": speed})
            decision = engine.update_perception(perception)
            voice_prompt = ""
            if isinstance(decision.get("voice_prompt"), str):
                voice_prompt = str(decision.get("voice_prompt", ""))
            result = {
                "frame_id": frame_id,
                "status": "processed",
                "scene": perception.get("scene", "unknown"),
                "speed": speed,
                "detected_signs": perception.get("detected_signs", []),
                "voice_prompt": voice_prompt,
                "decision": decision,
            }
            with control_lock:
                control_state["processed_count"] = int(control_state["processed_count"]) + 1
        except Exception as exc:
            result = {
                "frame_id": frame_id,
                "status": "process_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "decision": {
                    "decision_code": "PROCESS_ERROR",
                    "speak": False,
                    "priority": None,
                    "voice_prompt": "",
                    "match_source": None,
                    "matched_event_id": None,
                    "reason": f"exception:{type(exc).__name__}",
                    "ts": time.time(),
                },
            }
            with control_lock:
                control_state["process_error_count"] = int(control_state["process_error_count"]) + 1

        result_text = json.dumps(result, ensure_ascii=False)
        publisher.send_multipart([args.publish_topic.encode("utf-8"), result_text.encode("utf-8")])
        with control_lock:
            control_state["last_emit_ts"] = time.time()
            control_state["last_result"] = result
        print(result_text)

    def maybe_switch_to_latest(now: float) -> None:
        nonlocal effective_mode
        if args.fallback_mode != "auto" or effective_mode == "latest":
            return

        triggered = False
        reason = ""
        if args.fallback_drop_threshold >= 0 and dropped_timeout >= args.fallback_drop_threshold:
            triggered = True
            reason = f"配对超时丢弃累计={dropped_timeout}"
        elif args.fallback_mismatch_streak > 0 and mismatch_streak >= args.fallback_mismatch_streak:
            triggered = True
            reason = f"连续frame_id不一致={mismatch_streak}"

        if not triggered:
            return

        effective_mode = "latest"
        pending.clear()
        print(f"[moduleE] ⚠️ 检测到严重不匹配，已切换为最新消息模式: {reason}")
        if latest_payloads["B"] and latest_payloads["D"]:
            latest_frame_id = latest_frame_ids.get("B", latest_frame_ids.get("D", 0))
            emit_result(latest_frame_id, latest_payloads["B"], latest_payloads["D"])
            print(f"[moduleE] latest模式立即输出一次，frame_id={latest_frame_id}, ts={now:.3f}")

    try:
        control_server = ThreadingHTTPServer((args.control_host, args.control_port), ControlHandler)
        control_thread = threading.Thread(target=control_server.serve_forever, name="module-e-control-api", daemon=True)
        control_thread.start()

        while running:
            events = dict(poller.poll(args.timeout_ms))
            now = time.monotonic()

            for socket in sockets:
                if socket not in events:
                    continue

                frames = socket.recv_multipart()
                label = socket_to_label[socket]
                if label not in ("B", "D"):
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

                latest_payloads[label] = payload
                latest_frame_ids[label] = frame_id
                if "B" in latest_frame_ids and "D" in latest_frame_ids:
                    if latest_frame_ids["B"] == latest_frame_ids["D"]:
                        mismatch_streak = 0
                    else:
                        mismatch_streak += 1

                maybe_switch_to_latest(now)

                if effective_mode == "latest":
                    if latest_payloads["B"] and latest_payloads["D"]:
                        latest_frame_id = latest_frame_ids.get("B", latest_frame_ids.get("D", frame_id))
                        emit_result(latest_frame_id, latest_payloads["B"], latest_payloads["D"])
                    continue

                if frame_id not in pending:
                    pending[frame_id] = {"first_ts": now, "B": None, "D": None}

                pending[frame_id][label] = payload
                entry = pending[frame_id]

                if entry["B"] is not None and entry["D"] is not None:
                    emit_result(frame_id, entry["B"], entry["D"])
                    del pending[frame_id]

            if effective_mode == "frame_match":
                expire_before = now - (args.match_timeout_ms / 1000.0)
                expired_ids = [
                    frame_id
                    for frame_id, entry in pending.items()
                    if entry["first_ts"] < expire_before
                ]
                for frame_id in expired_ids:
                    del pending[frame_id]
                    dropped_timeout += 1
                maybe_switch_to_latest(now)

            if dropped_timeout > 0 and (now - last_stat_log_ts) >= 1.0:
                print(
                    f"[moduleE] mode={effective_mode}, 配对超时丢弃累计: {dropped_timeout}, "
                    f"连续mismatch: {mismatch_streak}, 当前待配对: {len(pending)}, "
                    f"match_timeout_ms={args.match_timeout_ms}"
                )
                last_stat_log_ts = now
    finally:
        running = False
        if control_server is not None:
            control_server.shutdown()
            control_server.server_close()
        if control_thread is not None:
            control_thread.join(timeout=2.0)
        for socket in sockets:
            poller.unregister(socket)
            socket.close(linger=0)
        publisher.close(linger=0)
        ctx.term()
        engine.shutdown()
        print("[moduleE] 已停止")


if __name__ == "__main__":
    main()

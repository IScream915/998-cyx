import argparse
import json
import signal
import time
from typing import Any

import zmq


def source_label(endpoint: str) -> str:
    if endpoint.endswith(":5052"):
        return "B"
    if endpoint.endswith(":5053"):
        return "CD"
    return endpoint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模拟模块E：同时订阅多个上游并消费消息")
    parser.add_argument(
        "--endpoints",
        default="tcp://localhost:5052,tcp://localhost:5053",
        help="订阅地址列表，逗号分隔",
    )
    parser.add_argument("--topic", default="Frame", help="订阅 topic")
    parser.add_argument("--timeout_ms", type=int, default=10, help="轮询等待(ms)")
    parser.add_argument("--match_timeout_ms", type=int, default=450, help="同一 frame_id 配对超时(ms)")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    endpoints = [item.strip() for item in args.endpoints.split(",") if item.strip()]
    if not endpoints:
        raise ValueError("endpoints 不能为空")

    ctx = zmq.Context()
    poller = zmq.Poller()
    sockets = []
    socket_to_endpoint = {}
    for endpoint in endpoints:
        socket = ctx.socket(zmq.SUB)
        socket.setsockopt_string(zmq.SUBSCRIBE, args.topic)
        socket.connect(endpoint)
        poller.register(socket, zmq.POLLIN)
        sockets.append(socket)
        socket_to_endpoint[socket] = endpoint

    running = True

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print(f"[moduleE] SUB 已连接: {', '.join(endpoints)}, topic={args.topic}")
    print("[moduleE] 按 Ctrl+C 停止")

    pending = {}

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
                    result = {
                        "frame_id": frame_id,
                        "from_B": entry["B"],
                        "from_CD": entry["CD"],
                    }
                    print(json.dumps(result, ensure_ascii=False))
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
    finally:
        for socket in sockets:
            poller.unregister(socket)
            socket.close(linger=0)
        ctx.term()
        print("[moduleE] 已停止")


if __name__ == "__main__":
    main()

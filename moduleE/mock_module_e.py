import argparse
import json
import signal
from typing import Any

import zmq


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模拟模块E：订阅5052并消费消息")
    parser.add_argument("--endpoint", default="tcp://localhost:5052", help="订阅地址")
    parser.add_argument("--topic", default="Frame", help="订阅 topic")
    parser.add_argument("--timeout_ms", type=int, default=1000, help="接收超时(ms)")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    ctx = zmq.Context()
    socket = ctx.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVTIMEO, args.timeout_ms)
    socket.setsockopt_string(zmq.SUBSCRIBE, args.topic)
    socket.connect(args.endpoint)

    running = True

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print(f"[moduleE] SUB 已连接: {args.endpoint}, topic={args.topic}")
    print("[moduleE] 按 Ctrl+C 停止")

    try:
        while running:
            try:
                frames = socket.recv_multipart()
            except zmq.Again:
                continue

            if len(frames) >= 2:
                topic = frames[0].decode("utf-8", errors="replace").strip()
                payload_text = frames[-1].decode("utf-8", errors="replace").strip()
            else:
                topic = args.topic
                payload_text = frames[0].decode("utf-8", errors="replace").strip()

            try:
                payload = json.loads(payload_text)
                print(f"[moduleE][From B topic={topic}] {json.dumps(payload, ensure_ascii=False)}")
            except json.JSONDecodeError:
                print(f"[moduleE][From B topic={topic}] {payload_text}")
    finally:
        socket.close(linger=0)
        ctx.term()
        print("[moduleE] 已停止")


if __name__ == "__main__":
    main()

import argparse
import json
import random
import signal
import time
from typing import Any

import zmq


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模拟模块CD：订阅moduleA的5051消息")
    parser.add_argument("--endpoint", default="tcp://localhost:5051", help="订阅地址")
    parser.add_argument("--topic", default="Frame", help="订阅 topic")
    parser.add_argument("--publish_bind", default="tcp://*:5053", help="发布地址，供 moduleE 订阅")
    parser.add_argument("--publish_topic", default="Frame", help="发布 topic")
    parser.add_argument("--timeout_ms", type=int, default=1000, help="接收超时(ms)")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    ctx = zmq.Context()
    socket = ctx.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVTIMEO, args.timeout_ms)
    socket.setsockopt_string(zmq.SUBSCRIBE, args.topic)
    socket.connect(args.endpoint)
    publisher = ctx.socket(zmq.PUB)
    publisher.bind(args.publish_bind)

    running = True

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print(f"[moduleCD] SUB 已连接: {args.endpoint}, topic={args.topic}")
    print(f"[moduleCD] PUB 已启动: {args.publish_bind}, topic={args.publish_topic}")
    print("[moduleCD] 按 Ctrl+C 停止")

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
                # 模拟真实耗时任务：随机睡眠 200ms~500ms
                time.sleep(random.uniform(0.2, 0.5))
                payload["image"] = "111111"
                output_text = json.dumps(payload, ensure_ascii=False)
                publisher.send_multipart(
                    [args.publish_topic.encode("utf-8"), output_text.encode("utf-8")]
                )
                print(output_text)
            except json.JSONDecodeError:
                print(f"[moduleCD][From A topic={topic}] {payload_text}")
    finally:
        socket.close(linger=0)
        publisher.close(linger=0)
        ctx.term()
        print("[moduleCD] 已停止")


if __name__ == "__main__":
    main()

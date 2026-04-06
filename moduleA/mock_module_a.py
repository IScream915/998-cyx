import argparse
import json
import signal
import time
from typing import Any

import zmq


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模拟模块A：持续发布JSON消息")
    parser.add_argument("--bind", default="tcp://*:5051", help="ZeroMQ PUB 绑定地址")
    parser.add_argument("--topic", default="Frame", help="发布 topic")
    parser.add_argument("--interval", type=float, default=1.0, help="发送间隔(秒)")
    parser.add_argument("--start_frame_id", type=int, default=1, help="起始 frame_id")
    parser.add_argument("--image", default="aaaa", help="消息中的 image 字段内容")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    ctx = zmq.Context()
    socket = ctx.socket(zmq.PUB)
    socket.bind(args.bind)

    running = True

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    frame_id = args.start_frame_id
    print(f"[moduleA] PUB 已启动，地址: {args.bind}")
    print(f"[moduleA] topic: {args.topic}")
    print("[moduleA] 按 Ctrl+C 停止")

    try:
        # 给订阅端连接留出时间，避免慢连接导致丢首条消息
        time.sleep(0.3)

        while running:
            payload = {
                "frame_id": frame_id,
                "image": args.image,
            }
            socket.send_multipart(
                [args.topic.encode("utf-8"), json.dumps(payload, ensure_ascii=False).encode("utf-8")]
            )
            print(f"[topic={args.topic}] {json.dumps(payload, ensure_ascii=False)}")
            frame_id += 1
            time.sleep(args.interval)
    finally:
        socket.close(linger=0)
        ctx.term()
        print("[moduleA] 已停止")


if __name__ == "__main__":
    main()

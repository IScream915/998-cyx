import argparse
import copy
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

import zmq

# 兼容以脚本方式运行: python moduleA/mock_module_a.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from imageProcess.codec import encode_jpg_file_to_base64


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模拟模块A：持续发布JSON消息")
    parser.add_argument("--bind", default="tcp://*:5051", help="ZeroMQ PUB 绑定地址")
    parser.add_argument("--topic", default="Frame", help="发布 topic")
    parser.add_argument("--interval", type=float, default=0.5, help="发送间隔(秒)")
    parser.add_argument("--start_frame_id", type=int, default=None, help="起始 frame_id（默认使用模板中的 frame_id）")
    parser.add_argument("--image_path", required=True, help="用于编码的 .jpg 图片路径")
    parser.add_argument(
        "--template_path",
        default=str((Path(__file__).resolve().parent / "pub_example.json")),
        help="发布模板 JSON 路径（默认 moduleA/pub_example.json）",
    )
    return parser


def _load_template_payload(path: str) -> dict[str, Any]:
    template_path = Path(path)
    if not template_path.is_absolute():
        template_path = PROJECT_ROOT / template_path
    with template_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError("模板 JSON 顶层必须是对象")
    return payload


def _inject_image_data(payload: dict[str, Any], image_base64: str) -> None:
    frames = payload.get("frames")
    if not isinstance(frames, dict):
        return
    for sensor_payload in frames.values():
        if not isinstance(sensor_payload, dict):
            continue
        sensor_data = sensor_payload.get("payload")
        if not isinstance(sensor_data, dict):
            continue
        image_obj = sensor_data.get("Image")
        if not isinstance(image_obj, dict):
            continue
        image_obj["data"] = image_base64


def _increment_frame_ids_inplace(node: Any, delta: int) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "frame_id" and isinstance(value, int):
                node[key] = value + delta
                continue
            _increment_frame_ids_inplace(value, delta)
        return
    if isinstance(node, list):
        for item in node:
            _increment_frame_ids_inplace(item, delta)


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

    image_base64 = encode_jpg_file_to_base64(args.image_path)
    template_payload = _load_template_payload(args.template_path)
    _inject_image_data(template_payload, image_base64)
    template_frame_id = template_payload.get("frame_id")
    if not isinstance(template_frame_id, int):
        raise ValueError("模板缺少合法 frame_id(int)")
    start_frame_id = args.start_frame_id if args.start_frame_id is not None else template_frame_id

    print(f"[moduleA] PUB 已启动，地址: {args.bind}")
    print(f"[moduleA] topic: {args.topic}")
    print(f"[moduleA] image_path: {args.image_path}")
    print(f"[moduleA] template_path: {args.template_path}")
    print(f"[moduleA] start_frame_id: {start_frame_id}")
    print("[moduleA] 按 Ctrl+C 停止")

    try:
        # 给订阅端连接留出时间，避免慢连接导致丢首条消息
        time.sleep(0.3)

        current_frame_id = start_frame_id
        while running:
            payload = copy.deepcopy(template_payload)
            delta = int(current_frame_id - template_frame_id)
            _increment_frame_ids_inplace(payload, delta)
            socket.send_multipart(
                [args.topic.encode("utf-8"), json.dumps(payload, ensure_ascii=False).encode("utf-8")]
            )
            print(f"[topic={args.topic}] {json.dumps(payload, ensure_ascii=False)}")
            current_frame_id += 1
            time.sleep(args.interval)
    finally:
        socket.close(linger=0)
        ctx.term()
        print("[moduleA] 已停止")


if __name__ == "__main__":
    main()

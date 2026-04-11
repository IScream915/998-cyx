import argparse
import json
import signal
import sys
import traceback
from pathlib import Path
from typing import Any

import zmq

# 兼容以脚本方式运行: python3 moduleCD/mock_module_cd.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moduleCD.coreDetector import CoreDetector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模块CD：订阅A并调用CoreDetector后发布")
    parser.add_argument("--endpoint", default="tcp://localhost:5051", help="订阅地址")
    parser.add_argument("--topic", default="Frame", help="订阅 topic")
    parser.add_argument("--publish_bind", default="tcp://*:5053", help="发布地址，供 moduleE 订阅")
    parser.add_argument("--publish_topic", default="Frame", help="发布 topic")
    parser.add_argument("--timeout_ms", type=int, default=1000, help="接收超时(ms)")
    parser.add_argument("--sign-model", default=None, help="CoreDetector 交通标志模型路径")
    parser.add_argument("--scene-model", default=None, help="CoreDetector 场景模型路径")
    parser.add_argument("--conf", type=float, default=0.25, help="CoreDetector 置信度阈值")
    parser.add_argument("--iou", type=float, default=0.45, help="CoreDetector IoU 阈值")
    parser.add_argument("--img-size", type=int, default=640, help="CoreDetector 推理尺寸")
    parser.add_argument("--device", default=None, help="CoreDetector 推理设备: cuda:0/cpu")
    parser.add_argument("--save-vis", action="store_true", help="是否保存检测可视化图片")
    parser.add_argument("--vis-dir", default=None, help="可视化输出目录（需配合 --save-vis）")
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
    detector = CoreDetector(
        sign_model_path=args.sign_model,
        scene_model_path=args.scene_model,
        conf=args.conf,
        iou=args.iou,
        img_size=args.img_size,
        device=args.device,
    )
    vis_dir = Path(args.vis_dir).resolve() if args.vis_dir else detector.output_dir
    if args.save_vis:
        vis_dir.mkdir(parents=True, exist_ok=True)

    running = True

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print(f"[moduleCD] SUB 已连接: {args.endpoint}, topic={args.topic}")
    print(f"[moduleCD] PUB 已启动: {args.publish_bind}, topic={args.publish_topic}")
    print("[moduleCD] CoreDetector 已加载")
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
                if "frame_id" not in payload or "image" not in payload:
                    raise ValueError("消息缺少 frame_id 或 image 字段")

                frame_id = int(payload["frame_id"])
                vis_out = None
                if args.save_vis:
                    vis_out = str((vis_dir / f"frame_{frame_id}_detected.jpg").resolve())

                detect_result = detector.detect_base64(
                    payload["image"],
                    save_visualization=args.save_vis,
                    vis_output_path=vis_out,
                )
                output_payload = {"frame_id": frame_id}
                output_payload.update(detect_result)
                output_text = json.dumps(output_payload, ensure_ascii=False)
                publisher.send_multipart(
                    [args.publish_topic.encode("utf-8"), output_text.encode("utf-8")]
                )
                print(output_text)
            except Exception as exc:
                err_payload = {
                    "success": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
                # 若能提取 frame_id，则保持输出契约：保留 frame_id
                try:
                    parsed = json.loads(payload_text)
                    if "frame_id" in parsed:
                        err_payload["frame_id"] = int(parsed["frame_id"])
                except Exception:
                    pass

                err_text = json.dumps(err_payload, ensure_ascii=False)
                print(f"[moduleCD][From A topic={topic}] {err_text}")
    finally:
        socket.close(linger=0)
        publisher.close(linger=0)
        ctx.term()
        print("[moduleCD] 已停止")


if __name__ == "__main__":
    main()

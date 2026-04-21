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
from moduleCD.coreDetector.traffic_sign_map import TRAFFIC_SIGN


def _decode_frame(frame: bytes) -> str:
    return frame.decode("utf-8", errors="replace").strip()


def _parse_json_message(frames: list[bytes], subscribed_topic: str) -> tuple[str, dict[str, Any], str]:
    if not frames:
        raise ValueError("收到空消息帧")

    topic = subscribed_topic
    payload_text = ""

    if len(frames) == 1:
        payload_text = _decode_frame(frames[0])
        # 兼容单帧消息: "Frame {json}"
        if subscribed_topic and payload_text.startswith(subscribed_topic + " "):
            payload_text = payload_text[len(subscribed_topic) + 1 :].strip()
    else:
        topic = _decode_frame(frames[0]) or subscribed_topic
        # 兼容多帧中存在空帧的情况，取最后一个非空帧作为 payload
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

    return topic, payload, payload_text


def _extract_frame_and_image(payload: dict[str, Any]) -> tuple[int, str]:
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


def _slim_detections(items: Any, include_class_name: bool = False) -> list[dict]:
    slim: list[dict] = []
    if not isinstance(items, list):
        return slim

    for item in items:
        if not isinstance(item, dict):
            continue
        row = {
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
        num_threads=args.num_threads,
        num_interop_threads=args.num_interop_threads,
        enable_parallel_infer=not args.disable_parallel_infer,
        enable_ocr=not args.disable_ocr,
        ocr_min_conf=args.ocr_min_conf,
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
    print(
        f"[moduleCD] 推理配置: num_threads={args.num_threads}, "
        f"num_interop_threads={args.num_interop_threads}, "
        f"parallel_infer={not args.disable_parallel_infer}, "
        f"ocr_enabled={not args.disable_ocr}, "
        f"ocr_min_conf={args.ocr_min_conf:.2f}"
    )
    print("[moduleCD] 按 Ctrl+C 停止")

    try:
        while running:
            try:
                frames = socket.recv_multipart()
            except zmq.Again:
                continue

            topic = args.topic
            payload_text = ""

            try:
                topic, payload, payload_text = _parse_json_message(frames, args.topic)
                frame_id, image_b64 = _extract_frame_and_image(payload)
                vis_out = None
                if args.save_vis:
                    vis_out = str((vis_dir / f"frame_{frame_id}_detected.jpg").resolve())

                detect_result = detector.detect_base64(
                    image_b64,
                    save_visualization=args.save_vis,
                    vis_output_path=vis_out,
                )

                traffic_signs = _slim_detections(
                    detect_result.get("traffic_signs", []), include_class_name=True
                )
                pedestrians = _slim_detections(detect_result.get("pedestrians", []))
                vehicles = _slim_detections(detect_result.get("vehicles", []))

                # 下发格式对齐 moduleCD/pub_example.json
                output_payload = {
                    "frame_id": frame_id,
                    "image_size": detect_result.get("image_size", {}),
                    "traffic_signs": traffic_signs,
                    "num_traffic_signs": len(traffic_signs),
                    "pedestrians": pedestrians,
                    "num_pedestrians": len(pedestrians),
                    "vehicles": vehicles,
                    "num_vehicles": len(vehicles),
                    "tracked_pedestrians": False,
                }
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
        detector.close()
        socket.close(linger=0)
        publisher.close(linger=0)
        ctx.term()
        print("[moduleCD] 已停止")


if __name__ == "__main__":
    main()

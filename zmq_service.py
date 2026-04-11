import argparse
import json
import logging
import os
import signal
import sys
import time
from typing import Any, Callable, Optional

# 兼容 macOS 下 OpenMP 重复加载导致的进程中止问题
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import torch
import zmq
from imageProcess.codec import decode_base64_to_pil_image
from inference import load_model, predict, preprocess_pil_image


class ZMQJsonSubscriber:
    """持续订阅 ZeroMQ 消息并解析 JSON。"""

    def __init__(
        self,
        endpoint: str,
        topic: str = "Frame",
        recv_timeout_ms: int = 1000,
        reconnect_delay_sec: float = 1.0,
    ) -> None:
        self.endpoint = endpoint
        self.topic = topic
        self.recv_timeout_ms = recv_timeout_ms
        self.reconnect_delay_sec = reconnect_delay_sec

        self._context: Optional[zmq.Context] = None
        self._socket: Optional[zmq.Socket] = None
        self._running = False

    def _setup_socket(self) -> None:
        if self._socket is not None:
            self._socket.close(linger=0)

        if self._context is None:
            self._context = zmq.Context()

        socket = self._context.socket(zmq.SUB)
        socket.setsockopt(zmq.RCVTIMEO, self.recv_timeout_ms)
        socket.setsockopt_string(zmq.SUBSCRIBE, self.topic)
        socket.connect(self.endpoint)

        self._socket = socket
        logging.info("已连接到 %s, topic=%r", self.endpoint, self.topic)

    @staticmethod
    def _decode_frame(frame: bytes) -> str:
        return frame.decode("utf-8", errors="replace").strip()

    def _parse_json_message(self, frames: list[bytes]) -> tuple[Optional[str], dict[str, Any]]:
        topic: Optional[str] = None
        payload_text: str

        if len(frames) == 1:
            payload_text = self._decode_frame(frames[0])

            # 兼容 "topic {json}" 单帧格式
            if self.topic and payload_text.startswith(self.topic + " "):
                payload_text = payload_text[len(self.topic) + 1 :]
                topic = self.topic
        else:
            topic = self._decode_frame(frames[0])
            payload_text = self._decode_frame(frames[-1])

        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError("JSON 顶层必须为对象")

        return topic, payload

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close(linger=0)
            self._socket = None

        if self._context is not None:
            self._context.term()
            self._context = None

    def run_forever(self, on_message: Callable[[dict[str, Any], Optional[str]], None]) -> None:
        self._running = True

        while self._running:
            try:
                if self._socket is None:
                    self._setup_socket()

                assert self._socket is not None
                frames = self._socket.recv_multipart()

                topic, payload = self._parse_json_message(frames)
                on_message(payload, topic)

            except zmq.Again:
                # 超时用于让循环有机会响应 stop/信号
                continue
            except (json.JSONDecodeError, ValueError) as exc:
                logging.warning("收到非法 JSON 消息，已跳过: %s", exc)
            except zmq.ZMQError as exc:
                logging.error("ZMQ 异常，准备重连: %s", exc)
                time.sleep(self.reconnect_delay_sec)
                self._setup_socket()
            except Exception as exc:  # 防止业务回调异常导致服务退出
                logging.exception("消息处理异常，继续运行: %s", exc)

        self.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ZeroMQ JSON 订阅服务")
    parser.add_argument("--endpoint", default="tcp://localhost:5051", help="订阅地址")
    parser.add_argument("--topic", default="Frame", help="订阅 topic，默认 Frame")
    parser.add_argument("--publish_bind", default="tcp://*:5052", help="发布地址，供 moduleE 订阅")
    parser.add_argument("--publish_topic", default="Frame", help="发布 topic，默认 Frame")
    parser.add_argument("--timeout_ms", type=int, default=1000, help="接收超时(ms)")
    parser.add_argument("--reconnect_delay", type=float, default=1.0, help="重连等待(秒)")
    parser.add_argument("--checkpoint", type=str, default="outputs/best_model.pth", help="模型检查点路径")
    parser.add_argument(
        "--model_size",
        type=str,
        default="2_0x",
        choices=["0_5x", "0_8x", "1_0x", "2_0x"],
        help="模型大小",
    )
    parser.add_argument("--num_classes", type=int, default=7, help="类别数量")
    parser.add_argument("--img_size", type=int, default=224, help="输入图像大小")
    parser.add_argument("--device", type=str, default="auto", choices=["auto", "cuda", "cpu"], help="推理设备")
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    class_names = ["city street", "gas stations", "highway", "parking lot", "residential", "tunnel", "unknown"]

    logging.info("加载模型: %s", args.checkpoint)
    model = load_model(
        checkpoint_path=args.checkpoint,
        model_size=args.model_size,
        num_classes=args.num_classes,
        device=device,
    )
    if model is None:
        raise RuntimeError("模型加载失败，请检查检查点文件")

    subscriber = ZMQJsonSubscriber(
        endpoint=args.endpoint,
        topic=args.topic,
        recv_timeout_ms=args.timeout_ms,
        reconnect_delay_sec=args.reconnect_delay,
    )
    publish_context = zmq.Context()
    publisher = publish_context.socket(zmq.PUB)
    publisher.bind(args.publish_bind)
    logging.info("已启动发布端: %s, topic=%r", args.publish_bind, args.publish_topic)

    def handle_signal(signum: int, _frame: Any) -> None:
        logging.info("收到信号 %s，正在停止订阅服务", signum)
        subscriber.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    def process_message(payload: dict[str, Any], _topic: Optional[str]) -> None:
        if "frame_id" not in payload or "image" not in payload:
            raise ValueError("消息缺少 frame_id 或 image 字段")

        frame_id = int(payload["frame_id"])
        image = decode_base64_to_pil_image(payload["image"])
        image_tensor, _ = preprocess_pil_image(image, args.img_size)
        scene, confidence, _ = predict(model, image_tensor, device, class_names)

        result = {
            "frame_id": frame_id,
            "scene": scene,
            "conference": confidence,
        }
        result_json = json.dumps(result, ensure_ascii=False)
        publisher.send_multipart([args.publish_topic.encode("utf-8"), result_json.encode("utf-8")])

        print(result_json)
        sys.stdout.flush()

    try:
        subscriber.run_forever(on_message=process_message)
    finally:
        publisher.close(linger=0)
        publish_context.term()


if __name__ == "__main__":
    main()

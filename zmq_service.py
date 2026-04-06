import argparse
import json
import logging
import signal
import sys
import time
from typing import Any, Callable, Optional

import zmq


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
    parser.add_argument("--timeout_ms", type=int, default=1000, help="接收超时(ms)")
    parser.add_argument("--reconnect_delay", type=float, default=1.0, help="重连等待(秒)")
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    subscriber = ZMQJsonSubscriber(
        endpoint=args.endpoint,
        topic=args.topic,
        recv_timeout_ms=args.timeout_ms,
        reconnect_delay_sec=args.reconnect_delay,
    )

    def handle_signal(signum: int, _frame: Any) -> None:
        logging.info("收到信号 %s，正在停止订阅服务", signum)
        subscriber.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    def print_message(payload: dict[str, Any], topic: Optional[str]) -> None:
        if topic:
            print(f"[topic={topic}] {json.dumps(payload, ensure_ascii=False)}")
        else:
            print(json.dumps(payload, ensure_ascii=False))
        sys.stdout.flush()

    subscriber.run_forever(on_message=print_message)


if __name__ == "__main__":
    main()

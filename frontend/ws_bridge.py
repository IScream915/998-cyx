#!/usr/bin/env python3
"""A+B+C+E ZMQ -> WebSocket bridge for frontend/fullflow page."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import time
from dataclasses import dataclass
from typing import Any

try:
    import zmq
    import zmq.asyncio
except Exception as exc:  # pragma: no cover - runtime dependency
    raise RuntimeError("缺少依赖 pyzmq，请先安装: pip install pyzmq") from exc

try:
    from websockets.server import serve
except Exception:  # pragma: no cover - compatibility fallback
    try:
        from websockets import serve  # type: ignore
    except Exception as exc:
        raise RuntimeError("缺少依赖 websockets，请先安装: pip install websockets") from exc


@dataclass
class MatchedFrame:
    frame_id: int
    image_base64: str
    module_b: dict[str, Any]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A+B+C+E 到 WebSocket 的实时桥接服务")
    parser.add_argument("--a-endpoint", default="tcp://192.168.31.157:5050", help="模块A订阅地址")
    parser.add_argument("--a-topic", default="Frame", help="模块A订阅topic")
    parser.add_argument("--b-endpoint", default="tcp://localhost:5052", help="模块B订阅地址")
    parser.add_argument("--b-topic", default="Frame", help="模块B订阅topic")
    parser.add_argument("--c-endpoint", default="tcp://localhost:5053", help="模块C订阅地址")
    parser.add_argument("--c-topic", default="Frame", help="模块C订阅topic")
    parser.add_argument("--e-endpoint", default="tcp://localhost:5054", help="模块E订阅地址")
    parser.add_argument("--e-topic", default="Frame", help="模块E订阅topic")
    parser.add_argument("--ws-host", default="0.0.0.0", help="WebSocket监听地址")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket监听端口")
    parser.add_argument("--match-timeout-ms", type=int, default=1500, help="A/B配对超时毫秒")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    return parser


class ABWsBridge:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.clients: set[Any] = set()
        self.pending: dict[int, dict[str, Any]] = {}
        self.stop_event = asyncio.Event()
        self.stats = {
            "matched": 0,
            "dropped_timeout": 0,
            "invalid_a": 0,
            "invalid_b": 0,
            "invalid_c": 0,
            "invalid_e": 0,
        }

    @staticmethod
    def _decode_frame(frame: bytes) -> str:
        return frame.decode("utf-8", errors="replace").strip()

    @staticmethod
    def _parse_frame_id(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"frame_id 非法: {value!r}") from exc

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed == parsed else None  # NaN guard

    @staticmethod
    def _to_non_negative_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def _compact_module_b_payload(self, payload: dict[str, Any], frame_id: int) -> dict[str, Any]:
        confidence = self._to_float(payload.get("confidence"))
        if confidence is None:
            confidence = self._to_float(payload.get("conference"))

        compact = {
            "frame_id": frame_id,
            "scene": payload.get("scene", "unknown"),
            "confidence": confidence,
            "speed": self._to_float(payload.get("speed")),
        }
        source_mode = payload.get("source_mode")
        if isinstance(source_mode, str):
            compact["source_mode"] = source_mode
        scene_folder = payload.get("scene_folder")
        if isinstance(scene_folder, str):
            compact["scene_folder"] = scene_folder
        image_relpath = payload.get("image_relpath")
        if isinstance(image_relpath, str):
            compact["image_relpath"] = image_relpath
        frame_index = self._to_non_negative_int(payload.get("frame_index"))
        if frame_index is not None:
            compact["frame_index"] = frame_index
        frame_total = self._to_non_negative_int(payload.get("frame_total"))
        if frame_total is not None:
            compact["frame_total"] = frame_total
        return compact

    def _compact_module_c_payload(self, payload: dict[str, Any], frame_id: int) -> dict[str, Any]:
        return {
            "frame_id": frame_id,
            "num_traffic_signs": self._to_non_negative_int(payload.get("num_traffic_signs")),
            "num_pedestrians": self._to_non_negative_int(payload.get("num_pedestrians")),
            "num_vehicles": self._to_non_negative_int(payload.get("num_vehicles")),
        }

    def _parse_json_message(self, frames: list[bytes], subscribed_topic: str) -> tuple[str, dict[str, Any]]:
        if not frames:
            raise ValueError("空消息帧")

        topic = subscribed_topic
        payload_text = ""

        if len(frames) == 1:
            payload_text = self._decode_frame(frames[0])
            if subscribed_topic and payload_text.startswith(subscribed_topic + " "):
                payload_text = payload_text[len(subscribed_topic) + 1 :].strip()
        else:
            topic = self._decode_frame(frames[0]) or subscribed_topic
            payload_text = self._decode_frame(frames[-1])

        if not payload_text:
            raise ValueError("消息payload为空")

        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError("JSON顶层必须是对象")
        return topic, payload

    @staticmethod
    def _extract_image_from_a(payload: dict[str, Any]) -> str:
        # 仅接受新协议: frames.top_camera.payload.Image.data
        frames = payload.get("frames")
        if not isinstance(frames, dict):
            raise ValueError("A消息缺少或非法字段: frames")

        top_camera = frames.get("top_camera")
        if not isinstance(top_camera, dict):
            raise ValueError("A消息缺少或非法字段: frames.top_camera")

        top_payload = top_camera.get("payload")
        if not isinstance(top_payload, dict):
            raise ValueError("A消息缺少或非法字段: frames.top_camera.payload")

        image_obj = top_payload.get("Image")
        if not isinstance(image_obj, dict):
            raise ValueError("A消息缺少或非法字段: frames.top_camera.payload.Image")

        image_data = image_obj.get("data")
        if not isinstance(image_data, str) or not image_data.strip():
            raise ValueError("A消息缺少或非法字段: frames.top_camera.payload.Image.data")
        return image_data

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if not self.clients:
            return

        text = json.dumps(payload, ensure_ascii=False)
        disconnected: list[Any] = []

        for websocket in list(self.clients):
            try:
                await websocket.send(text)
            except Exception:
                disconnected.append(websocket)

        for websocket in disconnected:
            self.clients.discard(websocket)

    async def _send_status(self, status: str, message: str) -> None:
        await self._broadcast(
            {
                "event": "status",
                "status": status,
                "message": message,
                "ts": time.time(),
                "stats": dict(self.stats),
            }
        )

    async def _emit_matched(self, matched: MatchedFrame) -> None:
        payload = {
            "event": "ab_frame",
            "frame_id": matched.frame_id,
            "image_base64": matched.image_base64,
            "image_src": f"data:image/jpeg;base64,{matched.image_base64}",
            "moduleB": matched.module_b,
            "ts": time.time(),
        }
        await self._broadcast(payload)

    async def _on_a_message(self, payload: dict[str, Any], now: float) -> None:
        frame_id = self._parse_frame_id(payload.get("frame_id"))
        image_base64 = self._extract_image_from_a(payload)

        entry = self.pending.setdefault(frame_id, {"first_ts": now, "a": None, "b": None})
        entry["a"] = {
            "frame_id": frame_id,
            "image_base64": image_base64,
        }

        if entry["b"] is not None:
            matched = MatchedFrame(
                frame_id=frame_id,
                image_base64=image_base64,
                module_b=entry["b"]["payload"],
            )
            self.stats["matched"] += 1
            del self.pending[frame_id]
            await self._emit_matched(matched)

    async def _on_b_message(self, payload: dict[str, Any], now: float) -> None:
        frame_id = self._parse_frame_id(payload.get("frame_id"))
        compact_payload = self._compact_module_b_payload(payload, frame_id)
        await self._broadcast(
            {
                "event": "b_frame",
                "frame_id": frame_id,
                "moduleB": compact_payload,
                "ts": time.time(),
            }
        )

        entry = self.pending.setdefault(frame_id, {"first_ts": now, "a": None, "b": None})
        entry["b"] = {"frame_id": frame_id, "payload": compact_payload}

        if entry["a"] is not None:
            matched = MatchedFrame(
                frame_id=frame_id,
                image_base64=entry["a"]["image_base64"],
                module_b=compact_payload,
            )
            self.stats["matched"] += 1
            del self.pending[frame_id]
            await self._emit_matched(matched)

    async def _on_c_message(self, payload: dict[str, Any]) -> None:
        frame_id = self._parse_frame_id(payload.get("frame_id"))
        compact_payload = self._compact_module_c_payload(payload, frame_id)
        await self._broadcast(
            {
                "event": "c_frame",
                "frame_id": frame_id,
                "moduleC": compact_payload,
                "ts": time.time(),
            }
        )

    async def _on_e_message(self, payload: dict[str, Any]) -> None:
        frame_id = self._parse_frame_id(payload.get("frame_id"))
        await self._broadcast(
            {
                "event": "e_frame",
                "frame_id": frame_id,
                "moduleE": payload,
                "ts": time.time(),
            }
        )

    def _evict_timeout(self, now: float) -> None:
        timeout_sec = self.args.match_timeout_ms / 1000.0
        expired = [
            frame_id
            for frame_id, entry in self.pending.items()
            if (now - entry["first_ts"]) > timeout_sec
        ]
        for frame_id in expired:
            del self.pending[frame_id]
            self.stats["dropped_timeout"] += 1

    async def _zmq_loop(self) -> None:
        context = zmq.asyncio.Context()
        socket_a = context.socket(zmq.SUB)
        socket_b = context.socket(zmq.SUB)
        socket_c = context.socket(zmq.SUB)
        socket_e = context.socket(zmq.SUB)
        try:
            socket_a.setsockopt_string(zmq.SUBSCRIBE, self.args.a_topic)
            socket_b.setsockopt_string(zmq.SUBSCRIBE, self.args.b_topic)
            socket_c.setsockopt_string(zmq.SUBSCRIBE, self.args.c_topic)
            socket_e.setsockopt_string(zmq.SUBSCRIBE, self.args.e_topic)
            socket_a.connect(self.args.a_endpoint)
            socket_b.connect(self.args.b_endpoint)
            socket_c.connect(self.args.c_endpoint)
            socket_e.connect(self.args.e_endpoint)

            poller = zmq.asyncio.Poller()
            poller.register(socket_a, zmq.POLLIN)
            poller.register(socket_b, zmq.POLLIN)
            poller.register(socket_c, zmq.POLLIN)
            poller.register(socket_e, zmq.POLLIN)

            logging.info(
                "ZMQ订阅已启动: A=%s[%s], B=%s[%s], C=%s[%s], E=%s[%s]",
                self.args.a_endpoint,
                self.args.a_topic,
                self.args.b_endpoint,
                self.args.b_topic,
                self.args.c_endpoint,
                self.args.c_topic,
                self.args.e_endpoint,
                self.args.e_topic,
            )

            while not self.stop_event.is_set():
                events = dict(await poller.poll(timeout=200))
                now = time.monotonic()

                if socket_a in events:
                    frames = await socket_a.recv_multipart()
                    try:
                        _topic, payload = self._parse_json_message(frames, self.args.a_topic)
                        await self._on_a_message(payload, now)
                    except Exception as exc:
                        self.stats["invalid_a"] += 1
                        logging.warning("A消息解析失败，已丢弃: %s", exc)

                if socket_b in events:
                    frames = await socket_b.recv_multipart()
                    try:
                        _topic, payload = self._parse_json_message(frames, self.args.b_topic)
                        await self._on_b_message(payload, now)
                    except Exception as exc:
                        self.stats["invalid_b"] += 1
                        logging.warning("B消息解析失败，已丢弃: %s", exc)

                if socket_c in events:
                    frames = await socket_c.recv_multipart()
                    try:
                        _topic, payload = self._parse_json_message(frames, self.args.c_topic)
                        await self._on_c_message(payload)
                    except Exception as exc:
                        self.stats["invalid_c"] += 1
                        logging.warning("C消息解析失败，已丢弃: %s", exc)

                if socket_e in events:
                    frames = await socket_e.recv_multipart()
                    try:
                        _topic, payload = self._parse_json_message(frames, self.args.e_topic)
                        await self._on_e_message(payload)
                    except Exception as exc:
                        self.stats["invalid_e"] += 1
                        logging.warning("E消息解析失败，已丢弃: %s", exc)

                self._evict_timeout(now)

        finally:
            socket_a.close(linger=0)
            socket_b.close(linger=0)
            socket_c.close(linger=0)
            socket_e.close(linger=0)
            context.term()

    async def ws_handler(self, websocket: Any, _path: str | None = None) -> None:
        self.clients.add(websocket)
        logging.info("WebSocket客户端已连接，当前连接数: %d", len(self.clients))
        try:
            await websocket.send(
                json.dumps(
                    {
                        "event": "status",
                        "status": "connected",
                        "message": "已连接实时桥接服务",
                        "ts": time.time(),
                    },
                    ensure_ascii=False,
                )
            )
            async for _ in websocket:
                # 前端无需向桥接发送业务消息，收到后忽略即可。
                pass
        finally:
            self.clients.discard(websocket)
            logging.info("WebSocket客户端已断开，当前连接数: %d", len(self.clients))

    def request_stop(self) -> None:
        if not self.stop_event.is_set():
            self.stop_event.set()

    async def run(self) -> None:
        logging.info("WebSocket服务启动: ws://%s:%d", self.args.ws_host, self.args.ws_port)
        await self._send_status("starting", "桥接服务正在启动")

        async with serve(self.ws_handler, self.args.ws_host, self.args.ws_port):
            zmq_task = asyncio.create_task(self._zmq_loop())
            await self._send_status("running", "桥接服务已启动")
            await self.stop_event.wait()

            zmq_task.cancel()
            try:
                await zmq_task
            except asyncio.CancelledError:
                pass

        await self._send_status("stopped", "桥接服务已停止")


async def async_main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    bridge = ABWsBridge(args)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bridge.request_stop)
        except NotImplementedError:
            # Windows 兼容: add_signal_handler 可能不可用
            signal.signal(sig, lambda *_: bridge.request_stop())

    await bridge.run()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

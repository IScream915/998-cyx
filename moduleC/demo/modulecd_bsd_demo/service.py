from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any

import zmq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from demo.modulecd_bsd_demo.protocol import DecodedSensorBundle, ModuleCDDemoMessageDecoder
from src.runtime.modulecd_payload import build_modulecd_bsd_payload
from src.runtime.pipeline import BSDRuntimePipeline
from src.utils.config import load_config
from src.utils.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CARLA-free BSD demo service with moduleCD-compatible ZMQ I/O."
    )
    parser.add_argument(
        "--config",
        default="demo/modulecd_bsd_demo/config.toml",
        help="Path to the demo TOML config.",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=0,
        help="Stop after processing this many valid messages (0 means run forever).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-frame JSON logs and only print startup/shutdown info.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override configured log level, e.g. DEBUG/INFO/WARNING.",
    )
    return parser


def _recv_topic_and_payload(frames: list[bytes]) -> tuple[str, bytes]:
    if len(frames) >= 2:
        topic = frames[0].decode("utf-8", errors="replace").strip()
        payload = frames[-1]
    elif len(frames) == 1:
        single = frames[0]
        try:
            text = single.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        stripped = text.lstrip()
        if stripped.startswith("{"):
            topic = "Frame"
            payload = single
        else:
            head, sep, tail = stripped.partition(" ")
            if sep and tail.lstrip().startswith("{"):
                topic = head.strip() or "Frame"
                payload = tail.lstrip().encode("utf-8")
            else:
                topic = "Frame"
                payload = single
    else:
        raise ValueError("Empty ZMQ message.")
    return topic, payload


def _build_browser_payload(
    decoded: DecodedSensorBundle,
    modulecd_payload: dict[str, object],
) -> dict[str, object]:
    return {
        "frame_id": int(modulecd_payload["frame_id"]),
        "t_sync": float(decoded.frame_input.timestamp),
        "cameras": decoded.browser_cameras,
        "moduleCD": modulecd_payload,
    }


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    configure_logging(config, level=args.log_level)
    logger = logging.getLogger("modulecd_bsd_demo.service")
    zmq_cfg = config["demo"]["zmq"]

    ctx = zmq.Context()
    socket = ctx.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVTIMEO, int(zmq_cfg["recv_timeout_ms"]))
    socket.setsockopt_string(zmq.SUBSCRIBE, str(zmq_cfg["input_topic"]))
    socket.connect(str(zmq_cfg["input_addr"]))

    publisher = ctx.socket(zmq.PUB)
    publisher.bind(str(zmq_cfg["output_bind"]))

    frontend_cfg = config["demo"].get("frontend", {})
    frontend_publisher = None
    frontend_topic = str(frontend_cfg.get("topic", zmq_cfg["output_topic"]))
    frontend_bind = str(frontend_cfg.get("bind", "")).strip()
    if frontend_bind:
        frontend_publisher = ctx.socket(zmq.PUB)
        frontend_publisher.bind(frontend_bind)

    decoder = ModuleCDDemoMessageDecoder(config)
    pipeline = BSDRuntimePipeline(config)

    logger.info(
        f"[moduleCD-BSD-demo] SUB connected to {zmq_cfg['input_addr']} topic={zmq_cfg['input_topic']}"
    )
    logger.info(
        f"[moduleCD-BSD-demo] PUB bound to {zmq_cfg['output_bind']} topic={zmq_cfg['output_topic']}"
    )
    if frontend_publisher is not None:
        logger.info(
            f"[moduleCD-BSD-demo] FRONTEND PUB bound to {frontend_bind} topic={frontend_topic}"
        )
    logger.info(
        f"[moduleCD-BSD-demo] detector={pipeline.detector_backend} device={pipeline.detector_device}"
    )

    processed = 0
    try:
        while True:
            try:
                frames = socket.recv_multipart()
            except zmq.Again:
                continue

            topic, payload_bytes = _recv_topic_and_payload(frames)
            try:
                decoded = decoder.decode_message(topic, payload_bytes)
            except Exception as exc:
                logger.warning(
                    "decode_failed topic=%s error_type=%s error=%s",
                    topic,
                    type(exc).__name__,
                    exc,
                )
                if not args.quiet:
                    print(
                        json.dumps(
                            {
                                "success": False,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            },
                            ensure_ascii=False,
                        )
                    )
                continue
            if decoded is None:
                continue

            result = pipeline.process_frame(decoded.frame_input)
            payload = build_modulecd_bsd_payload(
                result,
                class_names=pipeline.class_names,
                top_camera_present=decoded.top_camera_present,
                sensor_ids=decoded.sensor_ids,
            )
            payload_json = json.dumps(payload, ensure_ascii=False)
            publisher.send_multipart(
                [
                    str(zmq_cfg["output_topic"]).encode("utf-8"),
                    payload_json.encode("utf-8"),
                ]
            )
            if frontend_publisher is not None:
                browser_payload = _build_browser_payload(decoded, payload)
                frontend_publisher.send_multipart(
                    [
                        frontend_topic.encode("utf-8"),
                        json.dumps(browser_payload, ensure_ascii=False).encode("utf-8"),
                    ]
                )
            if not args.quiet:
                print(payload_json)
            processed += 1
            if args.max_messages > 0 and processed >= args.max_messages:
                break
    finally:
        socket.close(linger=0)
        publisher.close(linger=1000)
        if frontend_publisher is not None:
            frontend_publisher.close(linger=1000)
        ctx.term()
        logger.info("[moduleCD-BSD-demo] stopped after %d processed frame(s)", processed)


if __name__ == "__main__":
    main()

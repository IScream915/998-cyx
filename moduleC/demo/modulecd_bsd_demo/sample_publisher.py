from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
import time

import zmq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish sample JPG/base64 BSD demo frames.")
    parser.add_argument(
        "--config",
        default="demo/modulecd_bsd_demo/config.toml",
        help="Path to the demo TOML config.",
    )
    parser.add_argument("--count", type=int, default=3, help="Number of frames to publish.")
    parser.add_argument(
        "--interval-ms",
        type=int,
        default=200,
        help="Delay between messages in milliseconds.",
    )
    parser.add_argument(
        "--warmup-ms",
        type=int,
        default=2000,
        help="Wait this long after bind so subscribers can connect.",
    )
    parser.add_argument(
        "--first-frame-burst",
        type=int,
        default=3,
        help="Send the first frame this many times to reduce PUB/SUB slow-joiner drops.",
    )
    parser.add_argument(
        "--burst-gap-ms",
        type=int,
        default=150,
        help="Delay between repeated sends of the first frame in milliseconds.",
    )
    return parser


def _encode_file_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _build_frame_payload(image_b64: str) -> dict[str, object]:
    return {
        "payload": {
            "Image": {
                "format": "jpeg",
                "data": image_b64,
            }
        }
    }


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    zmq_cfg = config["demo"]["zmq"]
    assets_cfg = config["demo"]["assets"]

    left_path = PROJECT_ROOT / str(assets_cfg["left_image"])
    right_path = PROJECT_ROOT / str(assets_cfg["right_image"])
    top_path = PROJECT_ROOT / str(assets_cfg["top_image"])

    left_b64 = _encode_file_b64(left_path)
    right_b64 = _encode_file_b64(right_path)
    top_b64 = _encode_file_b64(top_path)

    ctx = zmq.Context()
    publisher = ctx.socket(zmq.PUB)
    publisher.bind(str(zmq_cfg["input_bind"]))
    time.sleep(max(0.0, args.warmup_ms / 1000.0))

    try:
        for idx in range(args.count):
            turn_signal = "left" if idx % 3 == 0 else ("right" if idx % 3 == 1 else "off")
            payload = {
                "t_sync": float(time.time()),
                "frame_id": idx,
                "frames": {
                    "left_camera": _build_frame_payload(left_b64),
                    "right_camera": _build_frame_payload(right_b64),
                    "top_camera": _build_frame_payload(top_b64),
                    "imu": {
                        "payload": {
                            "Imu": {
                                "gyro": {"z": 0.08 if turn_signal == "left" else -0.06 if turn_signal == "right" else 0.0},
                                "accel": {"x": 0.2},
                            }
                        }
                    },
                },
                "vehicle_states": {
                    "ego": {
                        "speed_mps": 8.5 + 0.2 * idx,
                        "light_state_bits": 0,
                        "turn_signal": turn_signal,
                    }
                },
                "sync_meta": {
                    "source": "sample_publisher",
                    "note": "demo/modulecd_bsd_demo sample payload",
                },
            }
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            send_repeats = args.first_frame_burst if idx == 0 else 1
            for attempt in range(max(1, send_repeats)):
                publisher.send_multipart(
                    [
                        str(zmq_cfg["input_topic"]).encode("utf-8"),
                        encoded,
                    ]
                )
                if attempt + 1 < max(1, send_repeats):
                    time.sleep(max(0.0, args.burst_gap_ms / 1000.0))
            print(f"[sample-publisher] sent frame_id={idx}")
            if idx + 1 < args.count:
                time.sleep(max(0.0, args.interval_ms / 1000.0))
    finally:
        publisher.close(linger=0)
        ctx.term()


if __name__ == "__main__":
    main()

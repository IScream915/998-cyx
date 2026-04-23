from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import zmq

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Subscribe to BSD demo output and print JSON.")
    parser.add_argument(
        "--config",
        default="demo/modulecd_bsd_demo/config.toml",
        help="Path to the demo TOML config.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="How many output messages to print before exiting.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=10000,
        help="Receive timeout in milliseconds.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    zmq_cfg = config["demo"]["zmq"]

    ctx = zmq.Context()
    socket = ctx.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVTIMEO, int(args.timeout_ms))
    socket.setsockopt_string(zmq.SUBSCRIBE, str(zmq_cfg["output_topic"]))
    socket.connect(str(zmq_cfg["output_endpoint"]))
    received = 0
    try:
        while received < args.count:
            try:
                frames = socket.recv_multipart()
            except zmq.Again as exc:
                raise SystemExit(
                    f"No message received on {zmq_cfg['output_endpoint']} within {args.timeout_ms} ms."
                ) from exc
            topic = frames[0].decode("utf-8", errors="replace").strip()
            payload = json.loads(frames[-1].decode("utf-8"))
            print(f"[sample-subscriber] topic={topic}")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            received += 1
    finally:
        socket.close(linger=0)
        ctx.term()


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import zmq


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_demo_service_roundtrip_over_zmq(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    base_config = (repo_root / "demo/modulecd_bsd_demo/config.toml").read_text(encoding="utf-8")
    in_port = _free_port()
    out_port = _free_port()
    patched = (
        base_config
        .replace('input_addr = "tcp://localhost:5051"', f'input_addr = "tcp://127.0.0.1:{in_port}"')
        .replace('input_bind = "tcp://*:5051"', f'input_bind = "tcp://*:{in_port}"')
        .replace('output_bind = "tcp://*:5058"', f'output_bind = "tcp://*:{out_port}"')
        .replace('output_endpoint = "tcp://localhost:5058"', f'output_endpoint = "tcp://127.0.0.1:{out_port}"')
    )
    config_path = tmp_path / "demo_config.toml"
    config_path.write_text(patched, encoding="utf-8")

    service = subprocess.Popen(
        [
            sys.executable,
            "demo/modulecd_bsd_demo/service.py",
            "--config",
            str(config_path),
            "--max-messages",
            "1",
            "--quiet",
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ctx = zmq.Context()
    subscriber = ctx.socket(zmq.SUB)
    subscriber.setsockopt(zmq.RCVTIMEO, 25000)
    subscriber.setsockopt_string(zmq.SUBSCRIBE, "Frame")
    subscriber.connect(f"tcp://127.0.0.1:{out_port}")
    try:
        time.sleep(1.5)
        # Reuse the demo publisher helper so the test exercises the same asset path and payload shape.
        pub = subprocess.run(
            [
                sys.executable,
                "demo/modulecd_bsd_demo/sample_publisher.py",
                "--config",
                str(config_path),
                "--count",
                "1",
                "--interval-ms",
                "1",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "sent frame_id=0" in pub.stdout

        frames = subscriber.recv_multipart()
        topic = frames[0].decode("utf-8")
        payload = json.loads(frames[-1].decode("utf-8"))
        assert topic == "Frame"
        assert payload["frame_id"] == 0
        assert "vehicles" in payload
        assert "bsd" in payload
        assert payload["traffic_signs"] == []
        assert payload["num_traffic_signs"] == 0
    finally:
        subscriber.close(linger=0)
        ctx.term()
        try:
            service.wait(timeout=30)
        except subprocess.TimeoutExpired:
            service.kill()
            raise
    stdout, stderr = service.communicate()
    assert service.returncode == 0, stderr or stdout

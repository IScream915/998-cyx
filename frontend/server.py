#!/usr/bin/env python3
"""前端静态资源启动脚本。

特性：
- 无需手动指定 --directory
- 可从任意工作目录启动
- 默认监听 0.0.0.0，便于本地/服务器访问
- 提供场景目录查询 API
- 代理 moduleB/moduleD 控制接口，供前端同源调用
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 frontend 静态页面服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=4173, help="监听端口，默认 4173")
    parser.add_argument("--module_b_control_host", default="127.0.0.1", help="moduleB控制接口地址")
    parser.add_argument("--module_b_control_port", type=int, default=5056, help="moduleB控制接口端口")
    parser.add_argument("--module_d_control_host", default="127.0.0.1", help="moduleD控制接口地址")
    parser.add_argument("--module_d_control_port", type=int, default=5057, help="moduleD控制接口端口")
    return parser


def resolve_lan_ip() -> str | None:
    """尽量获取当前机器局域网 IP，用于提示同网段访问地址。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 不会真正建立外部连接，仅用于触发本机路由选择
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def _natural_sort_key(text: str) -> list[Any]:
    return [int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", text)]


def _build_handler(
    frontend_dir: Path,
    scenes_root: Path,
    module_b_control_host: str,
    module_b_control_port: int,
    module_d_control_host: str,
    module_d_control_port: int,
):
    class FrontendHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(frontend_dir), **kwargs)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_json_body(self) -> dict[str, Any]:
            raw_len = self.headers.get("Content-Length", "0")
            try:
                body_len = int(raw_len)
            except ValueError as exc:
                raise ValueError("Content-Length 非法") from exc

            if body_len <= 0:
                return {}

            raw = self.rfile.read(body_len)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise ValueError("请求体不是合法JSON") from exc

            if not isinstance(payload, dict):
                raise ValueError("请求体JSON顶层必须是对象")
            return payload

        def _resolve_scene(self, scene_name: str) -> Path:
            if not re.fullmatch(r"[A-Za-z0-9._-]+", scene_name):
                raise ValueError("scene 名称非法")

            root = scenes_root.resolve()
            scene_dir = (root / scene_name).resolve()
            if scene_dir.parent != root:
                raise ValueError("scene 越界")
            if not scene_dir.is_dir():
                raise FileNotFoundError("scene 不存在")
            return scene_dir

        def _list_scene_names(self) -> list[str]:
            if not scenes_root.is_dir():
                return []
            names = [item.name for item in scenes_root.iterdir() if item.is_dir()]
            names.sort(key=_natural_sort_key)
            return names

        def _list_scene_frames(self, scene_name: str) -> list[str]:
            scene_dir = self._resolve_scene(scene_name)
            images = [
                item
                for item in scene_dir.iterdir()
                if item.is_file() and item.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
            ]
            images.sort(key=lambda p: _natural_sort_key(p.name))

            frames: list[str] = []
            for image in images:
                try:
                    rel = image.resolve().relative_to(frontend_dir.resolve()).as_posix()
                except Exception:
                    rel = f"assets/scenes/{scene_name}/{image.name}"
                frames.append(rel)
            return frames

        def _proxy_module_control(
            self,
            *,
            module_name: str,
            host: str,
            port: int,
            method: str,
            target_path: str,
            payload: dict[str, Any] | None = None,
        ) -> None:
            target_url = f"http://{host}:{port}{target_path}"
            req_data: bytes | None = None
            headers = {"Accept": "application/json"}
            if payload is not None:
                req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"

            req = urllib.request.Request(target_url, method=method, data=req_data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=4) as resp:
                    raw = resp.read()
                    try:
                        decoded = json.loads(raw.decode("utf-8"))
                    except Exception:
                        decoded = {"ok": False, "error": f"{module_name} 返回了非JSON响应"}
                    status = int(resp.getcode() or 200)
                    self._send_json(status, decoded if isinstance(decoded, dict) else {"ok": True, "data": decoded})
                    return
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    decoded = json.loads(raw.decode("utf-8"))
                except Exception:
                    decoded = {"ok": False, "error": raw.decode("utf-8", errors="replace") or f"{module_name} 请求失败"}
                self._send_json(exc.code, decoded if isinstance(decoded, dict) else {"ok": False, "error": str(decoded)})
                return
            except Exception as exc:
                self._send_json(
                    HTTPStatus.BAD_GATEWAY,
                    {"ok": False, "error": f"{module_name} 控制服务不可用: {exc}"},
                )
                return

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/api/scenes":
                scenes = []
                for scene_name in self._list_scene_names():
                    frame_count = len(self._list_scene_frames(scene_name))
                    scenes.append({"name": scene_name, "frame_count": frame_count})
                self._send_json(HTTPStatus.OK, {"ok": True, "scenes": scenes})
                return

            m = re.fullmatch(r"/api/scenes/([^/]+)/frames", path)
            if m:
                scene_name = urllib.parse.unquote(m.group(1))
                try:
                    frames = self._list_scene_frames(scene_name)
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "scene": scene_name,
                            "frame_count": len(frames),
                            "frames": frames,
                        },
                    )
                except FileNotFoundError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "scene 不存在"})
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            if path == "/api/module-b/state":
                self._proxy_module_control(
                    module_name="moduleB",
                    host=module_b_control_host,
                    port=module_b_control_port,
                    method="GET",
                    target_path="/state",
                )
                return

            if path == "/api/module-d/state":
                self._proxy_module_control(
                    module_name="moduleD",
                    host=module_d_control_host,
                    port=module_d_control_port,
                    method="GET",
                    target_path="/state",
                )
                return

            super().do_GET()

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path not in {
                "/api/module-b/mode",
                "/api/module-b/scene",
                "/api/module-b/player",
                "/api/module-d/mode",
                "/api/module-d/scene",
                "/api/module-d/player",
            }:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
                return

            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return

            if path == "/api/module-b/mode":
                self._proxy_module_control(
                    module_name="moduleB",
                    host=module_b_control_host,
                    port=module_b_control_port,
                    method="POST",
                    target_path="/mode",
                    payload=payload,
                )
                return
            if path == "/api/module-b/scene":
                self._proxy_module_control(
                    module_name="moduleB",
                    host=module_b_control_host,
                    port=module_b_control_port,
                    method="POST",
                    target_path="/scene",
                    payload=payload,
                )
                return
            if path == "/api/module-b/player":
                self._proxy_module_control(
                    module_name="moduleB",
                    host=module_b_control_host,
                    port=module_b_control_port,
                    method="POST",
                    target_path="/player",
                    payload=payload,
                )
                return

            if path == "/api/module-d/mode":
                self._proxy_module_control(
                    module_name="moduleD",
                    host=module_d_control_host,
                    port=module_d_control_port,
                    method="POST",
                    target_path="/mode",
                    payload=payload,
                )
                return
            if path == "/api/module-d/scene":
                self._proxy_module_control(
                    module_name="moduleD",
                    host=module_d_control_host,
                    port=module_d_control_port,
                    method="POST",
                    target_path="/scene",
                    payload=payload,
                )
                return
            if path == "/api/module-d/player":
                self._proxy_module_control(
                    module_name="moduleD",
                    host=module_d_control_host,
                    port=module_d_control_port,
                    method="POST",
                    target_path="/player",
                    payload=payload,
                )
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})

    return FrontendHandler


def main() -> None:
    args = build_parser().parse_args()

    frontend_dir = Path(__file__).resolve().parent
    scenes_root = (frontend_dir / "assets" / "scenes").resolve()

    handler = _build_handler(
        frontend_dir=frontend_dir,
        scenes_root=scenes_root,
        module_b_control_host=args.module_b_control_host,
        module_b_control_port=args.module_b_control_port,
        module_d_control_host=args.module_d_control_host,
        module_d_control_port=args.module_d_control_port,
    )

    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"[frontend] 静态服务目录: {frontend_dir}")
    print(f"[frontend] 场景目录API: {scenes_root}")
    print(
        f"[frontend] moduleB 控制代理 -> http://{args.module_b_control_host}:{args.module_b_control_port}"
    )
    print(
        f"[frontend] moduleD 控制代理 -> http://{args.module_d_control_host}:{args.module_d_control_port}"
    )
    if args.host in ("0.0.0.0", "::"):
        print(f"[frontend] 绑定地址: http://{args.host}:{args.port}")
        print(f"[frontend] 本机访问: http://127.0.0.1:{args.port}")
        print(f"[frontend] 本机访问: http://localhost:{args.port}")
        lan_ip = resolve_lan_ip()
        if lan_ip and lan_ip not in ("127.0.0.1", "0.0.0.0"):
            print(f"[frontend] 局域网访问: http://{lan_ip}:{args.port}")
        print("[frontend] 提示: 浏览器不要直接访问 0.0.0.0")
    else:
        print(f"[frontend] 访问地址: http://{args.host}:{args.port}")
    print("[frontend] 按 Ctrl+C 停止")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[frontend] 已停止")


if __name__ == "__main__":
    main()

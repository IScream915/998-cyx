#!/usr/bin/env python3
"""前端静态资源启动脚本。

特性：
- 无需手动指定 --directory
- 可从任意工作目录启动
- 默认监听 0.0.0.0，便于本地/服务器访问
"""

from __future__ import annotations

import argparse
import functools
import socket
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 frontend 静态页面服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=4173, help="监听端口，默认 4173")
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


def main() -> None:
    args = build_parser().parse_args()

    frontend_dir = Path(__file__).resolve().parent
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(frontend_dir))

    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"[frontend] 静态服务目录: {frontend_dir}")
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

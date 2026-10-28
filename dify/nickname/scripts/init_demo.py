#!/usr/bin/env python3
"""初始化演示历史。仅使用 Python 标准库，默认读取 ~/apps/powercontext/.env。"""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


SEED = (
    "nicknamehistory 玩家自述：写了一下午代码，关机后发现没有保存。"
    "本次封号：内存限定工程师。判词：硬盘对你一下午的努力毫不知情。"
)


def load_token(env_file):
    token = os.environ.get("POWERCONTEXT_SERVER_AUTH_TOKEN", "").strip()
    if not token:
        for line in env_file.expanduser().read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "POWERCONTEXT_SERVER_AUTH_TOKEN":
                parts = shlex.split(value, comments=True)
                token = parts[0].strip() if len(parts) == 1 else ""
    if not token or token.startswith("REPLACE_") or "\n" in token or "\r" in token:
        raise ValueError("请在环境变量或 --env-file 指定的配置中填写有效的访问令牌。")
    return token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    parser.add_argument("--env-file", type=Path, default=Path.home() / "apps/powercontext/.env")
    parser.add_argument("--name", default="nickname-demo-a", help="Scope 名称及幂等键；换新名称可恢复初始状态")
    parser.add_argument("--empty", action="store_true", help="仅创建 Scope，不写入演示历史")
    args = parser.parse_args()
    token = load_token(args.env_file)
    opener = build_opener(ProxyHandler({}))

    def post(path, payload):
        request = Request(
            args.url.rstrip("/") + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            method="POST",
        )
        with opener.open(request, timeout=15) as response:
            return json.load(response)

    scope = post("/v1/scopes", {
        "title": args.name,
        "summary": "黑历史封号机演示玩家A" if args.name == "nickname-demo-a" else "黑历史封号机演示记忆空间",
        "idempotency_key": args.name,
    })
    scope_id = scope["scope_id"]
    search = {"scope_id": scope_id, "query": "nicknamehistory", "limit": 3}
    hits = post("/v1/memory/search", search)["hits"]
    if args.empty:
        if hits:
            raise ValueError("该 Scope 已有演示历史，请换一个新的 --name 创建空白 Scope。")
    elif not hits:
        saved = post("/v1/memory/remember", {
            "scope_id": scope_id, "kind": "decision", "text": SEED,
        })
        if not saved.get("records"):
            raise ValueError("记忆写入未返回 records，请检查 PowerContext。")
        hits = post("/v1/memory/search", search)["hits"]
        if not hits:
            raise ValueError("已写入记忆，但搜索未命中，请检查 PowerContext。")

    print(json.dumps({
        "PC_SCOPE_A": scope_id,
        "history_count": len(hits),
        "history": [hit["memory"]["text"] for hit in hits],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        print(f"初始化失败：HTTP {error.code}，请检查服务地址、令牌及接口配置。", file=sys.stderr)
        sys.exit(1)
    except (OSError, URLError, ValueError, KeyError, TypeError) as error:
        print(f"初始化失败：{error}", file=sys.stderr)
        sys.exit(1)

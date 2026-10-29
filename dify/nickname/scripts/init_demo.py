#!/usr/bin/env python3
"""默认创建验收用空白 Scope；--seed 可选写入故事。仅使用 Python 标准库。"""

import argparse
from datetime import datetime, timezone
import json
import os
import secrets
import shlex
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
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
    if not token or token.startswith("REPLACE_") or any(c.isspace() for c in token):
        raise ValueError("请在环境变量或 --env-file 指定的配置中填写有效的访问令牌。")
    return token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18000")
    parser.add_argument("--env-file", type=Path, default=Path.home() / "apps/powercontext/.env")
    parser.add_argument("--name", help="Scope 名称及幂等键；省略则自动生成唯一名称")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--empty", action="store_true", help="仅创建空白 Scope（默认，兼容旧用法）")
    mode.add_argument("--seed", action="store_true", help="写入演示故事，不用于空白验收")
    args = parser.parse_args()
    if urlsplit(args.url).scheme not in ("http", "https") or not urlsplit(args.url).netloc:
        raise ValueError("--url 必须是有效的 http:// 或 https:// 服务地址。")
    name = args.name.strip() if args.name is not None else (
        "nickname-live-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-" + secrets.token_hex(4)
    )
    if not name:
        raise ValueError("--name 不能为空。")
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
            data = json.load(response)
        if not isinstance(data, dict):
            raise ValueError("接口返回格式错误：" + path)
        return data

    scope = post("/v1/scopes", {
        "title": name,
        "summary": "黑历史封号机验收记忆空间",
        "idempotency_key": name,
    })
    scope_id = scope.get("scope_id")
    if not isinstance(scope_id, str) or not scope_id:
        raise ValueError("创建 Scope 未返回有效 scope_id。")
    search = {"scope_id": scope_id, "query": "nicknamehistory", "limit": 3}

    def search_history():
        hits = post("/v1/memory/search", search).get("hits")
        if not isinstance(hits, list):
            raise ValueError("搜索接口未返回 hits 数组。")
        return hits

    hits = search_history()
    if not args.seed:
        if hits:
            raise ValueError("该 Scope 已有游戏历史；请省略 --name 或换新名称，原数据未删除。")
    elif not hits:
        saved = post("/v1/memory/remember", {
            "scope_id": scope_id, "kind": "decision", "text": SEED,
        })
        records = saved.get("records")
        if not isinstance(records, list) or not records:
            raise ValueError("记忆写入未返回非空 records 数组，请检查 PowerContext。")
        hits = search_history()
        if not hits:
            raise ValueError("已写入记忆，但搜索未命中，请检查 PowerContext。")

    print(json.dumps({
        "PC_SCOPE_A": scope_id,
        "scope_name": name,
        "mode": "seed" if args.seed else "empty",
        "matched_history_count": len(hits),
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

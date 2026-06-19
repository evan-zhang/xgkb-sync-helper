#!/usr/bin/env python3
"""
xgkb_retry.py — 消费重试队列，补推失败的同步

用法: python3 xgkb_retry.py
"""

import json
import sys
from pathlib import Path

RETRY_LOG_PATH = Path.home() / ".openclaw" / "xgkb-retry.jsonl"
MAX_RETRIES = 3

sys.path.insert(0, str(Path(__file__).parent))
from xgkb_push import load_global_config, get_project_id, upload_content, DEFAULT_SERVER_URL


def main():
    if not RETRY_LOG_PATH.exists():
        return

    # 读取所有待重试条目
    with open(RETRY_LOG_PATH) as f:
        lines = f.readlines()

    if not lines:
        return

    pending = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            pending.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not pending:
        RETRY_LOG_PATH.unlink()
        return

    global_cfg = load_global_config()
    app_key = global_cfg.get("appKey", "")
    server_url = global_cfg.get("serverUrl", DEFAULT_SERVER_URL)

    if not app_key:
        print("[xgkb-retry] 未配置 appKey，跳过", file=sys.stderr)
        return

    still_failing = []

    for entry in pending:
        retries = entry.get("retries", 0)
        if retries >= MAX_RETRIES:
            print(f"[xgkb-retry] ⚠️ 超过最大重试次数，放弃: {entry.get('file_name')}", file=sys.stderr)
            continue

        file_path = entry["file_path"]
        folder_name = entry["folder_name"]
        file_name = entry["file_name"]

        # 读取文件内容（如果是本地文件）
        content = None
        if not file_path.startswith("<stdin:"):
            try:
                content = Path(file_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                print(f"[xgkb-retry] 无法读取 {file_path}: {e}", file=sys.stderr)
                entry["retries"] = retries + 1
                still_failing.append(entry)
                continue
        else:
            # stdin 来源的内容无法重试
            print(f"[xgkb-retry] ⚠️ stdin 来源无法重试: {file_name}", file=sys.stderr)
            continue

        suffix = file_name.rsplit(".", 1)[-1] if "." in file_name else "txt"

        try:
            project_id = get_project_id(server_url, app_key)
            upload_content(server_url, app_key, project_id, folder_name, file_name, content, suffix)
            print(f"[xgkb-retry] ✅ 补推成功: {file_name} → {folder_name}/")
        except Exception as e:
            print(f"[xgkb-retry] ❌ 仍然失败: {file_name}: {e}", file=sys.stderr)
            entry["retries"] = retries + 1
            still_failing.append(entry)

    # 重写重试队列
    RETRY_LOG_PATH.unlink()
    if still_failing:
        with open(RETRY_LOG_PATH, "a") as f:
            for entry in still_failing:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[xgkb-retry] {len(still_failing)} 条仍在重试队列")


if __name__ == "__main__":
    main()

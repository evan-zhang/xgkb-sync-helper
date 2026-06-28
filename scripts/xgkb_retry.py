#!/usr/bin/env python3
"""
xgkb_retry.py — 消费重试队列，补推失败的同步

用法:
  python3 xgkb_retry.py <目录或文件路径>
  python3 xgkb_retry.py /path/to/workspace

从给定路径定位 workspace，找到 .xgkb-retry.jsonl 并补推。
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from xgkb_push import load_agent_config, resolve_project_id, upload_content, DEFAULT_SERVER_URL, get_retry_log_path

MAX_RETRIES = 3


def main():
    if len(sys.argv) < 2:
        print("用法: python3 xgkb_retry.py <目录或文件路径>", file=sys.stderr)
        sys.exit(1)

    hint_path = sys.argv[1]
    retry_log_path = get_retry_log_path(hint_path)

    if not retry_log_path.exists():
        return

    # 读取所有待重试条目
    with open(retry_log_path) as f:
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
        retry_log_path.unlink()
        return

    # 从第一条重试记录的文件路径定位 workspace
    first_file = pending[0].get("file_path", "") if pending else ""
    if first_file:
        global_cfg = load_agent_config(first_file)
    else:
        global_cfg = load_agent_config(hint_path)
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

        # 解析目标空间（优先用重试记录中保存的，其次环境变量）
        proj_cfg = {}
        if entry.get("project_id"):
            proj_cfg["projectId"] = entry["project_id"]
        elif entry.get("project_name"):
            proj_cfg["projectName"] = entry["project_name"]
        elif os.environ.get("XGKB_PROJECT_ID"):
            proj_cfg["projectId"] = os.environ["XGKB_PROJECT_ID"]
        elif os.environ.get("XGKB_PROJECT_NAME"):
            proj_cfg["projectName"] = os.environ["XGKB_PROJECT_NAME"]

        try:
            project_id = resolve_project_id(server_url, app_key, proj_cfg)
            upload_content(server_url, app_key, project_id, folder_name, file_name, content, suffix)
            print(f"[xgkb-retry] ✅ 补推成功: {file_name} → {folder_name}/")
        except Exception as e:
            print(f"[xgkb-retry] ❌ 仍然失败: {file_name}: {e}", file=sys.stderr)
            entry["retries"] = retries + 1
            still_failing.append(entry)

    # 重写重试队列
    retry_log_path.unlink()
    if still_failing:
        with open(retry_log_path, "a") as f:
            for entry in still_failing:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[xgkb-retry] {len(still_failing)} 条仍在重试队列")


if __name__ == "__main__":
    main()

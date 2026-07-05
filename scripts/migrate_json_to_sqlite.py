#!/usr/bin/env python3
"""
migrate_json_to_sqlite.py — 把 xgkb-sync-helper v2.0 的 JSON state 迁移到 v2.1 的 SQLite。

用法：
  # 列出所有可迁移的 JSON 文件
  python3 migrate_json_to_sqlite.py list

  # 干跑（不写，只打印会做什么）
  python3 migrate_json_to_sqlite.py migrate --all --dry-run

  # 迁移一个 JSON 到 SQLite
  python3 migrate_json_to_sqlite.py migrate ~/.openclaw/xgkb-state/TPR-Framework.json

  # 迁移所有 JSON
  python3 migrate_json_to_sqlite.py migrate --all

迁移行为：
  - 把 <name>.json → <name>.db（同目录）
  - 老 JSON 内容（projectId / remoteRoot / serverTime / files）写入 v2.1 schema
  - 备份原 JSON 到 <name>.json.v2-bak
  - 迁移后**老 JSON 不删**（保留作审计）
  - DB key 仍用 remoteRoot（与 v2.0 完全一致），无副作用

已知限制：
  - 迁移后的 DB 不享受 v2.1 的"跨项目隔离"（sha256 key 公式）
    如果你有两个项目都用同一个 remoteRoot，迁移后它们的 DB 仍然是同一个
    要享受跨项目隔离，跑一次 sync 即可（state 会被新公式重建）

"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import xgkb_state_sqlite as state


STATE_DIR = Path.home() / ".openclaw" / "xgkb-state"


def list_json_files() -> list[Path]:
    """列出所有可迁移的 JSON state 文件。"""
    if not STATE_DIR.exists():
        return []
    return sorted(STATE_DIR.glob("*.json"))


def cmd_list() -> int:
    files = list_json_files()
    if not files:
        print(f"在 {STATE_DIR} 下没找到 .json state 文件")
        return 0
    print(f"在 {STATE_DIR} 下找到 {len(files)} 个 JSON state 文件：")
    print()
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            files_count = len(data.get("files", {}))
            project_id = data.get("projectId", "")[:16]
            remote_root = data.get("remoteRoot", "?")
            size = p.stat().st_size
            print(f"  {p.name:40s} remoteRoot={remote_root:30s} "
                  f"projectId={project_id}... files={files_count:3d} size={size}")
        except Exception as e:
            print(f"  {p.name:40s} ⚠️ 损坏: {e}")
    return 0


def migrate_one(json_path: Path, dry_run: bool = False) -> bool:
    """迁移一个 JSON 文件到 SQLite。返回 True 成功。"""
    if not json_path.exists():
        print(f"  ✗ 文件不存在: {json_path}", file=sys.stderr)
        return False

    # 解析 JSON
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON 损坏: {json_path}: {e}", file=sys.stderr)
        return False

    remote_root = data.get("remoteRoot") or data.get("projectKey") or json_path.stem
    files_dict = data.get("files", {})
    project_id = data.get("projectId", "")
    server_time = data.get("serverTime", 0)

    db_path = json_path.with_suffix(".db")
    print(f"  迁移: {json_path.name}")
    print(f"    remoteRoot : {remote_root}")
    print(f"    projectId  : {project_id[:16]}...")
    print(f"    files      : {len(files_dict)}")
    print(f"    →          : {db_path.name}")

    if dry_run:
        print(f"    [DRY] 不写文件")
        return True

    # 1) 备份原 JSON
    backup_path = json_path.with_suffix(".json.v2-bak")
    if not backup_path.exists():
        shutil.copy2(json_path, backup_path)
        print(f"    备份     : {backup_path.name}")

    # 2) 写入 SQLite（用旧 key 公式 = remote_root，与 v2.0 行为一致）
    db_project_key = remote_root
    conn = state._get_conn(db_project_key)

    with state._atomic(conn):
        # meta
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            ("projectId", project_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            ("remoteRoot", remote_root),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            ("serverTime", str(server_time)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            ("schema_version", "1"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            ("migrated_at", str(int(time.time()))),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            ("migrated_from", "v2.0-json"),
        )

        # files
        for rel_path, rec in files_dict.items():
            file_id = rec.get("fileId", 0)
            version_number = rec.get("versionNumber", 1)
            content_hash = rec.get("contentHash", "")
            mtime = rec.get("mtime", 0)
            last_sync_at = rec.get("lastSyncAt", 0)
            conn.execute(
                "INSERT OR REPLACE INTO files"
                "(rel_path, file_id, version_number, content_hash, mtime, last_sync_at) "
                "VALUES(?,?,?,?,?,?)",
                (rel_path, int(file_id), int(version_number), content_hash,
                 int(mtime), int(last_sync_at)),
            )

    print(f"    ✅ 完成")
    return True


def cmd_migrate(args) -> int:
    if args.all:
        targets = list_json_files()
    elif args.json_path:
        targets = [Path(args.json_path).resolve()]
    else:
        print("用法: migrate --all 或 migrate <json_path>", file=sys.stderr)
        return 1

    if not targets:
        print(f"在 {STATE_DIR} 下没找到 JSON state 文件")
        return 0

    print(f"准备迁移 {len(targets)} 个 JSON state 文件")
    print(f"state dir: {STATE_DIR}")
    print(f"dry-run: {args.dry_run}")
    print()

    ok, fail = 0, 0
    for p in targets:
        if migrate_one(p, dry_run=args.dry_run):
            ok += 1
        else:
            fail += 1

    print()
    print(f"=== 迁移完成: ✅ {ok} 成功  ✗ {fail} 失败 ===")
    if not args.dry_run and ok > 0:
        print()
        print("注意：原 JSON 文件保留为 *.json.v2-bak，未删除。")
        print("      迁移后的 SQLite DB key 仍是 remoteRoot（兼容 v2.0）。")
        print("      享受 v2.1 跨项目隔离，只需跑一次 sync 即可（state 会被新公式重建）。")
    return 0 if fail == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="把 v2.0 JSON state 迁移到 v2.1 SQLite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有 JSON state 文件")

    p_mig = sub.add_parser("migrate", help="迁移")
    p_mig.add_argument("json_path", nargs="?", help="要迁移的 JSON 文件路径")
    p_mig.add_argument("--all", action="store_true", help="迁移所有 JSON 文件")
    p_mig.add_argument("--dry-run", action="store_true", help="干跑，只打印")

    args = parser.parse_args()

    if args.cmd == "list":
        return cmd_list()
    elif args.cmd == "migrate":
        return cmd_migrate(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
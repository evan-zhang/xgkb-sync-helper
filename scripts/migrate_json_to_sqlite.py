#!/usr/bin/env python3
"""
migrate_json_to_sqlite.py — 把 xgkb-sync-helper v2.0 的 JSON state 迁移到 v2.1 的 SQLite。

用法：
  # 列出所有可迁移的 JSON 文件
  python3 migrate_json_to_sqlite.py list

  # 干跑（不写，只打印会做什么）
  python3 migrate_json_to_sqlite.py migrate --all --dry-run

  # 迁移一个 JSON 到 SQLite（推荐：写入 v2.1 hash-key DB）
  python3 migrate_json_to_sqlite.py migrate ~/.openclaw/xgkb-state/TPR-Framework.json --proj-root /path/to/project

  # 迁移所有 JSON（仅当这些 JSON 属于同一个项目根时使用）
  python3 migrate_json_to_sqlite.py migrate --all --proj-root /path/to/project

迁移行为：
  - 默认写入 v2.1 hash-key DB（与 load_state(remote, server, appKey, projRoot) 一致）
  - 老 JSON 内容（projectId / remoteRoot / serverTime / files）写入 v2.1 schema
  - 备份原 JSON 到 <name>.json.v2-bak
  - 迁移后**老 JSON 不删**（保留作审计）
  - 如需旧行为，显式传 --legacy-key

已知限制：
  - v2.0 JSON 不包含 proj_root；迁移时必须通过 --proj-root 提供

"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import xgkb_state_sqlite as state


STATE_DIR = Path.home() / ".openclaw" / "xgkb-state"
DEFAULT_SERVER_URL = "https://sg-al-cwork-web.mediportal.com.cn/open-api/"


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


def migrate_one(
    json_path: Path,
    dry_run: bool = False,
    server_url: str = "",
    app_key: str = "",
    proj_root: Path | None = None,
    legacy_key: bool = False,
) -> bool:
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

    if legacy_key:
        db_project_key = remote_root
    else:
        if not server_url or not app_key or proj_root is None:
            print(
                "  ✗ v2.1 迁移需要 --proj-root，并能从 ~/.openclaw/.xgkb.json "
                "或 XGKB_APPKEY 取得 appKey；如确需旧 key，请显式传 --legacy-key",
                file=sys.stderr,
            )
            return False
        db_project_key = state.make_project_key(server_url, app_key, remote_root, proj_root)

    db_path = state._db_path_for(db_project_key)
    print(f"  迁移: {json_path.name}")
    print(f"    remoteRoot : {remote_root}")
    print(f"    projectId  : {project_id[:16]}...")
    print(f"    files      : {len(files_dict)}")
    print(f"    key        : {'legacy' if legacy_key else db_project_key[:16] + '...'}")
    print(f"    →          : {db_path.name}")

    if dry_run:
        print(f"    [DRY] 不写文件")
        return True

    # 1) 备份原 JSON
    backup_path = json_path.with_suffix(".json.v2-bak")
    if not backup_path.exists():
        shutil.copy2(json_path, backup_path)
        print(f"    备份     : {backup_path.name}")

    # 2) 写入 SQLite
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


def load_global_config() -> dict:
    p = Path.home() / ".openclaw" / ".xgkb.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


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

    cfg = load_global_config()
    server_url = args.server_url or cfg.get("serverUrl", DEFAULT_SERVER_URL)
    app_key = args.app_key or cfg.get("appKey", "") or os.environ.get("XGKB_APPKEY", "")
    proj_root = Path(args.proj_root).resolve() if args.proj_root else None

    ok, fail = 0, 0
    for p in targets:
        if migrate_one(
            p,
            dry_run=args.dry_run,
            server_url=server_url,
            app_key=app_key,
            proj_root=proj_root,
            legacy_key=args.legacy_key,
        ):
            ok += 1
        else:
            fail += 1

    print()
    print(f"=== 迁移完成: ✅ {ok} 成功  ✗ {fail} 失败 ===")
    if not args.dry_run and ok > 0:
        print()
        print("注意：原 JSON 文件保留为 *.json.v2-bak，未删除。")
        if args.legacy_key:
            print("      本次使用 legacy remoteRoot key；v2.1 hash-key 调用不会自动读取它。")
        else:
            print("      迁移后的 SQLite DB key 与 v2.1 load_state(...) 一致。")
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
    p_mig.add_argument("--proj-root", help="项目根目录（用于生成 v2.1 hash-key DB）")
    p_mig.add_argument("--server-url", default="", help="玄关服务地址（默认读 ~/.openclaw/.xgkb.json）")
    p_mig.add_argument("--app-key", default="", help="appKey（默认读 ~/.openclaw/.xgkb.json 或 XGKB_APPKEY）")
    p_mig.add_argument("--legacy-key", action="store_true", help="显式使用 v2.0 remoteRoot key")

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

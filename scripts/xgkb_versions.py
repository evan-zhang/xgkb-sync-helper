#!/usr/bin/env python3
"""
xgkb_versions.py — 玄关知识库版本控制工具

用法:
  # 列出某文件的所有历史版本（用 fileId 定位）
  python3 xgkb_versions.py list <fileId>

  # 列出本地某文件的所有历史版本（通过 xgkb-state 找 fileId）
  python3 xgkb_versions.py list-local <本地文件路径>

  # 把指定版本标记为定稿
  python3 xgkb_versions.py finalize <fileId> [--version N]

  # 列出指定项目根下的所有版本化文件 + 最新版本号
  python3 xgkb_versions.py tree <本地项目路径>
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

import xgkb_client as api
import xgkb_state as state


DEFAULT_SERVER_URL = api.DEFAULT_SERVER_URL


def load_global_config() -> dict:
    p = Path.home() / ".openclaw" / ".xgkb.json"
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def cmd_list(server_url: str, app_key: str, file_id: int) -> int:
    """列文件的所有历史版本。"""
    versions = api.get_version_list(server_url, app_key, file_id)
    if not versions:
        print(f"[xgkb-versions] 文件 {file_id} 没有历史版本")
        return 0
    print(f"[xgkb-versions] 文件 {file_id} 共有 {len(versions)} 个版本：")
    print()
    for v in versions:
        status = v.get("status", 1)
        status_str = "定稿" if status == 2 else "草稿"
        marker = " ← 最新" if v.get("lastVersion") else ""
        print(f"  v{v.get('versionNumber')} ({status_str}) - "
              f"{v.get('versionName', '')} - "
              f"{v.get('creator', '?')} - "
              f"{v.get('remark', '')}"
              f"{marker}")
    return 0


def cmd_list_local(server_url: str, app_key: str, local_path: Path) -> int:
    """列本地文件对应的云端版本。"""
    # 找 project_key 和 fileId
    current = local_path.resolve()
    if current.is_file():
        current = current.parent
    proj_root = None
    proj_cfg = None
    for parent in [current] + list(current.parents):
        cfg_path = parent / ".xgkb.json"
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    proj_cfg = json.load(f)
                proj_root = parent
                break
            except (json.JSONDecodeError, OSError):
                continue
    if proj_cfg is None or proj_root is None:
        print(f"[xgkb-versions] 项目未启用同步", file=sys.stderr)
        return 1

    remote_root = proj_cfg.get("remoteRoot", "OpenClaw")
    state_data = state.load_state(remote_root)
    rel_path = str(local_path.resolve().relative_to(proj_root)).replace(os.sep, "/")
    recorded = state.get_recorded(state_data, rel_path)
    if not recorded:
        print(f"[xgkb-versions] 本地文件 {rel_path} 未同步过，找不到云端 fileId")
        return 1

    file_id = recorded["fileId"]
    print(f"[xgkb-versions] 本地 {rel_path} → 云端 fileId={file_id}")
    return cmd_list(server_url, app_key, file_id)


def cmd_finalize(server_url: str, app_key: str, file_id: int, version: int) -> int:
    """标记指定版本为定稿。"""
    success = api.finalize_version(server_url, app_key, file_id, version)
    if success:
        print(f"[xgkb-versions] ✅ fileId={file_id} version={version or 'latest'} 已定稿")
        return 0
    print(f"[xgkb-versions] ❌ 定稿失败", file=sys.stderr)
    return 1


def cmd_tree(server_url: str, app_key: str, proj_root: Path) -> int:
    """列出项目下所有版本化文件的最新版本号。"""
    current = proj_root.resolve()
    if current.is_file():
        current = current.parent
    proj_cfg = None
    for parent in [current] + list(current.parents):
        cfg_path = parent / ".xgkb.json"
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    proj_cfg = json.load(f)
                proj_root = parent
                break
            except (json.JSONDecodeError, OSError):
                continue
    if proj_cfg is None:
        print(f"[xgkb-versions] 项目未启用同步", file=sys.stderr)
        return 1

    remote_root = proj_cfg.get("remoteRoot", "OpenClaw")
    state_data = state.load_state(remote_root)
    tracked = state.list_tracked_paths(state_data)

    if not tracked:
        print(f"[xgkb-versions] 项目 {proj_root} 下还没有同步记录")
        return 0

    print(f"[xgkb-versions] 项目 {proj_root} 下的版本化文件：")
    print()
    for rel_path in sorted(tracked):
        rec = state.get_recorded(state_data, rel_path)
        if not rec:
            continue
        try:
            last = api.get_last_version(server_url, app_key, rec["fileId"])
            ver_n = last.get("versionNumber", "?")
            ver_name = last.get("versionName", "")
            status = "定稿" if last.get("status") == 2 else "草稿"
            print(f"  v{ver_n:>3} ({status:2s})  {rel_path}  "
                  f"[{ver_name}]  fileId={rec['fileId']}")
        except Exception as e:
            print(f"  v?  {rel_path}  [查询失败: {e}]", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="玄关知识库版本控制工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列文件所有版本（用 fileId）")
    p_list.add_argument("file_id", type=int)

    p_list_local = sub.add_parser("list-local", help="列本地文件对应的云端版本")
    p_list_local.add_argument("path", help="本地文件路径")

    p_finalize = sub.add_parser("finalize", help="定稿某版本")
    p_finalize.add_argument("file_id", type=int)
    p_finalize.add_argument("--version", "-v", type=int, default=0,
                            help="版本号（默认 0=最新）")

    p_tree = sub.add_parser("tree", help="列项目下所有版本化文件")
    p_tree.add_argument("path", help="本地项目路径")

    args = parser.parse_args()

    cfg = load_global_config()
    app_key = cfg.get("appKey", "") or os.environ.get("XGKB_APPKEY", "")
    server_url = cfg.get("serverUrl", DEFAULT_SERVER_URL)
    if not app_key:
        print(f"[xgkb-versions] 未配置 appKey", file=sys.stderr)
        return 1

    if args.cmd == "list":
        return cmd_list(server_url, app_key, args.file_id)
    elif args.cmd == "list-local":
        return cmd_list_local(server_url, app_key, Path(args.path).resolve())
    elif args.cmd == "finalize":
        return cmd_finalize(server_url, app_key, args.file_id, args.version)
    elif args.cmd == "tree":
        return cmd_tree(server_url, app_key, Path(args.path).resolve())

    return 0


if __name__ == "__main__":
    sys.exit(main())
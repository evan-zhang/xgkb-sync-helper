#!/usr/bin/env python3
"""
xgkb_sync_full.py — xgkb-sync-helper 全量双向同步

用法:
  python3 xgkb_sync_full.py <本地项目路径> [--direction push|pull|sync] [--dry-run]
  python3 xgkb_sync_full.py <本地项目路径> --direction sync --conflict local|cloud|skip

方向:
  push  — 仅本地 → 云端（增删改），最常用、最安全
  pull  — 仅云端 → 本地（拉取云端变更到本地）
  sync  — 双向：先 pull 再 push（依赖时间戳合并，可能有冲突）

冲突策略（sync 模式）:
  local  — 本地覆盖云端（默认）
  cloud  — 云端覆盖本地
  skip   — 跳过冲突项并报告

输出格式:
  [xgkb-sync] 📤 上传: 2 个
  [xgkb-sync] 📥 拉取: 1 个
  [xgkb-sync] 🗑️  删除: 0 个
  [xgkb-sync] 📝 重命名: 1 个
  [xgkb-sync] ⚠️  冲突: 0 个（local-wins）
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import xgkb_client as api
import xgkb_state_sqlite as state


DEFAULT_SERVER_URL = api.DEFAULT_SERVER_URL


def load_project_config(local_root: Path):
    """从项目目录向上找 .xgkb.json 配置。

    返回 (global_cfg, proj_cfg, proj_root)
    """
    # 全局配置从 ~/.openclaw/.xgkb.json
    global_cfg_path = Path.home() / ".openclaw" / ".xgkb.json"
    global_cfg: dict = {}
    if global_cfg_path.exists():
        try:
            with open(global_cfg_path, "r", encoding="utf-8") as f:
                global_cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            global_cfg = {}

    # 项目配置（向上找 .xgkb.json）
    current = local_root.resolve()
    if current.is_file():
        current = current.parent
    proj_cfg: Optional[dict] = None
    proj_root: Optional[Path] = None
    for parent in [current] + list(current.parents):
        cfg_path = parent / ".xgkb.json"
        if cfg_path.exists() and cfg_path != global_cfg_path:
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    proj_cfg = json.load(f)
                proj_root = parent
                break
            except (json.JSONDecodeError, OSError):
                continue

    return global_cfg, proj_cfg, proj_root


def collect_local_files(proj_root: Path) -> dict:
    """递归扫描项目根下的所有文件，返回 {rel_path: abs_path}。

    排除 .git/、.xgkb.json、__pycache__ 等配置和无关文件。
    """
    exclude_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv"}
    exclude_files = {".xgkb.json", ".xgkb-cache.json", ".DS_Store"}
    result = {}
    for p in proj_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(proj_root)
        # 排除路径里有 exclude_dirs 的
        if any(part in exclude_dirs for part in rel.parts):
            continue
        # 排除特定文件名
        if rel.name in exclude_files:
            continue
        rel_str = str(rel).replace(os.sep, "/")
        result[rel_str] = p
    return result


def remote_folder_for(rel_path: str) -> str:
    """本地相对路径 → 云端 folderName（去掉文件名部分）。"""
    parts = rel_path.rsplit("/", 1)
    return parts[0] if len(parts) > 1 else ""


def compute_remote_path(remote_root: str, rel_path: str) -> str:
    """本地相对路径 → 云端完整路径（folderName/name）。"""
    if remote_root:
        return f"{remote_root}/{rel_path}"
    return rel_path


def hash_text_content(content: str) -> str:
    """Return the same sha256:... format used by state.hash_file()."""
    h = hashlib.sha256()
    h.update(content.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def is_supported_pull_text(rel_path: str) -> bool:
    return Path(rel_path).suffix.lower() in api.TEXT_EXTENSIONS


# === Push 模式：本地 → 云端 ===

def do_push(
    server_url: str,
    app_key: str,
    proj_cfg: dict,
    proj_root: Path,
    project_id: str,
    remote_root: str,
    state_data: dict,
    dry_run: bool = False,
) -> dict:
    """本地 → 云端（增删改）。

    返回 {"uploaded": int, "updated": int, "deleted": int, "renamed": int, "skipped": int}
    """
    result = {"uploaded": 0, "updated": 0, "deleted": 0, "renamed": 0, "skipped": 0}
    local_files = collect_local_files(proj_root)
    tracked = state.list_tracked_paths(state_data)
    tracked_set = set(tracked)

    # 1) 增 / 改
    for rel_path, local_path in sorted(local_files.items()):
        local_hash = state.hash_file(local_path)
        recorded = state.get_recorded(state_data, rel_path)
        folder_name = compute_remote_path(remote_root, rel_path.rsplit("/", 1)[0]) \
            if "/" in rel_path else remote_root

        version_control = proj_cfg.get("versionControl", False)

        if recorded is None:
            # 新增
            if dry_run:
                print(f"  + [DRY] 新增: {rel_path}")
                result["uploaded"] += 1
                continue
            try:
                file_id = api.upload_local_file(
                    server_url, app_key, project_id,
                    folder_name, local_path,
                )
                version_number = 1
                if version_control:
                    version_number = api.get_last_version(
                        server_url, app_key, file_id,
                    ).get("versionNumber", 1)
                state.mark_synced(state_data, rel_path, file_id, version_number, local_path)
                print(f"  + 新增: {rel_path} (fileId={file_id})")
                result["uploaded"] += 1
            except Exception as e:
                print(f"  ✗ 新增失败: {rel_path}: {e}", file=sys.stderr)
                result["skipped"] += 1
        elif recorded.get("contentHash") != local_hash:
            # 改了
            if dry_run:
                print(f"  ~ [DRY] 更新: {rel_path}")
                result["updated"] += 1
                continue
            try:
                update_file_id = recorded["fileId"]
                if version_control:
                    # 版本控制模式：保留历史
                    file_id = api.upload_local_file(
                        server_url, app_key, project_id,
                        folder_name, local_path,
                        update_file_id=update_file_id,
                        version_remark=f"本地更新 {time.strftime('%Y-%m-%d %H:%M:%S')}",
                    )
                    version_number = api.get_last_version(
                        server_url, app_key, file_id,
                    ).get("versionNumber", recorded.get("versionNumber", 1) + 1)
                else:
                    # 普通模式：覆盖（保留 fileId）
                    file_id = api.upload_local_file(
                        server_url, app_key, project_id,
                        folder_name, local_path,
                        update_file_id=update_file_id,
                    )
                    version_number = recorded.get("versionNumber", 1)
                state.mark_synced(state_data, rel_path, file_id, version_number, local_path)
                print(f"  ~ 更新: {rel_path} (fileId={file_id}, v{version_number})")
                result["updated"] += 1
            except Exception as e:
                print(f"  ✗ 更新失败: {rel_path}: {e}", file=sys.stderr)
                result["skipped"] += 1
        # else: 内容没变，跳过

    # 2) 删（云端有、本地没了）
    for rel_path in tracked:
        if rel_path not in local_files:
            recorded = state.get_recorded(state_data, rel_path)
            if recorded is None:
                continue
            if dry_run:
                print(f"  - [DRY] 删除: {rel_path} (云端 fileId={recorded.get('fileId')})")
                result["deleted"] += 1
                continue
            try:
                file_id = recorded["fileId"]
                api.delete_file(server_url, app_key, file_id, is_physical=False)
                state.mark_deleted(state_data, rel_path)
                print(f"  - 删除: {rel_path} (fileId={file_id})")
                result["deleted"] += 1
            except Exception as e:
                print(f"  ✗ 删除失败: {rel_path}: {e}", file=sys.stderr)
                result["skipped"] += 1

    return result


# === Pull 模式：云端 → 本地 ===

def do_pull(
    server_url: str,
    app_key: str,
    proj_cfg: dict,
    proj_root: Path,
    project_id: str,
    remote_root: str,
    state_data: dict,
    dry_run: bool = False,
    conflict: str = "local",
) -> dict:
    """云端 → 本地（全量列举子树 → 对比 state）。

    不用 listChanges + serverTime（容易把其他项目混进来），
    改用 getChildFiles 递归列举 remoteRoot 下的所有文件。

    返回 {"downloaded": int, "created": int, "deleted": int, "skipped": int}
    """
    result = {"downloaded": 0, "created": 0, "deleted": 0, "skipped": 0}

    # 1) 解析 remoteRoot 对应的 folderId
    # remoteRoot 是 .xgkb.json 里的字符串，如 "xgkb-sync-helper-test-2026-07-05"
    # 用 resolvePath 找它的 fileId
    try:
        folder_meta = api.resolve_path(
            server_url, app_key,
            root_file_id=0,
            path=remote_root,
            project_id=project_id,
        )
    except Exception as e:
        print(f"[xgkb-sync] 解析 remoteRoot 失败: {remote_root}: {e}",
              file=sys.stderr)
        return result

    if not folder_meta.get("exists"):
        print(f"[xgkb-sync] 云端 remoteRoot 不存在: {remote_root}",
              file=sys.stderr)
        return result

    folder_id = folder_meta.get("fileId")
    if not folder_id:
        print(f"[xgkb-sync] 解析到 remoteRoot 但 fileId 为空: {folder_meta}",
              file=sys.stderr)
        return result

    # 2) 递归列举文件夹下所有文件（DFS）
    cloud_files: dict = {}  # rel_path -> {fileId, name, type, parentPath}
    walk_ok = True

    def walk(parent_id: int, path_prefix: str) -> None:
        nonlocal walk_ok
        try:
            children = api.get_child_files(
                server_url, app_key, parent_id,
                file_type=None, order=6,  # 6=顺序名字
            )
        except Exception as e:
            print(f"  ✗ 列举子项失败 (parent={parent_id}): {e}",
                  file=sys.stderr)
            walk_ok = False
            return

        for item in children:
            child_id = item.get("id")
            child_name = item.get("name", "")
            child_type = item.get("type")
            if child_id is None or not child_name:
                continue
            child_path = f"{path_prefix}/{child_name}" if path_prefix else child_name

            if child_type == 1:
                # 文件夹 → 递归
                walk(child_id, child_path)
            elif child_type == 2:
                # 文件
                cloud_files[child_path] = {
                    "fileId": child_id,
                    "name": child_name,
                }

    walk(folder_id, "")
    if not walk_ok:
        print("[xgkb-sync] 云端枚举不完整，跳过本轮 pull 以避免部分写入或误删",
              file=sys.stderr)
        result["skipped"] += 1
        return result

    # 3) 对比 state，找出新增/更新/删除
    tracked = state.list_tracked_paths(state_data)
    cloud_set = set(cloud_files.keys())

    if tracked and not cloud_set:
        print("[xgkb-sync] 云端列表为空但本地存在已跟踪文件，跳过本地批量删除",
              file=sys.stderr)
        result["skipped"] += len(tracked)
        return result

    # 删除：state 有但云端没有（需要本地删）
    for rel_path in tracked:
        if rel_path not in cloud_set:
            if dry_run:
                print(f"  - [DRY] 删除: {rel_path}")
            else:
                local_file = proj_root / rel_path
                if local_file.exists():
                    local_file.unlink()
                state.mark_deleted(state_data, rel_path)
            result["deleted"] += 1

    # 创建/更新：云端有，本地没有或本地 hash 不同
    for rel_path in sorted(cloud_files.keys()):
        meta = cloud_files[rel_path]
        file_id = meta["fileId"]
        local_path = proj_root / rel_path

        if not is_supported_pull_text(rel_path):
            print(f"  ! 跳过非文本文件拉取: {rel_path}", file=sys.stderr)
            result["skipped"] += 1
            continue

        # 拉内容
        try:
            content_data = api.get_full_text_content(server_url, app_key, file_id)
            content_str = content_data.get("content", "")
        except Exception as e:
            print(f"  ✗ 拉取失败: {rel_path}: {e}", file=sys.stderr)
            result["skipped"] += 1
            continue

        is_new = not local_path.exists()
        local_hash = ""
        if not is_new:
            try:
                local_hash = state.hash_file(local_path)
            except OSError:
                pass

        recorded = state.get_recorded(state_data, rel_path)
        recorded_hash = recorded.get("contentHash") if recorded else ""
        cloud_hash = hash_text_content(content_str)

        if is_new:
            action = "创建"
        elif recorded is None:
            print(f"  ! 跳过未跟踪且本地已存在文件: {rel_path}", file=sys.stderr)
            result["skipped"] += 1
            continue
        elif cloud_hash == recorded_hash:
            action = None
        elif local_hash == recorded_hash:
            action = "更新"
        else:
            if conflict == "cloud":
                action = "更新"
            elif conflict == "skip":
                print(f"  ! 冲突跳过: {rel_path}", file=sys.stderr)
                result["skipped"] += 1
                continue
            else:
                print(f"  ! 冲突保留本地: {rel_path}", file=sys.stderr)
                result["skipped"] += 1
                continue

        if action is None:
            continue

        if dry_run:
            print(f"  ↓ [DRY] {action}: {rel_path}")
            result["downloaded" if action == "更新" else "created"] += 1
            continue

        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content_str, encoding="utf-8")

        # 记录到 state
        version_number = content_data.get("versionNumber", 1)
        state.mark_synced(state_data, rel_path, file_id, version_number, local_path)
        print(f"  {'+' if action == '创建' else '↓'} {action}: {rel_path} "
              f"(fileId={file_id})")
        if action == "创建":
            result["created"] += 1
        else:
            result["downloaded"] += 1

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="xgkb-sync-helper 全量双向同步",
    )
    parser.add_argument("path", help="项目路径")
    parser.add_argument(
        "--direction", "-d",
        choices=["push", "pull", "sync"],
        default="push",
        help="同步方向（默认 push）",
    )
    parser.add_argument(
        "--conflict", "-c",
        choices=["local", "cloud", "skip"],
        default="local",
        help="sync 模式冲突策略（默认 local-wins）",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="只看不执行",
    )
    args = parser.parse_args()

    local_root = Path(args.path).resolve()
    if not local_root.exists():
        print(f"[xgkb-sync] 路径不存在: {args.path}", file=sys.stderr)
        return 1
    if local_root.is_file():
        local_root = local_root.parent

    # 加载配置
    global_cfg, proj_cfg, proj_root = load_project_config(local_root)
    if proj_cfg is None or proj_root is None or not proj_cfg.get("enabled", False):
        print(f"[xgkb-sync] 项目未启用同步（缺 .xgkb.json 或 enabled=false）", file=sys.stderr)
        return 1

    app_key = global_cfg.get("appKey", "") or os.environ.get("XGKB_APPKEY", "")
    server_url = global_cfg.get("serverUrl", DEFAULT_SERVER_URL)
    if not app_key:
        print(f"[xgkb-sync] 未配置 appKey（~/.openclaw/.xgkb.json）", file=sys.stderr)
        return 1

    remote_root = proj_cfg.get("remoteRoot", "OpenClaw")
    state_data = state.load_state(remote_root, server_url, app_key, proj_root)

    # 解析 projectId
    try:
        project_id = api.resolve_project_id(server_url, app_key, proj_cfg)
    except Exception as e:
        print(f"[xgkb-sync] 解析 projectId 失败: {e}", file=sys.stderr)
        return 1
    state_data["projectId"] = project_id

    # 执行
    print(f"[xgkb-sync] 项目: {proj_root}  →  空间: {project_id}/{remote_root}/")
    print(f"[xgkb-sync] 方向: {args.direction}  冲突: {args.conflict}  干跑: {args.dry_run}")
    print()

    if args.direction == "push":
        r = do_push(server_url, app_key, proj_cfg, proj_root, project_id,
                    remote_root, state_data, dry_run=args.dry_run)
        print()
        print(f"[xgkb-sync] 📤 新增: {r['uploaded']}  📝 更新: {r['updated']}  "
              f"🗑️ 删除: {r['deleted']}  ⚠️ 跳过: {r['skipped']}")
    elif args.direction == "pull":
        r = do_pull(server_url, app_key, proj_cfg, proj_root, project_id,
                    remote_root, state_data, dry_run=args.dry_run,
                    conflict=args.conflict)
        print()
        print(f"[xgkb-sync] 📥 拉取: {r['downloaded']}  ✨ 创建: {r['created']}  "
              f"🗑️ 删除: {r['deleted']}  ⚠️ 跳过: {r['skipped']}")
    elif args.direction == "sync":
        # 先 pull 再 push
        print("--- pull ---")
        r1 = do_pull(server_url, app_key, proj_cfg, proj_root, project_id,
                     remote_root, state_data, dry_run=args.dry_run,
                     conflict=args.conflict)
        print(f"  拉取: {r1['downloaded']}  创建: {r1['created']}  删除: {r1['deleted']}")
        print()
        print("--- push ---")
        r2 = do_push(server_url, app_key, proj_cfg, proj_root, project_id,
                     remote_root, state_data, dry_run=args.dry_run)
        print(f"  新增: {r2['uploaded']}  更新: {r2['updated']}  删除: {r2['deleted']}")

    # 持久化 state
    if not args.dry_run:
        state.save_state(state_data)

    return 0


if __name__ == "__main__":
    sys.exit(main())

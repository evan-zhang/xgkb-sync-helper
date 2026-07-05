#!/usr/bin/env python3
"""
xgkb_push.py — 单文件 push 入口（v0.2，内部用 xgkb_client + xgkb_state）

用法（向后兼容 v0.1）:
  python3 xgkb_push.py <文件路径>
  python3 xgkb_push.py --stdin --name "note.md" --folder "TPR-Framework/notes" < content.md

退出码:
  0 — 成功 / 静默跳过 / 失败已写入重试队列（不阻断主流程）
  1 — 配置错误（非网络类）

依赖:
  - ~/.openclaw/.xgkb.json  — 全局配置（appKey + serverUrl）
  - 项目根/.xgkb.json       — 项目配置（enabled + remoteRoot + versionControl）

新能力（v0.2）:
  - 通过 xgkb-state 自动检测文件是否已同步过；已同步过则用 updateFileId 走版本更新
  - 若 .xgkb.json 启用 versionControl: true，每次 push 都成新版本
"""

import json
import os
import sys
import time
from pathlib import Path

# 复用新模块
import xgkb_client as api
import xgkb_state as state

DEFAULT_SERVER_URL = api.DEFAULT_SERVER_URL
MAX_FILE_SIZE = api.MAX_FILE_SIZE


# === 配置加载（保留旧 workspace 定位逻辑） ===

def get_workspace(file_path: Path | None = None) -> Path:
    ws = os.environ.get("OPENCLAW_WORKSPACE")
    if ws:
        return Path(ws)
    if file_path is not None:
        current = file_path.resolve()
        if current.is_file():
            current = current.parent
        for parent in [current] + list(current.parents):
            cfg_path = parent / ".xgkb.json"
            if cfg_path.exists():
                try:
                    with open(cfg_path) as f:
                        cfg = json.load(f)
                    if cfg.get("appKey"):
                        return parent
                except (json.JSONDecodeError, OSError):
                    pass
    return Path.home() / ".openclaw"


def get_agent_config_path(file_path: Path | None = None) -> Path:
    return get_workspace(file_path) / ".xgkb.json"


def get_retry_log_path(file_path: Path | None = None) -> Path:
    return get_workspace(file_path) / ".xgkb-retry.jsonl"


def find_project_config(start_path: Path):
    current = start_path.resolve()
    if current.is_file():
        current = current.parent
    for parent in [current] + list(current.parents):
        cfg_path = parent / ".xgkb.json"
        if cfg_path.exists():
            try:
                with open(cfg_path) as f:
                    return json.load(f), parent
            except (json.JSONDecodeError, OSError):
                continue
    return None, None


def load_global_config(file_path: Path | None = None) -> dict:
    config: dict = {}
    cfg_path = get_agent_config_path(file_path)
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    if not config.get("appKey"):
        config["appKey"] = os.environ.get("XGKB_APPKEY", "")
    if not config.get("serverUrl"):
        config["serverUrl"] = os.environ.get("XGKB_SERVER_URL", DEFAULT_SERVER_URL)
    return config


# === 重试队列 ===

def write_retry(file_path: str, folder_name: str, file_name: str, error: str,
                project_id: str = "", project_name: str = "") -> None:
    p = get_retry_log_path(Path(file_path) if file_path else None)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": int(time.time()),
        "file_path": file_path,
        "folder_name": folder_name,
        "file_name": file_name,
        "error": error,
        "retries": 0,
    }
    if project_id:
        entry["project_id"] = project_id
    if project_name:
        entry["project_name"] = project_name
    with open(p, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# === 主逻辑 ===

def push_file(file_path: str) -> None:
    path = Path(file_path).resolve()
    if not path.exists():
        print(f"[xgkb-push] 文件不存在: {file_path}", file=sys.stderr)
        return
    if path.stat().st_size > MAX_FILE_SIZE:
        print(f"[xgkb-push] 文件超过 10MB 限制: {file_path}", file=sys.stderr)
        return

    global_cfg = load_global_config(path)
    app_key = global_cfg.get("appKey", "")
    server_url = global_cfg.get("serverUrl", DEFAULT_SERVER_URL)
    if not app_key:
        print("[xgkb-push] 未配置 appKey，跳过", file=sys.stderr)
        return

    proj_cfg, proj_root = find_project_config(path)
    if proj_cfg is None or proj_root is None or not proj_cfg.get("enabled", False):
        print("[xgkb-push] 项目未启用同步，跳过")
        return

    remote_root = proj_cfg.get("remoteRoot", "OpenClaw")
    version_control = proj_cfg.get("versionControl", False)

    rel_path = str(path.relative_to(proj_root)).replace(os.sep, "/")
    folder_name = (f"{remote_root}/{rel_path.rsplit('/', 1)[0]}"
                   if "/" in rel_path else remote_root)

    # 查询 state 看是否已同步过
    state_data = state.load_state(remote_root)
    recorded = state.get_recorded(state_data, rel_path)
    update_file_id = recorded["fileId"] if recorded else None

    try:
        project_id = api.resolve_project_id(server_url, app_key, proj_cfg)
        project_name = proj_cfg.get("projectName", "") or \
            proj_cfg.get("projectId", "") or "个人知识库"

        version_remark = None
        version_name = None
        if version_control:
            version_remark = f"xgkb-push {time.strftime('%Y-%m-%d %H:%M:%S')}"
            version_name = f"v{int(time.time())}"

        file_id = api.upload_local_file(
            server_url, app_key, project_id, folder_name, path,
            update_file_id=update_file_id,
            version_remark=version_remark,
            version_name=version_name,
        )

        # 更新 state
        new_version = recorded.get("versionNumber", 0) + 1 if recorded and version_control else 1
        if not version_control and recorded:
            new_version = recorded.get("versionNumber", 1)
        state.mark_synced(state_data, rel_path, file_id, new_version, path)
        state.save_state(state_data)

        print(f"[xgkb-push] ✅ {path.name} → {project_name}/{folder_name}/ "
              f"(fileId={file_id}, v{new_version})")
    except Exception as e:
        error_msg = str(e)
        print(f"[xgkb-push] ❌ 同步失败: {error_msg}", file=sys.stderr)
        write_retry(str(path), folder_name, path.name, error_msg,
                    project_id=proj_cfg.get("projectId", ""),
                    project_name=proj_cfg.get("projectName", ""))


def push_stdin(name: str, folder: str, content: str) -> None:
    """stdin 模式（兼容 v0.1 行为）。"""
    global_cfg = load_global_config(None)
    app_key = global_cfg.get("appKey", "")
    server_url = global_cfg.get("serverUrl", DEFAULT_SERVER_URL)
    if not app_key:
        print("[xgkb-push] 未配置 appKey，跳过", file=sys.stderr)
        return

    suffix = name.rsplit(".", 1)[-1] if "." in name else "txt"

    try:
        proj_cfg: dict = {}
        proj_id = os.environ.get("XGKB_PROJECT_ID", "").strip()
        proj_name = os.environ.get("XGKB_PROJECT_NAME", "").strip()
        if proj_id:
            proj_cfg["projectId"] = proj_id
        elif proj_name:
            proj_cfg["projectName"] = proj_name

        project_id = api.resolve_project_id(server_url, app_key, proj_cfg)
        file_id = api.upload_content(
            server_url, app_key, project_id, folder, name, content, suffix,
        )
        print(f"[xgkb-push] ✅ {name} → {folder}/ (fileId={file_id})")
    except Exception as e:
        error_msg = str(e)
        print(f"[xgkb-push] ❌ 同步失败: {error_msg}", file=sys.stderr)
        write_retry(f"<stdin:{name}>", folder, name, error_msg)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: xgkb_push.py <文件路径>", file=sys.stderr)
        print("      xgkb_push.py --stdin --name <name> --folder <folder> < content",
              file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--stdin":
        name = ""
        folder = ""
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--name" and i + 1 < len(sys.argv):
                name = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--folder" and i + 1 < len(sys.argv):
                folder = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        if not name:
            print("[xgkb-push] --stdin 模式需要 --name 参数", file=sys.stderr)
            sys.exit(1)
        content = sys.stdin.read()
        push_stdin(name, folder, content)
    else:
        push_file(sys.argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
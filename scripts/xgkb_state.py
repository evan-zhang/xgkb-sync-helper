#!/usr/bin/env python3
"""
xgkb_state.py — xgkb-sync-helper 本地同步状态管理

在 ~/.openclaw/xgkb-state/ 下为每个项目维护一份 JSON 状态文件，
记录每个本地文件上次同步时的云端 fileId、versionNumber、contentHash，
实现增量同步：增删改可精确识别。

状态文件结构（每个项目一份）：
{
  "projectKey": "TPR-Framework",         # 来自 .xgkb.json 的 remoteRoot
  "remoteRoot": "TPR-Framework",         # 云端根目录名
  "projectId": "2025123456",             # 云端空间 ID（首次同步后缓存）
  "serverTime": 1714972812345,           # 上次同步的服务端时间（用于 listChanges since）
  "files": {
    "<rel_path>": {
      "fileId": 12345,                   # 云端 fileId
      "versionNumber": 3,                # 云端最新版本号
      "contentHash": "sha256:...",       # 本地文件 hash
      "mtime": 1714972812,               # 本地 mtime
      "lastSyncAt": 1714972812           # 同步时间
    }
  }
}
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional


STATE_DIR = Path.home() / ".openclaw" / "xgkb-state"


def _state_path(project_key: str) -> Path:
    """获取某项目的状态文件路径。

    project_key 中的特殊字符替换为安全字符（避免路径注入）。
    """
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in project_key)
    return STATE_DIR / f"{safe}.json"


def hash_file(path: Path) -> str:
    """计算本地文件 SHA-256。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load_state(project_key: str) -> dict:
    """加载项目状态。文件不存在则返回空状态骨架。"""
    p = _state_path(project_key)
    if not p.exists():
        return {
            "projectKey": project_key,
            "remoteRoot": project_key,
            "projectId": "",
            "serverTime": 0,
            "files": {},
        }
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # 状态文件损坏，退化为空
        return {
            "projectKey": project_key,
            "remoteRoot": project_key,
            "projectId": "",
            "serverTime": 0,
            "files": {},
        }


def save_state(state: dict) -> None:
    """保存项目状态（原子写：写临时文件再 rename）。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _state_path(state["projectKey"])
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    os.chmod(p, 0o600)


def mark_synced(
    state: dict,
    rel_path: str,
    file_id: int,
    version_number: int,
    local_path: Path,
) -> None:
    """记录一个本地文件已同步到云端。"""
    try:
        mtime = int(local_path.stat().st_mtime)
        content_hash = hash_file(local_path)
    except OSError:
        mtime = 0
        content_hash = ""
    state["files"][rel_path] = {
        "fileId": int(file_id),
        "versionNumber": int(version_number),
        "contentHash": content_hash,
        "mtime": mtime,
        "lastSyncAt": int(time.time()),
    }


def mark_deleted(state: dict, rel_path: str) -> None:
    """记录一个本地文件已被同步删除（云端也删了）。"""
    if rel_path in state["files"]:
        del state["files"][rel_path]


def get_recorded(state: dict, rel_path: str) -> Optional[dict]:
    """获取上次同步时记录的元信息。"""
    return state["files"].get(rel_path)


def list_tracked_paths(state: dict) -> list[str]:
    """列出所有已同步过的文件路径。"""
    return list(state["files"].keys())
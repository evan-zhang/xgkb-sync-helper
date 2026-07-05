#!/usr/bin/env python3
"""
xgkb_state_sqlite.py — xgkb-sync-helper 本地同步状态管理（SQLite 实现，v2.1）

相比 v2.0（JSON）的关键改进：

1. 并发安全：WAL 模式 + busy_timeout=5000ms，支持多进程/多 agent 同时
   push/pull（之前 JSON 后写覆盖前写，数据错乱）

2. 跨项目隔离：project_key 改成 sha256(serverUrl+appKey+remoteRoot+abs_proj_root)
   彻底解决"两个项目都用 remoteRoot='foo'"导致的 state 互相覆盖

3. 原子事务：每次 mark_synced/mark_deleted 是一个事务，要么全成功要么全回滚
   （之前 JSON 写一半崩溃会留下损坏文件）

4. 同 dict 接口：load_state() 仍然返回 dict（从内存缓存），
   调用方零改动；只是在 mark_* 时既更新内存也写 SQLite。

向后兼容：
- load_state(remote_root) 仍然工作（仅传 remote_root 时用旧 key 公式）
- 旧 state JSON 文件不会被自动迁移——运行 migrate_json_to_sqlite.py 手动迁移
"""

from __future__ import annotations

import atexit
import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

STATE_DIR = Path.home() / ".openclaw" / "xgkb-state"

INIT_SCHEMA_SQL = '''
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS files (
    rel_path        TEXT PRIMARY KEY,
    file_id         INTEGER NOT NULL,
    version_number  INTEGER,
    content_hash    TEXT,
    mtime           INTEGER,
    last_sync_at    INTEGER
);
CREATE TABLE IF NOT EXISTS retry_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path      TEXT,
    op            TEXT,
    payload       TEXT,
    attempts      INTEGER DEFAULT 0,
    last_error    TEXT,
    next_retry_at INTEGER,
    created_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_files_file_id ON files(file_id);
CREATE INDEX IF NOT EXISTS idx_retry_next ON retry_queue(next_retry_at);
'''


def make_project_key(
    server_url: str,
    app_key: str,
    remote_root: str,
    proj_root: str | Path,
) -> str:
    '''根据 (serverUrl, appKey, remoteRoot, abs_proj_root) 生成唯一 key。

    这是项目级的真正唯一标识——同一个 remoteRoot 用在不同项目下不冲突。
    返回 hex 字符串（64 字符）。
    '''
    abs_proj = str(Path(proj_root).resolve())
    salt = 'xgkb-state-v1|'
    raw = f'{salt}{server_url}|{app_key}|{remote_root}|{abs_proj}'
    return hashlib.sha256(raw.encode()).hexdigest()


def _db_path_for(project_key: str) -> Path:
    '''根据 project_key 推 DB 路径。'''
    safe = project_key[:32]
    return STATE_DIR / f'{safe}.db'


_conn_cache: dict[str, sqlite3.Connection] = {}


def _get_conn(project_key: str) -> sqlite3.Connection:
    '''获取某个项目的 SQLite 连接（带 WAL + busy_timeout）。'''
    if project_key in _conn_cache:
        conn = _conn_cache[project_key]
        try:
            conn.execute('SELECT 1')
            return conn
        except sqlite3.ProgrammingError:
            _conn_cache.pop(project_key, None)

    db_path = _db_path_for(project_key)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(INIT_SCHEMA_SQL)
    _conn_cache[project_key] = conn
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    '''初始化 schema（幂等）。'''
    conn.executescript(INIT_SCHEMA_SQL)


def hash_file(path: Path) -> str:
    '''计算本地文件 SHA-256。'''
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def load_state(
    remote_root: str,
    server_url: str = '',
    app_key: str = '',
    proj_root: str | Path = '',
) -> dict:
    '''加载项目状态。返回 dict（与 v2.0 接口兼容）。

    调用方式有两种：
      1. 新（推荐）：load_state(remote_root, server_url, app_key, proj_root)
         → project_key 用 sha256(server+app+remote+abs_path)
      2. 旧（兼容）：load_state(remote_root) 只传 remote_root
         → project_key = remote_root（与 v2.0 行为一致）
    '''
    if proj_root and server_url and app_key:
        project_key = make_project_key(server_url, app_key, remote_root, proj_root)
    else:
        project_key = remote_root

    conn = _get_conn(project_key)

    meta: dict[str, str] = {}
    for row in conn.execute('SELECT key, value FROM meta'):
        meta[row[0]] = row[1]

    if 'schema_version' not in meta:
        with _atomic(conn):
            conn.execute(
                'INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)',
                ('schema_version', str(SCHEMA_VERSION)),
            )

    files: dict[str, dict] = {}
    for row in conn.execute(
        'SELECT rel_path, file_id, version_number, content_hash, mtime, last_sync_at '
        'FROM files'
    ):
        files[row[0]] = {
            'fileId': int(row[1]),
            'versionNumber': int(row[2]) if row[2] is not None else 1,
            'contentHash': row[3] or '',
            'mtime': int(row[4]) if row[4] is not None else 0,
            'lastSyncAt': int(row[5]) if row[5] is not None else 0,
        }

    state = {
        'projectKey': project_key,
        'remoteRoot': meta.get('remoteRoot', remote_root),
        'projectId': meta.get('projectId', ''),
        'serverTime': int(meta.get('serverTime', '0') or 0),
        '_db_project_key': project_key,
        'files': files,
    }
    return state


def save_state(state: dict) -> None:
    '''保存项目状态。v2.1 时 mark_* 已自动持久化，此函数为兼容保留。'''
    project_key = state.get('_db_project_key') or state.get('projectKey', '')
    if not project_key:
        return
    conn = _get_conn(project_key)
    with _atomic(conn):
        for k in ('projectId', 'remoteRoot', 'serverTime'):
            if k in state and state[k] != '':
                conn.execute(
                    'INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
                    (k, str(state[k])),
                )


def mark_synced(
    state: dict,
    rel_path: str,
    file_id: int,
    version_number: int,
    local_path: Path,
) -> None:
    '''记录一个本地文件已同步到云端。'''
    project_key = state.get('_db_project_key', '')
    if not project_key:
        try:
            mtime = int(local_path.stat().st_mtime)
            content_hash = hash_file(local_path)
        except OSError:
            mtime = 0
            content_hash = ''
        state['files'][rel_path] = {
            'fileId': int(file_id),
            'versionNumber': int(version_number),
            'contentHash': content_hash,
            'mtime': mtime,
            'lastSyncAt': int(time.time()),
        }
        return

    conn = _get_conn(project_key)
    try:
        mtime = int(local_path.stat().st_mtime)
        content_hash = hash_file(local_path)
    except OSError:
        mtime = 0
        content_hash = ''
    last_sync_at = int(time.time())

    with _atomic(conn):
        conn.execute(
            'INSERT OR REPLACE INTO files'
            '(rel_path, file_id, version_number, content_hash, mtime, last_sync_at) '
            'VALUES(?,?,?,?,?,?)',
            (rel_path, int(file_id), int(version_number), content_hash, mtime, last_sync_at),
        )

    state['files'][rel_path] = {
        'fileId': int(file_id),
        'versionNumber': int(version_number),
        'contentHash': content_hash,
        'mtime': mtime,
        'lastSyncAt': last_sync_at,
    }


def mark_deleted(state: dict, rel_path: str) -> None:
    '''记录一个本地文件已被同步删除（云端也删了）。'''
    project_key = state.get('_db_project_key', '')
    if project_key:
        conn = _get_conn(project_key)
        with _atomic(conn):
            conn.execute('DELETE FROM files WHERE rel_path=?', (rel_path,))
    if rel_path in state['files']:
        del state['files'][rel_path]


def get_recorded(state: dict, rel_path: str) -> Optional[dict]:
    '''获取上次同步时记录的元信息。'''
    return state['files'].get(rel_path)


def list_tracked_paths(state: dict) -> list[str]:
    '''列出所有已同步过的文件路径。'''
    return list(state['files'].keys())


class _atomic:
    '''轻量事务上下文管理器（WAL 模式下 begin/end 显式提交）。'''

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute('BEGIN')
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.execute('COMMIT')
        else:
            self.conn.execute('ROLLBACK')
            raise


def close_all() -> None:
    '''关闭所有缓存的连接（用于进程退出时）。'''
    for conn in list(_conn_cache.values()):
        try:
            conn.close()
        except Exception:
            pass
    _conn_cache.clear()


atexit.register(close_all)


def cmd_inspect(remote_root: str, server_url: str, app_key: str, proj_root: str) -> int:
    '''打印某项目 state 的诊断信息。'''
    state = load_state(remote_root, server_url, app_key, proj_root)
    print(f'project_key (hash) = {state["_db_project_key"][:16]}...')
    print(f'remoteRoot        = {state["remoteRoot"]}')
    print(f'projectId         = {state["projectId"]}')
    print(f'serverTime        = {state["serverTime"]}')
    print(f'tracked files     = {len(state["files"])}')
    print()
    for rel_path, rec in list(state['files'].items())[:5]:
        print(f'  {rel_path:50s} fileId={rec["fileId"]} v{rec["versionNumber"]} mtime={rec["mtime"]}')
    if len(state['files']) > 5:
        print(f'  ... ({len(state["files"]) - 5} more)')
    return 0


if __name__ == '__main__':
    if len(sys.argv) >= 6:
        _, cmd, server_url, app_key, remote_root, proj_root = sys.argv[:6]
        if cmd == 'inspect':
            sys.exit(cmd_inspect(remote_root, server_url, app_key, proj_root))
    print('用法: xgkb_state_sqlite.py inspect <serverUrl> <appKey> <remoteRoot> <projRoot>',
          file=sys.stderr)
    sys.exit(1)

#!/usr/bin/env python3
"""
xgkb_push.py — 将单个文件同步到玄关个人知识库

用法:
  python3 xgkb_push.py <文件路径>
  python3 xgkb_push.py --stdin --name "note.md" --folder "TPR-Framework/notes" < content.md

退出码:
  0 — 成功 / 静默跳过 / 失败已写入重试队列（不阻断主流程）
  1 — 配置错误（非网络类）
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# === 配置查找 ===

GLOBAL_CONFIG_PATH = Path.home() / ".openclaw" / "xgkb.json"
RETRY_LOG_PATH = Path.home() / ".openclaw" / "xgkb-retry.jsonl"
DEFAULT_SERVER_URL = "https://sg-al-cwork-web.mediportal.com.cn/open-api/"
DEFAULT_REMOTE_ROOT = "OpenClaw"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def find_project_config(start_path: Path) -> dict | None:
    """向上查找最近的 .xgkb.json"""
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


def load_global_config() -> dict:
    """加载全局配置"""
    config = {}
    if GLOBAL_CONFIG_PATH.exists():
        try:
            with open(GLOBAL_CONFIG_PATH) as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # 环境变量兜底
    if not config.get("appKey"):
        config["appKey"] = os.environ.get("XGKB_APPKEY", "")
    if not config.get("serverUrl"):
        config["serverUrl"] = os.environ.get("XGKB_SERVER_URL", DEFAULT_SERVER_URL)

    return config


# === API 调用 ===

_project_id_cache = None


def api_call(server_url: str, app_key: str, path: str, method: str = "GET", body: dict = None) -> dict:
    """调用玄关知识库 API"""
    url = server_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {"appKey": app_key}

    data = None
    if method == "GET" and body:
        qs = "&".join(f"{k}={v}" for k, v in body.items() if v is not None)
        url = f"{url}?{qs}"
    elif method == "POST" and body:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    # 显式编码 header 值为 ASCII（appKey 应为纯 ASCII）
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
        result = json.loads(raw)
        if result.get("resultCode") != 1:
            raise RuntimeError(f"API error: code={result.get('resultCode')} msg={result.get('resultMsg')}")
        return result.get("data")


def get_project_id(server_url: str, app_key: str) -> str:
    global _project_id_cache
    if _project_id_cache:
        return _project_id_cache
    pid = api_call(server_url, app_key, "/document-database/project/personal/getProjectId")
    pid = str(pid)
    _project_id_cache = pid
    return pid


def upload_content(server_url: str, app_key: str, project_id: str,
                   folder_name: str, file_name: str, content: str, suffix: str) -> dict:
    """上传/更新文件（幂等：同名覆盖）"""
    return api_call(server_url, app_key, "/document-database/file/uploadContent", method="POST", body={
        "projectId": project_id,
        "folderName": folder_name,
        "fileName": file_name,
        "content": content,
        "suffix": suffix,
    })


# === 重试队列 ===

def write_retry(file_path: str, folder_name: str, file_name: str, error: str):
    """写入重试队列"""
    RETRY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": int(time.time()),
        "file_path": file_path,
        "folder_name": folder_name,
        "file_name": file_name,
        "error": error,
        "retries": 0,
    }
    with open(RETRY_LOG_PATH, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# === 主逻辑 ===

def push_file(file_path: str):
    """同步单个文件到知识库"""
    path = Path(file_path).resolve()
    if not path.exists():
        print(f"[xgkb-push] 文件不存在: {file_path}", file=sys.stderr)
        return

    if path.stat().st_size > MAX_FILE_SIZE:
        print(f"[xgkb-push] 文件超过 10MB 限制: {file_path}", file=sys.stderr)
        return

    # 读文件内容
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"[xgkb-push] 非文本文件，跳过: {file_path}", file=sys.stderr)
        return

    # 加载配置
    global_cfg = load_global_config()
    app_key = global_cfg.get("appKey", "")
    server_url = global_cfg.get("serverUrl", DEFAULT_SERVER_URL)

    if not app_key:
        print("[xgkb-push] 未配置 appKey，跳过", file=sys.stderr)
        return

    proj_cfg, proj_root = find_project_config(path)
    if proj_cfg is None or not proj_cfg.get("enabled", False):
        print("[xgkb-push] 项目未启用同步，跳过")
        return

    remote_root = proj_cfg.get("remoteRoot", DEFAULT_REMOTE_ROOT)

    # 计算知识库目标路径：remoteRoot/项目目录名/相对路径
    # proj_root 是 .xgkb.json 所在目录（即 projects/），文件相对路径已包含项目目录名
    if proj_root:
        rel_path = path.relative_to(proj_root)
        rel_parts = str(rel_path).replace("\\", "/")
        full_remote_path = f"{remote_root}/{rel_parts}"
    else:
        full_remote_path = f"{remote_root}/{path.name}"

    # 拆分为 folderName 和 fileName
    parts = full_remote_path.rsplit("/", 1)
    folder_name = parts[0] if len(parts) > 1 else remote_root
    file_name = parts[-1]
    suffix = file_name.rsplit(".", 1)[-1] if "." in file_name else "txt"

    # 上传
    try:
        project_id = get_project_id(server_url, app_key)
        result = upload_content(server_url, app_key, project_id, folder_name, file_name, content, suffix)
        file_id = result.get("fileId", "") if isinstance(result, dict) else str(result)
        print(f"[xgkb-push] ✅ {file_name} → {folder_name}/ (fileId={file_id})")
    except Exception as e:
        error_msg = str(e)
        print(f"[xgkb-push] ❌ 同步失败: {error_msg}", file=sys.stderr)
        write_retry(str(path), folder_name, file_name, error_msg)


def push_stdin(name: str, folder: str, content: str):
    """从 stdin 读取内容并上传"""
    global_cfg = load_global_config()
    app_key = global_cfg.get("appKey", "")
    server_url = global_cfg.get("serverUrl", DEFAULT_SERVER_URL)

    if not app_key:
        print("[xgkb-push] 未配置 appKey，跳过", file=sys.stderr)
        return

    suffix = name.rsplit(".", 1)[-1] if "." in name else "txt"

    try:
        project_id = get_project_id(server_url, app_key)
        result = upload_content(server_url, app_key, project_id, folder, name, content, suffix)
        file_id = result.get("fileId", "") if isinstance(result, dict) else str(result)
        print(f"[xgkb-push] ✅ {name} → {folder}/ (fileId={file_id})")
    except Exception as e:
        error_msg = str(e)
        print(f"[xgkb-push] ❌ 同步失败: {error_msg}", file=sys.stderr)
        write_retry(f"<stdin:{name}>", folder, name, error_msg)


def main():
    if len(sys.argv) < 2:
        print("用法: xgkb_push.py <文件路径>", file=sys.stderr)
        print("      xgkb_push.py --stdin --name <name> --folder <folder> < content", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--stdin":
        name = ""
        folder = DEFAULT_REMOTE_ROOT
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


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
xgkb_upload_file.py — 通过物理文件分片上传接口同步大文件到玄关知识库
适用于 uploadContent 接口因 body 过大失败的场景。

流程：bytes → 分片(5MB) → getSliceIdByMd5V2 → [PUT MinIO → uploadFileSliceV2] → saveResource → saveFileByPath

用法：
  python3 xgkb_upload_file.py <文件路径> [--folder "Obsidian/日常学习/CMS组织架构"]
"""

import json
import os
import sys
import hashlib
import time
import urllib.request
import urllib.error
from pathlib import Path

GLOBAL_CONFIG_PATH = Path.home() / ".openclaw" / "xgkb.json"
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB
DEFAULT_SERVER_URL = "https://sg-al-cwork-web.mediportal.com.cn/open-api/"
VERSION_REMARK = "xgkb-upload-file.py"

_project_id_cache = None


def load_config():
    config = {}
    if GLOBAL_CONFIG_PATH.exists():
        with open(GLOBAL_CONFIG_PATH) as f:
            config = json.load(f)
    if not config.get("appKey"):
        config["appKey"] = os.environ.get("XGKB_APPKEY", "")
    if not config.get("serverUrl"):
        config["serverUrl"] = DEFAULT_SERVER_URL
    return config


def find_project_config(start_path):
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


def api_call(server_url, app_key, path, method="GET", body=None, timeout=60):
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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        result = json.loads(raw)
        if result.get("resultCode") != 1:
            raise RuntimeError(f"API error: code={result.get('resultCode')} msg={result.get('resultMsg')}")
        return result.get("data")


def get_project_id(server_url, app_key):
    global _project_id_cache
    if _project_id_cache:
        return _project_id_cache
    pid = api_call(server_url, app_key, "/document-database/project/personal/getProjectId")
    pid = str(pid)
    _project_id_cache = pid
    return pid


def md5_bytes(data):
    return hashlib.md5(data).hexdigest()


def upload_file(file_path):
    path = Path(file_path).resolve()
    if not path.exists():
        print(f"文件不存在: {file_path}", file=sys.stderr)
        return False

    content_bytes = path.read_bytes()
    total_size = len(content_bytes)
    file_name = path.name
    suffix = file_name.rsplit(".", 1)[-1] if "." in file_name else "txt"

    print(f"[xgkb-upload] {file_name}: {total_size:,} bytes, suffix={suffix}")

    # Config
    global_cfg = load_config()
    app_key = global_cfg["appKey"]
    server_url = global_cfg["serverUrl"]

    proj_cfg, proj_root = find_project_config(path)
    if proj_cfg is None or not proj_cfg.get("enabled", False):
        print("[xgkb-upload] 项目未启用同步，跳过")
        return False

    remote_root = proj_cfg.get("remoteRoot", "OpenClaw")
    if proj_root:
        rel_path = path.relative_to(proj_root)
        full_remote_path = f"{remote_root}/{rel_path}".replace("\\", "/")
    else:
        full_remote_path = f"{remote_root}/{file_name}"

    parts = full_remote_path.rsplit("/", 1)
    folder_name = parts[0] if len(parts) > 1 else remote_root
    # folder_name should be the path without the filename
    # Remove the filename from the last part
    remote_file_name = parts[-1]

    print(f"[xgkb-upload] 目标: {folder_name}/{remote_file_name}")

    project_id = get_project_id(server_url, app_key)
    print(f"[xgkb-upload] projectId: {project_id}")

    # Step 1: Split into chunks and upload
    chunk_count = max(1, (total_size + CHUNK_SIZE - 1) // CHUNK_SIZE)
    slice_ids = []

    for i in range(chunk_count):
        start = i * CHUNK_SIZE
        end = min(start + CHUNK_SIZE, total_size)
        chunk = content_bytes[start:end]
        chunk_md5 = md5_bytes(chunk)
        chunk_size = len(chunk)

        print(f"[xgkb-upload] 分片 {i+1}/{chunk_count}: {chunk_size:,} bytes, md5={chunk_md5[:16]}...")

        # Check if slice already exists
        slice_data = api_call(server_url, app_key, "/document-database/file/getSliceIdByMd5V2", method="GET", body={
            "md5": chunk_md5,
            "size": chunk_size,
            "suffix": suffix,
        })

        if slice_data.get("sliceId"):
            print(f"[xgkb-upload]   ✅ 秒传 (sliceId={slice_data['sliceId']})")
            slice_ids.append(slice_data["sliceId"])
            continue

        upload_url = slice_data.get("uploadUrl")
        full_path = slice_data.get("fullPath")
        storage_type = slice_data.get("storageType", "MINIO")

        if not upload_url:
            print(f"[xgkb-upload]   ❌ 无 uploadUrl", file=sys.stderr)
            return False

        # PUT to MinIO
        print(f"[xgkb-upload]   上传到 MinIO...")
        req = urllib.request.Request(upload_url, data=chunk, method="PUT")
        req.add_header("Content-Type", "application/octet-stream")
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status < 200 or resp.status >= 300:
                print(f"[xgkb-upload]   ❌ MinIO PUT failed: HTTP {resp.status}", file=sys.stderr)
                return False

        # Register slice
        register_result = api_call(server_url, app_key, "/document-database/file/uploadFileSliceV2", method="POST", body={
            "filePath": full_path,
            "md5": chunk_md5,
            "size": chunk_size,
            "storageType": storage_type,
        })

        if isinstance(register_result, dict):
            slice_id = register_result.get("sliceId", register_result.get("id"))
        else:
            slice_id = register_result
        print(f"[xgkb-upload]   ✅ 已注册 (sliceId={slice_id})")
        slice_ids.append(slice_id)

    # Step 2: Merge slices into resource
    print(f"[xgkb-upload] 合并 {len(slice_ids)} 个分片...")
    resource_id = api_call(server_url, app_key, "/document-database/file/saveResource", method="POST", body={
        "name": file_name,
        "sliceIds": slice_ids,
        "suffix": suffix,
        "size": total_size,
    })
    print(f"[xgkb-upload] ✅ resourceId={resource_id}")

    # Step 3: Save file by path in knowledge base
    print(f"[xgkb-upload] 创建知识库文件: {folder_name}/{remote_file_name}")
    result = api_call(server_url, app_key, "/document-database/file/saveFileByPath", method="POST", body={
        "projectId": project_id,
        "path": folder_name,
        "name": remote_file_name,
        "fileType": "file",
        "suffix": suffix,
        "size": total_size,
        "resourceId": resource_id,
        "nameConflictStrategy": 1,  # overwrite
    })

    file_id = str(result) if not isinstance(result, dict) else result.get("fileId", str(result))
    print(f"[xgkb-upload] ✅ {file_name} → {folder_name}/ (fileId={file_id})")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 xgkb_upload_file.py <文件路径>", file=sys.stderr)
        sys.exit(1)

    success = upload_file(sys.argv[1])
    sys.exit(0 if success else 1)

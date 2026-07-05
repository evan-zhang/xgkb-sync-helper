#!/usr/bin/env python3
"""
xgkb_client.py — 玄关知识库 API 客户端

集中所有玄关知识库 API 调用，供 xgkb_push / xgkb_pull / xgkb_sync_full / xgkb_versions 共用。

参考规范：
  - xgjk/dev-guide (02.产品业务AI文档/知识库/API接口明细_v2)
  - 共支持 13 个 API，覆盖：上传/版本/检索/删除/改名/移动
"""

import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Optional

DEFAULT_SERVER_URL = "https://sg-al-cwork-web.mediportal.com.cn/open-api/"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# 文本文件扩展名（脚本内置白名单）
TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".html", ".htm",
    ".csv", ".xml", ".log", ".py", ".js", ".ts", ".sh", ".sql"
}


# === 底层 HTTP 调用 ===

def api_call(
    server_url: str,
    app_key: str,
    path: str,
    method: str = "GET",
    body: Optional[dict] = None,
    timeout: int = 60,
) -> dict:
    """调用玄关知识库 API（统一封装）。

    返回 data 字段（已解包）；非 resultCode=1 抛 RuntimeError。
    """
    url = server_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {"appKey": app_key}

    data = None
    if method == "GET" and body:
        qs = urllib.parse.urlencode(
            {k: v for k, v in body.items() if v is not None}
        )
        url = f"{url}?{qs}"
    elif method == "POST" and body:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        result = json.loads(raw)
        if result.get("resultCode") != 1:
            raise RuntimeError(
                f"API error: code={result.get('resultCode')} "
                f"msg={result.get('resultMsg')}"
            )
        return result.get("data")


# === 空间管理（1.x） ===

_project_id_cache: dict = {}


def get_personal_project_id(server_url: str, app_key: str) -> str:
    """1.1 获取个人知识库空间 ID"""
    pid = api_call(
        server_url, app_key,
        "/document-database/project/personal/getProjectId"
    )
    return str(pid)


def list_projects(server_url: str, app_key: str) -> list[dict]:
    """1.2 获取有权限访问的空间列表"""
    data = api_call(server_url, app_key, "/document-database/project/list")
    return data if isinstance(data, list) else []


def resolve_project_id(
    server_url: str, app_key: str, proj_cfg: dict
) -> str:
    """解析目标知识库 projectId。

    优先级：
      1. proj_cfg.projectId — 直接指定 ID
      2. proj_cfg.projectName — 按名称查找
      3. 默认 — 个人知识库
    """
    explicit_id = str(proj_cfg.get("projectId", "")).strip()
    if explicit_id:
        return explicit_id

    project_name = str(proj_cfg.get("projectName", "")).strip()
    if project_name:
        cache_key = f"name:{project_name}"
        if cache_key in _project_id_cache:
            return _project_id_cache[cache_key]
        projects = list_projects(server_url, app_key)
        for p in projects:
            if p.get("name") == project_name:
                pid = str(p["id"])
                _project_id_cache[cache_key] = pid
                return pid
        raise RuntimeError(f"未找到名为「{project_name}」的知识库空间")

    return get_personal_project_id(server_url, app_key)


def get_child_files(
    server_url: str,
    app_key: str,
    parent_id: int,
    file_type: Optional[int] = None,
    order: Optional[int] = None,
    include_path: bool = False,
) -> list[dict]:
    """1.5 根据父ID获取下级目录及文件列表。

    file_type: 1=只查文件夹, 2=只查文件, None=全部
    order: 1=倒序更新 2=顺序更新 3=倒序创建 4=顺序创建 5=倒序名字 6=顺序名字
    """
    params: dict = {"parentId": parent_id}
    if file_type is not None:
        params["type"] = file_type
    if order is not None:
        params["order"] = order
    if include_path:
        params["returnFileDesc"] = True
    data = api_call(
        server_url, app_key,
        "/document-database/file/getChildFiles",
        method="GET", body=params,
    )
    return data if isinstance(data, list) else []


def list_changes(
    server_url: str,
    app_key: str,
    project_id: Optional[str] = None,
    root_file_id: int = 0,
    since: Optional[int] = None,
    cursor: Optional[str] = None,
    limit: int = 200,
    include_path: bool = True,
    include_move_hint: bool = True,
) -> dict:
    """1.7 增量变更列表（双向同步核心 API）。

    返回 {"items": [...], "nextCursor": str|None, "serverTime": int}
    每个 item 含 fileId, parentId, type, name, updateTime, event(upsert/delete),
    可选 relativePath / previousName / previousParentId。
    """
    params: dict = {"rootFileId": root_file_id, "limit": limit}
    if project_id is not None:
        params["projectId"] = project_id
    if since is not None:
        params["since"] = since
    if cursor is not None:
        params["cursor"] = cursor
    if include_path:
        params["includePath"] = True
    if include_move_hint:
        params["includeMoveHint"] = True
    data = api_call(
        server_url, app_key,
        "/document-database/file/listChanges",
        method="GET", body=params,
    )
    if not isinstance(data, dict):
        return {"items": [], "nextCursor": None, "serverTime": 0}
    return data


def resolve_path(
    server_url: str,
    app_key: str,
    root_file_id: int,
    path: str,
    project_id: Optional[str] = None,
) -> dict:
    """1.14 按相对路径解析 fileId。

    返回 {"exists": bool, "fileId": int|None, "type": int|None}
    """
    params: dict = {"rootFileId": root_file_id, "path": path}
    if project_id is not None:
        params["projectId"] = project_id
    data = api_call(
        server_url, app_key,
        "/document-database/file/resolvePath",
        method="GET", body=params,
    )
    if not isinstance(data, dict):
        return {"exists": False, "fileId": None, "type": None}
    return data


def get_file_basic_info(
    server_url: str, app_key: str, file_id: int,
) -> dict:
    """1.17 获取文件/文件夹基本信息（轻量版）。"""
    data = api_call(
        server_url, app_key,
        "/document-database/file/getFileBasicInfo",
        method="GET", body={"fileId": file_id},
    )
    return data if isinstance(data, dict) else {}


# === 写操作（删除/改名/移动） ===

def delete_file(
    server_url: str, app_key: str, file_id: int, is_physical: bool = False
) -> bool:
    """1.12 删除文件。

    is_physical: True=物理彻底删除（不可恢复）, False=移入回收站（默认）
    """
    data = api_call(
        server_url, app_key,
        "/document-database/file/deleteFile",
        method="POST",
        body={"fileId": file_id, "isPhysical": is_physical},
    )
    return bool(data)


def update_file_name(
    server_url: str,
    app_key: str,
    file_id: int,
    new_name: str,
    name_conflict_strategy: int = 1,
    project_id: Optional[str] = None,
) -> bool:
    """1.13a 同目录改名。"""
    body = {
        "fileId": file_id,
        "newName": new_name,
        "nameConflictStrategy": name_conflict_strategy,
    }
    if project_id:
        body["projectId"] = project_id
    data = api_call(
        server_url, app_key,
        "/document-database/file/updateFileName",
        method="POST", body=body,
    )
    return bool(data)


def move_file(
    server_url: str,
    app_key: str,
    file_id: int,
    target_parent_id: int,
    name_conflict_strategy: int = 2,
    project_id: Optional[str] = None,
) -> dict:
    """1.13b 移动节点。

    name_conflict_strategy: 0=自动重命名 1=报错 2=覆盖(默认) 3=跳过
    """
    body = {
        "fileId": file_id,
        "targetParentId": target_parent_id,
        "nameConflictStrategy": name_conflict_strategy,
    }
    if project_id:
        body["projectId"] = project_id
    data = api_call(
        server_url, app_key,
        "/document-database/file/moveFile",
        method="POST", body=body,
    )
    return data if isinstance(data, dict) else {"success": bool(data)}


# === 上传 / 版本（2.x + 3.x） ===

def upload_binary_resource(
    server_url: str,
    app_key: str,
    file_path: str,
    file_name: str,
) -> str:
    """上传物理二进制文件，返回 resourceId。

    内部调用 /cwork-file/uploadWholeFile（multipart/form-data）。
    """
    url = server_url.rstrip("/") + "/cwork-file/uploadWholeFile"
    boundary = f"----xgkb{int(time.time() * 1000)}"
    with open(file_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST", headers={
        "appKey": app_key,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("resultCode") != 1:
            raise RuntimeError(
                f"uploadWholeFile error: {result.get('resultMsg')}"
            )
        return str(result["data"])


def save_file_by_path(
    server_url: str,
    app_key: str,
    project_id: str,
    folder_path: str,
    file_name: str,
    resource_id: str,
    suffix: str,
    size: int,
    name_conflict_strategy: int = 1,
) -> int:
    """2.3 根据路径保存文件到项目目录（绑定物理资源）。

    返回 fileId。"""
    data = api_call(
        server_url, app_key,
        "/document-database/file/saveFileByPath",
        method="POST",
        body={
            "projectId": str(project_id),
            "path": folder_path,
            "name": file_name,
            "fileType": "file",
            "resourceId": str(resource_id),
            "suffix": suffix,
            "size": size,
            "nameConflictStrategy": name_conflict_strategy,
        },
    )
    # 响应可能是裸 Long，也可能是 {"fileId": ..., ...} 的 dict
    if isinstance(data, dict):
        return int(data.get("fileId") or data.get("data") or 0)
    return int(data)


def upload_content(
    server_url: str,
    app_key: str,
    project_id: Optional[str],
    folder_name: str,
    file_name: str,
    content: str,
    suffix: str,
    update_file_id: Optional[int] = None,
    version_remark: Optional[str] = None,
    version_name: Optional[str] = None,
    name_conflict_strategy: int = 1,
) -> int:
    """3.1 一键快速保存纯文本。

    - update_file_id 不传 → 新建/覆盖（同名）
    - update_file_id 传入 → 版本更新模式

    name_conflict_strategy: 0=自动重命名 1=覆盖(默认,保留原ID新增版本) 2=报错
    返回 fileId。
    """
    body: dict = {
        "fileName": file_name,
        "fileSuffix": suffix,
        "content": content,
        "nameConflictStrategy": name_conflict_strategy,
    }
    if project_id is not None:
        body["projectId"] = project_id
    if folder_name != "":
        body["folderName"] = folder_name
    if update_file_id is not None:
        body["updateFileId"] = update_file_id
    if version_remark is not None:
        body["versionRemark"] = version_remark
    if version_name is not None:
        body["versionName"] = version_name

    data = api_call(
        server_url, app_key,
        "/document-database/file/uploadContent",
        method="POST", body=body,
    )
    # 响应格式：{"fileId": "...", ...}
    if isinstance(data, dict):
        return int(data.get("fileId") or data.get("data") or 0)
    return int(data)


def update_file_version(
    server_url: str,
    app_key: str,
    file_id: int,
    resource_id: str,
    version_status: int = 2,
    version_name: Optional[str] = None,
    version_remark: Optional[str] = None,
    suffix: Optional[str] = None,
    size: Optional[int] = None,
    project_id: Optional[str] = None,
) -> int:
    """2.4 上传新文件内容以更新文件版本。

    version_status: 1=覆盖当前草稿 2=强制新建版本(默认) 3=新建版本并立即定稿
    返回 fileId（与传入相同）。
    """
    body: dict = {
        "id": file_id,
        "resourceId": resource_id,
        "versionStatus": version_status,
    }
    if project_id is not None:
        body["projectId"] = project_id
    if version_name:
        body["versionName"] = version_name
    if version_remark:
        body["versionRemark"] = version_remark
    if suffix:
        body["suffix"] = suffix
    if size is not None:
        body["size"] = size
    data = api_call(
        server_url, app_key,
        "/document-database/file/updateFileVersion",
        method="POST", body=body,
    )
    if isinstance(data, dict):
        return int(data.get("fileId") or data.get("data") or 0)
    return int(data)


def get_version_list(
    server_url: str, app_key: str, file_id: int
) -> list[dict]:
    """2.5 获取文件的所有历史版本列表。"""
    data = api_call(
        server_url, app_key,
        "/document-database/file/getVersionList",
        method="GET", body={"fileId": file_id},
    )
    return data if isinstance(data, list) else []


def get_last_version(
    server_url: str, app_key: str, file_id: int
) -> dict:
    """2.6 获取文件的最新版本信息。"""
    data = api_call(
        server_url, app_key,
        "/document-database/file/getLastVersion",
        method="GET", body={"fileId": file_id},
    )
    return data if isinstance(data, dict) else {}


def finalize_version(
    server_url: str, app_key: str, file_id: int, version_number: int = 0
) -> bool:
    """2.7 将指定版本标记为定稿（versionNumber=0 表示最新版本）。"""
    data = api_call(
        server_url, app_key,
        "/document-database/file/finalizeVersion",
        method="POST",
        body={"fileId": file_id, "versionNumber": version_number},
    )
    return bool(data)


def get_full_text_content(
    server_url: str, app_key: str, file_id: int
) -> dict:
    """3.2 获取文件全文内容（用于拉取同步）。"""
    data = api_call(
        server_url, app_key,
        "/document-database/file/getFullFileContent",
        method="GET", body={"fileId": file_id},
    )
    return data if isinstance(data, dict) else {}


def batch_get_meta(
    server_url: str, app_key: str, file_ids: list[int]
) -> list[dict]:
    """1.8 批量获取文件元数据（不含正文）。"""
    data = api_call(
        server_url, app_key,
        "/document-database/file/batchGetMeta",
        method="POST", body={"fileIds": file_ids},
    )
    return data if isinstance(data, list) else []


# === 工具函数 ===

def is_text_file(path: Path) -> bool:
    """判断文件是否为文本（按扩展名白名单）。"""
    return path.suffix.lower() in TEXT_EXTENSIONS


def upload_local_file(
    server_url: str,
    app_key: str,
    project_id: Optional[str],
    folder_name: str,
    local_path: Path,
    update_file_id: Optional[int] = None,
    version_remark: Optional[str] = None,
    version_name: Optional[str] = None,
) -> int:
    """本地文件 → 云端 fileId（按文件类型自动分流）。

    - update_file_id 不传 → 新建/覆盖
    - update_file_id 传入 → 版本更新模式

    返回 fileId。
    """
    size = local_path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise RuntimeError(f"文件超过 10MB 限制: {local_path}")
    file_name = local_path.name
    suffix = file_name.rsplit(".", 1)[-1] if "." in file_name else "txt"

    if is_text_file(local_path):
        content = local_path.read_text(encoding="utf-8")
        assert project_id is not None, "project_id required"
        return upload_content(
            server_url, app_key, project_id, folder_name,
            file_name, content, suffix,
            update_file_id=update_file_id,
            version_remark=version_remark,
            version_name=version_name,
        )
    else:
        resource_id = upload_binary_resource(
            server_url, app_key, str(local_path), file_name,
        )
        assert project_id is not None, "project_id required"
        if update_file_id is not None:
            return update_file_version(
                server_url, app_key, update_file_id, resource_id,
                version_status=2,
                version_remark=version_remark,
                version_name=version_name,
                suffix=suffix,
                size=size,
                project_id=str(project_id),
            )
        return save_file_by_path(
            server_url, app_key, str(project_id), folder_name,
            file_name, resource_id, suffix, size,
            name_conflict_strategy=1,
        )

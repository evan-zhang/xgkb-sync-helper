---
name: xgkb-sync-helper
description: "玄关知识库同步助手。Agent 写文件后调用 xgkb-push 自动同步到玄关个人知识库。支持文本和二进制文件、幂等覆盖、批量目录同步。触发词：同步到知识库、xgkb-push、推送知识库、xgkb-sync"
version: "1.0.0"
---

# xgkb-sync-helper — 玄关知识库同步助手

> Agent 写本地文件后，一行命令同步到玄关个人知识库。

## 机制

**Fire-on-write**：Agent 执行 `write` / `edit` 后，调 `xgkb-push <文件路径>`，脚本自动完成上传/更新。

- **幂等**：同名文件重复上传不创建副本，直接覆盖
- **全类型**：文本文件（.md/.txt/.json 等）和二进制文件（.pdf/.docx/.png 等）均支持
- **实时**：写完即推，无轮询延迟
- **零进程依赖**：不需要常驻服务，调一次跑一次
- **失败安全**：网络失败写重试队列，不阻断主流程

## 配置

### 全局配置（必需）

`~/.openclaw/xgkb.json`：

```json
{
  "appKey": "你的玄关开放平台 appKey",
  "serverUrl": "https://sg-al-cwork-web.mediportal.com.cn/open-api/"
}
```

### 项目集合配置

项目集合根目录（如 `projects/`）放一个 `.xgkb.json`，所有子项目共用：

```json
{
  "enabled": true,
  "remoteRoot": "TPR-Framework"
}
```

- `enabled`：`false` 或文件不存在 → 跳过同步
- `remoteRoot`：知识库中的根目录名，默认 `OpenClaw`

### 环境变量（备选）

- `XGKB_APPKEY`：appKey（优先级低于配置文件）
- `XGKB_SERVER_URL`：API 地址（默认生产地址）

## 使用

### 同步单个文件

```bash
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py /path/to/file.md
```

### 批量同步目录

```bash
# 同步整个目录
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_dir.py /path/to/directory

# 预览模式（不实际推送）
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_dir.py /path/to/directory --dry-run

# 自定义间隔和文件类型
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_dir.py /path/to/directory --interval 5 --pattern "*.md,*.txt"
```

### Agent exec 调用

Skill 写完文件后追加：

```bash
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py <刚写的文件路径>
```

## 执行流程

```
xgkb-push(file_path)
  1. 读全局配置 ~/.openclaw/xgkb.json → appKey + serverUrl
  2. 向上找最近的 .xgkb.json → remoteRoot + enabled
  3. enabled=false 或无配置 → 静默退出 (exit 0)
  4. 获取/缓存 projectId（个人空间）
  5. 计算远端路径：remoteRoot/文件相对路径
  6. 按扩展名分流：
     - 文本 → uploadContent（幂等覆盖）
     - 二进制 → uploadWholeFile + saveFileByPath(nameConflictStrategy=1)（幂等覆盖）
  7. 成功 → exit 0
  8. 失败 → 写入重试队列，exit 0（不阻断主流程）
```

## 重试

- 失败记录写入 `~/.openclaw/xgkb-retry.jsonl`（JSONL 格式）
- 调 `xgkb_retry.py` 消费队列，最多重试 3 次

## API 参考

| 接口 | 用途 |
|------|------|
| `GET /document-database/project/personal/getProjectId` | 获取个人空间 ID |
| `POST /document-database/file/uploadContent` | 上传/更新文本文件（幂等） |
| `POST /cwork-file/uploadWholeFile` | 上传二进制物理文件 |
| `POST /document-database/file/saveFileByPath` | 绑定到知识库目录（nameConflictStrategy=1 幂等） |

## 限制

- 单文件大小限制 10MB
- 不支持删除同步 — 知识库文件管理在玄关网页端操作
- **Python 版本：必须 3.10+**（脚本用了 PEP 604 `str | Path` 语法）。Debian 默认 `python3` 是 3.9，跑不动。请用 `python3.11` 或自己装 3.10+。

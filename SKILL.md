---
name: xgkb-sync-helper
description: "玄关知识库同步助手。Agent 写文件后调用 xgkb-push 自动同步到玄关个人知识库。支持文本和二进制文件、增删改同步、版本控制、云端↔本地双向同步。触发词：同步到知识库、xgkb-push、推送知识库、xgkb-sync、xgkb-pull、xgkb-versions"
version: "2.1.1"
---

# xgkb-sync-helper — 玄关知识库同步助手

> Agent 写本地文件后，一行命令同步到玄关个人知识库。v2.1 引入：SQLite 状态、删除/改名同步、版本控制、双向同步。

## 能力

| 能力 | v0.1 | v2.1 |
|---|---|---|
| 上传/覆盖文件 | ✅ | ✅ |
| 二进制文件（pdf/png/...）| ✅ | ✅ |
| **删除本地文件 → 云端同步删** | ❌ | ✅ |
| **本地改名/移动 → 云端同步** | ❌ | ⚠️ 简化版：删除+新建（保版本历史不被云端改动） |
| **版本控制**（云端多版本） | ❌ | ✅（需 `.xgkb.json` 启用 `versionControl: true`） |
| **云端 → 本地 pull** | ❌ | ✅（多设备同步） |
| **冲突策略**（sync 模式） | n/a | local-wins / cloud-wins / skip |
| **本地状态缓存**（增量同步） | ❌ | ✅ `~/.openclaw/xgkb-state/` |
| **dry-run 预览** | ✅ | ✅ |
| **失败重试队列** | ✅ | ✅ |

## 机制

**Fire-on-write**：Agent 执行 `write` / `edit` 后，调 `xgkb-push <文件路径>`，脚本自动完成上传/更新。

- **幂等**：同名文件重复上传不创建副本，直接覆盖（保留原 fileId）
- **全类型**：文本文件（.md/.txt/.json 等）和二进制文件（.pdf/.docx/.png 等）均支持
- **增量**：本地 SQLite 状态缓存 `~/.openclaw/xgkb-state/<project-key>.db` 记录每个文件的 fileId、versionNumber、contentHash；push 时自动检测增删改
- **零进程依赖**：不需要常驻服务，调一次跑一次
- **失败安全**：网络失败写重试队列，不阻断主流程

## 本地质量门禁

改动后优先跑脚本，不靠人工手动检查：

```bash
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_check.py
```

它会执行本地无网络检查：语法编译、回归测试、关键 CLI 冒烟检查、旧入口 import 兼容检查。

## 配置

### 全局配置（必需）

`~/.openclaw/xgkb.json`：

```json
{
  "appKey": "你的玄关开放平台 appKey",
  "serverUrl": "https://sg-al-cwork-web.mediportal.com.cn/open-api/"
}
```

### 项目配置

每个项目根目录放一个 `.xgkb.json`：

```json
{
  "enabled": true,
  "remoteRoot": "TPR-Framework",
  "versionControl": false
}
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | 必须 | `false` 或文件不存在 → 跳过同步 |
| `remoteRoot` | str | `"OpenClaw"` | 知识库中的根目录名（必须与云端实际存在的文件夹名一致）|
| `versionControl` | bool | `false` | `true` 时 push 每次都成新版本；否则覆盖（保留原 fileId） |

### 环境变量（备选）

- `XGKB_APPKEY`：appKey（优先级低于配置文件）
- `XGKB_SERVER_URL`：API 地址（默认生产地址）

## 使用

### 单文件 push（最常用）

```bash
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py /path/to/file.md
```

写入 stdin 模式（兼容 v0.1）：

```bash
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py \
  --stdin --name "note.md" --folder "TPR-Framework/notes" < content.md
```

### 全量双向同步（推荐）

`xgkb_sync_full.py` 是 v2.1 的核心脚本：

```bash
# push 模式：本地 → 云端（增删改同步，最常用）
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_full.py /path/to/project --direction push

# pull 模式：云端 → 本地（拉取云端变更）
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_full.py /path/to/project --direction pull

# sync 模式：双向（先 pull 再 push）
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_full.py /path/to/project --direction sync --conflict local

# dry-run 预览
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_full.py /path/to/project --direction push --dry-run
```

**冲突策略**（sync 模式用）：
- `local`（默认）：本地覆盖云端
- `cloud`：云端覆盖本地
- `skip`：跳过冲突项

输出示例：
```
[xgkb-sync] 项目: /path/to/proj  →  空间: 1764536926399946754/TPR-Framework/

  + 新增: docs/readme.md (fileId=2073652431417360386)
  ~ 更新: docs/data.json (fileId=2073652442913947650, v3)
  - 删除: research/note-a.md (fileId=2073652452976082946)

[xgkb-sync] 📤 新增: 1  📝 更新: 1  🗑️ 删除: 1  ⚠️ 跳过: 0
```

### 版本控制工具

```bash
# 列出某 fileId 的所有历史版本
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_versions.py list <fileId>

# 通过本地文件路径查云端版本（依赖 xgkb-state 缓存的 fileId）
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_versions.py list-local /path/to/file.md

# 列出项目下所有版本化文件的最新版本
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_versions.py tree /path/to/project

# 定稿某版本（versionNumber=0 = 最新）
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_versions.py finalize <fileId> [--version N]
```

### 批量同步目录（保留的 v0.1 脚本）

```bash
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_dir.py /path/to/directory --dry-run
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_dir.py /path/to/directory --pattern "*.md,*.txt"
```

### Agent exec 调用

Skill 写完文件后追加：

```bash
# 单文件
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py <刚写的文件路径>

# 项目级（推荐：自动检测增删改）
python3.11 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_sync_full.py <项目根> --direction push
```

## 执行流程

```
xgkb-sync-full <path> --direction push
  1. 读全局配置 ~/.openclaw/xgkb.json → appKey + serverUrl
  2. 向上找最近的 .xgkb.json → remoteRoot + enabled + versionControl
  3. enabled=false → 静默退出 (exit 0)
  4. 加载项目状态 ~/.openclaw/xgkb-state/<project>.json
  5. 递归扫描本地目录（排除 .git/、.xgkb.json 等）
  6. 对每个本地文件：
     a. 算 contentHash (sha256)
     b. 与 state 中记录的 hash 比对
     c. 新增 → uploadContent / saveFileByPath
     d. 修改 → uploadContent (updateFileId) 走版本更新或覆盖
     e. 记入 state
  7. 对 state 中存在但本地不存在的 → deleteFile（逻辑删除）
  8. 持久化 state
```

## 重试

- 失败记录写入 `~/.openclaw/xgkb-retry.jsonl`（JSONL 格式）
- 调 `xgkb_retry.py` 消费队列，最多重试 3 次

## 本地状态缓存

`~/.openclaw/xgkb-state/<projectKey>.json` 结构：

```json
{
  "projectKey": "TPR-Framework",
  "remoteRoot": "TPR-Framework",
  "projectId": "1764536926399946754",
  "serverTime": 1714972812345,
  "files": {
    "docs/readme.md": {
      "fileId": 2073652431417360386,
      "versionNumber": 3,
      "contentHash": "sha256:2b627b...",
      "mtime": 1783232140,
      "lastSyncAt": 1783232244
    }
  }
}
```

**重要**：删除这个文件 = 强制重新全量同步（state 缺失时 push 会当本地文件全是新的）。

## API 参考（共 13 个）

| 文档 | 接口 | 用途 |
|---|---|---|
| 1.1 | `getProjectId` | 获取个人空间 ID |
| 1.5 | `getChildFiles` | 列举子目录/文件（pull 用） |
| 1.12 | `deleteFile` | 删除文件（逻辑/物理）|
| 1.13a | `updateFileName` | 同目录改名 |
| 1.13b | `moveFile` | 移动节点 |
| 1.14 | `resolvePath` | 路径→fileId（pull 用） |
| 1.17 | `getFileBasicInfo` | 获取文件基本信息 |
| 2.3 | `saveFileByPath` | 绑定物理资源到项目目录 |
| 2.4 | `updateFileVersion` | 上传新版本（versionStatus=2/3）|
| 2.5 | `getVersionList` | 列历史版本 |
| 2.6 | `getLastVersion` | 取最新版本 |
| 2.7 | `finalizeVersion` | 定稿版本 |
| 3.1 | `uploadContent` | 一键存文本（支持 updateFileId 版本模式）|
| 3.2 | `getFullFileContent` | 拉取全文（pull 用）|

详细规范见 `xgjk/dev-guide`（私有）：`02.产品业务AI文档/知识库/API接口明细_v2/`。

## 限制

- 单文件大小限制 10MB
- rename/move 当前简化处理：检测为「删除+新建」（保版本历史不会被云端改动；如需云端 rename，用 `updateFileName` API 直接调）
- **Python 版本：必须 3.10+**（脚本用了 PEP 604 `str | Path` 语法）。Debian 默认 `python3` 是 3.9，跑不动。请用 `python3.11` 或自己装 3.10+。

## 升级路径（v0.1 → v2.0）

- v0.1 调用 `xgkb_push.py` 完全兼容
- 新能力在 `xgkb_sync_full.py` / `xgkb_versions.py`
- v0.1 的 `.xgkb.json` 配置完全兼容；加 `versionControl: true` 即开启版本控制

## 升级路径（v2.0 → v2.1）—— state 从 JSON 迁到 SQLite

### 为什么改

v2.0 用 `~/.openclaw/xgkb-state/<remoteRoot>.json` 存同步状态。**两个已知风险**：

1. **并发覆盖**：多 agent / 多设备同时 push，后写覆盖前写，state 错乱
2. **跨项目撞名**：两个项目都用 `remoteRoot="foo"`，state 文件互相覆盖

v2.1 用 SQLite（WAL + busy_timeout）解决并发；用 SHA256 key 公式解决跨项目撞名。

### 新 key 公式

```
project_key = sha256(
  "xgkb-state-v1|" + serverUrl + "|" + appKey + "|" + remoteRoot + "|" + abs_proj_root
)
```

DB 文件名 = 前 32 hex 字符（`0d33bb3796b85eda734e8e154c2093eb.db`）。**不再能从文件名看出哪个项目**——这是有意的（防信息泄露 + 防撞名）。

### API 变化（向后兼容）

```python
# v2.0 旧调用（仍可用，仅传 remote_root 时 key = remote_root）
state_data = state.load_state(remote_root)

# v2.1 推荐（传全 4 参时用 hash key）
state_data = state.load_state(remote_root, server_url, app_key, proj_root)
```

所有调用方（`xgkb_push.py` / `xgkb_sync_full.py` / `xgkb_versions.py`）已自动切到新调用。

### 从 v2.0 升级

**你已经有 JSON state**：跑一次迁移（**手动，不自动**）：

```bash
# 1. 看有哪些 JSON 要迁
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/migrate_json_to_sqlite.py list

# 2. 先 dry-run（v2.1 需要项目根来生成 hash-key DB）
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/migrate_json_to_sqlite.py migrate --all --proj-root /path/to/project --dry-run

# 3. 确认没问题，真跑
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/migrate_json_to_sqlite.py migrate --all --proj-root /path/to/project
```

迁移行为：
- 默认写入 v2.1 hash-key DB，与 `load_state(remote_root, server_url, app_key, proj_root)` 一致
- 如确需旧 key，显式加 `--legacy-key`（v2.1 常规调用不会自动读取 legacy DB）
- 原 JSON 备份为 `<name>.json.v2-bak`
- DB schema 含 `meta.schema_version` / `meta.migrated_at` / `meta.migrated_from` 三个标记字段

**没 JSON（首次使用 v2.1）**：什么都不用做。首次 push 时 SQLite 会按新公式自动创建。

### 没改的东西

- `xgkb_push.py` / `xgkb_sync_full.py` / `xgkb_versions.py`：外部行为不变，**只换了内部 state 后端**
- `.xgkb.json` 配置格式：不变
- `xgkb_retry.py` 的 `.xgkb-retry.jsonl`：单独文件，**没迁**（独立功能，未来再说）

# xgkb-sync-helper

> 玄关知识库同步助手 — OpenClaw Agent 写文件后，一行命令同步到玄关知识库。

## 为什么需要它

OpenClaw Agent 在执行过程中会产出大量 Markdown 文件（项目文档、分析报告、决策记录等）。这些文件写在内网磁盘上，团队其他成员无法通过公网访问。

`xgkb-sync-helper` 解决这个问题：**Agent 写完文件后，自动或手动同步到玄关知识库**，无需额外进程或同步服务。

## 核心特性

- 🔥 **Fire-on-write** — 写完即推，无轮询延迟
- 🖐️ **手动触发** — 支持用户指令批量同步文件或目录
- 🔁 **幂等覆盖** — 同名文件重复上传自动覆盖，不产生副本
- 🎯 **多空间支持** — 可同步到个人空间或指定团队空间
- 🧩 **Agent 隔离** — 配置和重试队列都在 Agent workspace 内，多 Agent 互不干扰
- 🚫 **零进程依赖** — 不需要常驻服务，调一次跑一次
- 🛡️ **失败安全** — 网络失败写重试队列，不阻断主流程

## 安装

### 前置条件

- Python 3.10+
- 玄关开放平台 appKey（获取路径：玄关开放平台 → 个人设置 → API 密钥）

### 步骤

```bash
# 1. 克隆到 Agent 的 workspace 级 skills 目录
cd ~/.openclaw/gateways/<gateway>/state/workspace-<agent>/skills/
git clone https://github.com/evan-zhang/xgkb-sync-helper.git

# 2. 源码建议放到项目管理目录（如 TPR 项目目录）
cd ~/.openclaw/gateways/<gateway>/state/workspace-<agent>/projects/
git clone https://github.com/evan-zhang/xgkb-sync-helper.git TPR-xxxx-xgkb-sync-helper
```

> **注意**：Skill 目录（`skills/xgkb-sync-helper/`）只放 `SKILL.md`，源码放在项目目录里独立管理。

## 配置

### Agent 级配置（必需）

放在 Agent 的 workspace 根目录：`<workspace>/.xgkb.json`

```json
{
  "appKey": "你的玄关开放平台 appKey",
  "serverUrl": "https://sg-al-cwork-web.mediportal.com.cn/open-api/"
}
```

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `appKey` | 是 | — | 玄关开放平台 API 密钥 |
| `serverUrl` | 否 | 生产环境地址 | 知识库 Open API 地址 |

> ⚠️ 每个 Agent 各有自己的 `.xgkb.json`，互不干扰。不要放在 `~/.openclaw/` 下（Gateway 级，多 Agent 会共享）。

也支持环境变量 `XGKB_APPKEY`、`XGKB_SERVER_URL`（优先级低于配置文件）。

### 项目级配置（可选）

放在项目根目录：`<project>/.xgkb.json`

```json
{
  "enabled": true,
  "remoteRoot": "Obsidian/projects",
  "projectId": "2031659128119746561"
}
```

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | 否 | `false` | 是否启用同步 |
| `remoteRoot` | 否 | `OpenClaw` | 知识库中的根目录名 |
| `projectId` | 推荐 | 个人空间 ID | 目标知识库空间 ID（名称易变，推荐用 ID） |
| `projectName` | 备选 | — | 按名称查找空间（仅作便利，优先级低于 projectId） |

> - 不配 `projectId` 和 `projectName` → 默认同步到个人知识库
> - 没有 `.xgkb.json` 或 `enabled: false` → 静默跳过，不报错

#### 如何获取 projectId

调用知识库 API 获取你有权限的空间列表：

```bash
curl -s -H "appKey: 你的appKey" \
  "https://sg-al-cwork-web.mediportal.com.cn/open-api/document-database/project/list" \
  | python3 -m json.tool
```

找到目标空间的 `id` 字段，填入 `projectId`。

### Workspace 定位机制

脚本通过以下优先级定位 Agent workspace（用于读取 `.xgkb.json` 和写入重试队列）：

1. 环境变量 `OPENCLAW_WORKSPACE`（推荐，Agent exec 时显式传入）
2. 从脚本位置向上查找含 `AGENTS.md` 或 `SOUL.md` 的目录
3. 兜底 `~/.openclaw`（不应到达）

## 使用方式

### 模式一：自动 Fire-on-write

Agent 执行 `write` / `edit` 写完文件后，自动调用同步：

```bash
python3 <project>/scripts/xgkb_push.py <文件路径>
```

适用于 Ralph Loop、TPR 工作流等自动写文件的场景。

### 模式二：用户手动触发

当用户说"同步到知识库""推送知识库""更新知识库""批量同步"等指令时，Agent 主动调用：

**同步单个文件：**
```bash
python3 scripts/xgkb_push.py /path/to/file.md
```

**批量同步整个目录：**
```bash
# 实际同步
python3 scripts/xgkb_sync_dir.py /path/to/directory/

# 预览（不实际推送）
python3 scripts/xgkb_sync_dir.py /path/to/directory/ --dry-run

# 自定义文件过滤
python3 scripts/xgkb_sync_dir.py /path/to/directory/ --pattern "*.md,*.txt"
```

**上传大文件（>10MB，自动分片）：**
```bash
python3 scripts/xgkb_upload_file.py /path/to/large-file.pdf
```

**补推失败的同步：**
```bash
python3 scripts/xgkb_retry.py
```

同步完成后 Agent 向用户报告结果（成功数、失败数、目标路径）。

### 从 stdin 读取并上传

```bash
echo "# content" | python3 scripts/xgkb_push.py --stdin --name "note.md" --folder "TPR-Framework/notes"
```

stdin 模式默认同步到个人空间，可通过环境变量指定目标空间：
```bash
XGKB_PROJECT_ID=2031659128119746561 python3 scripts/xgkb_push.py --stdin --name "note.md" --folder "notes"
```

## 路径映射规则

本地文件路径自动映射到知识库路径：

```
本地：/projects/TPR-20260618-001/01-discovery/DISCOVERY.md
      ^^^^^^^^ .xgkb.json 在这层（remoteRoot = "Obsidian/projects"）
知识库：Obsidian/projects/TPR-20260618-001/01-discovery/DISCOVERY.md
```

`.xgkb.json` 放在项目集合的根目录，各子目录的文件相对路径自然包含项目目录名。

## 可用脚本

| 脚本 | 用途 |
|------|------|
| `xgkb_push.py` | 同步单个文件（文本走 uploadContent，二进制走 uploadWholeFile） |
| `xgkb_sync_dir.py` | 批量同步整个目录（支持 `--dry-run`、`--pattern`、`--interval`） |
| `xgkb_upload_file.py` | 分片上传大文件（>10MB，走 MinIO 直传） |
| `xgkb_retry.py` | 消费重试队列，补推失败的同步 |

## 重试机制

- 同步失败时，自动写入 `<workspace>/.xgkb-retry.jsonl`
- 重试队列在 Agent workspace 内，多 Agent 互不干扰
- 每条最多重试 3 次，超过后放弃并告警
- 重试记录包含目标空间信息，补推时沿用原目标
- 重试不阻断任何主流程

## 在 Skill 中集成

### 方式 A：exec 调用（推荐）

Skill 写完文件后追加一步：

```bash
python3 <project>/scripts/xgkb_push.py <文件路径>
```

### 方式 B：Python import

```python
import sys, os
sys.path.insert(0, os.path.expanduser("~/.openclaw/gateways/life/state/workspace-life/projects/TPR-20260621-001-xgkb-sync-helper/scripts"))
from xgkb_push import push_file

push_file("/path/to/file.md")
```

## API 接口

| 接口 | 用途 |
|------|------|
| `GET /document-database/project/personal/getProjectId` | 获取个人空间 ID |
| `GET /document-database/project/list` | 获取所有有权限的空间列表 |
| `POST /document-database/file/uploadContent` | 上传/更新文本文件（幂等） |
| `POST /cwork-file/uploadWholeFile` | 上传二进制物理文件 |
| `POST /document-database/file/saveFileByPath` | 绑定文件到知识库目录（nameConflictStrategy=1 幂等） |

## 限制

- 单文件大小限制 10MB（`xgkb_push.py`），大文件请用 `xgkb_upload_file.py`（分片上传）
- 不支持删除同步 — 知识库文件管理在玄关网页端操作

## 项目结构

```
xgkb-sync-helper/
├── README.md             # 本文件
├── .gitignore
└── scripts/
    ├── xgkb_push.py      # 核心同步脚本（单文件 + stdin）
    ├── xgkb_sync_dir.py   # 批量目录同步
    ├── xgkb_upload_file.py # 大文件分片上传
    └── xgkb_retry.py      # 重试队列消费
```

Skill 定义文件（`SKILL.md`）单独放在 Agent workspace 的 `skills/xgkb-sync-helper/` 目录。

## 相关项目

- [openclaw-xgkb-sync](https://github.com/xgjk/openclaw-xgkb-sync) — 完整的双向同步服务（独立进程模式）
- 玄关开放平台 API 文档 — 知识库接口说明

## License

MIT

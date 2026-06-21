# xgkb-sync-helper

> 玄关知识库同步助手 — Skill 写文件后一行命令同步到玄关个人知识库。

## 为什么需要它

OpenClaw Agent 在执行过程中会产出大量 Markdown 文件（项目文档、分析报告、决策记录等）。这些文件写在内网磁盘上，团队其他成员无法通过公网访问。

`xgkb-sync-helper` 解决这个问题：**Skill 写完文件后，自动同步到玄关个人知识库**，无需额外进程或同步服务。

## 核心特性

- 🔥 **Fire-on-write** — 写完即推，无轮询延迟
- 🔁 **幂等覆盖** — 同名文件重复上传自动覆盖，不产生副本
- 🚫 **零进程依赖** — 不需要常驻服务，Skill 调一次跑一次
- 🛡️ **失败安全** — 网络失败写重试队列，不阻断 Skill 主流程
- 🧩 **可复用** — 任何 OpenClaw Skill 都能用，不绑定特定项目

## 快速开始

### 1. 安装

```bash
# 克隆到 OpenClaw skills 目录
cd ~/.openclaw/skills/
git clone https://github.com/evan-zhang/xgkb-sync-helper.git
```

### 2. 配置 appKey

```bash
cat > ~/.openclaw/xgkb.json << 'EOF'
{
  "appKey": "你的玄关开放平台 appKey",
  "serverUrl": "https://sg-al-cwork-web.mediportal.com.cn/open-api/"
}
EOF
```

> appKey 获取：玄关开放平台 → 个人设置 → API 密钥

### 3. 在项目中启用同步

在项目根目录创建 `.xgkb.json`：

```bash
cd /path/to/your/project
echo '{"enabled": true, "remoteRoot": "TPR-Framework"}' > .xgkb.json
```

### 4. 推送文件

```bash
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py /path/to/file.md
```

输出：

```
[xgkb-push] ✅ DISCOVERY.md → TPR-Framework/01-discovery/ (fileId=2067991500729495554)
```

## 配置说明

### 全局配置 `~/.openclaw/xgkb.json`（必需）

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `appKey` | 是 | — | 玄关开放平台 API 密钥 |
| `serverUrl` | 否 | 生产环境地址 | 知识库 Open API 地址 |

也支持环境变量 `XGKB_APPKEY`、`XGKB_SERVER_URL`（优先级低于配置文件）。

### 项目配置 `.xgkb.json`（可选）

放在项目根目录，向上查找。

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | 否 | `false` | 是否启用同步 |
| `remoteRoot` | 否 | `OpenClaw` | 知识库中的根目录名 |

没有 `.xgkb.json` 或 `enabled: false` → 静默跳过，不报错。

## 路径映射规则

本地文件路径自动映射到知识库路径：

```
本地：/projects/TPR-20260618-001/01-discovery/DISCOVERY.md
      ^^^^^^^^ .xgkb.json 在这层
配置：remoteRoot = "TPR-Framework"
知识库：TPR-Framework/TPR-20260618-001/01-discovery/DISCOVERY.md
```

`.xgkb.json` 放在项目集合的根目录（如 `projects/`），各子目录的文件相对路径自然包含项目目录名。

## 在 Skill 中集成

### 方式 A：exec 调用（推荐）

Skill 写完文件后追加一步：

```bash
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py <文件路径>
```

### 方式 B：Python import

```python
import sys
sys.path.insert(0, "~/.openclaw/skills/xgkb-sync-helper/scripts")
from xgkb_push import push_file

push_file("/path/to/file.md")
```

## 重试机制

- 同步失败时，自动写入 `~/.openclaw/xgkb-retry.jsonl`
- Agent 启动时调 `xgkb_retry.py` 消费队列
- 每条最多重试 3 次，超过后放弃并告警
- 重试不阻断任何主流程

```bash
# 手动触发重试
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_retry.py
```

## API 说明

仅使用两个接口，保持极简：

| 接口 | 用途 |
|------|------|
| `GET /document-database/project/personal/getProjectId` | 获取个人空间 ID |
| `POST /document-database/file/uploadContent` | 上传/更新文本文件（幂等） |
| `POST /cwork-file/uploadWholeFile` | 上传二进制物理文件 |
| `POST /document-database/file/saveFileByPath` | 绑定文件到知识库目录（nameConflictStrategy=1 幂等） |

已验证（2026-06-21）：
- ✅ `uploadContent` 幂等 — 同名文件覆盖，不产生副本
- ✅ `saveFileByPath(nameConflictStrategy=1)` 幂等 — 二进制文件同名覆盖
- ✅ 上传后实时可读 — 无延迟
- ✅ 内容更新即时生效

## 限制

- 单文件大小限制 10MB
- 不支持删除同步 — 知识库文件管理在玄关网页端操作

## 项目结构

```
xgkb-sync-helper/
├── SKILL.md              # OpenClaw Skill 文档
├── README.md             # 本文件
├── .gitignore
└── scripts/
    ├── xgkb_push.py      # 核心同步脚本
    └── xgkb_retry.py     # 重试队列消费
```

## 相关项目

- [tpr-framework](https://github.com/evan-zhang/tpr-framework) — TPR 方法论，已集成 xgkb-sync-helper
- [openclaw-xgkb-sync](https://github.com/xgjk/openclaw-xgkb-sync) — 完整的双向同步服务（独立进程模式）
- [dev-guide](https://github.com/xgjk/dev-guide) — 玄关开放平台 API 文档

## License

MIT

---
name: xgkb-sync-helper
description: "玄关知识库同步助手。Skill 写文件后调用 xgkb-push 自动同步到玄关个人知识库。支持新建、更新、幂等覆盖。触发词：同步到知识库、xgkb-push、推送知识库"
version: "0.1.0"
---

# xgkb-sync-helper — 玄关知识库同步助手

> Skill 写本地文件后，一行命令同步到玄关个人知识库。

## 机制

**Fire-on-write**：Skill 执行 `write` / `edit` 后，调 `xgkb-push <文件路径>`，脚本自动完成上传/更新。

- **幂等**：同名文件重复上传不创建副本，直接覆盖（已验证 API 行为）
- **实时**：写完即推，无轮询延迟
- **零进程依赖**：不需要常驻服务，Skill 调一次跑一次
- **失败安全**：网络失败写重试队列，Agent 下次启动时补推

## 配置

### 全局配置（必需）

`~/.openclaw/xgkb.json`：

```json
{
  "appKey": "你的玄关开放平台 appKey",
  "serverUrl": "https://sg-al-cwork-web.mediportal.com.cn/open-api/"
}
```

### 项目级配置（可选）

项目根目录 `.xgkb.json`：

```json
{
  "enabled": true,
  "remoteRoot": "TPR-Framework"
}
```

- `enabled`：`false` 或文件不存在 → 跳过同步
- `remoteRoot`：知识库中的根目录名，默认 `OpenClaw`

### 环境变量（备选）

- `XGKB_APPKEY`：appKey（优先级低于全局配置文件）
- `XGKB_SERVER_URL`：API 地址（默认生产地址）

## 使用

### CLI

```bash
# 同步单个文件
python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py /path/to/file.md

# 从 stdin 读取内容
echo "# content" | python3 ~/.openclaw/skills/xgkb-push-helper/scripts/xgkb_push.py --stdin --name "note.md" --folder "TPR-Framework/notes"
```

### Python 模块

```python
import sys
sys.path.insert(0, "~/.openclaw/skills/xgkb-sync-helper/scripts")
from xgkb_push import push_file

push_file("/path/to/file.md")
```

### Agent exec 调用

Skill 写完文件后追加：

```bash
exec: python3 ~/.openclaw/skills/xgkb-sync-helper/scripts/xgkb_push.py <刚写的文件路径>
```

## 执行流程

```
xgkb-push(file_path)
  1. 读全局配置 ~/.openclaw/xgkb.json → appKey + serverUrl
  2. 向上找最近的 .xgkb.json → remoteRoot + enabled
  3. enabled=false 或无配置 → 静默退出 (exit 0)
  4. 获取/缓存 projectId（个人空间）
  5. 计算远端路径：remoteRoot/项目目录名/文件相对路径
  6. 读文件内容
  7. 调 uploadContent API（幂等：存在则覆盖，不存在则新建）
  8. 成功 → exit 0
  9. 失败 → 写入 ~/.openclaw/xgkb-retry.jsonl，exit 0（不阻断 Skill 主流程）
```

## 重试

- 失败记录写入 `~/.openclaw/xgkb-retry.jsonl`（JSONL 格式，每行一条）
- Agent 启动时自动调 `xgkb_retry.py` 消费队列
- 最多重试 3 次，超过后放弃并告警

## API 参考

| 接口 | 用途 |
|------|------|
| `GET /document-database/project/personal/getProjectId` | 获取个人空间 ID |
| `POST /document-database/file/uploadContent` | 上传/更新文件（幂等） |

仅使用以上两个接口，保持极简。

## 限制

- 仅支持文本文件（.md / .txt / .json / .yaml 等）
- 单文件大小限制 10MB
- 不支持二进制文件（图片、PDF 等）—— 用 doc-viewer skill 的路径 C
- 不支持删除同步 —— 知识库文件管理在玄关网页端操作

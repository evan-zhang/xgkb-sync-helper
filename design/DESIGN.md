# DESIGN.md — xgkb-sync-helper 产品设计档案

## 产品目标

为 OpenClaw Agent 提供轻量的玄关知识库同步能力：写完本地文件后，一行命令同步到玄关知识库，无需常驻进程。

## 边界

- 做：单文件/目录同步到知识库（文本+二进制）、多空间支持、失败重试
- 不做：双向同步、删除同步、知识库内容浏览

## 核心流程

```
Agent write/edit 文件
  → xgkb_push.py <文件>
    → 定位 Agent workspace（OPENCLAW_WORKSPACE > 向上查找 > 兜底）
    → 读 <workspace>/.xgkb.json（appKey + serverUrl）
    → 向上找项目 .xgkb.json（enabled + remoteRoot + projectId）
    → resolve_project_id（projectId > projectName > 个人空间）
    → 文本走 uploadContent，二进制走 uploadWholeFile + saveFileByPath
    → 失败写 <workspace>/.xgkb-retry.jsonl
```

## 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 配置层级 | Agent 级 + 项目级 | Agent 各有自己的 appKey；项目各自控制开关和目标空间 |
| Workspace 定位 | 环境变量 > 向上查找 > 兜底 | 不硬编码路径，适配多 Agent |
| 目标空间指定 | projectId 推荐 | 名称易变，ID 不变 |
| 同步模式 | fire-on-write + 手动触发 | 覆盖自动工作流和用户指令两种场景 |
| 退出码 | 0=成功/跳过/失败入队列，1=配置错误 | 不阻断主流程 |
| 重试上限 | 3 次 | 平衡可靠性和资源消耗 |

## 架构

```
workspace-life/
├── .xgkb.json                    # Agent 配置（appKey）
├── .xgkb-retry.jsonl             # 重试队列
├── skills/xgkb-sync-helper/
│   └── SKILL.md                  # Skill 定义
└── projects/TPR-20260621-001-xgkb-sync-helper/
    ├── README.md                 # 完整使用文档
    ├── VERSION                   # 版本号
    ├── .gitignore
    ├── design/                   # 设计档案
    └── scripts/
        ├── xgkb_push.py          # 核心：单文件同步
        ├── xgkb_sync_dir.py      # 批量：目录同步
        ├── xgkb_upload_file.py   # 大文件：分片上传
        └── xgkb_retry.py         # 重试：补推失败
```

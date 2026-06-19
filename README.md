# xgkb-sync-helper

玄关知识库同步助手 — Skill 写文件后一行命令同步到玄关个人知识库。

## 快速开始

```bash
# 1. 配置 appKey
echo '{"appKey":"你的appKey","serverUrl":"https://sg-al-cwork-web.mediportal.com.cn/open-api/"}' > ~/.openclaw/xgkb.json

# 2. 在项目根目录创建 .xgkb.json
echo '{"enabled":true,"remoteRoot":"TPR-Framework"}' > /path/to/your/project/.xgkb.json

# 3. 推送文件
python3 scripts/xgkb_push.py /path/to/file.md
```

## 机制

- **Fire-on-write**：Skill 写完文件后调 `xgkb-push`，立即同步
- **幂等**：同名文件重复上传自动覆盖，不产生副本
- **零进程依赖**：不需要常驻服务
- **失败安全**：网络失败写重试队列，不阻断主流程

详细文档见 [SKILL.md](SKILL.md)。

# DISCUSSION-LOG.md — xgkb-sync-helper 讨论记录

## 2026-06-19 — 方案起源

- **背景**：TPR 项目文件需要自动同步到玄关知识库
- **讨论**：fire-on-write 方案 vs 独立同步服务
- **结论**：采用 fire-on-write，零进程依赖，Skill 内嵌
- **评审**：RT-20260619-001-xgkb-sync-design 方案评审通过

## 2026-06-21 — 项目化 + Agent 级隔离

- **问题**：代码混在 Skill 目录，配置在 Gateway 级（~/.openclaw/），多 Agent 会互相干扰
- **决策**：
  1. 代码迁移到 TPR 项目目录，Skill 目录只留 SKILL.md
  2. 配置改为 Agent workspace 级（<workspace>/.xgkb.json）
  3. 重试队列也放 Agent workspace 内
  4. Skill 从实例级移到 Workspace 级

## 2026-06-22 — 多空间支持 + 手动触发

- **需求**：同步到团队空间而非仅个人空间
- **方案**：调 /document-database/project/list 获取空间列表，配置支持 projectId/projectName
- **决策**：推荐 projectId（名称易变）
- **补充**：SKILL.md 增加手动触发模式说明

## 2026-06-23 — D 类评审修复

- **评审结论**：FAIL，13 项必修
- **修复范围**：补 design 档案、修 SKILL.md（frontmatter/路径/结构/行数）、修代码（退出码/统计逻辑）、补 VERSION

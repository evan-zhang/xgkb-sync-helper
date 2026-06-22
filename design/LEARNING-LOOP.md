# LEARNING-LOOP.md — xgkb-sync-helper 学习复盘

## 复盘维度

### 做对的

1. **方案极简** — 只用 4 个 API 接口，零常驻进程
2. **幂等性早期验证** — 设计阶段就通过 curl 测试 API 行为
3. **Agent 级隔离及时纠偏** — 用户指出后立即重构，避免后续多 Agent 部署翻车
4. **多空间支持渐进式扩展** — 先做个人空间，需要时再加多空间

### 做错的

1. **代码先落实例级** — 初版混在 ~/.openclaw/skills/，没考虑代码归属问题
2. **跳过 design 档案** — 项目化时只迁了代码，没建 design/
3. **SKILL.md frontmatter 加 version 字段** — 违反 SOP 规范（应写 VERSION 文件）
4. **硬编码绝对路径** — SKILL.md 里写死 `~/.openclaw/gateways/life/...`，影响复用
5. **退出码语义不清** — 配置错误也返回 0，错过了告警机会
6. **批量同步统计口径错** — 写入重试队列的失败被误计为 success

### 经验教训

- **配置归属问题先想清楚再写代码** — Agent 级 vs Gateway 级，决定了多用户/多 Agent 能不能跑
- **路径不要硬编码** — 用环境变量定位（OPENCLAW_WORKSPACE），不假设固定位置
- **SKILL.md 要符合 SOP frontmatter 规范** — 不要加平台不支持的字段
- **退出码要承载语义** — 失败入队列 ≠ 静默成功

## 迭代方向（按优先级）

### P0 — 必修（D 类评审 FAIL 项）

1. 补 design/ 档案
2. SKILL.md 合规化（移除 version frontmatter、去硬编码、瘦身、修结构）
3. 修代码（退出码语义、批量统计、Python import 路径展开）
4. 补 VERSION 文件

### P1 — 改进

1. 测试用例补充（unit test 覆盖核心路径）
2. 重试队列升级（按 file_path 去重，避免重复堆积）
3. 大文件阈值自动判断（>10MB 自动走 upload_file）

### P2 — 探索

1. 双向同步（知识库改动拉回本地）
2. 删除同步（本地删除 → 知识库归档）
3. 增量同步（按 mtime 跳过未变更文件）

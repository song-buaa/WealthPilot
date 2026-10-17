# CLAUDE.md

> Claude Code 项目说明（指针）

## 主文档

**请优先阅读 [AGENTS.md](./AGENTS.md)** —— 项目级 AI Agent 协作说明，遵循 [agents.md](https://agents.md/) 开放标准。

AGENTS.md 维护默认行为规则和必读入口；详细架构见 [ARCHITECTURE_CURRENT](docs/ARCHITECTURE_CURRENT.md)，开发/质量/发布流程见 [PROJECT_GOVERNANCE](docs/PROJECT_GOVERNANCE.md)，已有能力入口见 [CAPABILITY_REGISTRY](docs/CAPABILITY_REGISTRY.md)。本文仅保留 Claude Code 交互偏好，不另维护架构版本或第二套工程流程。

## Claude Code 特有约定

### 沟通风格
- 偏好直接、结构化的反馈
- 主动指出过度设计 / 可简化方案
- 长期主义优先（不为短期讨巧损害长期演进）
- 按"产品体验 / 长期演进 / 面试讲述"三维度评估技术决策，不按工作量评估

### 代码改动纪律
- 大改前先勘探（看真实代码，不凭记忆）
- 每个 step 独立验证 + commit（小步前进，可回退）
- 测试与验收统一执行 [治理文档第 6 节](docs/PROJECT_GOVERNANCE.md#6-质量门与当前基线)，不以本文摘要降低 Gate
- 任何改动失败立即停止并报告，不要"试试看修一下"

### 工具偏好
- 改文件用 str_replace（精确匹配）而非 sed
- 测试用 pytest（项目已采用 pytest 框架）
- 提交 commit 时遵循前缀规范：`feat()` / `chore:` / `docs:` / `fix:` / `test:` 等 conventional commits 风格

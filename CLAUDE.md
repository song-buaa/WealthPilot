# CLAUDE.md

> Claude Code 项目说明（指针）

## 主文档

**请优先阅读 [AGENTS.md](./AGENTS.md)** —— 项目级 AI Agent 协作说明，遵循 [agents.md](https://agents.md/) 开放标准。

AGENTS.md 包含：
- 项目概述与当前架构（v3.2 Multi-Agent + Skills（投资行动模块））
- 技术栈与目录结构
- 重要约定（评测命令 / 禁区 / 外部依赖 optional）
- 演进路径（v3.3 / v4.0）

## Claude Code 特有约定

### 沟通风格
- 偏好直接、结构化的反馈
- 主动指出过度设计 / 可简化方案
- 长期主义优先（不为短期讨巧损害长期演进）
- 按"产品体验 / 长期演进 / 面试讲述"三维度评估技术决策，不按工作量评估

### 代码改动纪律
- 大改前先勘探（看真实代码，不凭记忆）
- 每个 step 独立验证 + commit（小步前进，可回退）
- 评测 18/18 是硬底线（v3 唯一路径，每次大改前后跑评测）
- 任何改动失败立即停止并报告，不要"试试看修一下"

### 工具偏好
- 改文件用 str_replace（精确匹配）而非 sed
- 测试用 pytest（项目已采用 pytest 框架）
- 提交 commit 时遵循前缀规范：`feat()` / `chore:` / `docs:` / `fix:` / `test:` 等 conventional commits 风格

# v3.6.0 Release Notes：知识层基础设施

## 一句话定位

WealthPilot 从"结构化投资助手"升级为"有私有知识沉淀的投资 Agent"——决策不再只依赖结构化字段，而是同时利用投研原文、投资纪律定性描述、配置方法论等语义知识。

## 背景：v3.5 之前的痛点

| 模块 | 用户输入 | 系统消费方式 | 信息损失 |
|------|---------|------------|---------|
| 投研观点 | 完整 Markdown 对话/分析 | LLM 解析后只存结构化字段 | 原文推理过程未参与后续决策 |
| 投资纪律 | 定量规则 + 定性偏好文字 | 只消费定量规则 | 定性偏好"给人看但不参与决策" |
| 资产配置原则 | 配置方法论 | 硬编码在 Python 常量中 | 无法迭代，与代码耦合 |

核心矛盾：**用户沉淀了大量定性知识，但系统只消费了结构化的 20%，丢弃了语义信息的 80%。**

## 核心设计：File-as-Source-of-Truth

- **Markdown 文件是知识真相源**：人和机器共享同一份内容
- **Chroma 向量库是索引层**：机器检索用，可随时从 MD 文件全量重建
- **Git 管理知识演进**：每次知识更新有版本记录
- **失败可见可恢复**：index_status 机制追踪每个文件的索引状态

## 功能变化

### 新增

- **knowledge_base/ 目录**：投资纪律、投资风格、资产配置原则、投研观点均以 Markdown 文件存储
- **wp-retrieve-principles Skill**：从知识库语义检索用户原则类知识，与 wp-fetch-research 形成"标的知识 vs 用户原则"的语义二分法
- **投研观点 MD 落盘**：用户粘贴 Markdown 后，原文自动写入 knowledge_base/research_views/ 并触发索引
- **决策引用来源**：AI 决策输出末尾附"📚 参考来源"区块，列出知识来源文件路径和日期
- **LLM time_sensitivity 字段**：permanent / slow_decay / medium_decay / fast_decay 四档内容时效类型

### 改进

- **投资纪律**：定性偏好文字进入 RAG 参与决策（此前仅定量规则参与）
- **资产配置原则**：从 `WEALTHPILOT_ALLOCATION_PRINCIPLES` 硬编码常量迁移到知识库（常量保留为 fallback）
- **Education 意图**：从"LLM 通用知识"升级为"知识库 RAG + LLM"，回答更贴合用户的实际配置体系

## 技术决策回顾

### Skill 边界：方案 B（语义二分法）

评估了四个候选方案（A 单 Skill 多参数 / B 保留+新增1个 / C 新增3个 / D 删旧建新），方案 B 以 23 分显著领先（第二名 16 分）。

核心理由：
1. 不破坏现有 `wp-fetch-research` 接口
2. `general` 路由需要独立轻量 Skill
3. "标的知识 vs 用户原则"是投资决策的天然语义边界
4. 12 → 13 个 Skill，增量克制

完整评估见 `docs/v3.6/v3.6_skill_boundary_decision.md`。

### File-as-Source-of-Truth

选择 Markdown 文件而非 DB 作为知识真相源：
- **人机可读**：用户可以用任何编辑器修改知识，不需要通过 UI
- **Git 友好**：知识变更有版本历史，可 diff、可回滚
- **可重建性**：向量库损坏时，从 MD 文件全量重建即可恢复

## 已知限制与 v3.6.1 计划

### v3.6 MVP 不做的事

- 投资风格 UI 输入框（v3.6.1）
- 时效衰减打分（v3.6.2，代码已就位但 `decay.enabled=false`）
- LLM rerank（v3.6.2）
- Small-to-Big 展开（v3.6.2）
- 负样本与冲突样本评测（v3.6.2）

### v3.6.1 计划

- 投资风格 UI 输入框 + 保存到 style.md
- 时效类型标签可点击修改
- 投研观点卡片上新增"在文件中查看"链接
- 英文 query 召回质量评估（M1 已知约束，观察是否需要切换 embedding 模型）

## 工作量统计

| 指标 | 数量 |
|------|------|
| 开发批次 | 5 批（M1 → M5b），拆分 6 次提交周期 |
| Git commits | 13 个 |
| 新增测试 | 44 个（31 知识层 + 13 Skill） |
| 新增代码/配置文件 | 27 个 |
| 知识库 MD 文件 | 5 个（3 配置原则 + 1 纪律手册 + 1 风格模板） |
| 改动现有文件 | 7 个（research_service / ai_advisor / data_loader / discipline_service / executing_agent / expressing_agent / planning_agent） |
| 设计文档 | 4 份（PRD / 架构 / 现状勘探 / Skill 边界决策） |

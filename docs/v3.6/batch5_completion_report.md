# v3.6 第五批 M6 完成报告

## 完成清单

- [x] CHANGELOG.md 顶部新增 v3.6.0 条目（Added / Changed / Technical）
- [x] AGENTS.md 更新：版本号 v3.6.0、13 个 Skill 清单、知识检索语义二分法表格、知识层架构说明、演进路径更新
- [x] README.md 更新：版本号 v3.6、功能表格新增"私有知识库"行
- [x] `docs/v3.6/RELEASE_NOTES_v3.6.0.md` 新建：定位 / 痛点 / 设计 / 功能 / 技术决策 / 限制 / 统计
- [x] `docs/interview/v3.6_knowledge_layer_narrative.md` 新建：中英文各一版面试叙事脚本
- [x] `.gitignore` 确认：`backend/knowledge_base/_index/` 已在第 73 行
- [x] `pytest tests/knowledge/ tests/skills/` 44/44 通过

## v3.6 全程统计

| 指标 | 数量 |
|------|------|
| 开发批次 | 5 批（M1 → M5b），6 次提交周期 |
| Git commits | 13 个（不含本 M6 文档 commit） |
| 新增测试 | 44 个 |
| 新增代码/配置文件 | 27 个 |
| 改动现有文件 | 7 个 |
| 知识库 MD 文件 | 5 个 |
| 设计文档 | 4 份（PRD / 架构 / 现状勘探 / Skill 边界决策） |
| 完成报告 | 5 份（每批一份） |

## Songbin 待办（手动）

1. **Case 1 + Case 2 端到端验收**：启动后端服务器，分别问"理想汽车现在能加仓吗"和"美团现在要不要买"，检查引用来源区块
2. **Git Tag**：`git tag -a v3.6.0 -m "v3.6.0: 知识层基础设施" && git push origin v3.6.0`

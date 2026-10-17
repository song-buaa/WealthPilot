## 问题与结果

<!-- 需求/非目标、触发场景与最终变化；不要只粘贴提交列表。 -->

## 风险与复用证据

- 风险等级：L0 / L1 / L2 / L3（理由：）
- 已检索的服务、adapter、模型、接口及测试路径：
- 复用的权威入口 / 无法复用的具体缺口：
- 数据及状态 owner、账户/来源/币种/单位/时间/去重影响（无变化填 N/A + 理由）：
- 平行服务/模型/流程：无 / 有（必须附架构依据、迁移/退出方案及实施前人工批准记录）：

## 验证与验收

<!-- 勾选仅代表有证据；不适用写 N/A 及理由。代码变更保留全部既有 Gate；L0 本地无需重跑全部业务测试，但 CI 仍全跑。 -->

- [ ] 文档检查与 `git diff --check`
- [ ] 定向测试、全量 pytest、frontend lint/build、Offline M5 18/18（结果/日志；L0 可说明本地 N/A，CI 单列）
- [ ] exact HEAD 的四项 CI（完整 SHA / Run 链接 / 结果）
- [ ] 完整用户场景（输入 → 来源/状态 → Web/MCP → 确认/错误/重试；设备分别记录）或 N/A 理由
- [ ] 已确认 / 推断 / UNKNOWN / runtime-pending 与未验收项目分开，未用测试数量或单接口 PASS 替代业务验收

## 文档与生产边界

- [ ] Registry 维护判断已填写：新增能力检查已有覆盖并按需更新；权威入口/职责变化、废弃/迁移/合并必须同步更新；无能力变化注明“Registry 无需更新”及原因（参见[规则](../docs/PROJECT_GOVERNANCE.md#registry-维护检查)）
- [ ] 架构/接口/口径变化已同步 ARCHITECTURE_CURRENT / 对应契约，或 N/A + 理由
- [ ] 无凭证、数据库、备份、真实账户标识/财务 payload、无关用户工作
- [ ] Dev/临时库/Mock 与 Mini Prod 隔离；生产影响、备份/预检/部署验收/回滚说明，或 N/A + 理由
- [ ] 权限范围明确：PR / 合并 / Tag / 部署 / 同步写入 / 真实交易分别授权；仅 PR 时不得自动合并/部署

工作流：[治理文档](../docs/PROJECT_GOVERNANCE.md#9-标准开发流程与风险分级)；数据 owner：[当前架构](../docs/ARCHITECTURE_CURRENT.md)；复用索引：[能力登记](../docs/CAPABILITY_REGISTRY.md)。

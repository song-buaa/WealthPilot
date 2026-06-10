# WealthPilot 投研观点模块收敛 PRD

- 版本：v3.x（按你的排期确认，独立于 v3.10/3.11 IBKR 交易线）
- 模块：投研观点（Research）
- 状态：M0 排查完成 → 策略已定稿 → 待放行 M1
- 日期：2026-06-09（含 M0 两轮排查结论修订）

---

## 1. 背景与问题

投研观点承载两类性质不同的内容：长期价值判断（手动导入）与短期资讯信号（自动拉取 Alpha Vantage / AKShare）。二者被塞进同一套「观点库 + 待审核队列」，导致管理模型崩溃：V2 表 160 条全 pending_review、无人逐条 review；短期信号时效短、量大、会变质（决策检索仍在喂 2026-04-27 过期内容）。

**核心原则：持久化跟随时效性。** 长期判断进库、人工 sign-off；短期信号用时现取、用完即弃。

## 2. M0 排查关键结论（修订原计划的前提）

1. **存储双轨并存**：V1 三表（research_documents 3 / research_cards 3 / research_viewpoints 7）+ V2 单表（viewpoint_cards_v2 160）。
2. **前端「观点库」只读 V2**（GET /api/research/v2/cards，top_k=500）。V1 的 7 条（6 strong + 1 reference，人工 endorse 的长期研判）**前端无列表视图，处于"写得进、看不见"的半孤立状态**。
3. **V2 来源拆分**：短期自动拉取 156 条（alpha_vantage_* + akshare_*）/ 长期 user_upload 4 条。
4. **短期信号不写 knowledge_base/ 或 Chroma**：V2 ingest 链路只写 viewpoint_cards_v2。原 PRD「M1 知识层净化」是误判，已重写。
5. **手动导入在三写**（架构债根因）：V1 三表 + V2 副作用卡（_try_generate_v2_card）+ knowledge_base 落盘（_persist_to_knowledge_base，静默失败、research_views/ 一直为空）。
6. **决策引擎按标的直查 V2 表消费投研观点，不走 Chroma 语义检索** → research view 进 Chroma 这条线目前是摆设。

## 3. 策略决策（定稿）

- **D1｜V1 的 7 条 → 迁移进 V2**：迁成 user_upload 卡、status=approved（不进 pending_review），与现有 4 条 user_upload 去重；迁完冻结 V1 三表，停止写入。避免幽灵数据。
- **D2｜归档只动 V2 短期 156 条**：谓词 `source_type != 'user_upload'`，软删除（status=archived）。V1 迁入后，V2 长期库 = 原 4 + 迁入 7（去重后），全部可见。
- **D3｜knowledge_base/ + Chroma（research view 这条）默认砍掉**：手动导入不再往 knowledge_base/ 写；V2 SQLite 作为长期观点 source of truth；research view 语义层记为已放弃技术债。Chroma + wp-retrieve-principles 仍服务 discipline/philosophy/principles，不受影响。
  - ⚠️ 待确认开关：若决策引擎将来要对长期研判做**语义召回**（而非按标的直查），则改为"修好 V2→Chroma 写入"，单列里程碑。

## 4. 目标

1. 投研观点模块只服务长期价值判断。
2. 短期信号后端能力保留，但不入库、前端不展示、用时现取。
3. 模块收敛为两个 tab：资料导入 + 观点库。
4. 收敛后单一长期存储（V2），消除双轨与三写。
5. 全程数据安全：软删除归档、可回滚、操作前备份。

## 5. 非目标 / 本期不做

- 不删除/不改动 Alpha Vantage / AKShare 连接器与拉取逻辑（能力保留）。
- 不重构投资决策模块的引擎消费逻辑（仅移除「决策检索」可浏览 UI）。
- 不做物理删除（仅软删除归档）。
- 不复活 research view 的 markdown/Chroma 语义层（除非 D3 开关翻转）。

## 6. 里程碑

### M1 停止短期信号入库（修订自原"知识层净化"）
- 关闭/拦截两个 ingest 端点（v2_ingest_alpha_vantage、v2_ingest_akshare）向 viewpoint_cards_v2 写入。
- 连接器与拉取逻辑保留，供决策时现取。
- 验证：调用拉取，确认无新增 viewpoint_cards_v2 记录。

### M2 V1 → V2 长期内容合并
- 先备份 V1 三表 + V2 表 + knowledge_base/ 到 ~/.wealthpilot-backup/<日期>。
- 迁移脚本：V1 research_viewpoints 7 条 → V2 user_upload 卡，构造 facts_json/narrative_json/judgment_json，status=approved；与现有 4 条 user_upload 去重（按标的+thesis）。
- 迁完冻结 V1 三表（停止三写中的 V1 与 knowledge_base 写入）。

### M3 归档 V2 短期信号（软删除）
- 操作前再次确认备份存在。
- `UPDATE viewpoint_cards_v2 SET status='archived' WHERE source_type != 'user_upload' AND status != 'archived';`
- 预期归档 156、保留长期（4 + 迁入 7 去重后）；可回滚；归档后报告剩余长期条目数。
- 注意：归档集内含历史 endorse/disagree 卡（12 endorse + 1 disagree），软删可回滚，知悉即可。

### M4 前端收敛
- 移除资料导入 tab 内「自动拉取资讯」面板。
- 移除「待审核观点卡」队列。
- 移除「决策检索」整个 tab。
- 模块只剩：资料导入、观点库。

### M5 写入链路 + 审核瘦身
- 手动导入改为单写 V2（user_upload，入库即 approved），取消 V1 三写与 knowledge_base 落盘（依 D3）。
- 取消逐条 pending_review；来源单一、低频，重审核流程不再必要。

## 7. 数据安全约束（硬约束）

- 任何删除一律软删除/归档，禁止物理删除。
- M2、M3 操作前必须备份，归档可回滚。
- 归档谓词用 `!= 'user_upload'` 反向写法：保证"绝不误伤长期"在逻辑上成立（漏列短期类型只会少归档、不会错伤）。

## 8. 关联事项（本期外，建议跟进）

- 决策可解释性：移除「决策检索」浏览列表后，建议把"本次决策消费了哪些信号"作为快照附在投资决策/行动记录里，保住 principle-grounded + human sign-off 审计链路（涉及投资决策模块，单独立项）。
- 短期信号现取的短 TTL 缓存（应对 Alpha Vantage 频率限制），原则仍是用时新鲜、过期失效。
- research view 语义层（已放弃技术债）：如未来需对长期研判做语义召回，再评估 V2→Chroma 写入。

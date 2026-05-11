# M1-Step1 验证脚本使用说明

> 脚本：`scripts/m1_path_verification.py`  
> 目的：验证 PRD v1.4 §2.2 的产品哲学（"模糊输入主动推断"）在当前 v2.5.1 代码中是否成立  
> 预期工作量：≤30 分钟（含跑脚本 + 解读结果 + 决策）

---

## 一、为什么要做这个验证

PRD v1.4 §2.2 声明了产品哲学："用户输入未指明具体标的时，系统应基于持仓数据主动推断最匹配的目标，而非询问'您说的是哪只？'"

M0 的 PD_001/002/003 三个用例的 expected 全部基于这条哲学设计：
- `asset_required: true`
- `asset_in: [<推断出的具体持仓>]`
- `needs_clarification: false`

但当前代码（v2.5.1）的实际行为是否符合这条哲学，我们**没有验证过**。如果实际走的是澄清路径，那么 M0 的 3 个用例 expected 全部错了，**M1 后续 3.5 天的工作受影响**：

- 如果当前代码本来就支持推断 → M1 只需把现有逻辑包装到 ResearchAgent
- 如果当前代码走澄清 → M1 必须新增 `infer_target_from_holdings` Tool，工作量 +0.5 天

**所以 M1 第一步必须先做这个验证**，再决定后续路径。

---

## 二、如何运行

### 前置条件

1. 后端服务运行在 `http://127.0.0.1:8000`：
   ```bash
   cd backend && uvicorn main:app --reload
   ```

2. **建议但非必须**：按 `m0/schema/fixtures_v0.1.md` 布置 fixture 数据
   - 默认 portfolio 至少含 1 只浮盈持仓 + 1 只浮亏持仓
   - 否则推断结果会因真实持仓不匹配而失真，但脚本仍能跑

### 执行

```bash
AV_DEV_MOCK=1 python scripts/m1_path_verification.py
```

### 产出

- 控制台输出：路径分布 + 用例细节 + M1 行动建议
- `docs/m1_path_verification_report.md`：可贴回的 markdown 报告
- `docs/m1_path_verification_raw.json`：原始 SSE 数据（debug 用）

---

## 三、如何解读结果

脚本对每个用例输出 **4 种路径判定** 之一：

| 路径 | 判定条件 | 含义 |
|-----|---------|------|
| **A**（主动推断） | IntentResult.asset 非空 + 回答含具体持仓名 + 无澄清话术 | ✅ 符合产品哲学 |
| **B**（走澄清） | IntentResult.asset 为空 或 回答含澄清话术 | ⚠️ 不符合，需在 M1 实现推断 Tool |
| **C**（异常/中断） | has_exception=true 或 was_aborted=true | ❌ 需先排查 bug |
| **U**（人工判读） | 信号矛盾（如 asset 推断成功但回答含澄清话术） | ❓ 需人工判断 |

### 三种典型结局对应 M1 行动

#### 结局 1：3/3 全部 A → "代码已支持推断"

最理想的情况。证明：
- `app/decision_engine/data_loader.py` 在 asset 模糊时已有推断逻辑
- M1 的 ResearchAgent 只需把现有逻辑包装成 LangGraph 节点，**不需要新增 Tool**

**M1 行动**：v1.4 §4.2 ResearchAgent 中的 `infer_target_from_holdings` Tool 可降级为"已有能力的 wrapper"，不增加工作量。

#### 结局 2：3/3 全部 B → "代码完全走澄清"

最坏的情况，但可控。证明：
- 当前代码遇模糊输入直接进入 `_build_clarification_reply`
- M1 必须**先实现** `infer_target_from_holdings` Tool 再做后续 Agent 拆分

**M1 行动**：把 Step1 验证后第二件事就改成"实现推断 Tool"，工作量 +0.5 天吸收进 M1 总工作量。如果 M1 还是 3.5 天预算紧张，考虑从 M2 借 0.5 天（M2 的 Tool 抽象本来就要做，提前一部分）。

#### 结局 3：1-2/3 是 A，其余是 B → "推断逻辑部分覆盖"

最常见的情况。比如：
- PD_001（"涨了不少"）→ A，因为代码可能有"基于盈亏推断"逻辑
- PD_002（"持续亏损"）→ A，同上
- PD_003（"已经不轻了"）→ B，因为代码可能没有"基于仓位重量推断"逻辑

**M1 行动**：针对失败的具体 fuzz_type 补足推断维度。失败的用例会显示 fuzz_type 提示哪个维度缺失。

#### 结局 4：含 C → "需先修 bug"

不应继续往下做后续判断。先把 C 的 case 单独跟 Claude Code 排查（看 `aborted_reason` 字段或 `error` 字段）。

---

## 四、把结果贴回来给我

跑完之后，**有两种方式**把结果发给我：

### 方式 A（推荐）：贴 markdown 报告

把 `docs/m1_path_verification_report.md` 的全文贴给我（约 200 行）。我会基于此给出：
- M1 工作量调整（如果路径 B 占多数）
- ResearchAgent 中 `infer_target_from_holdings` Tool 的具体设计
- 是否需要修订 PRD v1.4 的某些字段

### 方式 B（最小信息）：贴控制台总结

如果不方便贴 markdown，至少贴最后几行的"路径分布 + 结论"那段，配上每个用例的一行汇总：

```
路径 A（主动推断）: 1/3
路径 B（走澄清）  : 2/3
路径 C（异常/中断）: 0/3
路径 U（人工判读）: 0/3

✅ PD_001: 路径 A, asset=贵州茅台, is_clarify=False
⚠️ PD_002: 路径 B, asset=(空), is_clarify=True
⚠️ PD_003: 路径 B, asset=(空), is_clarify=True
```

---

## 五、运行后的常见问题

**Q：脚本报"无法连接到后端"**  
A：先确认 `http://127.0.0.1:8000/api/decision/chat` 能正常响应。是否本地 backend 没起、或者端口不是 8000？

**Q：所有用例都是路径 C（异常）**  
A：通常是 portfolio 数据缺失或数据库未初始化。先在 Streamlit 前端走一遍正常流程确认决策接口能用，再回头跑这个脚本。

**Q：路径 U（人工判读）出现**  
A：脚本的关键词匹配可能误判。把对应用例的 `full_text` 贴给我，我帮你判断到底是 A 还是 B。

**Q：澄清话术正则没有覆盖到我们系统的某种澄清模式**  
A：把 `is_clarification: false` 但实际是澄清的用例的回答原文贴给我，我加正则。

---

## 六、跑完之后

如果路径 A ≥ 2/3：直接进 M1 主体工作（LangGraph + Agent 拆分）。

如果路径 A < 2/3：先解决推断 Tool 问题，再进 Agent 拆分。

无论哪种情况，跑完这个脚本就把 M1 的"假设验证"环节闭环了，可以正式开始 LangGraph 重构。

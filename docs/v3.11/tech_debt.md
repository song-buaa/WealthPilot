# v3.11 执行计划引擎 — 技术债务与修复记录

## 已修复 (2026-06-10)

### 前端交互 Bug

1. **Step E "生成计划"按钮无反应**
   - 根因: `plan_generated_ref` 使用 `useRef`，改值不触发 re-render；`setShowExecPlan(true)` 在已经为 true 时是 no-op
   - 修复: `useRef` → `useState(planGenerated/setPlanGenerated)`，guard/按钮/onClose 三处同步改
   - 文件: `Decision.tsx`

2. **"确认计划→加入投资行动"按钮无反应（作用域错误）**
   - 根因: `AiMessage` 是独立组件（非 Decision 嵌套），`onConfirmPlan` 回调内直接引用 `setPlanMetaForModal`/`setCurrentDraft`/`setDraftCardOpen`——这些 setter 不在 AiMessage 作用域内，运行时 ReferenceError 被 React 静默吞掉
   - 修复: 在 Decision 组件内新增 `handleConfirmExecPlan()` 回调，通过 `onConfirmExecPlan` prop 传给 AiMessage，AiMessage 的 `onConfirmPlan` 委托调用
   - 文件: `Decision.tsx`

3. **投资行动页空白（渲染 guard 漏检 planGrouped）**
   - 根因: `Action.tsx:262` 的 guard `if (activeIntents.length === 0 && orphanStrategies.length === 0) return null` 未检查 `planGrouped`，导致有执行计划策略时整个"已执行中"区块不渲染
   - 修复: guard 条件补充 `&& planGrouped.size === 0`
   - 文件: `Action.tsx`

### 后端 Bug

4. **孤儿 unknown 订单无法收敛**
   - 根因: `scan_orphan_orders` 只处理 submitted_to_broker → unknown，但 unknown + broker_order_id IS NULL 的订单（broker 从未收到）会永远卡在 unknown
   - 修复: 新增第二类孤儿处理：unknown + 无 broker_order_id → cancelled
   - 文件: `backend/services/action/order_poller.py`

### 工程基础设施

5. **TS 类型检查误用空配置**
   - 根因: `npx tsc --noEmit` 使用根 `tsconfig.json`（`"files": []` + references），实际不检查任何 app 代码，导致作用域错误、类型错误一路漏到运行时
   - 修复: 明确正确命令为 `npx tsc -p tsconfig.app.json --noEmit`；全量清理所有 TS 错误至 0 error（含 Decision/Action/Dashboard/Research/ActionDraftCard/ExecutionPlanPanel 等文件的 unused imports/vars + 类型错误）
   - 以后验证统一用: `npx tsc -p tsconfig.app.json --noEmit`

## 待办 (Backlog, 不在 v3.11 范围)

- **投资行动-行动记录时间线展示优化**: 草稿生成/已确认两条事件重复展示；rationale 长文密度高，需要折叠或摘要
- **Step E 观望主动发起入口**: 已上线完成，标记为 done

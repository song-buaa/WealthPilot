# 投研观点模块精修日志（回归修复轮）

**修复日期**：2026-03-20
**修复依据**：Manus AI 回归测试报告（基于上一轮修复后的版本）
**修复前状态**：核心流程可用，但仍有 2 个 P1 功能缺陷 + 1 个 P2 体验问题
**修复后状态**：稳定可用，主路径无已知阻塞问题

---

## 本轮修复的问题

### P1 · 功能缺陷

#### 1. Markdown 文件上传后内容依然不显示

**回归测试结论**：上一轮修复无效，问题换了表现形式——从「内容丢失」变为「后台抛 `StreamlitAPIException`」。

**根因**：上一轮的修复思路正确（用 session_state 缓存），但注入时序仍然错误：
```
st.text_area(key="ri_md_content")   ← widget 此时已实例化
...
st.session_state["ri_md_content"] = content  ← 在 widget 后修改，Streamlit 明确禁止
st.rerun()
```
Streamlit 规则：widget 的 `key` 对应的 `session_state` 值，**必须在该 widget 渲染之前设置完毕**，之后修改会抛出异常。

**修复**：将 `st.session_state["ri_md_content"] = content` 移到 `st.text_area(key="ri_md_content")` 之前执行，同时删除不必要的末尾 `st.rerun()`。

```python
# 正确顺序：
if uploaded_md is not None:
    if st.session_state.get("_ri_md_cache_name") != uploaded_md.name:
        content = uploaded_md.read().decode(...)
        st.session_state["ri_md_content"] = content   # ← 先写
        st.session_state["_ri_md_cache_name"] = uploaded_md.name

raw_content = st.text_area(..., key="ri_md_content")  # ← 后渲染，自动读取上面设置的值
```

**涉及文件**：`app_pages/research.py`（`_render_import()` 的 markdown 分支）

---

#### 2. Tab 导航跳转底层有 Streamlit 警告（部分修复 → 彻底修复）

**回归测试结论**：跳转功能表面可用，但日志仍有警告。底层原因与 Fix 1 相同：在 `segmented_control(key="research_nav")` 渲染之后，又用 `st.session_state["research_nav"] = ...` 修改其绑定的 state，违反 Streamlit 规则。

**修复**：引入独立中转变量 `_research_nav_target`，实现「先约定跳转意图 → 下次 rerun 时在 widget 前统一应用」的模式：

```python
# 触发跳转的地方（如解析完成后）：
st.session_state["_research_nav_target"] = _NAV_ITEMS[1]
st.rerun()

# render() 顶部，所有 widget 实例化之前：
if "_research_nav_target" in st.session_state:
    st.session_state["research_nav"] = st.session_state.pop("_research_nav_target")

active_nav = _research_nav()  # ← segmented_control 在此创建，此时 state 已正确设置
```

**涉及文件**：`app_pages/research.py`（`render()`、`_render_import()`、`_run_parse_for_doc()`）

---

### P2 · 体验优化

#### 3. 决策检索排序区分度极低（同分并列）

**回归测试结论**：搜「拼多多现在适不适合加仓」，美团和拼多多观点卡均得 18 分，无法区分。

**根因**：基础分（validity=10 + freshness ≤5 + approval=3）占比过高，最强基础分 = 18，而关键词匹配每次仅 +1，差距完全被掩盖。此外，自然语言中的标的名（如「拼多多」）与 `vp.object_name` 的精确匹配逻辑仅在显式传入 `object_name` 参数时才触发，不填参数时完全失效。

**修复内容**：

① **降低基础分权重**（避免淹没相关性得分）：

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| validity active | +10 | +5 |
| validity watch | +5 | +3 |
| freshness 上限 | +5 | +2 |
| approval strong | +3 | +2 |
| approval partial | +2 | +1 |
| approval reference | +1 | +0 |
| **最强基础分合计** | **18** | **9** |

② **新增「查询文本中含标的名」子串匹配（+15）**：

```python
# 不需要分词，直接用子串包含：
# query="拼多多现在适不适合加仓" → vp.object_name="拼多多" → +15
# query="拼多多现在适不适合加仓" → vp.object_name="美团"   → +0
if query and vp.object_name and not object_name:
    if vp.object_name.lower() in query.lower():
        score += 15
```

**修复后得分对比**（同条件测试）：

| 观点 | 修复前得分 | 修复后得分 |
|------|-----------|-----------|
| 拼多多观点（active，近期更新）| 18.0 | 22.x（+15 标的命中 + 基础 7.x）|
| 美团观点（active，近期更新） | 18.0 | 7.x（无标的命中，仅基础分）|

排序区分度从 0 提升到 15 分差距。

**涉及文件**：`app/research.py`（`_APPROVAL_WEIGHT`、`_VALIDITY_WEIGHT`、`_score_viewpoint()`）

---

## 涉及的核心文件

| 文件 | 改动类型 |
|------|---------|
| `app_pages/research.py` | Fix 1（MD 上传）+ Fix 2（Tab 导航） |
| `app/research.py` | Fix 3（评分权重重平衡） |

---

## 当前模块状态

### ✅ 稳定可用的流程

- 纯文本 / Markdown 文件上传 / 链接 / PDF 四种资料导入（文本+MD 完整可用）
- AI 解析：调用 gpt-4.1-mini 提炼结构化观点卡
- 解析完成后自动跳转到「候选观点卡」Tab（无日志警告）
- 候选卡审核：认可 / 修改后录入 / 仅保留 / 丢弃
- 正式观点库：5 维度筛选 + 关键词搜索 + 有效性内联修改
- 决策检索：自然语言含标的名时可有效区分排序

### ⚠️ 已知剩余限制

| 问题 | 说明 | 影响程度 |
|------|------|---------|
| 中文关键词切词弱 | 无空格的长句无法有效切词，需 jieba | 低（标的子串匹配已覆盖主要场景）|
| PDF / 链接自动抓取未实现 | 需手动粘贴正文 | 中（UI 有明确提示）|

---

## 后续建议

1. **中文分词**：`_keyword_score()` 函数内部引入 `jieba.cut()`，只需修改该函数，外部调用不变。适合单独小迭代处理。
2. **评分可观测性**：在检索结果中可选展示分项得分（标的匹配分、关键词分、基础分），便于用户理解和调试。
3. **RAG 升级路径**：`retrieve_research_context()` 接口不变，内部可替换为 embedding 检索，建议观点库积累至 30+ 条后评估。

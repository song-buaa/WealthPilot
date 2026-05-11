# 投研观点模块修复日志

**修复日期**：2026-03-20
**修复依据**：Manus AI 测试报告（WealthPilot 投研观点模块测试报告，2026-03-19）
**修复前状态**：模块不可用（P0 级阻塞 Bug，候选卡页面全红崩溃）
**修复后状态**：核心流程可用，主路径已验证通过

---

## 修复的问题

### P0 · 阻塞级

#### 1. 候选观点卡页面崩溃（`DetachedInstanceError`）

**问题**：点击「候选观点卡」Tab 后，页面抛出 `sqlalchemy.orm.exc.DetachedInstanceError`，原因是 `_render_cards()` 在 `finally` 块中关闭了 SQLAlchemy Session，随后代码在列表推导中访问了 `card.viewpoint` 和 `card.document` 这两个懒加载（lazy-load）关系，触发了对已关闭 Session 的二次访问。

**修复**：在查询时添加 `.options(joinedload(ResearchCard.viewpoint), joinedload(ResearchCard.document))`，改为预加载（eager-load），确保所有关联对象在 Session 关闭前已完整加载到内存。

**涉及文件**：`app_pages/research.py`

---

### P1 · 重要级

#### 2. Markdown 文件上传后内容丢失

**问题**：上传 `.md` 文件后，点击「仅保存」或「AI 解析」按钮，Streamlit 重新运行时 `st.file_uploader` 的状态重置为 `None`，导致 `raw_content` 走入空分支，触发「请粘贴资料正文」报错。

**修复**：在文件上传时将内容缓存至独立的 `session_state` key（`_ri_md_cache`），以文件名作为去重标志避免重复读取；`st.text_area` 改为始终从 session_state 读取，不再依赖 `value=` 参数覆盖。切换资料类型时主动清除旧缓存，避免数据串扰。

**涉及文件**：`app_pages/research.py`

---

#### 3. `saved_only` 状态的资料无法触发 AI 解析

**问题**：「已导入资料」列表下方的「待解析资料」下拉框，过滤条件硬编码为 `parse_status == "pending"`，`saved_only` 状态的资料被排除在外，用户无法找到触发解析的入口。

**修复**：过滤条件改为 `parse_status in ("pending", "saved_only")`，两种状态均可出现在待解析下拉框中。

**涉及文件**：`app_pages/research.py`

---

#### 4. 重复解析同一资料会生成多张候选卡

**问题**：`_run_parse_for_doc()` 函数每次执行都直接 `session.add(ResearchCard(...))`，对同一 `document_id` 多次触发解析会在数据库中产生多张重复卡片。

**修复**：解析前先 `filter_by(document_id=doc.id)` 查询是否已存在卡片。若存在则更新字段（覆盖旧内容并提示用户），若不存在则正常插入新卡片。

**涉及文件**：`app_pages/research.py`

---

#### 5. 重复导入同名资料无任何警告

**问题**：资料保存流程中没有标题查重逻辑，连续导入相同标题的资料不会有任何提示，导致底部列表出现同名重复条目，污染数据。

**修复**：在 `session.add(doc)` 前，按 `title` 查询已有资料。若存在同名记录，展示 `st.warning` 提示（包含已有资料的上传时间），并 `return` 中止当前提交。用户如需重新解析旧资料，可在下方列表直接触发。

**涉及文件**：`app_pages/research.py`

---

#### 6. 程序跳转 Tab 后未立即生效（导航状态错位）

**问题**：AI 解析完成后，代码通过 `st.session_state["research_nav"] = _NAV_ITEMS[1]` 跳转到「候选观点卡」，但没有调用 `st.rerun()`，新的 nav 值在当前帧未生效，可能导致页面停留在旧 Tab 或内容与 Tab 不一致。另外，路由末尾使用 `else` 兜底，任何 session_state 异常值都会渲染「决策检索」页，掩盖问题。

**修复**：
- 程序跳转 Tab 后统一调用 `st.rerun()` 强制当前帧刷新；跳转前用 `st.toast()` 代替 `st.success()` 传递反馈（`st.toast` 跨 rerun 可见，需 Streamlit ≥1.31）。
- 路由逻辑改为全量 `elif`，不再用 `else` 兜底；真正的非法值触发重置并 rerun。

**涉及文件**：`app_pages/research.py`

---

### P2 · 优化级

#### 7. 超长文本截断无提示

**问题**：AI 解析函数内部将正文截断至前 4000 字，但 UI 层无任何提示，用户不知道后半部分信息被丢弃。

**修复**：在提交表单时，若 `raw_content` 超过 4000 字符，在按钮下方展示 `st.warning`，明确告知截断范围和字数。

**涉及文件**：`app_pages/research.py`

---

#### 8. AI 生成标签数量过多

**问题**：测试中发现 AI 为单篇研报生成了 11 个标签，导致标签库臃肿，检索噪音增大。

**修复**：在 `generate_research_card()` 的 user prompt 末尾追加约束：「suggested_tags 最多输出 5 个最核心的标签，不要超过 5 个。」

**涉及文件**：`app/ai_advisor.py`

---

#### 9. 面包屑与 Tab 名称文案不一致

**问题**：页面顶部 caption 显示「决策检索」，但 Tab 名称为「检索测试」，两处不一致。

**修复**：将 `_NAV_ITEMS` 中的第四项从「🔍  检索测试」改为「🔍  决策检索」，与面包屑对齐。

**涉及文件**：`app_pages/research.py`

---

## 涉及的核心文件

| 文件 | 改动类型 |
|------|---------|
| `app_pages/research.py` | 主要修改（P0 + P1 × 5 + P2 × 2）|
| `app/ai_advisor.py` | 小改（Prompt 追加 tags 数量约束）|

---

## 当前模块状态

### ✅ 已可用的流程

- 纯文本 / Markdown / 链接 / PDF 四种资料导入（文本类完整可用，链接/PDF 需手动粘贴正文）
- AI 解析：调用 gpt-4.1-mini 提炼结构化观点卡，质量稳定
- 候选卡审核：查看、直接认可、编辑后录入、仅保留资料、丢弃
- 正式观点库：5 维度筛选、关键词搜索、有效性状态内联修改
- 决策检索：多因子评分召回，支持自然语言 + 标的 + 市场过滤

### ⚠️ 已知限制（未修复）

| 问题 | 说明 | 建议处理时机 |
|------|------|-------------|
| 中文检索分词弱 | 仅按标点和空格切分，「美团现在适合加仓吗」无法拆出「美团」和「加仓」 | 单独迭代引入 `jieba`，影响范围仅 `app/research.py` |
| Markdown 首次注入有一次额外 rerun | 文件上传后触发一次 `st.rerun()` 以注入缓存，用户会感知到轻微闪烁 | 可接受，暂不处理 |
| PDF / 链接自动抓取未实现 | 两种类型依赖用户手动粘贴正文，UI 已有提示 | 规划中，需引入第三方库 |

---

## 后续建议

1. **中文分词**：`app/research.py` 的 `_keyword_score()` 函数预留了升级接口，引入 `jieba` 只需修改该函数内部，不影响调用方。建议作为下一个小迭代独立处理。
2. **RAG 升级路径**：`retrieve_research_context()` 函数已设计为可替换内部实现，接口不变。当观点库积累到一定数量后，可替换为 embedding + 向量检索，调用方无需改动。
3. **AI 解析异步化**：当前解析为同步阻塞（`st.spinner`），对于长文本（接近 4000 字）可能需要 10-15 秒。若用户反馈体验差，可考虑后台线程 + 状态轮询方案。

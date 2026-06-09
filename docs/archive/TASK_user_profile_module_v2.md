# TASK: 用户画像与投资目标模块（v2.0 架构）

> 给 Claude Code 的开发任务文件。请严格按照本文件实现，不要自行扩展范围。

---

## 0. 开始前必读（强制执行）

本模块是全新功能，需要新建表、新建 API、新建前端页面。

开始前先读以下文件，了解项目约定，**不要凭记忆假设**：

```bash
cat app/models.py          # 了解现有 ORM 模型写法，新表要风格一致
cat app/database.py        # 了解 get_db / Base 的用法
cat backend/main.py        # 了解路由挂载位置
cat backend/api/portfolio.py   # 了解 API 文件的写法规范
cat frontend/src/App.tsx       # 了解路由注册位置
cat frontend/src/store/decisionStore.ts   # 了解前端状态管理约定
cat frontend/src/lib/api.ts    # 了解前端 API 调用封装约定
```

读完再动手。

---

## 1. 本次任务范围（严格限定）

**做这些：**
- [ ] 新建 `UserProfile` ORM 表（`app/models.py` 追加）
- [ ] 数据库迁移（新增表，不修改现有表）
- [ ] 后端 API：`backend/api/profile.py`
- [ ] 后端 Service：`backend/services/profile_service.py`
- [ ] 前端页面：`frontend/src/pages/UserProfile.tsx`
- [ ] 前端状态：`frontend/src/store/profileStore.ts`
- [ ] 注册路由（前端 `App.tsx` + 后端 `main.py`）
- [ ] 侧边栏加入口（`components/layout/` 里找 Sidebar 文件）

**不做：**
- 不修改 `Portfolio` 模型的现有字段
- 不修改任何已有 API 的逻辑
- 不实现行为校准、自动资产配置、外部 API 拉取风评

---

## 2. 数据库模型（`app/models.py` 追加）

在文件末尾追加，风格与现有模型保持一致：

```python
class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    version = Column(Integer, default=1)

    # 风险画像
    risk_source = Column(String, nullable=True)       # "external" | "ai"
    risk_provider = Column(String, nullable=True)     # "招商银行" | "ai_generated"
    risk_original_level = Column(String, nullable=True)  # "C3" | "A2" | "高"
    risk_normalized_level = Column(Integer, nullable=True)  # 1-5
    risk_type = Column(String, nullable=True)         # "保守型"|"稳健型"|"平衡型"|"成长型"|"进取型"
    risk_assessed_at = Column(DateTime, nullable=True)   # 用于判断是否过期（12个月）

    # 基础信息
    income_level = Column(String, nullable=True)       # "<10万"|"10-30万"|"30-100万"|">100万"
    income_stability = Column(String, nullable=True)   # "稳定"|"较稳定"|"波动"
    total_assets = Column(String, nullable=True)       # "<50万"|"50-200万"|"200-500万"|">500万"
    investable_ratio = Column(String, nullable=True)   # "<20%"|"20-50%"|"50-80%"|">80%"
    liability_level = Column(String, nullable=True)    # "无"|"低"|"中"|"高"
    family_status = Column(String, nullable=True)      # "未婚"|"已婚无子"|"已婚有子"|"退休"
    asset_structure = Column(String, nullable=True)    # "现金为主"|"固收为主"|"股票基金为主"|"多元配置"
    investment_motivation = Column(String, nullable=True)  # "新增资金"|"调整配置"|"市场波动调整"|"长期规划"
    fund_usage_timeline = Column(String, nullable=True)    # "1年内"|"1-3年"|"3年以上"|"不确定"

    # 投资目标
    goal_type = Column(String, nullable=True)          # JSON 字符串，存多选结果
    target_return = Column(String, nullable=True)      # "<5%"|"5-10%"|"10-20%"|">20%"
    max_drawdown = Column(String, nullable=True)       # "<5%"|"5-15%"|"15-30%"|">30%"
    investment_horizon = Column(String, nullable=True) # "<1年"|"1-3年"|"3-5年"|">5年"

    # AI 生成结果
    ai_summary = Column(Text, nullable=True)           # 自然语言总结
    ai_style = Column(String, nullable=True)           # "稳健"|"平衡"|"进取"
    ai_confidence = Column(String, nullable=True)      # "high"|"medium"|"low"
```

建表方式与现有项目保持一致（查看 `app/database.py` 的 `Base.metadata.create_all` 调用位置）。

---

## 3. 后端 Service（`backend/services/profile_service.py`）

### 3.1 风险等级标准化

```python
def normalize_risk_level(source_type: str, original_level: str) -> int:
    """
    source_type: "bank" | "broker" | "custom"
    
    银行(A1-A5): A1→1, A2→2, A3→3, A4→4, A5→5
    券商(C1-C6): C1→1, C2→1, C3→2, C4→3, C5→4, C6→5
    自定义:      低→2, 中→3, 高→4
    
    返回 int 1-5
    """
```

### 3.2 冲突检测

```python
def check_conflicts(max_drawdown: str, target_return: str, fund_usage_timeline: str) -> list[dict]:
    """
    规则1: fund_usage_timeline == "1年内" AND max_drawdown in ["15-30%", ">30%"]
    规则2: max_drawdown == "<5%" AND target_return in ["10-20%", ">20%"]
    
    有冲突返回:
    [{"type": "conflict", "message": "...", "options": ["优先收益", "优先稳健"]}]
    
    无冲突返回空列表
    """
```

冲突解决逻辑：
- 用户选"优先收益" → 提升 `risk_normalized_level`（+1，上限5）
- 用户选"优先稳健" → 将 `target_return` 降一档

### 3.3 AI 槽位提取

用 `openai` 直接调用，与项目现有方式一致（参考 `decision_engine/llm_engine.py` 的调用方式）。
模型统一用 `gpt-4.1-mini`（槽位提取不需要最强模型）。

```python
def extract_profile_from_text(user_input: str, existing_fields: dict) -> dict:
    """
    从自然语言提取画像字段。
    
    System prompt 要求：
    - 只提取能确定的字段，不确定的返回 null
    - 所有字段值必须在枚举值范围内（枚举值见数据模型）
    - 返回严格 JSON，无 markdown 包裹
    
    返回格式：
    {
      "extracted": {<字段名>: <值> | null},
      "missing_fields": ["字段名列表，优先级：total_assets > goal_type > max_drawdown > investment_horizon"],
      "next_question": "下一个追问的自然语言问题（如果 missing_fields 不为空）"
    }
    """
```

### 3.4 画像生成

```python
def generate_ai_profile(profile: UserProfile) -> dict:
    """
    调用 gpt-4.1 生成 summary 和 style。
    confidence 本地计算（不调 LLM）：
      - risk_source == "external" → "high"
      - 所有核心字段有值（risk_normalized_level, goal_type, max_drawdown, investment_horizon）→ "medium"
      - 否则 → "low"
    
    返回: {"summary": "...", "style": "稳健|平衡|进取", "confidence": "high|medium|low"}
    """
```

---

## 4. 后端 API（`backend/api/profile.py`）

风格参考 `backend/api/portfolio.py`，统一用 FastAPI + SQLAlchemy `get_db`。

```
GET    /api/profile          → 获取当前画像（不存在返回空结构，不报错）
PUT    /api/profile          → 保存/更新画像（upsert，始终只有一条记录）
POST   /api/profile/extract  → AI槽位提取（body: {text, existing_fields}）
POST   /api/profile/generate → 生成 AI 画像总结（触发 generate_ai_profile）
POST   /api/profile/conflicts → 冲突检测（body: {max_drawdown, target_return, fund_usage_timeline}）
GET    /api/profile/risk-expired → 检查风险评估是否过期（超过12个月返回 true）
```

在 `backend/main.py` 挂载：
```python
from backend.api import profile
app.include_router(profile.router, prefix="/api/profile", tags=["profile"])
```

---

## 5. 前端实现

### 5.1 状态管理（`frontend/src/store/profileStore.ts`）

参考 `decisionStore.ts` 的写法，用 `useState` + Context 或同样的状态管理方案。

存储内容：
- `profile`：完整 UserProfile 对象
- `step`：当前步骤（1-7）
- `conflicts`：当前冲突列表
- `isLoading`：加载状态

### 5.2 页面（`frontend/src/pages/UserProfile.tsx`）

分步骤实现，每步一个子组件，放在 `frontend/src/components/profile/` 下：

**Step 1 — 风险评估**
```
问：是否有银行/券商风险评估？
├── 有 → RadioGroup 选来源（银行A1-A5 / 券商C1-C6 / 自定义低中高）
│        → 输入等级 → 实时显示标准化结果（调 normalize 逻辑，前端本地计算即可）
└── 没有 → 显示3个问题（最大回撤/投资期限/投资目标下拉选择）
          → 调 POST /api/profile/generate 获取 AI 评估风险等级
```

显示过期提示：如果 `GET /api/profile/risk-expired` 返回 true，在顶部显示黄色 banner：
"当前风险评估可能已过期，建议重新评估"

**Step 2 — 基础信息**
- 9个字段全部用 `<select>` 下拉，选项值严格按枚举
- 标注哪些是必填（`*`），哪些可跳过
- 必填：`total_assets`、`income_stability`、`family_status`
- 可跳过的字段跳过后存 `null`

**Step 3 — 投资目标**
- `goal_type`：多选 checkbox（"资本增值" / "稳健增长" / "保值" / "现金流"）
- 其余3个字段：下拉选择

**Step 4 — AI 对话补全**（仅当有字段为 null 时显示）
- 文本输入框 + 发送按钮
- 调 `POST /api/profile/extract`
- 展示已提取字段（绿色标注）+ 下一个追问问题
- 最多3轮，超过后显示"跳过，继续"按钮
- 用户可随时点"跳过"

**Step 5 — 冲突检测**（自动触发，用户无感知进入）
- 调 `POST /api/profile/conflicts`
- 有冲突 → 展示冲突说明卡片 + 两个选项按钮
- 无冲突 → 直接进入 Step 6

**Step 6 — 确认与修改**

展示三组卡片：
1. 风险画像（risk_normalized_level / risk_type / 来源标签）
2. 基础信息（9个字段）
3. 投资目标（4个字段）

每个字段右侧有铅笔图标，点击后 inline 展开下拉修改。
**关键行为：任意字段修改保存后，自动调 `POST /api/profile/generate` 更新 AI 画像，不重新走流程。**

**Step 7 — 画像结果**

展示：
- AI 自然语言总结（大字，突出展示）
- 置信度 badge（high=绿/medium=黄/low=红）
- 风格标签（稳健/平衡/进取）
- "保存画像"按钮 → 调 `PUT /api/profile`，成功后跳转 `/dashboard`

### 5.3 路由注册（`frontend/src/App.tsx`）

在现有路由列表追加：
```tsx
<Route path="/profile" element={<UserProfile />} />
```

### 5.4 侧边栏入口

在 Sidebar 组件（查找 `components/layout/` 下的文件）中，在现有导航项末尾追加用户画像入口，图标用 `lucide-react` 的 `User` 或 `UserCircle`。

---

## 6. 样式约定

- 沿用项目现有 Tailwind CSS v4 类名风格，不引入新的 UI 库
- 颜色约定（与首页金色主题一致）：
  - 主色：参考现有页面的主色调，不要自己定义新颜色变量
  - 置信度：`high` → `text-green-600`，`medium` → `text-yellow-600`，`low` → `text-red-500`
- 分步骤进度条：顶部简单数字步骤指示（1/7 ... 7/7）即可

---

## 7. 验收清单

完成后逐项自检：

- [ ] `app/models.py` 追加了 `UserProfile` 表，现有模型未被修改
- [ ] 数据库建表成功（重启后端不报错）
- [ ] `GET /api/profile` 在无画像时返回空结构而非 404
- [ ] 风险标准化覆盖银行/券商/自定义三种来源
- [ ] 有外部风评时 `confidence` 自动为 `"high"`（本地计算，不调 LLM）
- [ ] 冲突检测覆盖两条规则
- [ ] Step 6 修改任意字段后自动重新生成 AI 画像，不重走流程
- [ ] `ai_summary` 不为空字符串（必须生成）
- [ ] 风险过期提示正常显示（mock 一条12个月前的记录验证）
- [ ] 前端路由 `/profile` 可正常访问
- [ ] 侧边栏有用户画像入口
- [ ] 未修改任何现有 API 和页面的核心逻辑

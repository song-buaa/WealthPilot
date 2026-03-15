# WealthPilot M1 项目说明文档

## 1. 项目概述

WealthPilot 是一个个人资产配置与智能投顾系统的最小可行产品（MVP）。M1 版本旨在快速验证核心业务逻辑：**数据输入 → 分析 → AI解读** 的闭环。用户可以通过上传标准的CSV文件，将分散在多个平台的资产和负债数据导入系统，系统会自动生成一份全面的资产负债全景分析报告，并通过AI大模型对当前配置的健康度、风险点进行自然语言解读，给出调整建议。

## 2. 系统架构

M1版本采用了一个务实、轻量、快速迭代的技术栈，其核心思想是“后端逻辑用专业框架，前端界面用低代码工具”，以在最短时间内交付一个功能完整的原型。

- **语言**: Python 3.11
- **Web框架 (UI)**: Streamlit
- **数据库**: SQLite
- **数据操作**: SQLAlchemy
- **AI模型调用**: OpenAI SDK

架构上遵循了关注点分离（Separation of Concerns）的原则，将系统划分为清晰的几个层次：

```mermaid
graph TD
    subgraph 用户界面 (UI Layer)
        A[streamlit_app.py]
    end

    subgraph 业务逻辑 (Business Logic)
        B[app/analyzer.py]
        C[app/csv_importer.py]
        D[app/ai_advisor.py]
    end

    subgraph 数据层 (Data Layer)
        E[app/models.py]
        F[(data/wealthpilot.db)]
    end

    subgraph 外部服务 (External Services)
        G[OpenAI API]
    end

    A --> B
    A --> C
    A --> D
    B --> E
    C --> E
    D --> G
    E --> F
```

- **UI Layer (`streamlit_app.py`)**: 负责所有页面的渲染、用户交互和流程控制。它作为总指挥，调用其他模块完成具体任务。
- **Business Logic Layer (`app/*.py`)**: 包含三个核心模块：
    - `analyzer.py`: 分析引擎，负责计算资产负债表和检测风险。
    - `csv_importer.py`: 数据导入器，负责解析CSV和写入数据库。
    - `ai_advisor.py`: AI顾问，负责调用LLM生成分析报告。
- **Data Layer (`app/models.py`, `data/`)**: 定义了所有的数据结构（通过SQLAlchemy ORM），并直接与SQLite数据库文件交互。
- **External Services**: 调用OpenAI的API来完成AI分析部分。

## 3. 项目文件结构说明

```
/WealthPilot
├── app/                     # 核心业务逻辑模块
│   ├── __init__.py          # 包初始化文件
│   ├── models.py            # 数据库模型定义 (SQLAlchemy ORM)
│   ├── csv_importer.py      # CSV数据导入与解析逻辑
│   ├── analyzer.py          # 资产分析与风险检测引擎
│   └── ai_advisor.py        # AI顾问，调用LLM生成报告
├── data/                    # 数据存储目录
│   └── wealthpilot.db       # SQLite 数据库文件
├── static/                  # 静态资源 (此版本未使用)
└── streamlit_app.py         # Streamlit 主应用入口文件
```

| 文件路径 | 功能说明 |
| :--- | :--- |
| `streamlit_app.py` | **主应用入口**。包含了所有UI界面的代码，负责页面路由、组件渲染、事件处理（如按钮点击），并调用`app`目录下的各个模块来完成后端任务。 |
| `app/models.py` | **数据模型定义**。使用SQLAlchemy定义了`Portfolio`, `Position`, `Liability`, `DecisionLog`四个核心数据表结构。这也是理解系统数据核心最关键的文件。**你的资产配置数据就存储在这里定义的表中。** |
| `app/csv_importer.py` | **数据导入器**。定义了持仓和负债CSV文件的格式，提供了将CSV内容解析为Python对象，并存入数据库的全部逻辑。 |
| `app/analyzer.py` | **分析引擎**。这是系统的“大脑”，`analyze_portfolio`函数负责计算资产负债表（总资产、净资产、各大类资产占比等），`check_deviations`函数负责将当前配置与策略目标对比，生成风险告警。 |
| `app/ai_advisor.py` | **AI顾问**。将`analyzer.py`生成的结构化数据（资产负债表、告警列表）组织成一个详细的Prompt，调用OpenAI的`gpt-4.1-mini`模型，获取自然语言分析报告。 |
| `data/wealthpilot.db` | **数据库文件**。你通过CSV导入的所有持仓和负债数据，最终都保存在这个SQLite数据库文件中。这是一个二进制文件，不建议直接编辑。 |

## 4. 核心数据流

理解系统如何工作，最快的方式是跟一遍核心数据流：

1.  **数据导入**: 
    - 用户在`streamlit_app.py`渲染的“数据导入”页面上传CSV文件。
    - `streamlit_app.py`接收到文件后，调用`app.csv_importer.parse_positions_csv`进行解析。
    - 解析成功后，调用`app.csv_importer.import_to_db`，将数据写入`data/wealthpilot.db`数据库。

2.  **资产分析**:
    - 用户切换到“资产全景”页面。
    - `streamlit_app.py`调用`app.analyzer.analyze_portfolio`函数。
    - `analyzer.py`从数据库读取所有持仓和负债，计算出`BalanceSheet`对象（包含总资产、各类资产占比等）。
    - `streamlit_app.py`再调用`app.analyzer.check_deviations`，传入`BalanceSheet`对象，获得风险告警列表。
    - `streamlit_app.py`使用Plotly库将`BalanceSheet`和告警列表渲染成图表和表格。

3.  **AI解读**:
    - 用户在“AI分析”页面点击“生成报告”按钮。
    - `streamlit_app.py`调用`app.ai_advisor.generate_portfolio_analysis`函数，并将上一步生成的`BalanceSheet`和告警列表作为参数传入。
    - `ai_advisor.py`将这些数据打包成一个JSON，构造一个详细的Prompt，发送给OpenAI API。
    - `streamlit_app.py`接收到AI返回的Markdown文本，直接在页面上渲染出来。

## 5. 如何运行

1.  **安装依赖**: 
    ```bash
    pip3 install streamlit sqlalchemy pandas openai plotly
    ```
2.  **设置API Key**: 
    确保你的环境变量中设置了 `OPENAI_API_KEY`。
    ```bash
    export OPENAI_API_KEY="sk-your-key"
    ```
3.  **启动应用**: 
    在项目根目录 (`/WealthPilot`) 下运行：
    ```bash
    streamlit run streamlit_app.py
    ```
4.  **访问应用**: 
    浏览器会自动打开或提示一个URL（通常是 `http://localhost:8501`），访问即可。

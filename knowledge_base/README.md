# WealthPilot 知识库

> v3.6 知识层基础设施的 Markdown 内容存储目录

## 目录结构

```
knowledge_base/
├── README.md                     # 本文件
├── _template.md                  # 新建知识文件模板
├── _index/                       # 系统生成区（勿手动修改）
│   ├── chroma_db/                # Chroma 向量库持久化
│   └── file_index.json           # 文件路径 → hash + 状态映射
├── allocation_principles/        # 资产配置原则（系统预置）
├── investment_principles/        # 投资纪律（用户定义）
├── investment_style/             # 投资风格（用户定义）
└── research_views/               # 投研观点（按标的分子目录）
```

## 使用方式

### 新增知识文件

1. 参照 `_template.md` 创建 `.md` 文件，放入对应子目录
2. 填写 YAML frontmatter 元数据
3. 系统启动时自动扫描并索引，或通过 API 手动触发重新索引

### 修改知识文件

直接编辑 MD 文件内容。系统启动时会检测 hash 变化并重新索引。

### 删除知识文件

直接删除 MD 文件。系统启动时会检测缺失并清理对应的向量索引。

## 注意事项

- `_index/` 目录是系统生成的索引数据，**已加入 .gitignore**，不要手动修改
- `_index/` 可以随时删除，系统会从 MD 文件全量重建
- 所有 `.md` 文件是知识的**唯一真相源**，向量库只是可重建的索引

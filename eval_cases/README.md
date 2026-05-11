# eval_cases — 评测用例库

## 目录用途

`eval_cases/cases/` 存放 18 个 YAML 评测用例（5 种意图覆盖），用于回归测试 v3 决策路径。

跑评测：
```bash
AV_DEV_MOCK=1 python scripts/m5_e2e_18_cases.py
```

## 命名历史

本目录原名 `m0/`，源于 v2.x 时代"Milestone 0"评测体系命名。v3.2 阶段
重命名为 `eval_cases/`，命名更直白。

git history 通过 `git mv` 保留（`git log --follow eval_cases/cases/xxx.yaml` 可追溯）。

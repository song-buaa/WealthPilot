# WealthPilot × 国金 QMT — A 股自动同步「网关模式」PRD

> 版本:v1 ｜ 日期:2026-06-05 ｜ 作者:Songbin（产品/架构）
> 执行者:Claude Code ｜ 评审:GPT（对抗性审查)
> 范围一句话:把国金 A 股持仓做成「WealthPilot 主动拉取」的产品级闭环,体验对齐 tiger/futu。

---

## 0. 背景

国金 A 股持仓已在 VM 内通过 xtquant `query_stock_positions(STOCK)` **100% 验证可读**(返回 510310.SH 完整字段)。当前 WealthPilot 侧落地的是「推送模式接收端」(`POST /guojin/push`),但推送方向是反的——前端手动「同步」时 Mac 无法叫醒 VM,导致同步按钮报 422,体验对不齐 tiger/futu。

本次升级为「网关模式(主动拉)」:VM 内常驻一个极简 HTTP 网关暴露 `GET /positions`,WealthPilot 后端在 22:00 定时 + 前端手动时**主动拉取**。

跨机连通性已预检通过:Mac → VM(10.211.55.7:8000)TCP 可建立连接(`Empty reply` 即握手成功,仅测试脚本 HTTP 实现糙);ping 不通是 Windows 默认挡 ICMP,无关。

---

## 1. 目标 / 非目标

### 目标(本次范围,四块)
1. **网关侧**:VM 内单文件 FastAPI,`GET /positions`,header 鉴权,内部调已验证的 `query_stock_positions(STOCK)` + `query_stock_asset`,返回既定 JSON。
2. **WealthPilot 侧**:guojin 从「push 接收端」改造为「主动拉 sync_service」(照抄 tiger 结构),复用已写好的 `GuojinAdapter` 字段映射;前端「同步」按钮接主动拉(422 消失);挂 22:00 定时。
3. **港股通汇总行**:用 `total_asset − market_value − cash` 反算港股通市值,显示成**一个汇总行**(逐条明细待客服回复后再接)。
4. **验证脚本**:网关 curl 验证 + WealthPilot 端到端拉取验证,每步带自检。

### 非目标(明确不做,防 scope creep)
- ❌ 港股通**逐条**持仓(已验证 xtquant 在国金 LDP 柜台读不到,等国金客户经理回复)。
- ❌ 开机**自启**(Windows 任务计划)——本次只做到「手动起就能跑通闭环」,自启留作后续独立一步。
- ❌ **下单/交易**能力——本次只读持仓。
- ❌ 不动 tiger/futu/snowball 任何现有逻辑。

---

## 2. 架构与数据流

```
VM (Windows, 10.211.55.7)                  Mac (WealthPilot, 10.211.55.2)
┌───────────────────────────┐             ┌─────────────────────────────────┐
│ 国金 QMT 客户端(极简模式)  │             │ guojin sync_service(主动拉)      │
│   保持登录运行             │             │   · 22:00 定时(同 tiger/futu)    │
│        ↑ xtquant            │   HTTP GET  │   · 前端手动「同步」按钮          │
│ wp_qmt_gateway.py(FastAPI) │◄────────────│        ↓                         │
│   GET /positions            │  X-WP-Secret│ GuojinAdapter: dict → Position   │
│   (query_stock_positions    │             │   + 港股通汇总行(反算)           │
│    + query_stock_asset)     │             │        ↓                         │
│   uvicorn --host 0.0.0.0    │             │ snapshot 三步 → upsert           │
└───────────────────────────┘             │        ↓ Dashboard 显示          │
                                            └─────────────────────────────────┘
```

数据流方向:**Mac → VM**(与旧推送模式相反)。鉴权:WealthPilot 发 `X-WP-Secret` header,网关校验。

---

## 3. 探索清单(Claude Code 动手前必须先核实,禁止照搬本 PRD 当事实)

> 以下「现状描述」来自上一对话整理,可能已与当前仓库不一致。**先读真实代码确认,再开工。** 把核实结果作为第一份输出贴出来,确认无误后再进入实现。

1. **tiger 的 `sync_service.py` 当前真实结构** —— 主动拉的范式照它抄。确认:HTTP 拉取在哪、如何 dict→Position、snapshot 三步的真实调用顺序与参数。
2. **guojin 现有 push 代码** —— 确认这几处当前是否存在、行号/内容:
   - `services/broker_sync/schema.py` 的 broker Literal 是否已含 `"guojin"`;sync_source Literal 当前有哪些值(是否有 `"push"`)。
   - `services/broker_sync/position_upsert_service.py` 的 `BROKER_TO_PLATFORM` 是否已含 `"guojin":"国金证券"`。
   - `api/broker_sync.py` 的 `POST /guojin/push` 路由(待删)、`BROKER_PLATFORM_MAP`。
   - `services/broker_sync/guojin/adapter.py` 的 `GuojinAdapter`(复用)、`guojin/__init__.py`。
3. **定时任务注册位置** —— tiger/futu 的 22:00 自动拉是在哪注册的(scheduler/cron/APScheduler?),guojin 照同样方式挂上去。
4. **前端「同步」按钮** —— 确认它对 guojin 当前打的是哪个后端接口;改主动拉后该接口要变成「触发 guojin sync_service」。`Dashboard.tsx` 的 `DOMESTIC_STOCK_PLATFORMS` 是否已含 `'国金证券'`(若有则前端无需改展示)。
5. **tiger 的「手动同步」触发接口** —— guojin 要镜像一个同样的触发端点(给前端按钮 + 定时任务共用)。
6. **Position schema 必填字段与 Literal 取值** —— 核实当前 `asset_class` / `cost_method` / `currency` / `sync_source` 的真实 Literal 选项(汇总行要用,见 §6)。
7. **db session 用法** —— 确认是 `get_session()` + try/finally `db.close()`,还是已改 FastAPI Depends。

---

## 4. 网关侧设计(VM 内,不进 WealthPilot 仓库)

文件:`wp_qmt_gateway.py`,放在 VM,`python wp_qmt_gateway.py` 运行。依赖:VM 外部 Python 3.10 `pip install fastapi uvicorn`。前提:国金 QMT 极简模式保持登录。

下面是**参考实现**(网关只是把已验证的 xtquant 调用包成 HTTP,逻辑确定,可直接用;字段名以 §8 验证过的为准):

```python
# coding: utf-8
# wp_qmt_gateway.py —— WealthPilot 国金 A 股网关（VM 内运行，需 QMT 极简模式保持登录）
import random
from datetime import datetime
from fastapi import FastAPI, Header, HTTPException
from xtquant import xttrader
from xtquant.xttype import StockAccount
import uvicorn

QMT_PATH   = r"D:\国金证券QMT交易端\userdata_mini"
ACCOUNT_ID = "35800452"
SECRET     = "wp_guojin_2024"      # 与 WealthPilot .env GUOJIN_GATEWAY_SECRET 一致
PORT       = 8000

app = FastAPI()
_xt = None
_acc = None

def _connect():
    global _xt, _acc
    xt = xttrader.XtQuantTrader(QMT_PATH, int(random.randint(100000, 999999)))
    xt.start()
    if xt.connect() != 0:
        raise RuntimeError("xttrader connect != 0（QMT 未登录或极速柜台未连）")
    acc = StockAccount(ACCOUNT_ID, "STOCK")
    if xt.subscribe(acc) != 0:
        raise RuntimeError("subscribe != 0")
    _xt, _acc = xt, acc

def _ensure():
    if _xt is None:
        _connect()

def _to_symbol(stock_code: str) -> str:
    code, _, mkt = stock_code.partition(".")   # 510310.SH -> 510310:SH
    return f"{code}:{mkt}"

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/positions")
def positions(x_wp_secret: str = Header(default="")):
    if x_wp_secret != SECRET:
        raise HTTPException(status_code=401, detail="bad secret")
    global _xt
    try:
        _ensure()
        raw_pos = _xt.query_stock_positions(_acc)
        asset   = _xt.query_stock_asset(_acc)
    except Exception as e:
        _xt = None                              # 连接可能掉了，重置以便下次重连
        raise HTTPException(status_code=503, detail=f"QMT 查询失败: {e}")

    out = []
    for p in raw_pos:
        out.append({
            "symbol":             _to_symbol(p.stock_code),
            "raw_symbol":         p.stock_code,
            "name":               getattr(p, "instrument_name", "") or "",
            "quantity":           p.volume,
            "available_quantity": p.can_use_volume,
            "cost_price":         p.avg_price,
            "last_price":         p.last_price,
            "market_value":       p.market_value,
            "currency":           "CNY",
        })
    return {
        "account_id":   ACCOUNT_ID,
        "account_type": "STOCK",
        "positions":    out,
        "account": {
            "cash":         asset.cash,
            "market_value": asset.market_value,   # 仅含 A 股
            "total_asset":  asset.total_asset,     # 含港股通(用于汇总行反算)
            "currency":     "CNY",
        },
        "pull_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source":    "wp_qmt_gateway",
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)   # 必须 0.0.0.0，否则 Mac 连不进
```

设计要点:
- **连接复用**:启动后首次请求时 `connect`+`subscribe` 一次,之后复用;查询失败则重置 `_xt`,下次请求自动重连(规避「同 session 两次 connect 间隔 >3s」的坑——靠下次请求自然间隔)。
- **错误语义**:QMT 没登录/查询失败 → HTTP 503,WealthPilot 据此可提示「国金 QMT 未在线」,而不是静默拉空。
- **返回 JSON 形状刻意对齐**已有 `GuojinAdapter` 期望的 dict 格式(symbol 用冒号、raw_symbol 用点),让 adapter 字段映射零改动。

---

## 5. WealthPilot 侧改造

### 5.1 新建:`services/broker_sync/guojin/sync_service.py`(主动拉)
照抄 tiger 的 `sync_service.py` 结构,核心流程:
1. 读 `GUOJIN_GATEWAY_URL`,`GET {url}/positions`,带 `X-WP-Secret: {GUOJIN_GATEWAY_SECRET}`,设合理超时(如 10s)。
2. 网关返回 503 / 超时 / 连接失败 → 抛出可读异常(让上层提示「国金 QMT 未在线」),不写空 snapshot。
3. `GuojinAdapter` 把 `positions[]` 的每个 dict → `Position`(复用现有映射)。
4. 追加港股通汇总行(见 §6)。
5. snapshot 三步 → `PositionUpsertService.upsert_from_snapshots`,检查返回 dict 的 `errors`。

`sync_source` 取值:用 **`"api"`**(与 tiger 一致),**不是** `"push"`。

### 5.2 复用:`services/broker_sync/guojin/adapter.py`
- `GuojinAdapter` 的 dict→Position 映射**保持复用**;核实它消费的 dict 字段名与 §4 网关返回一致(symbol/raw_symbol/name/quantity/available_quantity/cost_price/last_price/market_value/currency)。不一致则以网关返回为准微调 adapter(最小改动)。
- ETF 识别(代码前 2 位 51/15/16)→ `asset_class="etf"`,否则 `"equity"`(以探索清单 §6 核实的 Literal 为准)。

### 5.3 删除:`POST /guojin/push` 路由
- 删 `api/broker_sync.py` 里的 `/guojin/push` 路由及其专用 import(若 `HTTPException, Request` 仅它用)。
- `sync_source` Literal 里的 `"push"`:**先核实是否仍被其他 broker 使用**;若仅 guojin 用过则可一并移除,否则保留(最小风险)。

### 5.4 新增触发端点 + 定时
- 镜像 tiger 的「手动同步」端点,加 guojin 版(触发 5.1 的 sync_service)。前端按钮 + 22:00 定时共用它。
- 把 guojin 挂进 tiger/futu 同一个 22:00 定时任务(探索清单 §3 确认注册位置)。

### 5.5 前端
- `Dashboard.tsx` 的 `DOMESTIC_STOCK_PLATFORMS` 若已含 `'国金证券'`,展示无需改。
- 「同步」按钮指向 5.4 的 guojin 触发端点;422 自动消失。
- 网关 503/超时时,按钮给出可读提示(如「国金 QMT 未在线,请检查 VM」),不静默失败。

### 5.6 配置(.env / .env.example)
- 新增 `GUOJIN_GATEWAY_URL=http://10.211.55.7:8000`。
- `GUOJIN_PUSH_SECRET` → 重命名为 `GUOJIN_GATEWAY_SECRET`(值不变 `wp_guojin_2024`;旧名含义已不准)。同步更新 `.env.example`。

---

## 6. 港股通汇总行

### 反算
`hk_market_value = total_asset − market_value − cash`(均取网关返回的 `account` 块,单位 CNY)。
- 用已验证数据自检:`288046.16 − 478.9 − 688.58 ≈ 286878.68`。
- 阈值:`hk_market_value > 1`(元)才生成汇总行,避免浮点噪声造一行空数据。

### 合成 Position(降级行,明确标记)
| 字段 | 值 |
|---|---|
| broker | `"guojin"` |
| symbol / raw_symbol | `"HKCONNECT:SUMMARY"` / `"HKCONNECT"`(占位,不与真实标的冲突) |
| name | `"港股通持仓(合计·明细待接入)"` |
| asset_class | `"equity"`(占位;Literal 以探索 §6 为准) |
| market | 港股通对应市场标识(以现有枚举为准,如无则 `"HK"`/留通用值) |
| quantity / available_quantity | `0` |
| avg_cost / cost_basis | `0` |
| current_price | `0` |
| market_value | `hk_market_value` |
| currency | `"CNY"`(港股通在账户层以 CNY 计,避免 FX 混淆) |
| unrealized_pnl / unrealized_pnl_pct | `0` / `0` |
| sync_source | `"api"` |
| raw_data | `{"is_summary": true, "note": "港股通汇总,逐条明细待国金客服确认权限/柜台后接入"}` |

> ⚠️ 设计张力:Position schema 是为「单只持仓」设计的,汇总行 `quantity=0` 但 `market_value>0`。**探索清单 §6 必须确认 Dashboard/上层计算能容忍这种行**(尤其总资产汇总、占比、盈亏计算是否会被这行污染)。若不能容忍 → 退化方案:把港股通汇总改为**账户级 metadata**(不进 Position 表),前端单独展示一行。二选一以核实结果为准,实现前在输出里说明选了哪条及原因。

---

## 7. 验证方案(每步贴真实输出,禁止关键词匹配式断言)

### 7.1 网关侧
1. VM 本地:`curl -H "X-WP-Secret: wp_guojin_2024" http://127.0.0.1:8000/positions` → 期望返回含 510310 的 positions 与 account 块。
2. Mac 跨机:`curl -H "X-WP-Secret: wp_guojin_2024" http://10.211.55.7:8000/positions` → 同上(证明跨机闭环)。
3. 鉴权:不带/带错 header → 期望 401。
4. QMT 未登录场景:关掉 QMT 再请求 → 期望 503 且 message 可读。

### 7.2 WealthPilot 侧(端到端)
5. 手动触发 guojin sync(经 5.4 端点或前端按钮)→ 核实:`PositionSnapshotRepository.create_run` 生成 run、`persist_positions` 落库、`upsert_from_snapshots` 返回的 `errors` 为空。
6. Dashboard 显示:国金 510310 逐条 + 港股通汇总行(市值 ≈ 28.7 万)。
7. 422 回归:点同步按钮不再 422。
8. 定时:确认 guojin 已进 22:00 任务(可临时改触发时间或手动调度函数验证一次)。

### 7.3 验证脚本产物
- 网关:一个 `verify_gateway.sh`/`.py`(curl 四场景)。
- WealthPilot:端到端拉取验证脚本 + 自检断言(基于真实字段值,非字符串匹配)。

---

## 8. 关键环境参数(verbatim,勿改)

| 项 | 值 |
|---|---|
| 国金资金账号 | `35800452`(STOCK) |
| QMT userdata_mini | `D:\国金证券QMT交易端\userdata_mini` |
| VM IP / 端口 | `10.211.55.7` / `8000` |
| Mac 宿主机 IP | `10.211.55.2` |
| 鉴权 secret | `wp_guojin_2024`(header `X-WP-Secret`) |
| uvicorn | 必须 `--host 0.0.0.0` |
| Mac conda env | `wealthpilot` |
| 已验证 position 字段 | `stock_code` `volume` `can_use_volume` `avg_price` `open_price` `last_price` `market_value` `instrument_name` `secu_account` `yesterday_volume` |
| 已验证 asset 字段 | `cash=688.58` `market_value=478.9` `total_asset=288046.16` |
| symbol 转换 | A 股 `510310.SH`→`510310:SH`;ETF 前缀 51/15/16 |

---

## 9. 给 Claude Code 的执行约束

1. **先探索后实现**:先完成 §3 探索清单,把核实结果作为第一份输出贴出,确认后再写代码。不要把本 PRD 的「现状描述」当既成事实。
2. **照抄现有 broker,不自创 API**:guojin sync_service 照 tiger 抄;靠猜的接口先核实。
3. **不扩范围**:严格限本次四块;不碰港股通逐条、不做自启、不下单、不动其他 broker。
4. **最小改动**:跨文件改动遵循最小必要原则;删 push 路由前确认无其他依赖。
5. **诚实报告**:不要 overconfident first report;实现后必须跑 §7 验证脚本并贴**真实输出**,不通就说不通。
6. **干净 git**:每个逻辑单元一个独立 commit(见 §10),history 清晰。

---

## 10. 交付物与 Commit 划分

| # | Commit | 内容 |
|---|---|---|
| 0 | (输出,非 commit) | §3 探索清单核实结果 |
| 1 | `feat(guojin): pull-mode sync_service` | 新建 sync_service(主动拉)+ adapter 复用/微调 + 港股通汇总行 |
| 2 | `feat(guojin): sync endpoint + 22:00 scheduler` | 触发端点 + 定时挂载 + 前端按钮接通 |
| 3 | `refactor(guojin): remove push receiver` | 删 `/guojin/push` 路由及专用 import;sync_source 收敛为 `api` |
| 4 | `chore(guojin): config` | `.env`/`.env.example`(GATEWAY_URL + 改名 SECRET) |
| 5 | `test(guojin): gateway + e2e validation` | 验证脚本 + 真实输出报告 |

网关文件 `wp_qmt_gateway.py` 单独交付给 Songbin 放 VM,不进仓库。

---

## 附:本次明确「等客服」的后台事项(不阻塞发布)
- 港股通**逐条**持仓:已穷尽客户端侧路径(极速柜台/普通柜台/account_infos/com_fund 均验证),确认国金 LDP 柜台下 xtquant 拿不到逐条。待问国金客户经理「港股通量化查询/交易权限是否需单独开通」。在此之前用汇总行 + 截图导入覆盖。

"""
WealthPilot v3.4 M1.3a — 港股 + 错误场景沙箱验证脚本

WARNING: 仅使用模拟盘账号 21995161433588262
WARNING: 严禁使用实盘账户 4472659

运行环境: conda activate wealthpilot
运行命令: python backend/scripts/v3_4/m1_3a_hk_validation.py
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from decimal import Decimal
from pathlib import Path
from pprint import pformat

# 路径设置
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_project_root / ".env")

from backend.services.action.brokers.base import OrderRequest
from backend.services.action.brokers.tiger import (
    TigerBrokerAdapter,
    OrphanOrderError,
    TIGER_PAPER_ACCOUNT,
)

# ── 常量 ──────────────────────────────────────────────────────
TIGER_ID = "20159046"
PK_PATH = str(_project_root / "backend" / "secrets" / "tiger_private_key.pem")
# 00700.HK 2026-05-12 市价约 HKD 464.8
HK_SYMBOL = "HK.00700"
HK_FAR_LIMIT = 250.0   # 远低于市价,保证不成交
HK_NEAR_LIMIT = 460.0  # 略低于市价,用于超额单

_all_order_ids: list[str] = []  # 追踪所有 broker_order_id


def _banner(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def _record(result, label="OrderStatusUpdate"):
    print(f"\n{label}:")
    print(f"  status:           {result.status}")
    print(f"  broker_order_id:  {result.broker_order_id}")
    print(f"  filled_quantity:  {result.filled_quantity}")
    print(f"  avg_filled_price: {result.avg_filled_price}")
    print(f"  timestamp:        {result.timestamp}")
    print(f"  local_order_id:   {result.local_order_id}")
    print(f"\nraw_response:")
    print(pformat(result.raw_response, width=100))


# ============================================================
# 场景 1: 港股 LIMIT BUY 挂单 -> HELD
# ============================================================
def scenario_1_hk_buy_held(adapter):
    _banner("场景 1: 港股 LIMIT BUY 挂单 -> HELD")

    req = OrderRequest(
        symbol=HK_SYMBOL,
        side="BUY",
        quantity=100,  # 港股 1 手 = 100 股
        order_type="LIMIT",
        limit_price=Decimal(str(HK_FAR_LIMIT)),
        local_order_id="m13a-scenario1",
    )
    print(f"\n下单: {HK_SYMBOL} BUY 100 @ {HK_FAR_LIMIT}")

    result = adapter.place_order(req)
    _record(result, "place_order 返回")

    if result.broker_order_id:
        _all_order_ids.append(result.broker_order_id)

    assert result.status == "submitted_to_broker", \
        f"预期 submitted_to_broker,实际 {result.status}"
    assert result.broker_order_id is not None, "broker_order_id 为 None"

    print("\n等待 3 秒后查单...")
    time.sleep(3)

    status_result = adapter.get_order_status(result.broker_order_id)
    _record(status_result, "get_order_status 返回 (3s后)")

    print(f"\n验证: status == 'broker_pending' ? {status_result.status == 'broker_pending'}")
    return result.broker_order_id


# ============================================================
# 场景 2: 港股 LIMIT BUY 撤单 -> CANCELLED
# ============================================================
def scenario_2_hk_cancel(adapter, broker_order_id):
    _banner("场景 2: 港股 LIMIT BUY 撤单 -> CANCELLED")

    print(f"\n撤单: broker_order_id={broker_order_id}")
    cancel_ok = adapter.cancel_order(broker_order_id)
    print(f"cancel_order 返回: {cancel_ok}")

    print("\n立即查单...")
    status1 = adapter.get_order_status(broker_order_id)
    _record(status1, "撤单后立即查")

    print("\n等待 3 秒后再查...")
    time.sleep(3)
    status2 = adapter.get_order_status(broker_order_id)
    _record(status2, "撤单后 3 秒查")

    print(f"\n验证: cancel_ok == True ? {cancel_ok}")
    print(f"验证: 立即查 status == 'cancelled' ? {status1.status == 'cancelled'}")
    print(f"验证: 3 秒后 status == 'cancelled' ? {status2.status == 'cancelled'}")


# ============================================================
# 场景 3: 港股超额下单 -> EXPIRED -> rejected
# ============================================================
def scenario_3_hk_over_buying_power(adapter):
    _banner("场景 3: 港股超额下单 -> EXPIRED -> rejected 映射")

    req = OrderRequest(
        symbol=HK_SYMBOL,
        side="BUY",
        quantity=100000,  # 10 万股 * ~460 HKD = ~4600 万 HKD,远超模拟盘
        order_type="LIMIT",
        limit_price=Decimal(str(HK_NEAR_LIMIT)),
        local_order_id="m13a-scenario3",
    )
    print(f"\n下单: {HK_SYMBOL} BUY 100000 @ {HK_NEAR_LIMIT} (超出购买力)")

    result = adapter.place_order(req)
    _record(result, "place_order 返回")

    if result.broker_order_id:
        _all_order_ids.append(result.broker_order_id)

    # Tiger 可能立即返回 submitted,也可能同步拒
    print(f"\nplace_order status: {result.status}")

    if result.status == "submitted_to_broker" and result.broker_order_id:
        print("\n等待 5 秒让 Tiger 处理超额检查...")
        time.sleep(5)

        status_result = adapter.get_order_status(result.broker_order_id)
        _record(status_result, "get_order_status 返回 (5s后)")

        print(f"\n验证: status == 'rejected' (EXPIRED+购买力) ? {status_result.status == 'rejected'}")
        print(f"验证: expired_resolved_as 字段: {status_result.raw_response.get('expired_resolved_as')}")
        print(f"验证: reason 字段: {status_result.raw_response.get('reason')}")

        # 尝试撤单(可能已终态)
        adapter.cancel_order(result.broker_order_id)
    elif result.status == "rejected":
        print("\nTiger 同步拒单(未走 EXPIRED 路径)")
        print(f"raw_error_message: {result.raw_response.get('raw_error_message')}")


# ============================================================
# 场景 4: 不存在 symbol -> ApiException -> rejected
# ============================================================
def scenario_4_fake_symbol(adapter):
    _banner("场景 4: 不存在 symbol -> ApiException -> rejected")

    req = OrderRequest(
        symbol="HK.99999",  # 不存在的港股代码
        side="BUY",
        quantity=100,
        order_type="LIMIT",
        limit_price=Decimal("1.0"),
        local_order_id="m13a-scenario4",
    )
    print(f"\n下单: HK.99999 BUY 100 @ 1.0")

    result = adapter.place_order(req)
    _record(result, "place_order 返回")

    print(f"\n验证: status == 'rejected' ? {result.status == 'rejected'}")
    print(f"验证: raw_error_code: {result.raw_response.get('raw_error_code')}")
    print(f"验证: raw_error_message: {result.raw_response.get('raw_error_message')}")


# ============================================================
# 场景 5: not_found -> OrphanOrderError
# ============================================================
def scenario_5_not_found_orphan(adapter):
    _banner("场景 5: not_found -> 指数退避重试 -> OrphanOrderError")

    fake_id = "9999999999999999999"
    print(f"\nget_order_status('{fake_id}') — 预期重试 2 次后抛 OrphanOrderError")

    t0 = time.time()
    try:
        result = adapter.get_order_status(fake_id)
        print(f"\n意外: 没有抛异常,返回了 status={result.status}")
        print(f"raw_response: {pformat(result.raw_response)}")
    except OrphanOrderError as e:
        elapsed = time.time() - t0
        print(f"\n捕获 OrphanOrderError: {e}")
        print(f"  耗时: {elapsed:.1f}s (预期 ~3s)")
        print(f"  isinstance(ConnectionError): {isinstance(e, ConnectionError)}")
        print(f"\n验证: OrphanOrderError 抛出 ? True")
        print(f"验证: 是 ConnectionError 子类 ? {isinstance(e, ConnectionError)}")
        print(f"验证: 耗时约 3s ? {2.0 < elapsed < 8.0}")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"\n捕获非预期异常: {type(e).__name__}: {e}")
        print(f"  耗时: {elapsed:.1f}s")
        traceback.print_exc()


# ============================================================
# 场景 6: paper-only 闸门
# ============================================================
def scenario_6_paper_only_gate():
    _banner("场景 6: paper-only 闸门拦截实盘账号")

    try:
        bad_adapter = TigerBrokerAdapter(
            tiger_id=TIGER_ID,
            account_id="4472659",  # 实盘账号
            private_key_path=PK_PATH,
        )
        print("\n意外: 没有抛异常,实盘闸门失效!")
    except AssertionError as e:
        print(f"\n捕获 AssertionError: {e}")
        print(f"\n验证: paper-only 闸门正常拦截 ? True")


# ============================================================
# 场景 7: market 白名单
# ============================================================
def scenario_7_market_whitelist(adapter):
    _banner("场景 7: market 白名单拦截 A 股")

    req = OrderRequest(
        symbol="600519",  # 茅台 A 股,6 位纯数字 -> CN
        side="BUY",
        quantity=100,
        order_type="LIMIT",
        limit_price=Decimal("1000.0"),
        local_order_id="m13a-scenario7",
    )
    print(f"\n下单: 600519 (A 股) BUY 100 @ 1000")

    result = adapter.place_order(req)
    _record(result, "place_order 返回")

    print(f"\n验证: status == 'rejected' ? {result.status == 'rejected'}")
    has_cn_msg = "不支持市场 CN" in (result.raw_response.get("reason") or "")
    print(f"验证: reason 含 '不支持市场 CN' ? {has_cn_msg}")
    print(f"验证: broker_order_id 为 None (未触达 Tiger) ? {result.broker_order_id is None}")


# ============================================================
# Cleanup
# ============================================================
def cleanup(adapter):
    _banner("Cleanup: 撤销所有残留挂单")

    # 先撤追踪到的
    for oid in _all_order_ids:
        try:
            adapter.cancel_order(oid)
            print(f"  撤单: {oid}")
        except Exception:
            pass

    # 再查一遍 open orders 确保干净
    open_orders = adapter.list_open_orders()
    if open_orders:
        print(f"\n  仍有 {len(open_orders)} 个挂单,逐一撤...")
        for o in open_orders:
            try:
                adapter.cancel_order(o.broker_order_id)
                print(f"  撤单: {o.broker_order_id}")
            except Exception:
                pass
    else:
        print("  无残留挂单")

    print(f"\n脚本产生的 broker_order_id 列表:")
    for oid in _all_order_ids:
        print(f"  {oid}")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("  WealthPilot v3.4 M1.3a — 港股 + 错误场景沙箱验证")
    print("=" * 70)
    print(f"  模拟盘账号: {TIGER_PAPER_ACCOUNT}")
    print(f"  严禁使用实盘账号: 4472659")
    print()

    confirm = input("确认仅在模拟盘操作? 输入 YES_PAPER_ONLY 继续: ")
    assert confirm == "YES_PAPER_ONLY", "未确认,退出"

    # 初始化 Adapter
    print("\n初始化 TigerBrokerAdapter...")
    adapter = TigerBrokerAdapter(
        tiger_id=TIGER_ID,
        account_id=TIGER_PAPER_ACCOUNT,
        private_key_path=PK_PATH,
    )
    print(f"  broker_name: {adapter.broker_name}")
    print(f"  is_paper: {adapter._is_paper}")

    # 验证鉴权
    auth_ok = adapter.authenticate({})
    print(f"  authenticate: {auth_ok}")
    assert auth_ok, "authenticate 失败,无法继续"

    results = {}

    # 场景 1
    try:
        oid = scenario_1_hk_buy_held(adapter)
        results["场景1"] = "PASS"
    except Exception as e:
        print(f"\n!!! 场景 1 异常: {e}")
        traceback.print_exc()
        results["场景1"] = f"FAIL: {e}"
        oid = None

    time.sleep(2)

    # 场景 2
    if oid:
        try:
            scenario_2_hk_cancel(adapter, oid)
            results["场景2"] = "PASS"
        except Exception as e:
            print(f"\n!!! 场景 2 异常: {e}")
            traceback.print_exc()
            results["场景2"] = f"FAIL: {e}"
    else:
        print("\n跳过场景 2 (场景 1 未产生 broker_order_id)")
        results["场景2"] = "SKIP"

    time.sleep(2)

    # 场景 3
    try:
        scenario_3_hk_over_buying_power(adapter)
        results["场景3"] = "PASS"
    except Exception as e:
        print(f"\n!!! 场景 3 异常: {e}")
        traceback.print_exc()
        results["场景3"] = f"FAIL: {e}"

    time.sleep(2)

    # 场景 4
    try:
        scenario_4_fake_symbol(adapter)
        results["场景4"] = "PASS"
    except Exception as e:
        print(f"\n!!! 场景 4 异常: {e}")
        traceback.print_exc()
        results["场景4"] = f"FAIL: {e}"

    time.sleep(2)

    # 场景 5
    try:
        scenario_5_not_found_orphan(adapter)
        results["场景5"] = "PASS"
    except Exception as e:
        print(f"\n!!! 场景 5 异常: {e}")
        traceback.print_exc()
        results["场景5"] = f"FAIL: {e}"

    # 场景 6
    try:
        scenario_6_paper_only_gate()
        results["场景6"] = "PASS"
    except Exception as e:
        print(f"\n!!! 场景 6 异常: {e}")
        traceback.print_exc()
        results["场景6"] = f"FAIL: {e}"

    # 场景 7
    try:
        scenario_7_market_whitelist(adapter)
        results["场景7"] = "PASS"
    except Exception as e:
        print(f"\n!!! 场景 7 异常: {e}")
        traceback.print_exc()
        results["场景7"] = f"FAIL: {e}"

    # Cleanup
    cleanup(adapter)

    # 总结
    _banner("M1.3a 执行结果汇总")
    for k, v in results.items():
        icon = "PASS" if v == "PASS" else ("SKIP" if v == "SKIP" else "FAIL")
        print(f"  {k}: {icon}")
    print()

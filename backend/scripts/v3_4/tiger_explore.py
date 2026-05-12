"""
WealthPilot v3.4 M0 -- Tiger SDK 沙箱探索脚本

WARNING: 此脚本仅用于模拟盘账号 21995161433588262
WARNING: 严禁切换到实盘账户 4472659

================================================
运行环境要求(必读):
================================================
本脚本必须用 conda 环境 wealthpilot(Python 3.11.13)运行,
因为 tigeropen 3.5.8 装在这个环境里。

推荐运行方式:
    conda activate wealthpilot
    python backend/scripts/v3_4/tiger_explore.py

或直接用绝对路径:
    /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python \\
        backend/scripts/v3_4/tiger_explore.py

WARNING: 严禁用系统 Python 3.7.1(/usr/local/bin/python3)运行,
   tigeropen 没有装在那里,会 ImportError。
================================================
"""
import os
import sys
import time
from pathlib import Path
from pprint import pprint

# -- 路径设置 --
_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent.parent.parent  # backend/scripts/v3_4 -> project root
sys.path.insert(0, str(_project_root / "backend"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_project_root / ".env")

from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.common.util.signature_utils import read_private_key
from tigeropen.common.consts import Language, Market, Currency
from tigeropen.trade.trade_client import TradeClient
from tigeropen.trade.domain.order import OrderStatus
from tigeropen.common.util.order_utils import market_order, limit_order

# -- 常量 --
TIGER_ID = "20159046"
SANDBOX_ACCOUNT = "21995161433588262"
REAL_ACCOUNT = "4472659"  # 仅用于安全校验,绝不使用
PK_PATH = _project_root / "backend" / "secrets" / "tiger_private_key.pem"


# ============================================================
# Step 0: 环境与 SDK 初始化
# ============================================================
_trade_client = None  # 全局复用


def step_0_init():
    """环境自检 + 初始化 ClientConfig (sandbox_debug=True) + TradeClient"""
    global _trade_client

    print("=" * 60)
    print("环境自检")
    print("=" * 60)
    print(f"Python 可执行文件: {sys.executable}")
    print(f"Python 版本: {sys.version}")
    print(f"工作目录: {os.getcwd()}")

    # 强校验: 必须是 wealthpilot conda 环境
    expected_env_path = "anaconda3/envs/wealthpilot"
    if expected_env_path not in sys.executable:
        print()
        print("!! 错误: 当前 Python 不在 wealthpilot conda 环境中")
        print(f"   当前路径: {sys.executable}")
        print(f"   期望包含: {expected_env_path}")
        print()
        print("请先激活环境: conda activate wealthpilot")
        sys.exit(1)

    print("Python 环境正确")
    print()

    # 检查 tigeropen 安装
    import tigeropen
    try:
        tiger_version = tigeropen.__VERSION__
    except AttributeError:
        tiger_version = "(版本读取失败,继续运行)"
    print(f"tigeropen 版本: {tiger_version}")
    print()

    print("=" * 60)
    print("Step 0: SDK 初始化")
    print("=" * 60)

    # 私钥检查
    assert PK_PATH.exists(), f"私钥文件不存在: {PK_PATH}"
    pk_content = PK_PATH.read_text()
    print(f"私钥路径: {PK_PATH}")
    print(f"私钥指纹(前16字符): {pk_content.strip().splitlines()[1][:16]}...")
    print(f"私钥格式: {'PKCS#1' if 'RSA PRIVATE KEY' in pk_content else 'PKCS#8'}")

    # ClientConfig -- sandbox_debug 已在 tigeropen 3.5.8 中废弃
    # 模拟盘通过 account ID 自动识别(AccountUtil.is_paper_account)
    config = TigerOpenClientConfig(sandbox_debug=False)
    config.private_key = read_private_key(str(PK_PATH))
    config.tiger_id = TIGER_ID
    config.account = SANDBOX_ACCOUNT
    config.language = Language.zh_CN

    print(f"\nClientConfig:")
    print(f"  tiger_id: {config.tiger_id}")
    print(f"  account: {config.account}")
    print(f"  is_paper: {config.is_paper}")

    # 安全校验
    assert config.account == SANDBOX_ACCOUNT, \
        f"FATAL: 账号不是模拟盘! 当前={config.account}, 期望={SANDBOX_ACCOUNT}"
    assert config.account != REAL_ACCOUNT, \
        "FATAL: 检测到实盘账号,立即终止!"

    _trade_client = TradeClient(config)
    print(f"\nTradeClient 创建成功")
    print(f"  type: {type(_trade_client)}")


# ============================================================
# Step 1: 账户查询(验证鉴权 + 模拟盘是否可访问)
# ============================================================
def step_1_account():
    print("\n" + "=" * 60)
    print("Step 1: 账户查询")
    print("=" * 60)

    # 1a. 托管账户列表
    print("\n--- 1a. get_managed_accounts() ---")
    accounts = _trade_client.get_managed_accounts()
    print(f"type: {type(accounts)}")
    pprint(accounts)
    print(f"\n模拟盘 {SANDBOX_ACCOUNT} 在列表中: {SANDBOX_ACCOUNT in str(accounts)}")

    # 1b. 资产快照
    print("\n--- 1b. get_assets() ---")
    assets = _trade_client.get_assets(account=SANDBOX_ACCOUNT)
    print(f"type: {type(assets)}")
    if hasattr(assets, '__dict__'):
        pprint(vars(assets))
    else:
        pprint(assets)

    # 1c. 持仓
    print("\n--- 1c. get_positions() ---")
    positions = _trade_client.get_positions(account=SANDBOX_ACCOUNT)
    print(f"type: {type(positions)}")
    print(f"持仓数量: {len(positions) if positions else 0}")
    if positions:
        for i, pos in enumerate(positions[:3]):
            print(f"\n  [{i}]:")
            pprint(vars(pos) if hasattr(pos, '__dict__') else pos)


# ============================================================
# Step 2: 最小买单(LIMIT 单买 1 股 SPY)
# ============================================================
_step2_order_id = None


def step_2_place_buy_order():
    global _step2_order_id

    print("\n" + "=" * 60)
    print("Step 2: 最小买单 (1 股 SPY, LIMIT)")
    print("=" * 60)

    # 构造 LIMIT 单,价格设为远低于市价以避免立即成交
    # SPY 2026-05-12 市价约 $739,设 $370 (约 50%) 保证不成交
    SYMBOL = "SPY"
    LIMIT_PRICE = 370.0
    QUANTITY = 1

    print(f"\n下单参数:")
    print(f"  symbol: {SYMBOL}")
    print(f"  side: BUY")
    print(f"  quantity: {QUANTITY}")
    print(f"  order_type: LIMIT")
    print(f"  limit_price: {LIMIT_PRICE}")
    print(f"  account: {SANDBOX_ACCOUNT}")

    # 构造订单对象
    order = limit_order(
        account=SANDBOX_ACCOUNT,
        contract=_make_us_stock_contract(SYMBOL),
        action='BUY',
        limit_price=LIMIT_PRICE,
        quantity=QUANTITY,
    )

    print(f"\n订单对象 (下单前):")
    print(f"  type: {type(order)}")
    pprint(vars(order) if hasattr(order, '__dict__') else order)

    # 提交订单
    print("\n--- place_order() ---")
    result = _trade_client.place_order(order)
    print(f"\nplace_order 返回值:")
    print(f"  type: {type(result)}")
    pprint(vars(result) if hasattr(result, '__dict__') else result)

    # 提取 broker_order_id
    print(f"\n--- 提取 broker_order_id ---")
    print(f"  place_order() 返回值 (int): {result}")
    print(f"  order.order_id = {getattr(order, 'order_id', 'N/A')} (本地自增序号,不是 broker ID)")
    print(f"  order.id = {getattr(order, 'id', 'N/A')} (= place_order 返回值,这才是 broker_order_id)")

    # place_order() 返回的 int 就是 broker_order_id,也等于 order.id
    _step2_order_id = order.id
    print(f"\n记录的 broker_order_id: {_step2_order_id}")


# ============================================================
# Step 3: 查单(get_order 状态轮询)
# ============================================================
def step_3_get_order_status(broker_order_id=None):
    oid = broker_order_id or _step2_order_id
    assert oid is not None, "没有可用的 broker_order_id,请先运行 Step 2"

    print("\n" + "=" * 60)
    print(f"Step 3: 查单 (broker_order_id={oid})")
    print("=" * 60)

    for i, delay in enumerate([0, 3, 10]):
        if delay > 0:
            print(f"\n等待 {delay} 秒...")
            time.sleep(delay)

        print(f"\n--- 第 {i+1} 次查询 (累计等待 {delay}s) ---")
        order = _trade_client.get_order(id=oid)
        print(f"type: {type(order)}")
        pprint(vars(order) if hasattr(order, '__dict__') else order)

        # 重点关注 status
        if hasattr(order, 'status'):
            print(f"\n  >>> status = {order.status} (type: {type(order.status)})")
        if hasattr(order, 'remaining_quantity'):
            print(f"  >>> remaining_quantity = {order.remaining_quantity}")
        if hasattr(order, 'filled_quantity'):
            print(f"  >>> filled_quantity = {order.filled_quantity}")

    # 打印 OrderStatus 枚举,看 SDK 定义了哪些状态值
    print("\n--- Tiger SDK OrderStatus 枚举 ---")
    if hasattr(OrderStatus, '__members__'):
        for name, val in OrderStatus.__members__.items():
            print(f"  {name} = {val.value if hasattr(val, 'value') else val}")
    else:
        print(f"  OrderStatus = {OrderStatus}")
        pprint(dir(OrderStatus))


# ============================================================
# Step 4: 撤单
# ============================================================
def step_4_cancel_order():
    print("\n" + "=" * 60)
    print("Step 4: 撤单")
    print("=" * 60)

    # 先挂一笔新买单(价格离市价更远,保证不成交)
    SYMBOL = "SPY"
    LIMIT_PRICE = 350.0  # 远离市价 ($739)
    QUANTITY = 1

    print(f"\n挂新买单: {SYMBOL} @ {LIMIT_PRICE} x {QUANTITY}")
    order = limit_order(
        account=SANDBOX_ACCOUNT,
        contract=_make_us_stock_contract(SYMBOL),
        action='BUY',
        limit_price=LIMIT_PRICE,
        quantity=QUANTITY,
    )
    _trade_client.place_order(order)

    cancel_oid = order.id  # place_order() 后 order.id 被设置为 broker_order_id
    print(f"新订单 broker_order_id: {cancel_oid}")

    # 等 2 秒让订单状态稳定
    print("\n等待 2 秒...")
    time.sleep(2)

    # 查单(撤单前)
    print("\n--- 撤单前查单 ---")
    pre = _trade_client.get_order(id=cancel_oid)
    print(f"status = {getattr(pre, 'status', 'N/A')}")
    pprint(vars(pre) if hasattr(pre, '__dict__') else pre)

    # 撤单
    print("\n--- cancel_order() ---")
    cancel_result = _trade_client.cancel_order(id=cancel_oid)
    print(f"cancel_order 返回值:")
    print(f"  type: {type(cancel_result)}")
    pprint(vars(cancel_result) if hasattr(cancel_result, '__dict__') else cancel_result)

    # 立即查单(撤单后)
    print("\n--- 撤单后立即查单 ---")
    post1 = _trade_client.get_order(id=cancel_oid)
    print(f"status = {getattr(post1, 'status', 'N/A')}")

    # 等 3 秒再查(观察是同步还是异步)
    print("\n等待 3 秒...")
    time.sleep(3)
    print("\n--- 撤单后 3 秒查单 ---")
    post2 = _trade_client.get_order(id=cancel_oid)
    print(f"status = {getattr(post2, 'status', 'N/A')}")
    pprint(vars(post2) if hasattr(post2, '__dict__') else post2)


# ============================================================
# Step 5: 错误场景探测
# ============================================================
def step_5_error_scenarios():
    print("\n" + "=" * 60)
    print("Step 5: 错误场景探测")
    print("=" * 60)

    # 5a. 超出购买力: 10000 股 SPY @ $730 (约 730 万 vs 模拟盘 100 万)
    print("\n--- 5a. 超出购买力 (10000股 SPY @ 730) ---")
    order_a = limit_order(
        account=SANDBOX_ACCOUNT,
        contract=_make_us_stock_contract("SPY"),
        action='BUY',
        limit_price=730.0,
        quantity=10_000,
    )
    result_a = _trade_client.place_order(order_a)
    print(f"type: {type(result_a)}")
    pprint(vars(result_a) if hasattr(result_a, '__dict__') else result_a)
    print(f"order.id (broker_order_id) = {order_a.id}")
    print(f"order.status = {order_a.status}")
    # 查一下这笔超额单的状态
    time.sleep(2)
    print("\n--- 5a 续: 查超额单状态 ---")
    oa_status = _trade_client.get_order(id=order_a.id)
    pprint(vars(oa_status) if hasattr(oa_status, '__dict__') else oa_status)
    print(f"status = {oa_status.status}")
    # 撤掉
    _trade_client.cancel_order(id=order_a.id)
    print("已撤超额单")

    # 5b. 不存在的 symbol
    print("\n--- 5b. 不存在的 symbol (FAKESYMBOL123) ---")
    order_b = limit_order(
        account=SANDBOX_ACCOUNT,
        contract=_make_us_stock_contract("FAKESYMBOL123"),
        action='BUY',
        limit_price=1.0,
        quantity=1,
    )
    result_b = _trade_client.place_order(order_b)
    print(f"type: {type(result_b)}")
    pprint(vars(result_b) if hasattr(result_b, '__dict__') else result_b)
    print(f"order.status = {order_b.status}")

    # 5c. 错误的 broker_order_id
    print("\n--- 5c. 错误的 broker_order_id ---")
    fake_order = _trade_client.get_order(id=9999999999)
    print(f"type: {type(fake_order)}")
    pprint(vars(fake_order) if hasattr(fake_order, '__dict__') else fake_order)

    # 5d. 撤一个不存在的单
    print("\n--- 5d. 撤不存在的单 ---")
    cancel_result = _trade_client.cancel_order(id=9999999999)
    print(f"type: {type(cancel_result)}")
    pprint(vars(cancel_result) if hasattr(cancel_result, '__dict__') else cancel_result)


# ============================================================
# Step 6 (可选): 港股最小调用
# ============================================================
def step_6_hk_market():
    print("\n" + "=" * 60)
    print("Step 6: 港股最小调用 (00700 腾讯)")
    print("=" * 60)

    order = limit_order(
        account=SANDBOX_ACCOUNT,
        contract=_make_hk_stock_contract("00700"),
        action='BUY',
        limit_price=300.0,  # 远低于市价
        quantity=100,  # 港股最低 1 手 = 100 股
    )
    print(f"\n下单参数:")
    pprint(vars(order) if hasattr(order, '__dict__') else order)

    result = _trade_client.place_order(order)
    print(f"\nplace_order 返回值:")
    print(f"  type: {type(result)}")
    pprint(vars(result) if hasattr(result, '__dict__') else result)

    hk_oid = order.id
    if hk_oid:
        print(f"\n港股订单 broker_order_id: {hk_oid}")
        time.sleep(2)
        hk_status = _trade_client.get_order(id=hk_oid)
        print(f"status = {getattr(hk_status, 'status', 'N/A')}")
        # 撤单
        _trade_client.cancel_order(id=hk_oid)
        print("已撤单")


# ============================================================
# Step 7 (可选): A 股最小调用
# ============================================================
def step_7_cn_market():
    print("\n" + "=" * 60)
    print("Step 7: A 股最小调用 (600519 贵州茅台)")
    print("=" * 60)

    # Tiger 的 A 股 contract 构造方式可能不同,先尝试
    from tigeropen.trade.domain.contract import Contract
    contract = Contract(symbol="600519", currency="CNH", sec_type='STK', market='CN')

    order = limit_order(
        account=SANDBOX_ACCOUNT,
        contract=contract,
        action='BUY',
        limit_price=1000.0,  # 远低于市价
        quantity=100,  # A 股最低 1 手 = 100 股
    )
    print(f"\n下单参数:")
    pprint(vars(order) if hasattr(order, '__dict__') else order)

    result = _trade_client.place_order(order)
    print(f"\nplace_order 返回值:")
    print(f"  type: {type(result)}")
    pprint(vars(result) if hasattr(result, '__dict__') else result)

    cn_oid = order.id
    if cn_oid:
        print(f"\nA 股订单 broker_order_id: {cn_oid}")
        time.sleep(2)
        cn_status = _trade_client.get_order(id=cn_oid)
        print(f"status = {getattr(cn_status, 'status', 'N/A')}")
        _trade_client.cancel_order(id=cn_oid)
        print("已撤单")


# ============================================================
# 辅助函数
# ============================================================
def _make_us_stock_contract(symbol):
    """构造美股 contract 对象"""
    from tigeropen.trade.domain.contract import Contract
    return Contract(symbol=symbol, currency='USD', sec_type='STK', market='US')


def _make_hk_stock_contract(symbol):
    """构造港股 contract 对象"""
    from tigeropen.trade.domain.contract import Contract
    return Contract(symbol=symbol, currency='HKD', sec_type='STK', market='HK')


# ============================================================
# Main
# ============================================================
def _run_step(name, fn, *args):
    """安全执行单个 Step,异常不中断后续 Step"""
    try:
        fn(*args)
    except Exception as e:
        print(f"\n!!! Step {name} 异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    print(f"\n=== {name} 完成,继续 ===")
    time.sleep(2)


if __name__ == "__main__":
    print("=" * 60)
    print("WealthPilot v3.4 M0 -- Tiger SDK 沙箱探索")
    print("=" * 60)
    print(f"WARNING: 当前模式: SANDBOX")
    print(f"WARNING: 模拟盘账号: {SANDBOX_ACCOUNT}")
    print(f"WARNING: 严禁使用实盘账号: {REAL_ACCOUNT}")
    print()

    # 自动模式: 断言模拟盘账号,无需人工输入
    assert SANDBOX_ACCOUNT == "21995161433588262", "禁止使用非模拟盘账号"
    print("模拟盘账号校验通过,自动执行模式启动")
    print()

    _run_step("Step 0", step_0_init)
    _run_step("Step 1", step_1_account)
    _run_step("Step 2", step_2_place_buy_order)
    _run_step("Step 3", step_3_get_order_status)
    _run_step("Step 4", step_4_cancel_order)
    _run_step("Step 5", step_5_error_scenarios)
    _run_step("Step 6 (港股)", step_6_hk_market)
    _run_step("Step 7 (A 股)", step_7_cn_market)

    print("\n" + "=" * 60)
    print("全部 Step 完成!")
    print("=" * 60)

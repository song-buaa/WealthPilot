"""
Tiger OpenAPI 连通性测试脚本（只读）。

使用方法:
    1. 把 tiger_openapi_config.properties 的 private_key_pk8 内容
       提取出来,转成 PEM 格式,保存到 backend/secrets/tiger_private_key.pem
    2. 在 backend/.env 中填入 TIGER_ID、TIGER_ACCOUNT 等字段
    3. 运行: cd backend && python -m scripts.test_tiger_connectivity

本脚本只调用查询类接口(get_assets / get_positions),不下单,不改任何账户状态。
代码层有 READ_ONLY_MODE 护栏,任何写操作类调用会被拒绝。
"""
import sys
from pathlib import Path

# 让脚本能从 backend 根目录导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# 加载 .env（优先 backend/.env，其次项目根 .env）
_backend_root = Path(__file__).parent.parent
_project_root = _backend_root.parent
load_dotenv(_backend_root / ".env")
load_dotenv(_project_root / ".env")

from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.common.util.signature_utils import read_private_key
from tigeropen.common.consts import Language
from tigeropen.trade.trade_client import TradeClient

from core.config import settings


# 只读模式下被禁止的方法名前缀/关键字
WRITE_METHOD_KEYWORDS = (
    "place_order", "cancel_order", "modify_order",
    "preview_order", "submit_order",
)


class ReadOnlyTradeClient:
    """TradeClient 的只读包装器,拒绝任何写操作类调用。"""

    def __init__(self, inner: TradeClient):
        self._inner = inner

    def __getattr__(self, name):
        if settings.tiger_read_only_mode:
            for kw in WRITE_METHOD_KEYWORDS:
                if kw in name.lower():
                    raise RuntimeError(
                        f"READ_ONLY_MODE 已开启,拒绝调用写操作方法: {name}"
                    )
        return getattr(self._inner, name)


def build_client_config() -> TigerOpenClientConfig:
    """构造老虎 SDK 配置（实盘账户）。"""
    if not settings.tiger_id:
        raise RuntimeError("TIGER_ID 未配置,请检查 backend/.env")
    if not settings.tiger_private_key_path:
        raise RuntimeError("TIGER_PRIVATE_KEY_PATH 未配置")
    if not settings.tiger_account:
        raise RuntimeError("TIGER_ACCOUNT 未配置")

    # 私钥路径相对项目根目录
    pk_path = _project_root / settings.tiger_private_key_path
    if not pk_path.exists():
        raise FileNotFoundError(f"私钥文件不存在: {pk_path}")

    config = TigerOpenClientConfig()
    config.private_key = read_private_key(str(pk_path))
    config.tiger_id = settings.tiger_id
    config.account = settings.tiger_account
    config.language = Language.zh_CN
    return config


def test_connectivity():
    print("=" * 60)
    print("Tiger OpenAPI 连通性测试（只读模式）")
    print("=" * 60)

    config = build_client_config()
    print(f"Tiger ID: {config.tiger_id}")
    print(f"账户: {config.account} (实盘)")
    print(f"只读模式: {settings.tiger_read_only_mode}")
    print()

    raw_client = TradeClient(config)
    trade_client = ReadOnlyTradeClient(raw_client)

    print("[1/2] 查询账户资产...")
    try:
        assets = trade_client.get_assets(account=config.account)
        print(f"  返回类型: {type(assets)}")
        print(f"  返回内容: {assets}")
    except Exception as e:
        print(f"  ❌ 失败: {type(e).__name__}: {e}")
        return False

    print()
    print("[2/2] 查询持仓列表...")
    try:
        positions = trade_client.get_positions(account=config.account)
        print(f"  返回类型: {type(positions)}")
        print(f"  持仓数量: {len(positions) if positions else 0}")
        if positions:
            first = positions[0]
            print(f"  第一条持仓字段: {dir(first)}")
            print(f"  第一条持仓内容: {first.__dict__ if hasattr(first, '__dict__') else first}")
            # 打印前 3 条持仓的简要信息
            print()
            print("  前 3 条持仓预览:")
            for i, pos in enumerate(positions[:3]):
                print(f"    [{i}] {pos.__dict__ if hasattr(pos, '__dict__') else pos}")
    except Exception as e:
        print(f"  ❌ 失败: {type(e).__name__}: {e}")
        return False

    print()
    print("✅ 连通性测试通过")
    return True


if __name__ == "__main__":
    success = test_connectivity()
    sys.exit(0 if success else 1)

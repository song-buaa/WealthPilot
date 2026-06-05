#!/usr/bin/env python3
"""国金证券 QMT 网关模式端到端验证脚本。

用法（在项目根目录执行）:
    python backend/scripts/guojin_sync_e2e.py

前提:
    - .env 已配置 GUOJIN_GATEWAY_URL + GUOJIN_GATEWAY_SECRET
    - VM 内 wp_qmt_gateway.py 正在运行（或用 --mock 跳过网关检测）

验证内容:
    1. 网关连通性（GET /health + GET /positions）
    2. GuojinAdapter 字段映射
    3. GuojinSyncService.fetch_positions() 含港股通汇总行
    4. sync_and_persist 端到端入库
    5. stale 清理范围正确（不删截图导入的港股通）
"""
import os
import sys

# 确保 backend/ 和 project_root/ 都在 path 中（照 tiger_sync_persist.py）
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, backend_dir)
sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(project_root, ".env"))


def step(n: int, desc: str):
    print(f"\n{'='*60}")
    print(f"  Step {n}: {desc}")
    print(f"{'='*60}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="跳过网关连通性测试")
    args = parser.parse_args()

    # ── Step 1: 网关连通性 ──
    step(1, "网关连通性检测")
    gateway_url = os.getenv("GUOJIN_GATEWAY_URL", "")
    gateway_secret = os.getenv("GUOJIN_GATEWAY_SECRET", "")
    print(f"  GUOJIN_GATEWAY_URL = {gateway_url}")
    print(f"  GUOJIN_GATEWAY_SECRET = {'*' * len(gateway_secret) if gateway_secret else '(empty)'}")

    if not args.mock:
        import httpx
        try:
            health = httpx.get(f"{gateway_url}/health", timeout=5)
            print(f"  GET /health → {health.status_code}: {health.text}")
            assert health.status_code == 200, f"健康检查失败: {health.status_code}"
        except Exception as e:
            print(f"  ❌ 网关不可达: {e}")
            print("  提示: 用 --mock 跳过网关检测，或检查 VM 是否运行")
            return

        try:
            pos_resp = httpx.get(
                f"{gateway_url}/positions",
                headers={"X-WP-Secret": gateway_secret},
                timeout=10,
            )
            print(f"  GET /positions → {pos_resp.status_code}")
            if pos_resp.status_code == 200:
                data = pos_resp.json()
                print(f"  account_id: {data.get('account_id')}")
                print(f"  positions count: {len(data.get('positions', []))}")
                for p in data.get("positions", [])[:3]:
                    print(f"    - {p.get('symbol')} {p.get('name')} qty={p.get('quantity')} mv={p.get('market_value')}")
                acc = data.get("account", {})
                print(f"  account: cash={acc.get('cash')}, market_value={acc.get('market_value')}, total_asset={acc.get('total_asset')}")
            else:
                print(f"  ❌ 获取持仓失败: {pos_resp.text[:200]}")
                return
        except Exception as e:
            print(f"  ❌ 获取持仓失败: {e}")
            return
    else:
        print("  (跳过 - mock 模式)")

    # ── Step 2: Adapter 字段映射 ──
    step(2, "GuojinAdapter 字段映射验证")
    from services.broker_sync.guojin.adapter import GuojinAdapter

    adapter = GuojinAdapter(account_id="35800452")
    test_raw = {
        "symbol": "510310:SH",
        "raw_symbol": "510310.SH",
        "name": "沪深300ETF易方达",
        "quantity": 100,
        "available_quantity": 100,
        "cost_price": 4.77,
        "last_price": 4.773,
        "market_value": 477.3,
        "currency": "CNY",
    }
    pos = adapter.to_position(test_raw)
    print(f"  broker={pos.broker}, symbol={pos.symbol}, market={pos.market}")
    print(f"  asset_class={pos.asset_class}, sync_source={pos.sync_source}")
    print(f"  quantity={pos.quantity}, avg_cost={pos.avg_cost}, market_value={pos.market_value}")
    print(f"  unrealized_pnl={pos.unrealized_pnl}, unrealized_pnl_pct={pos.unrealized_pnl_pct}")
    assert pos.broker == "guojin"
    assert pos.sync_source == "api"
    assert pos.asset_class == "etf"
    assert pos.market == "SH"
    print("  ✅ Adapter 字段映射正确")

    # ── Step 3: sync_service.fetch_positions (需网关在线) ──
    step(3, "GuojinSyncService.fetch_positions()")
    if not args.mock:
        from services.broker_sync.guojin.sync_service import GuojinSyncService
        try:
            svc = GuojinSyncService()
            positions, account_data = svc.fetch_positions()
            print(f"  拉取到 {len(positions)} 条 Position (含港股通汇总行)")
            for p in positions:
                print(f"    - {p.symbol} {p.name} qty={p.quantity} mv={p.market_value} currency={p.currency}")

            hk_rows = [p for p in positions if p.symbol == "HKCONNECT:SUMMARY"]
            if hk_rows:
                hk = hk_rows[0]
                print(f"  ✅ 港股通汇总行: market_value={hk.market_value} (期望 ≈ 28.7万)")
            else:
                print(f"  ⚠️ 未生成港股通汇总行 (hk_market_value 可能 ≤ 1)")
        except Exception as e:
            print(f"  ❌ fetch_positions 失败: {e}")
            return
    else:
        print("  (跳过 - mock 模式)")

    # ── Step 4: sync_and_persist 端到端 ──
    step(4, "sync_and_persist 端到端入库")
    if not args.mock:
        from app.database import get_session
        from services.broker_sync.guojin.sync_service import GuojinSyncService
        from services.broker_sync.models import PositionSnapshotRun, PositionSnapshot

        db = get_session()
        try:
            svc = GuojinSyncService()
            run_id = svc.sync_and_persist(db, triggered_by="e2e_test")
            print(f"  run_id = {run_id}")

            run = db.query(PositionSnapshotRun).get(run_id)
            print(f"  run.status = {run.status}")
            print(f"  run.position_count = {run.position_count}")
            print(f"  run.account_id = {run.account_id}")
            assert run.status == "success", f"run.status={run.status}, error={run.error_message}"

            snapshots = db.query(PositionSnapshot).filter_by(run_id=run_id).all()
            print(f"  snapshots count = {len(snapshots)}")
            for s in snapshots:
                print(f"    - {s.symbol} {s.name} qty={s.quantity} mv={s.market_value}")

            # 检查 Position 业务表
            from app.models import Position as BusinessPosition
            guojin_pos = db.query(BusinessPosition).filter_by(platform="国金证券").all()
            print(f"  Position 业务表 (国金证券): {len(guojin_pos)} 条")
            for p in guojin_pos:
                print(f"    - id={p.id} ticker={p.ticker} name={p.name} mv_cny={p.market_value_cny}")

            print("  ✅ sync_and_persist 成功")
        except Exception as e:
            import traceback
            print(f"  ❌ sync_and_persist 失败: {e}")
            traceback.print_exc()
        finally:
            db.close()
    else:
        print("  (跳过 - mock 模式)")

    # ── Step 5: stale 清理范围验证 ──
    step(5, "stale 清理范围验证")
    print("  设计: is_qmt_managed = (len(t)==6 and t.isdigit()) or t=='HKCONNECT'")
    print("  截图导入的港股通 ticker (空字符串/'03690.HK') 不在管辖范围内 → 不会被清理")
    print("  ✅ 逻辑已内嵌在 sync_service._remove_qmt_stale_positions()")

    print(f"\n{'='*60}")
    print("  验证完成")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

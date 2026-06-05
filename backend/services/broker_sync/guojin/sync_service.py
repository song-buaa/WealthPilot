"""国金证券持仓同步服务（网关模式：主动拉取）。

架构：WealthPilot 后端 → HTTP GET → VM 内 wp_qmt_gateway.py → xtquant
照抄 tiger sync_service 结构，区别在于数据来源是 HTTP 网关而非 SDK。
"""
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx
from pydantic import ValidationError

from services.broker_sync.schema import Position
from services.broker_sync.guojin.adapter import GuojinAdapter


class GuojinSyncService:
    """国金证券持仓同步主服务。"""

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 5
    REQUEST_TIMEOUT = 10  # 秒

    def __init__(self):
        self.gateway_url = os.getenv("GUOJIN_GATEWAY_URL", "")
        self.gateway_secret = os.getenv("GUOJIN_GATEWAY_SECRET", "")
        if not self.gateway_url:
            raise RuntimeError("GUOJIN_GATEWAY_URL 未配置")
        if not self.gateway_secret:
            raise RuntimeError("GUOJIN_GATEWAY_SECRET 未配置")

        # account_id 从网关返回的 JSON 里取，初始化时不需要
        self.adapter: Optional[GuojinAdapter] = None

    def fetch_positions(self) -> tuple[list[Position], dict]:
        """拉取持仓 → 转换为统一 Position 列表。

        返回 (positions, account_data)。
        account_data 含 cash/market_value/total_asset，用于港股通汇总行反算。

        异常语义：
        - 网关 503 / 超时 / 连接失败 → 抛 ConnectionError（上层可重试）
        - 网关 401 → 抛 RuntimeError（配置错误，不重试）
        - 返回 0 条持仓 → 抛 RuntimeError（安全守卫，禁止空结果触发 stale 清理）
        """
        url = f"{self.gateway_url.rstrip('/')}/positions"
        try:
            resp = httpx.get(
                url,
                headers={"X-WP-Secret": self.gateway_secret},
                timeout=self.REQUEST_TIMEOUT,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise ConnectionError(
                f"国金 QMT 网关不可达 ({url}): {type(e).__name__}: {e}"
            ) from e

        if resp.status_code == 401:
            raise RuntimeError(
                "国金 QMT 网关鉴权失败 (401)，请检查 GUOJIN_GATEWAY_SECRET"
            )
        if resp.status_code == 503:
            detail = resp.json().get("detail", "") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise ConnectionError(
                f"国金 QMT 未在线 (503): {detail}"
            )
        if resp.status_code != 200:
            raise ConnectionError(
                f"国金网关异常 HTTP {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        account_id = data.get("account_id", "unknown")
        raw_positions = data.get("positions", [])
        account_data = data.get("account", {})

        # ── 空结果守卫（最高优先级）──
        # 绝不能让空 current_tickers 触发 stale 清理删除所有持仓
        if not raw_positions:
            raise RuntimeError(
                "国金网关返回 0 条持仓，中止同步（防止误删）。"
                "请检查 QMT 是否已登录、账户是否有持仓。"
            )

        self.adapter = GuojinAdapter(account_id=account_id)
        snapshot_time = datetime.now(timezone.utc)
        positions = self.adapter.to_positions(raw_positions, snapshot_time)

        # ── 港股通汇总行 ──
        hk_summary = self._build_hk_summary(account_data, account_id, snapshot_time)
        if hk_summary is not None:
            positions.append(hk_summary)

        return positions, account_data

    def _build_hk_summary(
        self,
        account_data: dict,
        account_id: str,
        snapshot_time: datetime,
    ) -> Optional[Position]:
        """用 total_asset − market_value − cash 反算港股通市值，生成汇总行。

        阈值: hk_market_value > 1 (元) 才生成，避免浮点噪声。
        """
        total_asset = Decimal(str(account_data.get("total_asset", 0)))
        market_value = Decimal(str(account_data.get("market_value", 0)))
        cash = Decimal(str(account_data.get("cash", 0)))

        hk_market_value = total_asset - market_value - cash
        if hk_market_value <= 1:
            return None

        return Position(
            broker="guojin",
            account_id=account_id,
            symbol="HKCONNECT:SUMMARY",
            raw_symbol="HKCONNECT",
            name="港股通持仓(合计·明细待接入)",
            name_en=None,
            asset_class="equity",
            market="HK",
            quantity=Decimal("0"),
            available_quantity=None,
            avg_cost=Decimal("0"),
            cost_method="weighted_average",
            cost_basis=Decimal("0"),
            current_price=Decimal("0"),
            market_value=hk_market_value,
            currency="CNY",
            unrealized_pnl=Decimal("0"),
            unrealized_pnl_pct=Decimal("0"),
            realized_pnl=None,
            day_pnl=None,
            option_meta=None,
            snapshot_time=snapshot_time,
            sync_source="api",
            raw_data={
                "is_summary": True,
                "note": "港股通汇总，逐条明细待国金客服确认权限/柜台后接入",
            },
        )

    # ── 持久化同步（写库）─────────────────────────────────────────

    def sync_and_persist(
        self,
        db_session,
        triggered_by: str = "manual",
    ) -> int:
        """同步持仓并写入数据库。

        照抄 tiger sync_service 的 snapshot 三步流程：
        1. create_run
        2. fetch_positions（失败 → mark_run_failed + 抛异常）
        3. persist_positions → upsert 到业务表
        4. 收窄的 stale 清理（只清 QMT 管辖的 ticker）

        返回 run_id。失败时抛出最后一次的异常。
        """
        from services.broker_sync.repository import PositionSnapshotRepository

        repo = PositionSnapshotRepository(db_session)
        run = repo.create_run(
            broker="guojin",
            account_id="pending",  # fetch 后才知道，先占位
            sync_source="api",
            triggered_by=triggered_by,
        )

        last_exception: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                positions, _account_data = self.fetch_positions()

                # 回填 account_id
                if self.adapter:
                    run.account_id = self.adapter.account_id
                    db_session.commit()

                repo.persist_positions(
                    run_id=run.id,
                    positions=positions,
                    total_market_value_cnh=None,
                )

                # ── upsert 到 Position 业务表（不走 upsert_from_snapshots，
                #     因为其内含的 _remove_stale_positions 会按 platform
                #     全量清理，误删截图导入的港股通逐条持仓）──
                from services.broker_sync.position_upsert_service import PositionUpsertService
                from services.broker_sync.models import PositionSnapshot

                snapshots = db_session.query(PositionSnapshot).filter_by(run_id=run.id).all()
                upsert_service = PositionUpsertService(db_session)

                errors = []
                inserted = 0
                updated = 0
                for snap in snapshots:
                    try:
                        # NOTE: _upsert_single 是 PositionUpsertService 的私有方法。
                        # 此处有意直接调用以绕过 _remove_stale_positions 全量清理。
                        # 如果 _upsert_single 签名变更，此处需同步更新。
                        if upsert_service._upsert_single(snap):
                            inserted += 1
                        else:
                            updated += 1
                    except Exception as e:
                        errors.append({
                            "symbol": snap.symbol,
                            "error": f"{type(e).__name__}: {e}",
                        })

                if errors:
                    db_session.rollback()
                    raise RuntimeError(f"业务表同步失败: {errors}")

                # ── 收窄的 stale 清理 ──
                self._remove_qmt_stale_positions(db_session, upsert_service, snapshots)

                db_session.commit()
                return run.id

            except (ConnectionError, TimeoutError, OSError) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY_SECONDS)
                    continue
                db_session.rollback()
                repo.mark_run_failed(
                    run_id=run.id,
                    error_message=(
                        f"网络错误，重试 {self.MAX_RETRIES} 次后仍失败: "
                        f"{type(e).__name__}: {e}"
                    ),
                    retry_count=attempt,
                )
                raise

            except (ValidationError, KeyError, AttributeError, ValueError,
                    RuntimeError) as e:
                db_session.rollback()
                repo.mark_run_failed(
                    run_id=run.id,
                    error_message=f"数据/逻辑错误（不重试）: {type(e).__name__}: {e}",
                    retry_count=attempt,
                )
                raise

        if last_exception:
            raise last_exception
        raise RuntimeError("sync_and_persist 内部逻辑错误")

    @staticmethod
    def _remove_qmt_stale_positions(db_session, upsert_service, snapshots) -> int:
        """收窄的 stale 清理：只清理 QMT 管辖范围内的过期持仓。

        QMT 管辖范围（is_qmt_managed）：
        - A 股 ticker：6 位纯数字（如 510310）
        - 港股通汇总行：ticker == "HKCONNECT"

        不碰截图导入的持仓（ticker 为空字符串或含 .HK 后缀的旧格式）。

        NOTE: 将来港股通逐条接通后，is_qmt_managed 判据需要扩展以覆盖
        港股 ticker（如 "0700"、"03690" 等 4-5 位数字），否则港股通逐条
        会落进 stale 逻辑的缝隙——新同步的港股逐条不会被清理，但也不会
        被当作 stale 删除，导致卖出的港股残留。届时需要把港股 ticker 模式
        加入 is_qmt_managed。
        """
        from app.models import Position as BusinessPosition
        from services.broker_sync.position_upsert_service import BROKER_TO_PLATFORM

        platform = BROKER_TO_PLATFORM.get("guojin")
        if not platform:
            return 0

        # NOTE: _denormalize_ticker 是 PositionUpsertService 的私有方法。
        # 此处有意直接调用以保持 ticker 格式与 _upsert_single 一致。
        # 如果 _denormalize_ticker 逻辑变更，此处需同步更新。
        current_tickers = {
            upsert_service._denormalize_ticker(s.symbol)
            for s in snapshots
        }

        all_guojin = db_session.query(BusinessPosition).filter(
            BusinessPosition.platform == platform,
        ).all()

        removed = 0
        for pos in all_guojin:
            t = pos.ticker or ""
            if t in current_tickers:
                continue  # 在本次结果里，保留

            # 判断是否属于 QMT 管辖范围
            is_qmt_managed = (len(t) == 6 and t.isdigit()) or t == "HKCONNECT"
            if is_qmt_managed:
                db_session.delete(pos)
                removed += 1

        return removed

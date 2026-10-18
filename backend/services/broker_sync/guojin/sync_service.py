"""国金证券持仓同步服务（网关模式：主动拉取）。

架构：WealthPilot 后端 → HTTP GET → VM 内 wp_qmt_gateway.py → xtquant
照抄 tiger sync_service 结构，区别在于数据来源是 HTTP 网关而非 SDK。
"""
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx

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

        # Response owner is verified against the reservation before publishing.
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

        from backend.services.instruments.classification import (
            AssetClassificationEvidence,
            broker_position_classification_fields,
            classify_instrument,
        )
        evidence = AssetClassificationEvidence(
            broker="guojin",
            broker_security_type="HKCONNECT_SUMMARY",
            vehicle_type_hint="COMMON_STOCK",
            explicit_economic_asset_class="EQUITY",
            explicit_source="BROKER_AGGREGATE_DETERMINISTIC",
            long_name="港股通持仓(合计·明细待接入)",
            currency="CNY",
        )
        return Position(
            broker="guojin",
            account_id=account_id,
            symbol="HKCONNECT:SUMMARY",
            raw_symbol="HKCONNECT",
            name="港股通持仓(合计·明细待接入)",
            name_en=None,
            **broker_position_classification_fields(
                classify_instrument(evidence), evidence=evidence
            ),
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

    def sync_and_persist(self, db_session, triggered_by="manual"):
        from services.broker_sync.repository import PositionSnapshotRepository
        def reconcile(db, upsert, snapshots):
            for snap in snapshots:
                upsert._upsert_single(snap)
            self._remove_qmt_stale_positions(db, upsert, snapshots)
        return PositionSnapshotRepository(db_session).synchronize(
            "guojin", os.environ.get("GUOJIN_ACCOUNT") or db_session.info.get("sync_account_id")
                or getattr(getattr(self,"adapter",None),"account_id",None),
            self.fetch_positions, triggered_by=triggered_by,
            metadata=lambda:{"complete":True,"source":"guojin_qmt","positions_as_of":None},
            reconcile=reconcile, retries=self.MAX_RETRIES, retry_delay=self.RETRY_DELAY_SECONDS)

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
        )
        owner = snapshots[0].account_id if snapshots else None
        if owner is None:
            return 0
        all_guojin = all_guojin.filter_by(
            portfolio_id=upsert_service.portfolio_id, broker="guojin",
            broker_account_id=owner, sync_source="api")
        all_guojin = all_guojin.all()

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

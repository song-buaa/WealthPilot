"""
快照 → Position 业务表 同步服务。

读取一次同步产生的 PositionSnapshot 列表,upsert 到 Position 业务表。
- 字段覆盖策略:混合模式(核心数据覆盖,用户语义字段保护)
- 汇率:复用 app.fx_service.fx_service.convert()
- ticker 去归一化:AAPL.US → AAPL
- asset_class:调 classify_position 做 5 大类中文归类
"""
import json
from typing import Iterable

from sqlalchemy import null
from sqlalchemy.orm import Session

from app.fx_service import fx_service
from app.models import Position as BusinessPosition
from services.broker_sync.models import PositionSnapshot
from services.broker_sync.schema import is_cash_balance
from backend.services.instruments.classification import (
    AssetClassification,
    AssetClassificationEvidence,
    business_position_classification_fields,
    classify_instrument,
)


# broker → platform 映射
BROKER_TO_PLATFORM = {
    "tiger": "老虎证券",
    "futu": "富途证券",
    "snowball": "雪盈证券",
    "guojin": "国金证券",
}

# 受保护字段(首次写入,后续不覆盖)
PROTECTED_FIELDS = {"name", "asset_class", "segment"}

# 合法中文 5 大类(Position 表 asset_class 应存这些值)
LEGAL_CN_CLASSES = {"权益", "固收", "货币", "另类", "衍生", "未分类"}

def _resolve_asset_class(sec_type_en: str, name: str, ticker: str) -> str:
    """Legacy test/caller shim delegated to the canonical classifier."""
    return classify_instrument(AssetClassificationEvidence(
        vehicle_type_hint=sec_type_en,
        long_name=name,
    )).asset_class_cn


def _snapshot_classification(
    snap: PositionSnapshot,
) -> tuple[AssetClassification, AssetClassificationEvidence]:
    """Rebuild canonical evidence from new or historical snapshot rows."""
    try:
        raw = json.loads(snap.raw_data_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = {}
    try:
        stored_evidence = json.loads(
            getattr(snap, "classification_evidence_json", None) or "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        stored_evidence = {}
    source = {**raw, **stored_evidence}
    stored_source = getattr(snap, "classification_source", None)
    stored_verification = getattr(
        snap, "classification_verification_status", None
    )
    is_user_explicit = (
        str(stored_source or "").upper().startswith("USER_")
        or str(stored_verification or "").upper() == "EXPLICIT"
    )
    evidence = AssetClassificationEvidence(
        broker=snap.broker,
        broker_security_type=(
            getattr(snap, "broker_security_type", None)
            or source.get("broker_security_type")
            or source.get("sec_type")
        ),
        stock_type=source.get("stock_type"),
        vehicle_type_hint=(
            getattr(snap, "vehicle_type", None)
            or source.get("vehicle_type")
            or snap.asset_class
        ),
        explicit_economic_asset_class=(
            getattr(snap, "economic_asset_class", None)
            if is_user_explicit else None
        ),
        explicit_source=stored_source if is_user_explicit else None,
        con_id=source.get("con_id") or source.get("conId"),
        isin=source.get("isin") or source.get("ISIN"),
        long_name=source.get("long_name") or snap.name,
        category=source.get("category"),
        subcategory=source.get("subcategory"),
        industry=source.get("industry"),
        exchange=source.get("exchange"),
        primary_exchange=source.get("primary_exchange"),
        currency=snap.currency,
    )
    return classify_instrument(evidence), evidence


class PositionUpsertService:
    """把 PositionSnapshot 同步到 Position 业务表。"""

    def __init__(self, session: Session):
        self.session = session
        from backend.services.action.ownership import portfolio_scope
        self.portfolio_id = portfolio_scope(session)

    def _business_query(self):
        return self.session.query(BusinessPosition).filter_by(portfolio_id=self.portfolio_id)

    def upsert_from_snapshots(
        self,
        snapshots: Iterable[PositionSnapshot],
        *,
        broker: str | None = None,
        account_id: str | None = None,
        sync_source: str | None = None,
        commit: bool = True,
        complete_snapshot: bool = False,
        run_id: int | None = None,
    ) -> dict:
        """
        把 snapshots 同步到 Position 业务表。

        逻辑：
        1. upsert 本次 snapshot 中的所有持仓
        2. 删除该 platform 中本次 snapshot 不再包含的持仓（已卖出清仓）

        返回报告字典:
        {
            "inserted": int,
            "updated": int,
            "removed": int,
            "errors": list,
        }
        """
        inserted = 0
        updated = 0
        errors = []

        snap_list = list(snapshots)
        if not complete_snapshot:
            raise ValueError("COMPLETE_ACCOUNT_SNAPSHOT_REQUIRED")
        scopes = self._resolve_scopes(
            snap_list,
            broker=broker,
            account_id=account_id,
            sync_source=sync_source,
        )
        for snap in snap_list:
            if is_cash_balance(snap):
                self._validate_cash_identity(snap)

        from services.broker_sync.models import PositionSnapshotRun
        for scope_broker, scope_account, scope_source in scopes:
            conflict = self.session.query(BusinessPosition.id).filter(
                BusinessPosition.broker == scope_broker, BusinessPosition.broker_account_id == scope_account,
                BusinessPosition.portfolio_id != self.portfolio_id).first()
            if conflict:
                raise ValueError("ACCOUNT_PORTFOLIO_AMBIGUOUS")
            if not snap_list:
                run = self.session.get(PositionSnapshotRun, run_id) if run_id else None
                if (run is None or run.portfolio_id != self.portfolio_id or run.broker != scope_broker
                        or run.account_id != scope_account or run.sync_source != scope_source
                        or run.status not in {"running", "success"}):
                    raise ValueError("EMPTY_SNAPSHOT_RUN_OWNER_REQUIRED")
                if run.context_json:
                    import json as _json
                    meta = _json.loads(run.context_json)
                    if (self.session.info.get("sync_publish_run_id") != run.id or run.status != "running"
                            or not meta.get("complete") or not meta.get("empty_confirmed")):
                        raise ValueError("EMPTY_SOURCE_REQUIRES_REVIEW")
        for snap in snap_list:
            try:
                if self._upsert_single(snap):
                    inserted += 1
                else:
                    updated += 1
            except Exception as e:
                errors.append({
                    "symbol": snap.symbol,
                    "error": f"{type(e).__name__}: {e}",
                })

        if errors:
            self.session.rollback()
            return {"inserted": 0, "updated": 0, "removed": 0, "errors": errors}

        # 删除已清仓的持仓：本次 snapshot 覆盖的 platform 中，
        # ticker 不在本次 snapshot 里的记录应被删除
        removed = 0
        for scope_broker, scope_account, scope_source in scopes:
            self._claim_legacy_positions_from_history(
                broker=scope_broker,
                account_id=scope_account,
                sync_source=scope_source,
            )
            scope_snapshots = [
                snap for snap in snap_list
                if snap.broker == scope_broker and snap.account_id == scope_account
            ]
            removed += self._remove_stale_positions(
                scope_snapshots,
                broker=scope_broker,
                account_id=scope_account,
                sync_source=scope_source,
            )

        if commit:
            self.session.commit()
        else:
            self.session.flush()
        return {"inserted": inserted, "updated": updated, "removed": removed, "errors": []}

    @staticmethod
    def _resolve_scopes(
        snap_list: list[PositionSnapshot],
        *,
        broker: str | None,
        account_id: str | None,
        sync_source: str | None,
    ) -> list[tuple[str, str, str]]:
        """得到 authoritative snapshot scopes；空快照必须显式给出。"""
        explicit_scope = any(value is not None for value in (broker, account_id, sync_source))
        if explicit_scope and not all((broker, account_id, sync_source)):
            raise ValueError("broker/account_id/sync_source 必须同时提供")

        if explicit_scope:
            scopes = [(broker, account_id, sync_source)]
        else:
            scopes = sorted({(snap.broker, snap.account_id, "api") for snap in snap_list})

        if not scopes:
            raise ValueError("空 snapshot reconciliation 必须提供 broker/account_id/sync_source")

        if explicit_scope:
            for snap in snap_list:
                if snap.broker != broker or snap.account_id != account_id:
                    raise ValueError("snapshot 包含 scope 外的 broker/account")

        for scope in scopes:
            if not all(scope):
                raise ValueError("snapshot 包含 scope 外的 broker/account")

        return scopes

    def _claim_legacy_positions_from_history(
        self,
        *,
        broker: str,
        account_id: str,
        sync_source: str,
    ) -> None:
        """用既有成功快照证据为升级前的 API 行补齐 ownership。"""
        from services.broker_sync.models import PositionSnapshotRun

        platform = BROKER_TO_PLATFORM.get(broker)
        if not platform:
            return

        historical_symbols = (
            self.session.query(PositionSnapshot.symbol)
            .join(PositionSnapshotRun, PositionSnapshot.run_id == PositionSnapshotRun.id)
            .filter(
                PositionSnapshotRun.portfolio_id == self.portfolio_id,
                PositionSnapshotRun.broker == broker,
                PositionSnapshotRun.account_id == account_id,
                PositionSnapshotRun.sync_source == sync_source,
                PositionSnapshotRun.status == "success",
            )
            .distinct()
            .all()
        )
        for (symbol,) in historical_symbols:
            if symbol.upper().startswith("CASH-"):
                continue  # new sync history is not evidence of a manual cash row's owner
            ticker = self._denormalize_ticker(symbol)
            legacy = self._business_query().filter_by(
                ticker=ticker,
                platform=platform,
                broker=None,
                broker_account_id=None,
                sync_source=None,
            ).first()
            if legacy is not None and not is_cash_balance(legacy):
                legacy.broker = broker
                legacy.broker_account_id = account_id
                legacy.sync_source = sync_source

    def _remove_stale_positions(
        self,
        snap_list: list[PositionSnapshot],
        *,
        broker: str,
        account_id: str,
        sync_source: str,
    ) -> int:
        """只删除同 broker + account + source 下已不在成功快照中的持仓。"""
        current_symbols = {snap.symbol for snap in snap_list}
        query = self._business_query().filter_by(
            broker=broker,
            broker_account_id=account_id,
            sync_source=sync_source,
        )
        if current_symbols:
            query = query.filter(~BusinessPosition.symbol.in_(current_symbols))

        stale = query.all()
        run_id = self.session.info.get("sync_publish_run_id")
        if run_id:
            from services.broker_sync.models import PositionSnapshotRun
            run = self.session.get(PositionSnapshotRun, run_id)
            meta = json.loads(run.context_json or "{}")
            coverage, markets = meta.get("coverage"), meta.get("markets")
            removable = [p for p in stale if (not coverage or p.broker_security_type in coverage)
                and (not markets or (p.symbol and p.symbol.rsplit(":",1)[-1] in markets))]
            if len(removable) != len(stale):
                meta.update(quality="partial", issues=sorted(set(meta.get("issues",[])+["UNCOVERED_POSITIONS_RETAINED"])))
                run.context_json = json.dumps(meta)
            stale = removable
        for pos in stale:
            self.session.delete(pos)
        return len(stale)

    def _validate_cash_identity(self, snap):
        """Ambiguous legacy/imported cash requires an explicit offline mapping."""
        matches = []
        for row in self._business_query().all():
            if not is_cash_balance(row):
                continue
            owner_known = bool(row.broker and row.broker_account_id)
            same_owner = (row.broker, row.broker_account_id) == (snap.broker, snap.account_id)
            if owner_known and not same_owner:
                continue
            currency = str(row.currency or "").upper()
            if currency and currency != snap.currency and row.symbol != snap.symbol:
                continue
            exact = (same_owner and row.sync_source == "api" and row.symbol == snap.symbol
                     and row.currency == snap.currency)
            if not exact:
                raise ValueError("CASH_IDENTITY_COLLISION_REQUIRES_REVIEW")
            matches.append(row.id)
        if len(matches) > 1:
            raise ValueError("CASH_IDENTITY_COLLISION_REQUIRES_REVIEW")

    def _upsert_single(self, snap: PositionSnapshot, *, allow_legacy_claim: bool = True) -> bool:
        """单条 upsert。返回 True 表示新增,False 表示更新。"""
        from services.broker_sync.models import PositionSnapshotRun
        run = self.session.get(PositionSnapshotRun, snap.run_id)
        if (run is None or run.portfolio_id != self.portfolio_id or run.broker != snap.broker
                or run.account_id != snap.account_id or run.sync_source != "api"
                or run.status not in {"running", "success"}):
            raise ValueError("SNAPSHOT_OWNER_MISMATCH")
        if run.context_json and (run.status != "running" or self.session.info.get("sync_publish_run_id") != run.id):
            raise ValueError("SYNC_PUBLICATION_REQUIRED")
        if is_cash_balance(snap):
            self._validate_cash_identity(snap)
        conflict = self.session.query(BusinessPosition.id).filter(
            BusinessPosition.broker == snap.broker, BusinessPosition.broker_account_id == snap.account_id,
            BusinessPosition.portfolio_id != self.portfolio_id).first()
        if conflict:
            raise ValueError("ACCOUNT_PORTFOLIO_AMBIGUOUS")
        ticker = self._denormalize_ticker(snap.symbol)
        platform = BROKER_TO_PLATFORM.get(snap.broker)
        if not platform:
            raise ValueError(f"未知 broker: {snap.broker}")

        # 1. 汇率换算
        import json as _json
        meta = _json.loads(run.context_json or "{}")
        quote = meta.get("fx", {}).get(snap.currency)
        if quote:
            if quote.get("rate") is None and (snap.market_value != 0 or snap.unrealized_pnl != 0):
                raise ValueError("FX_RATE_UNAVAILABLE")
            fx_rate = quote["rate"]
            fx_date = quote.get("rate_date") if quote.get("quality") in {"current","historical"} else quote.get("quality")
            market_value_cny = float(snap.market_value) * fx_rate if fx_rate is not None else 0.0
        elif run.context_json:
            raise ValueError("SYNC_FX_PREFLIGHT_REQUIRED")
        else:
            market_value_cny, fx_rate, fx_date = fx_service.convert(float(snap.market_value), snap.currency, "CNY")

        # 2. 查现有行
        existing = self._business_query().filter_by(
            symbol=snap.symbol,
            broker=snap.broker,
            broker_account_id=snap.account_id,
            sync_source="api",
        ).first()

        if existing is None and snap.market == "LSE":
            from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
            aliases = {value for item in TRUSTED_INSTRUMENTS.values()
                       for value in (item.user_alias, item.symbol, item.local_symbol)
                       if ticker in {item.user_alias, item.symbol, item.local_symbol}}
            collision = self._business_query().filter(
                BusinessPosition.broker == snap.broker, BusinessPosition.broker_account_id == snap.account_id,
                BusinessPosition.sync_source == "api", BusinessPosition.currency == snap.currency,
                BusinessPosition.ticker.in_(aliases), BusinessPosition.symbol != snap.symbol).first()
            if collision:
                raise ValueError("EVIDENCED_IDENTITY_CORRECTION_REQUIRED_BEFORE_SYNC")

        # 旧库升级后的首轮同步：只有存在相同 scope 的历史 snapshot 证据时，
        # 才接管旧的 platform+ticker 行，避免把手工/CSV 行误认作 API 持仓。
        if allow_legacy_claim and existing is None and not is_cash_balance(snap):
            has_snapshot_evidence = self.session.query(PositionSnapshot.id).join(
                PositionSnapshotRun, PositionSnapshot.run_id == PositionSnapshotRun.id).filter(
                PositionSnapshotRun.portfolio_id == self.portfolio_id,
                PositionSnapshotRun.status == "success",
                PositionSnapshot.broker == snap.broker,
                PositionSnapshot.account_id == snap.account_id,
                PositionSnapshot.symbol == snap.symbol,
                PositionSnapshot.run_id != snap.run_id).first()
            if has_snapshot_evidence:
                existing = self._business_query().filter_by(
                    ticker=ticker,
                    platform=platform,
                    broker=None,
                    broker_account_id=None,
                    sync_source=None,
                ).first()

        # 3. 计算盈亏
        pnl_original = float(snap.unrealized_pnl)
        pnl_cny = pnl_original * fx_rate if fx_rate is not None else 0.0
        # Position 表 profit_loss_rate 存百分数(如 30.5 表示 +30.5%)
        pnl_pct = float(snap.unrealized_pnl_pct) * 100

        # 4. 由唯一 canonical authority 解析 vehicle 与经济资产类别。
        classification, classification_evidence = _snapshot_classification(snap)
        classification_fields = business_position_classification_fields(
            classification,
            evidence=classification_evidence,
        )
        if existing is None:
            # 新建(需要 portfolio_id,默认用 1)
            new_pos = BusinessPosition(
                portfolio_id=self.portfolio_id,
                ticker=ticker,
                symbol=snap.symbol,  # v3.11: 存完整 TICKER:MARKET 真值
                platform=platform,
                broker=snap.broker,
                broker_account_id=snap.account_id,
                sync_source="api",
                name=snap.name,
                **classification_fields,
                segment="投资",
                currency=snap.currency,  # v3.4 修复: 存原币种(USD/HKD),不是 CNY
                quantity=float(snap.quantity),
                cost_price=float(snap.avg_cost),
                current_price=float(snap.current_price),
                market_value_cny=market_value_cny,
                original_currency=snap.currency,
                original_value=float(snap.market_value),
                fx_rate_to_cny=fx_rate if fx_rate is not None else null(),
                fx_rate_date=fx_date,
                profit_loss_value=pnl_cny,
                profit_loss_rate=pnl_pct,
                profit_loss_original_value=pnl_original,
            )
            self.session.add(new_pos)
            return True
        else:
            # 更新:只覆盖非保护字段
            existing.quantity = float(snap.quantity)
            existing.cost_price = float(snap.avg_cost)
            existing.current_price = float(snap.current_price)
            existing.market_value_cny = market_value_cny
            existing.original_currency = snap.currency
            existing.original_value = float(snap.market_value)
            existing.fx_rate_to_cny = fx_rate
            existing.fx_rate_date = fx_date
            existing.profit_loss_value = pnl_cny
            existing.profit_loss_rate = pnl_pct
            existing.profit_loss_original_value = pnl_original
            existing.currency = snap.currency  # v3.4 修复: 存原币种(USD/HKD)
            existing.symbol = snap.symbol      # v3.11: 每次 sync 更新 symbol 真值
            existing.broker = snap.broker
            existing.broker_account_id = snap.account_id
            existing.sync_source = "api"
            # API 同步行的资产类型由 Broker 元数据权威更新；name/segment 仍保护。
            for field, value in classification_fields.items():
                setattr(existing, field, value)
            return False

    _KNOWN_MARKETS = {"US", "HK", "SH", "SZ", "LSE"}

    @staticmethod
    def _denormalize_ticker(symbol: str) -> str:
        """从 normalize 后的 symbol 还原纯 ticker。

        支持新旧格式 + 兼容 ticker 自身含点的特殊标的（BRK.B 等）：
          "AAPL:US"          → "AAPL"
          "BRK.B:US"         → "BRK.B"
          "AAPL.US"          → "AAPL"          (旧格式)
          "BRK.B.US"         → "BRK.B"         (旧格式,保护 .B)
          "LU2416422678.USD" → "LU2416422678.USD"  (USD 不是市场,保留)
        """
        if not symbol:
            return symbol

        # 新格式优先：TICKER:MARKET
        if ":" in symbol:
            return symbol.split(":", 1)[0]

        # 旧格式：仅当最后一段是 KNOWN_MARKET 才剥离
        if "." in symbol:
            parts = symbol.rsplit(".", 1)
            if len(parts) == 2 and parts[1].upper() in PositionUpsertService._KNOWN_MARKETS:
                return parts[0]

        return symbol


def backfill_bare_names(session: Session, *, portfolio_id=None, broker=None, account_id=None, commit=True) -> int:
    """补全 name == ticker（裸代码）的持仓行的中文名。

    按 symbol（如 LI:US）分组，组内若存在 name≠ticker 的行（有中文名）
    则作为 donor，回填给组内 name==ticker 的行。

    自限制：一旦补上中文名，name≠ticker，后续不再触发。
    """
    query = session.query(BusinessPosition).filter(
        BusinessPosition.market_value_cny > 0,
        BusinessPosition.symbol.isnot(None),
        BusinessPosition.symbol != "",
    )
    if portfolio_id is not None:
        query = query.filter_by(portfolio_id=portfolio_id)
    all_positions = query.all()

    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for p in all_positions:
        groups[p.symbol].append(p)

    count = 0
    for symbol, members in groups.items():
        donor_name = None
        for p in members:
            if p.ticker and p.name and p.name != p.ticker:
                donor_name = p.name
                break

        if not donor_name:
            continue

        for p in members:
            if broker is not None and (p.broker != broker or p.broker_account_id != account_id):
                continue
            if p.ticker and p.name == p.ticker:
                p.name = donor_name
                count += 1

    if count and commit:
        session.commit()
        print(f"[backfill] 补全 {count} 条裸代码名称", flush=True)
    return count

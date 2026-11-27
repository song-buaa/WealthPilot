"""Compare native owner observations without attributing movements to trades."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.asset_views import _manual_date_current, _source_current, _stored_fx_current
from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
from backend.services.instruments.issuers import extended_common_issuer

ZERO = Decimal(0)
REACHED = "CHECKPOINT_REACHED"
PARTIAL = "PARTIAL_PROGRESS_OBSERVED"
UNOBSERVED = "NOT_OBSERVED"
UNKNOWN = "COVERAGE_UNKNOWN"


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.utcoffset() is not None else None
    except (AttributeError, ValueError):
        return None


def decimal(value, *, signed=False):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and (signed or result >= 0) else None


def symbol_key(symbol, currency):
    for product in TRUSTED_INSTRUMENTS.values():
        if currency == product.currency and symbol in {
            f"{name}:LSE" for name in (product.symbol, product.local_symbol, product.user_alias)
        }:
            return f"{product.local_symbol}:LSE"
    return symbol


def listing(row):
    from backend.utils.symbol import parse_symbol

    symbol = row.get("security_symbol") or row.get("symbol")
    currency = row.get("security_currency") or row.get("original_currency") or row.get("currency")
    if not symbol or ":" not in symbol or not currency:
        return None
    try:
        ticker, market = parse_symbol(symbol)
    except ValueError:
        return None
    # Preserve the market and share class. Do not infer a listing from a name.
    if symbol != f"{ticker}:{market}":
        return None
    return (symbol_key(symbol, currency), currency)


def _amount_current(row, at):
    from backend.services.execution_plan.market_hours import valuation_quality

    key = listing(row)
    if not key or not _stored_fx_current(row, at):
        return None
    price = valuation_quality(row.get("price_as_of"), key[0].rsplit(":", 1)[-1],
                              at=at, basis=row.get("price_time_basis"))
    return decimal(row.get("market_value_cny")) if price["status"] in {"intraday", "recent_close"} else None


class SnapshotFacts:
    def __init__(self, view, *, at=None):
        self.view = view
        self.captured = instant(view["captured_at"])
        self.current = self.captured is not None and (at is None or _source_current(view["captured_at"], at))
        self.accounts = {}
        quality = view.get("data_quality", {})
        for row in quality.get("investable_accounts", quality.get("accounts", [])):
            ref = row.get("account_ref")
            self.accounts[ref] = row if ref not in self.accounts else {}
        self.sources = view.get("views", {})
        native_rows = {row["id"]: row for row in self.sources.get("positions", {}).get("items", [])}
        self.positions, self.cash, self.by_ref = defaultdict(list), defaultdict(list), {}
        self.unmapped_accounts = set()
        for row in quality.get("investable_security_inputs", quality.get("security_inputs", [])):
            account = row.get("account_ref")
            currency = row.get("original_currency") or row.get("currency")
            ref = f"position:{row['id']}"
            self.by_ref[ref] = row
            if row.get("vehicle_type") == "CASH":
                native = native_rows.get(row["id"], {})
                if row.get("original_value") is None and native.get("original_currency") == currency:
                    row = {**row, "original_value": native.get("original_value")}
                self.cash[(account, currency)].append(row)
            elif key := listing(row):
                self.positions[(account, *key)].append(row)
            else:
                self.unmapped_accounts.add(account)
        self.manual = {f"wealth:{row['id']}": row for row in
                       self.sources.get("get_manual_wealth_items", {}).get("items", [])}

    def covered(self, account_ref):
        account = self.accounts.get(account_ref, {})
        return bool(self.current and account.get("complete") is True and account.get("quality") == "current"
                    and account.get("publication_status") == "matched"
                    and account.get("last_attempt_status") == "success"
                    and account.get("fill_sync_pending") is False
                    and _source_current(account.get("account_synced_at"), self.captured))

    def quantity(self, key):
        rows = self.positions.get(key, [])
        if not self.covered(key[0]) or key[0] in self.unmapped_accounts or len(rows) > 1:
            return None
        if not rows:
            # Absence needs a source-date receipt for the whole account.
            return ZERO if _source_current(self.accounts[key[0]].get("positions_as_of"), self.captured) else None
        row = rows[0]
        return decimal(row.get("quantity")) if _source_current(row.get("positions_as_of"), self.captured) else None

    def amount(self, key):
        if self.quantity(key) is None:
            return None
        rows = self.positions.get(key, [])
        return _amount_current(rows[0], self.captured) if rows else ZERO

    def native_cash(self, key):
        rows = self.cash.get(key, [])
        if not self.covered(key[0]) or not key[1] or len(rows) != 1:
            return None
        row = rows[0]
        if not _source_current(row.get("cash_as_of"), self.captured):
            return None
        if row.get("original_value") is not None:
            return decimal(row["original_value"], signed=True)
        return decimal(row.get("market_value_cny"), signed=True) if key[1] == "CNY" else None

    def cash_as_of(self, key):
        rows = self.cash.get(key, [])
        return rows[0].get("cash_as_of") if len(rows) == 1 else None

    def cash_purpose(self, key):
        rows = self.cash.get(key, [])
        disposition = (self.view.get("data_quality", {}).get("investable_scope_evidence") or {}).get("dispositions", {})
        if len(rows) == 1:
            row = disposition.get(f"position:{rows[0]['id']}", {})
            if row.get("state") in {"INCLUDED", "EXCLUDED"}:
                return row.get("reason")
        return None

    def loan(self, ref):
        row = self.manual.get(ref, {})
        if (not self.current or str(row.get("kind", "")).lower() != "liability"
                or not row.get("effective_included_in_net_worth", row.get("included_in_net_worth"))
                or row.get("currency") != "CNY" or row.get("quality_issues")
                or not _manual_date_current(row.get("value_as_of"), self.captured)
                or not _manual_date_current(row.get("last_verified_at"), self.captured)):
            return None
        try:
            verified = datetime.fromisoformat(row["last_verified_at"])
        except (KeyError, TypeError, ValueError):
            return None
        try:
            value_date = datetime.fromisoformat(row["value_as_of"]).date()
        except (TypeError, ValueError):
            return None
        if (value_date > self.captured.date() or verified.date() > self.captured.date()
                or verified.tzinfo is not None and verified > self.captured):
            return None
        return {"principal_cny": decimal(row.get("current_value")), "value_as_of": row["value_as_of"],
                "last_verified_at": verified.isoformat(), "source_type": row.get("source_type") or "MANUAL",
                "quality": "WEALTH_OWNER_BALANCE" if verified.tzinfo else "WEALTH_OWNER_DATE_ONLY",
                "payment_proof": "NOT_ATTESTED"}


def _possible_split(left, right, old_qty, new_qty):
    if not (left and right and old_qty and new_qty and old_qty != new_qty):
        return False
    old_px, new_px = decimal(left.get("current_price")), decimal(right.get("current_price"))
    if not old_px or not new_px:
        return False
    ratio = new_qty / old_qty
    ratio = max(ratio, 1 / ratio)
    return ratio >= Decimal("1.2") and abs(new_qty * new_px / (old_qty * old_px) - 1) <= Decimal("0.02")


def compare_positions(old, new, old_listings, new_listings, corporate_accounts=()):
    grouped = defaultdict(list)
    result = {}
    def identity(row):
        return (row.get("account_ref"), listing(row), row.get("vehicle_type"))
    changed_refs = {ref for ref in old.by_ref.keys() & new.by_ref.keys()
                    if identity(old.by_ref[ref]) != identity(new.by_ref[ref])
                    and old.by_ref[ref].get("account_ref") == new.by_ref[ref].get("account_ref")}
    for key in sorted(old.positions.keys() | new.positions.keys(), key=str):
        before, after = old.quantity(key), new.quantity(key)
        left = next(iter(old.positions.get(key, [])), None)
        right = next(iter(new.positions.get(key, [])), None)
        market_before, market_after = old.amount(key), new.amount(key)
        old_owner = extended_common_issuer(left, old_listings) if left else None
        new_owner = extended_common_issuer(right, new_listings) if right else None
        before_clock = left.get("positions_as_of") if left else old.accounts.get(key[0], {}).get("positions_as_of")
        current_clock = right.get("positions_as_of") if right else new.accounts.get(key[0], {}).get("positions_as_of")
        if any(row and f"position:{row['id']}" in changed_refs for row in (left, right)) or (
                old_owner and new_owner and old_owner != new_owner) or (
                left and right and left.get("vehicle_type") != right.get("vehicle_type")):
            issue = "SECURITY_IDENTITY_CHANGED"
        elif key[0] in corporate_accounts or _possible_split(left, right, before, after):
            issue = "CORPORATE_ACTION_RECONCILIATION_REQUIRED"
        elif any(r and r.get("classification_verification_status") not in {"VERIFIED", "EXPLICIT", "DETERMINISTIC"}
                 for r in (left, right)):
            issue = "SECURITY_IDENTITY_UNVERIFIED"
        elif (left is None or right is None) and set(old.accounts) != set(new.accounts):
            issue = "ACCOUNT_UNIVERSE_CHANGED"
        elif before is None or after is None:
            issue = "POSITION_COVERAGE_OR_SOURCE_TIME_UNVERIFIED"
        elif instant(before_clock) is None or instant(current_clock) is None or instant(current_clock) <= instant(before_clock):
            issue = "NEW_POSITION_SOURCE_TIME_UNVERIFIED"
        else:
            issue = None
        delta = after-before if issue is None and before is not None and after is not None else None
        # The exact same listing and currency are compared across accounts.
        # Cross-listed or different share classes only aggregate by money.
        grouped[key[1:]].append(key)
        result[key] = {"account_ref": key[0], "symbol": key[1], "currency": key[2],
                       "broker": new.accounts.get(key[0], old.accounts.get(key[0], {})).get("broker"),
                       "issuer_id": new_owner or old_owner, "before_quantity": before, "current_quantity": after,
                       "quantity_delta": delta, "before_amount_cny": market_before, "current_amount_cny": market_after,
                       "amount_delta_cny": market_after-market_before if issue is None and market_before is not None
                                           and market_after is not None else None,
                       "before_as_of": before_clock, "current_as_of": current_clock,
                       "issue": issue, "comparable": issue is None, "attribution": "PRICE_OR_FX_ONLY" if delta == 0 else "UNATTRIBUTED"}
    for keys in grouped.values():
        rows = [result[key] for key in keys]
        if len(rows) > 1 and not any(row["issue"] for row in rows):
            changes = [row["quantity_delta"] for row in rows]
            if any(changes) and sum(changes, ZERO) == 0:
                for row in rows:
                    if row["quantity_delta"] != 0:
                        row["attribution"] = "POSSIBLE_ACCOUNT_TRANSFER"
    return result


def issuer_value_deltas(positions):
    issuers = defaultdict(list)
    for row in positions.values():
        if row["issuer_id"]:
            issuers[row["issuer_id"]].append(row)
    return {key: {"before_amount_cny": sum((r["before_amount_cny"] for r in rows), ZERO)
                  if all(r["before_amount_cny"] is not None and not r["issue"] for r in rows) else None,
                  "current_amount_cny": sum((r["current_amount_cny"] for r in rows), ZERO)
                  if all(r["current_amount_cny"] is not None and not r["issue"] for r in rows) else None,
                  "members": [{"account_ref":r["account_ref"], "symbol":r["symbol"]} for r in rows]}
            for key, rows in sorted(issuers.items())}


def compare_cash(old, new):
    result = {}
    for key in sorted(old.cash.keys() | new.cash.keys(), key=str):
        before, after = old.native_cash(key), new.native_cash(key)
        before_clock, current_clock = old.cash_as_of(key), new.cash_as_of(key)
        left, right = instant(before_clock), instant(current_clock)
        delta = after-before if before is not None and after is not None and left and right and right > left else None
        result[key] = {"account_ref":key[0], "currency":key[1], "before_native":before, "current_native":after,
                       "delta_native": delta, "before_as_of":before_clock, "current_as_of":current_clock,
                       "issue":None if delta is not None else "CASH_COMPARABILITY_UNVERIFIED",
                       "availability": "SETTLEMENT_OR_RESTRICTION_UNVERIFIED",
                       "owner_purpose":new.cash_purpose(key),
                       "arrival_proof": "EVIDENCE_PENDING"}
    return result


def compare_debt(old, new):
    refs = {ref for account in (old, new) for ref, row in account.manual.items()
            if str(row.get("kind", "")).lower() == "liability"}
    result = {}
    for ref in sorted(refs):
        before, after = old.loan(ref), new.loan(ref)
        a, b = (before or {}).get("principal_cny"), (after or {}).get("principal_cny")
        newer = False
        identity_unchanged = all(old.manual.get(ref, {}).get(field) == new.manual.get(ref, {}).get(field)
                                 for field in ("item_type", "name"))
        if (identity_unchanged and before and after
                and datetime.fromisoformat(after["value_as_of"]).date() > datetime.fromisoformat(before["value_as_of"]).date()):
            left, right = (datetime.fromisoformat(row["last_verified_at"]) for row in (before, after))
            newer = right > left if left.tzinfo and right.tzinfo else right.date() > left.date()
        result[ref] = {"before":before, "current":after, "principal_delta_cny":b-a if newer and a is not None and b is not None else None,
                       "issue":None if newer and a is not None and b is not None else "DEBT_EVIDENCE_PENDING"}
    return result

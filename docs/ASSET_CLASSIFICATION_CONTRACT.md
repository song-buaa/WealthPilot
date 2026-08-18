# Canonical Asset Classification Contract

WealthPilot separates three facts that must not be used interchangeably:

| Layer | Meaning | Examples |
| --- | --- | --- |
| `broker_security_type` | Broker-native trading contract fact | IBKR `STK`, `BOND`, `CASH` |
| `vehicle_type` | Legal/traded wrapper | `COMMON_STOCK`, `ETF`, `BOND`, `FUND`, `CASH` |
| `economic_asset_class` | Portfolio economic exposure | `EQUITY`, `FIXED_INCOME`, `CASH`, `COMMODITY`, `MULTI_ASSET`, `ALTERNATIVE`, `UNKNOWN` |

`economic_asset_subclass`, classification source, confidence, verification status,
version, and raw evidence accompany the classification. The historical Chinese
`asset_class` field remains a presentation compatibility label; portfolio,
allocation, risk, Decision, and API consumers use `economic_asset_class` as the
authority.

## Resolution priority

1. A verified `conId` or ISIN mapping. Conflicting stable identifiers fail closed.
2. User-explicit classification, retaining its provenance.
3. Deterministic broker metadata: direct bond is fixed income, cash is cash, and an
   explicitly identified common stock is equity.
4. ETF/fund exposure metadata such as category, subcategory, industry, or an
   unambiguous long name.
5. `UNKNOWN` when the evidence is insufficient.

IBKR `secType=STK` is never enough to infer equity because IBKR represents both
common stock and ETFs as `STK`. `stockType=ETF` establishes the vehicle only; an
ETF needs separate exposure evidence. An unresolved ETF stays `UNKNOWN` and must
not default to equity.

## Examples

| Instrument | Broker type | Vehicle | Economic class | Authority |
| --- | --- | --- | --- | --- |
| AAPL | `STK` + `COMMON` | `COMMON_STOCK` | `EQUITY` | deterministic metadata |
| CBU3 / IB01 / CBU0 / VDCA | `STK` + `ETF` | `ETF` | `FIXED_INCOME` | verified conId/ISIN |
| SPY | `STK` + `ETF` | `ETF` | `EQUITY` | verified conId/ISIN |
| Direct Treasury | `BOND` | `BOND` | `FIXED_INCOME` | deterministic metadata |
| USD cash | `CASH` | `CASH` | `CASH` | deterministic metadata |

Broker adapters only normalize raw facts into evidence. The sole economic
classification authority is `backend/services/instruments/classification.py`.

"""Binance Spot read-only source for the existing U0 publication path."""

import re


def valid_asset_code(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Z0-9]{1,20}", value) is not None

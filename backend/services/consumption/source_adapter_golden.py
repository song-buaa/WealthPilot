"""Reusable source-adapter golden assertions for redacted local fixtures.

The harness deliberately works on the normalized ``ParsedStatement`` contract:
source files (including real PDFs/EML) stay outside Git, while a fixture can
assert every source fact required by downstream Raw persistence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from backend.services.consumption.contracts import ParsedStatement, source_file_hash


def assert_source_adapter_golden(
    parsed: ParsedStatement,
    *,
    source_bytes: bytes,
    expected_statement: Mapping[str, Any],
    expected_transactions: Sequence[Mapping[str, Any]],
) -> None:
    """Assert the complete source-fact contract produced by one Adapter.

    The transaction representation includes identity, dates, descriptions,
    signed source and original amounts, currencies, availability, source type,
    card/instrument, and adapter provenance.  This keeps correctness checks
    explicit without copying sensitive source files into the repository.
    """
    expected_metadata = deepcopy(dict(expected_statement))
    expected_metadata["source_file_hash"] = source_file_hash(source_bytes)

    assert parsed.metadata.to_dict() == expected_metadata
    assert [item.to_dict() for item in parsed.transactions] == list(expected_transactions)
    assert parsed.transactions, "a source statement must contain transaction rows"

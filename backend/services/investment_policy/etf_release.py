"""Owner publication and read-only verification of ETF evidence versions."""
from datetime import datetime
from decimal import Decimal
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, model_validator

from backend.services.instruments.etf_holdings import ETFHoldings, ETF_IDENTITIES

from .contracts import Contract, canonical

MAX_BYTES = 512 * 1024
AUTOMATED_SPY_PROOF = "+spy-close-nav.v2"
HASH = r"^[0-9a-f]{64}$"


class OwnerEvidence(Contract):
    schema_version: Literal["wealthpilot.etf-evidence.v1"]
    portfolio_id: int
    records: tuple[ETFHoldings, ...] = Field(max_length=3)


class ReleaseReceipt(Contract):
    schema_version: Literal["wealthpilot.etf-release.v1"] = "wealthpilot.etf-release.v1"
    portfolio_id: int = Field(gt=0)
    sequence: int = Field(gt=0)
    manifest_sha256: str = Field(pattern=HASH)
    published_at: datetime
    close_caps_usd: dict[str, Decimal] = Field(default_factory=dict)
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")

    @model_validator(mode="after")
    def publication_clock(self):
        if self.published_at.utcoffset() is None:
            raise ValueError("ETF_RELEASE_CLOCK_INVALID")
        return self

    def signed_bytes(self):
        return canonical(self.model_dump(mode="json", exclude={"signature"})).encode()


def release_root(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("ETF_RELEASE_PATH_INVALID")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o027:
        raise ValueError("ETF_RELEASE_DIRECTORY_WRITABLE_BY_CONSUMER")
    return path


def consumer_directory(path):
    root = release_root(path)
    if root.stat().st_uid == os.getuid():
        raise ValueError("ETF_RELEASE_CONSUMER_MUST_BE_READ_ONLY")
    return root


def read_owned(root, name, *, limit=MAX_BYTES):
    """Reader may share the Owner's group but has read access only."""
    with os.fdopen(os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != root.stat().st_uid
                or info.st_mode & 0o027):
            raise ValueError("ETF_RELEASE_FILE_PERMISSIONS_INVALID")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("ETF_RELEASE_FILE_TOO_LARGE")
    return data


def read_release(root, public_key, portfolio_id, at, *, pointer="current.json"):
    if pointer != "current.json" and not re.fullmatch(r"receipt-[0-9a-f]{64}\.json", pointer):
        raise ValueError("ETF_RELEASE_RECEIPT_PATH_INVALID")
    receipt_bytes = read_owned(root, pointer, limit=4096)
    if pointer != "current.json" and pointer != f"receipt-{sha256(receipt_bytes).hexdigest()}.json":
        raise ValueError("ETF_RELEASE_RECEIPT_DIGEST_MISMATCH")
    receipt = ReleaseReceipt.model_validate_json(receipt_bytes)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(bytes.fromhex(receipt.signature),
                                                            receipt.signed_bytes())
    except (InvalidSignature, ValueError) as error:
        raise ValueError("ETF_RELEASE_SIGNATURE_INVALID") from error
    if receipt.portfolio_id != portfolio_id or receipt.published_at > at:
        raise ValueError("ETF_RELEASE_BINDING_INVALID")
    raw = read_owned(root, f"manifest-{receipt.manifest_sha256}.json")
    if sha256(raw).hexdigest() != receipt.manifest_sha256:
        raise ValueError("ETF_RELEASE_DIGEST_MISMATCH")
    manifest = OwnerEvidence.model_validate_json(raw)
    if (not receipt.close_caps_usd.keys() <= {r.symbol for r in manifest.records}
            or any(cap <= 0 for cap in receipt.close_caps_usd.values())
            or any(r.parser_version.endswith(AUTOMATED_SPY_PROOF) and r.symbol not in receipt.close_caps_usd
                   for r in manifest.records)
            or manifest.portfolio_id != portfolio_id
            or len({record.symbol for record in manifest.records}) != len(manifest.records)
            or any(record.known_at > receipt.published_at for record in manifest.records)):
        raise ValueError("ETF_RELEASE_MANIFEST_BINDING_INVALID")
    return receipt, manifest


def write_version(root, name, data, *, activate=False):
    fd, temp = tempfile.mkstemp(dir=root)
    temp = Path(temp)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchown(handle.fileno(), -1, root.stat().st_gid)
            os.fchmod(handle.fileno(), 0o640)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        target = root / name
        if activate:
            os.replace(temp, target)
        else:
            try:
                os.link(temp, target)
            except FileExistsError:
                if read_owned(root, name) != data:
                    raise ValueError("ETF_RELEASE_IMMUTABLE_VERSION_CONFLICT")
        parent = os.open(root, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temp.unlink(missing_ok=True)


def publish(root, key, portfolio_id, updates, at, *, rollback_receipt=None, close_caps=None):
    """Serialize activations. A rollback creates a fresh receipt, keeping original clocks."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    root = release_root(root)
    if root.stat().st_uid != os.getuid() or at.utcoffset() is None:
        raise ValueError("ETF_RELEASE_OWNER_OR_CLOCK_INVALID")
    if (not updates.keys() <= ETF_IDENTITIES.keys() or any(
        record.symbol != symbol or record.known_at > at or record.valid_until < at
        or (record.parser_version.endswith(AUTOMATED_SPY_PROOF) and symbol not in (close_caps or {}))
        for symbol, record in updates.items()
    )):
        raise ValueError("ETF_RELEASE_UPDATE_INVALID")
    close_caps = close_caps or {}
    if not close_caps.keys() <= updates.keys() or any(not cap.is_finite() or cap <= 0 for cap in close_caps.values()):
        raise ValueError("ETF_RELEASE_CLOSE_CAP_INVALID")
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    with os.fdopen(os.open(root / ".publish.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.lexists(root / "current.json"):
            previous, current = read_release(root, public, portfolio_id, at)
        else:
            previous, current = None, None
        records = {r.symbol: r for r in current.records} if current else {}
        caps = dict(previous.close_caps_usd) if previous else {}
        if rollback_receipt:
            if updates:
                raise ValueError("ETF_RELEASE_ROLLBACK_WITH_UPDATES")
            old_receipt, target = read_release(root, public, portfolio_id, at, pointer=rollback_receipt)
            records = {r.symbol: r for r in target.records if r.valid_until >= at}
            caps = {symbol: cap for symbol, cap in old_receipt.close_caps_usd.items() if symbol in records}
        else:
            for symbol, record in updates.items():
                old = records.get(symbol)
                if old and record.holdings_as_of < old.holdings_as_of:
                    raise ValueError("ETF_RELEASE_SOURCE_REGRESSION")
                if old and (record.holdings_as_of, record.source_sha256) == (old.holdings_as_of, old.source_sha256):
                    continue  # Same official bytes keep original known_at and expiry.
                records[symbol] = record
                caps.pop(symbol, None)
                if symbol in close_caps:
                    caps[symbol] = close_caps[symbol]
        manifest = OwnerEvidence(schema_version="wealthpilot.etf-evidence.v1", portfolio_id=portfolio_id,
                                 records=tuple(records[symbol] for symbol in sorted(records)))
        raw = canonical(manifest).encode()
        manifest_sha = sha256(raw).hexdigest()
        if previous and manifest_sha == previous.manifest_sha256 and caps == previous.close_caps_usd:
            return previous
        if not previous and not records:
            return None
        receipt = ReleaseReceipt(portfolio_id=portfolio_id, sequence=previous.sequence + 1 if previous else 1,
                                 manifest_sha256=manifest_sha, published_at=at, close_caps_usd=caps, signature="0" * 128)
        receipt = receipt.model_copy(update={"signature": key.sign(receipt.signed_bytes()).hex()})
        signed = canonical(receipt).encode()
        write_version(root, f"manifest-{manifest_sha}.json", raw)
        write_version(root, f"receipt-{sha256(signed).hexdigest()}.json", signed)
        write_version(root, "current.json", signed, activate=True)
        return receipt

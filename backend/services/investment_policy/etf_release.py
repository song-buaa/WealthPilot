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
from .etf_observation import ETFObservation, freshness, latest_completed_session

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


def consumer_directory(path, owner_uid):
    root = release_root(path)
    if root.stat().st_uid != owner_uid:
        raise ValueError("ETF_RELEASE_OWNER_UID_MISMATCH")
    if owner_uid == os.getuid():
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



class ObservationEvidence(Contract):
    schema_version: Literal["wealthpilot.etf-observation.v1"] = "wealthpilot.etf-observation.v1"
    portfolio_id: int
    observations: tuple[ETFObservation, ...] = Field(max_length=3)


class ObservationAttestation(Contract):
    schema_version: Literal["wealthpilot.etf-observation-attestation.v1"] = "wealthpilot.etf-observation-attestation.v1"
    receipt_sha256: str = Field(pattern=HASH)
    observation_sha256: str = Field(pattern=HASH)
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")

    def signed_bytes(self):
        return canonical(self.model_dump(mode="json", exclude={"signature"})).encode()


def read_observations(root, receipt, public_key):
    receipt_sha = sha256(canonical(receipt).encode()).hexdigest()
    try:
        attestation = ObservationAttestation.model_validate_json(
            read_owned(root, f"observation-attestation-{receipt_sha}.json", limit=4096))
    except FileNotFoundError:
        return {}
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            bytes.fromhex(attestation.signature), attestation.signed_bytes())
    except (InvalidSignature, ValueError) as error:
        raise ValueError("ETF_OBSERVATION_SIGNATURE_INVALID") from error
    if attestation.receipt_sha256 != receipt_sha:
        raise ValueError("ETF_OBSERVATION_RECEIPT_MISMATCH")
    raw = read_owned(root, f"observation-manifest-{attestation.observation_sha256}.json")
    if sha256(raw).hexdigest() != attestation.observation_sha256:
        raise ValueError("ETF_OBSERVATION_DIGEST_MISMATCH")
    value = ObservationEvidence.model_validate_json(raw)
    records = {r.symbol: r for r in value.observations}
    if (value.portfolio_id != receipt.portfolio_id or len(records) != len(value.observations)
            or any(r.known_at > receipt.published_at for r in value.observations)):
        raise ValueError("ETF_OBSERVATION_BINDING_INVALID")
    return records


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


def publish(root, key, portfolio_id, updates, at, *, rollback_receipt=None, close_caps=None,
            observations=None, with_event=False):
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
    observations = observations or {}
    latest = latest_completed_session(at) if observations else None
    if any(symbol != record.symbol or freshness(record, at, latest)[0] != "OBSERVED"
           for symbol, record in observations.items()):
        raise ValueError("ETF_OBSERVATION_UPDATE_INVALID")
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
        previous_observed = read_observations(root, previous, public) if previous else {}
        observed = dict(previous_observed)
        if rollback_receipt:
            if updates or observations:
                raise ValueError("ETF_RELEASE_ROLLBACK_WITH_UPDATES")
            old_receipt, target = read_release(root, public, portfolio_id, at, pointer=rollback_receipt)
            records = {r.symbol: r for r in target.records if r.valid_until >= at}
            caps = {symbol: cap for symbol, cap in old_receipt.close_caps_usd.items() if symbol in records}
            prior_observations = read_observations(root, old_receipt, public)
            if prior_observations:
                latest = latest_completed_session(at)
            observed = {s: r for s, r in prior_observations.items() if freshness(r, at, latest)[0] == "OBSERVED"}
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
            for symbol, record in observations.items():
                old = observed.get(symbol)
                if old and record.holdings_as_of < old.holdings_as_of:
                    continue  # One lagging provider must not block other updates.
                if old and (old.source_sha256, old.parser_version, old.issuer_version, old.listing_sha256) == (
                        record.source_sha256, record.parser_version, record.issuer_version, record.listing_sha256):
                    continue
                observed[symbol] = record
        manifest = OwnerEvidence(schema_version="wealthpilot.etf-evidence.v1", portfolio_id=portfolio_id,
                                 records=tuple(records[symbol] for symbol in sorted(records)))
        raw = canonical(manifest).encode()
        manifest_sha = sha256(raw).hexdigest()
        observation_raw = canonical(ObservationEvidence(
            portfolio_id=portfolio_id, observations=tuple(observed[s] for s in sorted(observed)))).encode()
        observation_sha = sha256(observation_raw).hexdigest() if observed else None
        if (previous and manifest_sha == previous.manifest_sha256 and caps == previous.close_caps_usd
                and observed == previous_observed):
            return (previous, False) if with_event else previous
        if not previous and not records and not observed:
            return (None, False) if with_event else None
        receipt = ReleaseReceipt(portfolio_id=portfolio_id, sequence=previous.sequence + 1 if previous else 1,
                                 manifest_sha256=manifest_sha, published_at=at, close_caps_usd=caps, signature="0" * 128)
        receipt = receipt.model_copy(update={"signature": key.sign(receipt.signed_bytes()).hex()})
        signed = canonical(receipt).encode()
        receipt_sha = sha256(signed).hexdigest()
        write_version(root, f"manifest-{manifest_sha}.json", raw)
        if observation_sha:
            write_version(root, f"observation-manifest-{observation_sha}.json", observation_raw)
            attestation = ObservationAttestation(receipt_sha256=receipt_sha, observation_sha256=observation_sha,
                                                 signature="0" * 128)
            attestation = attestation.model_copy(update={"signature": key.sign(attestation.signed_bytes()).hex()})
            write_version(root, f"observation-attestation-{receipt_sha}.json", canonical(attestation).encode())
        write_version(root, f"receipt-{receipt_sha}.json", signed)
        write_version(root, "current.json", signed, activate=True)
        return (receipt, True) if with_event else receipt

"""Self-service API keys, stored hashed in SQLite.

A key is shown once, at creation; only its SHA-256 is kept. Every key has a
tier. Today every tier may use every feature (`enforce` is a no-op); paid
plans only need a tier change on the key and a rule in `enforce`.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

KEY_PREFIX = "am2m_"

TIERS: list[dict] = [
    {
        "id": "free",
        "name": "Free",
        "price": "$0",
        "period": "forever",
        "available": True,
        "summary": "Everything AutoM2M and AgentHOT offer, with no limits during the research preview.",
        "features": [
            "All MCP tools (AgentHOT + AutoM2M) over streamable HTTP",
            "REST API with the same operations",
            "W1–W6 checker, host-mode builder and binding loop",
            "Persistent projects per API key",
            "No rate limits during the preview",
        ],
        "limits": {},
    },
    {
        "id": "pro",
        "name": "Pro",
        "price": "TBD",
        "period": "per month",
        "available": False,
        "summary": "For teams that want the server to do the LLM work and keep longer histories.",
        "features": [
            "Unattended auto_solve with a hosted engine LLM",
            "Higher concurrency and larger projects",
            "Longer run history and exports",
            "Priority support",
        ],
        "limits": {},
    },
]


class FeatureNotInTier(PermissionError):
    pass


def enforce(tier: str, feature: str) -> None:
    """Gate a feature by tier. No restrictions yet: every tier may use everything."""
    return


@dataclass
class KeyRecord:
    id: str
    prefix: str
    label: str
    email: str
    tier: str
    created: float
    last_used: float | None
    request_count: int
    revoked: bool

    def public(self) -> dict:
        d = asdict(self)
        return d


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY, prefix TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
                    tier TEXT NOT NULL DEFAULT 'free', created REAL NOT NULL, last_used REAL,
                    request_count INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0)"""
            )

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _rec(row: sqlite3.Row) -> KeyRecord:
        return KeyRecord(row["id"], row["prefix"], row["label"], row["email"], row["tier"], row["created"],
                         row["last_used"], row["request_count"], bool(row["revoked"]))

    def create(self, label: str = "", email: str = "", tier: str = "free") -> tuple[KeyRecord, str]:
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        kid = secrets.token_hex(8)
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO api_keys (id, prefix, sha256, label, email, tier, created) VALUES (?,?,?,?,?,?,?)",
                      (kid, key[: len(KEY_PREFIX) + 6], _hash(key), label[:80], email[:120], tier, now))
        rec = self.get(kid)
        assert rec is not None
        return rec, key

    def get(self, kid: str) -> KeyRecord | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM api_keys WHERE id = ?", (kid,)).fetchone()
        return self._rec(row) if row else None

    def resolve(self, key: str | None, *, touch: bool = True) -> KeyRecord | None:
        """The active key record for a presented key, or None."""
        if not key or not key.startswith(KEY_PREFIX):
            return None
        with self._conn() as c:
            row = c.execute("SELECT * FROM api_keys WHERE sha256 = ? AND revoked = 0", (_hash(key),)).fetchone()
            if row is None:
                return None
            rec = self._rec(row)
            if touch:
                rec.last_used = time.time()
                rec.request_count += 1
                c.execute("UPDATE api_keys SET last_used = ?, request_count = request_count + 1 WHERE id = ?",
                          (rec.last_used, rec.id))
        return rec

    def revoke(self, kid: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("UPDATE api_keys SET revoked = 1 WHERE id = ? AND revoked = 0", (kid,))
        return cur.rowcount > 0

    def counts(self) -> dict:
        with self._conn() as c:
            active = c.execute("SELECT COUNT(*) FROM api_keys WHERE revoked = 0").fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
        return {"active": active, "total": total}


def key_from_headers(headers) -> str | None:
    """`Authorization: Bearer <key>` or `X-API-Key: <key>`."""
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return (headers.get("x-api-key") or "").strip() or None

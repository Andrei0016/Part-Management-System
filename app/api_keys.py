"""API key issuance and verification for the internal /api/* blueprint.

Keys are generated with high entropy (32 random bytes) and stored only as a
SHA-256 hash, the same "show once, verify by hash lookup" pattern used by
GitHub/Stripe-style tokens. This lets any number of independent keys exist
(one per client, e.g. the MCP server, a future mobile app, a personal
script) and be revoked individually without touching other keys or
restarting anything.
"""
import hashlib
import secrets
from datetime import datetime, timezone

from app.extensions import db
from app.models import ApiKey


class ApiKeyError(ValueError):
    """Raised for user-facing validation failures (e.g. duplicate label)."""


def generate_key():
    """Return a new high-entropy raw key. Caller must show/store this now —
    it is never recoverable once created (only its hash is kept)."""
    return secrets.token_hex(32)


def hash_key(raw_key):
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_api_key(label, owner_user=None):
    """Create and persist a new key, returning (ApiKey row, raw key).

    Rejects a label already used by an active key with the same owner —
    two active keys both called e.g. "MCP server" for the same person is
    just confusing (same idea as GitHub blocking duplicate token names).
    Revoking a key frees its label back up; unowned keys are compared
    against other unowned keys the same way.
    """
    existing = ApiKey.query.filter_by(
        label=label, owner_user_id=owner_user.id if owner_user else None, is_active=True
    ).first()
    if existing is not None:
        owner_desc = f"'{owner_user.username}'" if owner_user else "no owner"
        raise ApiKeyError(f"An active key named '{label}' already exists for {owner_desc}.")

    raw_key = generate_key()
    key = ApiKey(
        label=label,
        key_hash=hash_key(raw_key),
        owner_user_id=owner_user.id if owner_user else None,
    )
    db.session.add(key)
    db.session.commit()
    return key, raw_key


def verify_api_key(raw_key):
    """Look up an active key by hash. Returns the ApiKey row, or None.
    Bumps last_used_at on success."""
    if not raw_key:
        return None
    key = ApiKey.query.filter_by(key_hash=hash_key(raw_key), is_active=True).first()
    if key is None:
        return None
    key.last_used_at = datetime.now(timezone.utc)
    db.session.commit()
    return key


def revoke_api_key(key):
    key.is_active = False
    db.session.commit()


def reactivate_api_key(key):
    key.is_active = True
    db.session.commit()


def delete_api_key(key):
    """Permanently remove a key. Safe to hard-delete (unlike User): nothing
    holds a foreign key to ApiKey — LogEntry attribution is captured via
    User at write time, not a live reference to the key itself."""
    db.session.delete(key)
    db.session.commit()

"""Track the weekly audit and saved comparison revisions behind a live decision."""

import hmac
from contextlib import contextmanager

from . import audit_trust
from .contracts import InputError, canonical

DOMAIN = "weekly-derived-comparison-v1"
STAMP = "weekly_comparison_auth"


def check_source_head(db, source: dict) -> None:
    head = db.execute(
        "SELECT revision,signature FROM reports WHERE id=?",
        (source["source_publication"],),
    ).fetchone()
    if head != (source["source_revision"], source["source_signature"]):
        raise InputError(
            "weekly proposal refers to a stale or unknown audit revision; regenerate it from the current report"
        )


def _signature(state, revision, previous, key):
    evidence = {k: v for k, v in state.items() if k != STAMP}
    return audit_trust.mac(
        key, DOMAIN, canonical([revision, previous, evidence]).encode("ascii")
    )


def _stamp(state, revision, previous, key):
    signature = _signature(state, revision, previous, key)
    state[STAMP] = {"revision": revision, "previous": previous, "signature": signature}
    return signature


def _check_comparison(db, state, key):
    stamp = state.get(STAMP)
    if not isinstance(stamp, dict):
        raise InputError(
            "historical weekly proposal comparison has no signed source binding; "
            "replay a current signed weekly proposal before using this gate"
        )
    revision, previous, signature = (
        stamp.get(k) for k in ("revision", "previous", "signature")
    )
    if (
        type(revision) is not int
        or revision < 1
        or not isinstance(signature, str)
        or not signature.isascii()
        or not hmac.compare_digest(
            signature, _signature(state, revision, previous, key)
        )
    ):
        raise InputError(
            "weekly comparison evidence does not match the originating team signature"
        )
    db.execute(
        "CREATE TABLE IF NOT EXISTS comparisons (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, signature TEXT NOT NULL)"
    )
    ident = state["comparison_id"]
    head = db.execute(
        "SELECT revision,signature FROM comparisons WHERE id=?", (ident,)
    ).fetchone()
    if head == (revision, signature):
        return
    if head is None and revision == 1 and previous is None:
        db.execute(
            "INSERT INTO comparisons VALUES(?,?,?)", (ident, revision, signature)
        )
    else:
        raise InputError(
            "stale or diverging weekly-derived comparison; open the latest saved comparison revision"
        )


def sign_initial(state: dict) -> None:
    with audit_trust.registry() as (db, key):
        check_source_head(db, state["weekly_proposal"])
        _stamp(state, 1, None, key)
        _check_comparison(db, state, key)


@contextmanager
def current(state: dict, *, require_source=True):
    """Hold both registry heads stable through an action on a proposal comparison."""
    if "weekly_proposal" not in state:
        yield None
        return
    with audit_trust.registry() as (db, key):
        _check_comparison(db, state, key)
        if require_source:
            check_source_head(db, state["weekly_proposal"])
        yield db, key


def advance(state: dict, store) -> None:
    """Sign a change to checked evidence before publishing inside the same transaction."""
    if store is None:
        return
    db, key = store
    check_source_head(db, state["weekly_proposal"])
    parent = state[STAMP]
    revision = parent["revision"] + 1
    signature = _stamp(state, revision, parent["signature"], key)
    db.execute(
        "UPDATE comparisons SET revision=?,signature=? WHERE id=?",
        (revision, signature, state["comparison_id"]),
    )

"""Trusted standalone selectors for the synthetic refund example."""


def _rank(batch, capacity, field):
    ranked = sorted(batch, key=lambda request: (-request[field], request["id"]))
    return [request["id"] for request in ranked[:capacity]]


def rank_by_amount(batch, capacity):
    return _rank(batch, capacity, "refund_cents")


def rank_by_order_conflict(batch, capacity):
    return _rank(batch, capacity, "conflict_priority")


def rank_without_mandatory(batch, capacity):
    return _rank(
        [request for request in batch if request["id"] != "R-041"],
        capacity,
        "conflict_priority",
    )


def rank_by_refined_conflict(batch, capacity):
    return _rank(batch, capacity, "refined_priority")

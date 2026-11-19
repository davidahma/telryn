"""Equivalent Python rankings for the synthetic v0.1.1 benchmark."""


def original(batch, capacity):
    ordered = sorted(batch, key=lambda row: (-row["priority"], row["id"]))
    return [row["id"] for row in ordered[:capacity]]


def candidate(batch, capacity):
    ordered = sorted(batch, key=lambda row: (-row["risk"], row["id"]))
    return [row["id"] for row in ordered[:capacity]]

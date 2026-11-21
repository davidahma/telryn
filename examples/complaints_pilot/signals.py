"""The rehearsal's transparent screen uses only evidence visible at selection."""

import re
from datetime import timedelta

from telryn import screen
from telryn.contracts import timestamp

# These example prompts for human review deliberately include broad triggers.
TRIGGERS = {
    "expressed_dissatisfaction": r"\b(complain\w*|investigat\w*|unhappy)\b",
    "repeated_request": r"\b(third time|keep asking|again and again)\b",
    "external_escalation": r"\b(ombudsman|regulator)\b",
    "essential_expense": r"\b(medicine|rent|groceries|hardship)\b",
}


def prior_related(case, contacts, days):
    """Look back from the conversation, ignoring subsequent and unrelated contacts."""
    closed = timestamp(case["closed_at"], "conversation closure")
    start = (closed[0] - timedelta(days=days), closed[1])
    subject, matter = case["subject_ref"], case["matter_ref"]
    return [
        c
        for c in contacts
        if subject
        and matter
        and c["subject_ref"] == subject
        and c["matter_ref"] == matter
        and c["case_id"] != case["case_id"]
        and start <= timestamp(c["contact_at"], "earlier contact time") < closed
    ]


def flags(case, prior_contacts, known):
    text = case["message"].casefold()
    cues = [
        name for name, expression in TRIGGERS.items() if re.search(expression, text)
    ]
    if prior_contacts:
        cues.append("prior_contact_about_same_payment")
    if "refund issued" in case["agent_note"].casefold() and re.search(
        r"\b(still|unhappy|why)\b", text
    ):
        cues.append("dissatisfaction_after_refund")
    facts = dict.fromkeys(screen.SIGNALS, "unknown")
    facts.update(
        complaint="yes" if cues else "unknown",
        complaint_logged="yes" if known else "unknown",
    )
    return screen.decide(facts, "unlogged-complaint"), cues

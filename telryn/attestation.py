"""Issue and verify portable vendor conduct evidence."""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from . import bank_report, evidence_signatures, oversight, seed, weekly_report
from .artifacts import new_directory
from .contracts import InputError, canonical, decode_json, read_json, timestamp

PURPOSE = "telryn-vendor-attestation-v1"
FILES = ("seed-receipt.json", "calibration.html", "bank-statement.html")
LIMIT = 32 * 1024 * 1024
AIUC = {
    "seed-receipt.json": {
        "control": "E004.1",
        "source": "https://standard.aiuc-1.com/accountability/assign-accountability",
        "support": "One conduct test receipt for the release approver",
    },
    "calibration.html": {
        "control": "E008",
        "source": "https://standard.aiuc-1.com/accountability/review-internal-processes",
        "support": "Weekly complaint oversight records for the reviewed scope",
    },
    "bank-statement.html": {
        "control": "E008",
        "source": "https://standard.aiuc-1.com/accountability/review-internal-processes",
        "support": "Dated registration and aggregate follow-up evidence for the selected week",
    },
}


def _read(path, *, bundled=False):
    if bundled and path.is_symlink():
        raise InputError("attestation members must be regular bundle files")
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise InputError("attestation inputs must be files no larger than 32 MiB")
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise InputError("attestation inputs must be files no larger than 32 MiB")
    return raw


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _profile(path):
    if path.stat().st_size > LIMIT:
        raise InputError("profile exceeds 32 MiB")
    profile = read_json(path, "attestation profile")
    required = {
        "schema",
        "vendor",
        "customer",
        "source_queue",
        "bank_queue",
        "scorecard",
        "scorecard_version",
        "evidence",
        "customer_controls",
    }
    if set(profile) != required or profile["evidence"] not in (
        "synthetic",
        "operational",
    ):
        raise InputError("use the documented attestation profile")
    for name in required - {"schema", "evidence", "customer_controls"}:
        profile[name] = bank_report._public_label(profile[name])
    controls = profile["customer_controls"]
    if not isinstance(controls, dict) or set(controls) != set(FILES):
        raise InputError("supply customer control IDs for each evidence file")
    for ids in controls.values():
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 20
            or any(
                not isinstance(item, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}", item)
                for item in ids
            )
            or len(set(ids)) != len(ids)
        ):
            raise InputError("supply one to twenty distinct local control IDs per file")
    return profile


def _bank_scope(statement, profile):
    if (
        statement["declared_by_issuer"]["queue"] != profile["bank_queue"]
        or statement["scorecard"] != profile["scorecard"]
        or statement["scorecard_version"] != profile["scorecard_version"]
        or statement["synthetic"] != (profile["evidence"] == "synthetic")
    ):
        raise InputError("the bank statement does not match the approved profile")


def _sources(reports):
    return [weekly_report.verified_html(path) for path in reports]


def issue(args):
    if args.out.exists() or args.out.is_symlink():
        raise InputError("choose a new attestation output directory")
    profile = _profile(args.profile)
    bank = _read(args.bank)
    statement = bank_report._checked_statement(bank)
    _bank_scope(statement, profile)

    # Preflight signed report provenance before starting a test with side effects.
    originals = _sources(args.reports)
    series = oversight.compile_series(args.reports, args.quarter, require_scores=True)
    if _sources(args.reports) != originals:
        raise InputError(
            "a weekly revision changed during attestation; retry with current reports"
        )
    scope = series["scope"]
    if (
        scope["queue"] != profile["source_queue"]
        or scope["scorecard"] != profile["scorecard"]
        or scope["version"] != profile["scorecard_version"]
        or scope["synthetic"] != (profile["evidence"] == "synthetic")
    ):
        raise InputError("the quarterly series does not match the approved profile")
    report = next(
        (raw for raw in originals if _digest(raw) == statement["source_report_sha256"]),
        None,
    )
    if (
        report is None
        or bank_report._html(
            bank_report._statement(
                report, statement["declared_by_issuer"], statement["schema"]
            )
        )
        != bank
    ):
        raise InputError(
            "bank statement must replay from a current signed week in the series"
        )

    calibration = oversight.html(series, "vendor").encode("utf-8")
    # Fail early on a malformed or inaccessible signing key before an external
    # test adapter has an opportunity to submit synthetic messages.
    evidence_signatures.sign({}, args.key, PURPOSE)
    receipt = seed.run(
        args.suite, args.run_id, args.agent_adapter, args.register_adapter
    )
    seed.verify_receipt(args.suite, receipt)
    if not receipt["passed"]:
        raise InputError("the test seed failed; no attestation was issued")
    if _sources(args.reports) != originals:
        raise InputError(
            "a weekly revision changed during the test; retry with the same run ID"
        )
    files = {
        "seed-receipt.json": (canonical(receipt) + "\n").encode("ascii"),
        "calibration.html": calibration,
        "bank-statement.html": bank,
    }
    if any(len(raw) > LIMIT for raw in files.values()):
        raise InputError("attestation exceeded the file size limit")
    weeks = [
        {
            "week": envelope["state"]["week"],
            "revision": envelope["auth"]["revision"],
            "sha256": _digest(raw),
        }
        for raw in originals
        for envelope in (weekly_report._envelope(raw),)
    ]
    payload = {
        "schema": 1,
        "issued_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile": profile,
        "quarter": args.quarter,
        "scope": receipt["scope"],
        "run_id": receipt["run_id"],
        "suite_digest": receipt["suite_digest"],
        "weeks": sorted(weeks, key=lambda week: week["week"]),
        "aiuc_1_evidence_references": AIUC,
        "file_sha256": {name: _digest(raw) for name, raw in files.items()},
    }
    signed = evidence_signatures.sign(payload, args.key, PURPOSE)
    with new_directory(args.out) as staging:
        for name, raw in files.items():
            (staging / name).write_bytes(raw)
        (staging / "attestation.json").write_text(
            canonical(signed) + "\n", encoding="ascii"
        )
    return {"directory": str(args.out), "quarter": args.quarter, "test": "passed"}


def verify(args):
    if args.folder.is_symlink() or not args.folder.is_dir():
        raise InputError("supply the attestation directory")
    if {path.name for path in args.folder.iterdir()} != {"attestation.json", *FILES}:
        raise InputError("attestation directory must contain exactly the signed bundle")
    raw = _read(args.folder / "attestation.json", bundled=True)
    doc = evidence_signatures.verify(decode_json(raw, "attestation"), args.key, PURPOSE)
    names = {
        "schema",
        "issued_at",
        "profile",
        "quarter",
        "scope",
        "run_id",
        "suite_digest",
        "weeks",
        "aiuc_1_evidence_references",
        "file_sha256",
    }
    if (
        set(doc) != names
        or type(doc["schema"]) is not int
        or doc["schema"] != 1
        or doc["profile"] != _profile(args.profile)
        or doc["quarter"] != args.quarter
        or doc["run_id"] != args.run_id
        or doc["aiuc_1_evidence_references"] != AIUC
        or not isinstance(doc["file_sha256"], dict)
        or set(doc["file_sha256"]) != set(FILES)
    ):
        raise InputError(
            "attestation does not match the approved profile, period, or run"
        )
    timestamp(doc["issued_at"], "attestation time")
    files = {name: _read(args.folder / name, bundled=True) for name in FILES}
    if {name: _digest(raw) for name, raw in files.items()} != doc["file_sha256"]:
        raise InputError("attestation member digest differs from the signed manifest")
    receipt = decode_json(files["seed-receipt.json"], "seed receipt")
    seed.verify_receipt(args.suite, receipt)
    if (
        not receipt["passed"]
        or receipt["run_id"] != doc["run_id"]
        or receipt["scope"] != doc["scope"]
        or receipt["suite_digest"] != doc["suite_digest"]
    ):
        raise InputError("seed receipt does not match the attestation")
    statement = bank_report._checked_statement(files["bank-statement.html"])
    _bank_scope(statement, doc["profile"])
    valid_weeks = set(oversight.quarter_weeks(doc["quarter"]))
    seen = set()
    bank_week = {
        "week": statement["week"],
        "revision": statement["report_revision"],
        "sha256": statement["source_report_sha256"],
    }
    if not isinstance(doc["weeks"], list) or bank_week not in doc["weeks"]:
        raise InputError("bank statement is outside the signed calibration series")
    for row in doc["weeks"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"week", "revision", "sha256"}
            or not isinstance(row["week"], str)
            or row["week"] not in valid_weeks - seen
            or type(row["revision"]) is not int
            or row["revision"] < 1
            or not isinstance(row["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        ):
            raise InputError("invalid weekly identity in signed series")
        seen.add(row["week"])
    return {
        "verified": True,
        "vendor": doc["profile"]["vendor"],
        "customer": doc["profile"]["customer"],
        "evidence": doc["profile"]["evidence"],
        "quarter": doc["quarter"],
        "scope": doc["scope"],
        "weeks": len(seen),
        "bank_week": bank_week["week"],
        "file_sha256": doc["file_sha256"],
        "customer_controls": doc["profile"]["customer_controls"],
        "aiuc_1_evidence_references": AIUC,
    }


def register_arguments(commands):
    cmd = commands.add_parser(
        "attestation", help="issue or verify agent-vendor conduct evidence"
    )
    operation = cmd.add_subparsers(dest="attestation_action", required=True)
    create = operation.add_parser(
        "issue", help="run the release test and sign the evidence package"
    )
    check = operation.add_parser(
        "verify", help="verify scoped evidence with the approved issuer public key"
    )
    for sub in (create, check):
        for name in ("suite", "profile", "key"):
            sub.add_argument("--" + name, required=True, type=Path)
        sub.add_argument("--quarter", required=True)
        sub.add_argument("--run-id", required=True)
    for name in ("bank", "agent-adapter", "register-adapter", "out"):
        create.add_argument("--" + name, required=True, type=Path)
    create.add_argument("reports", nargs="+", type=Path)
    check.add_argument("folder", type=Path)


def execute(args):
    result = issue(args) if args.attestation_action == "issue" else verify(args)
    print(canonical(result))
    return 0

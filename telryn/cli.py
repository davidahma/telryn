"""Command-line entry point for Telryn."""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from . import (
    __version__,
    audit_trust,
    benchmark,
    intake,
    integrations,
    packs,
    planning,
    policy,
    screen,
    seed,
    weekly,
    weekly_report,
    weekly_review,
)
from .contracts import InputError, canonical
from .decision import SHADOW
from .grades import parse_minutes
from .proposal import propose
from .report import load_report, recommendation, signed_range, stopping_text
from .session import compare, publish, record_shadow, refresh

# The wheel keeps the source fixtures under Telryn's own namespace.
BUNDLED = Path(__file__).resolve().parent / "_bundled"
FIXTURE_ROOT = BUNDLED if BUNDLED.is_dir() else BUNDLED.parent.parent
EXAMPLE = FIXTURE_ROOT / "examples" / "refunds"


def _result_flags(parser: argparse.ArgumentParser, *, gate: bool = True) -> None:
    parser.add_argument("--format", choices=("text", "json"), default="text")
    if gate:
        parser.add_argument(
            "--require-shadow",
            action="store_true",
            help="exit 1 unless the recommendation is Try in shadow",
        )
        parser.add_argument(
            "--require-weekly-source",
            action="store_true",
            help="with --require-shadow, require a current comparison derived from a signed weekly proposal",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telryn",
        description="Audit unreviewed support cases and replay changes to the QA queue.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    week = commands.add_parser(
        "weekly", help="start a weekly audit or read its saved report"
    )
    week.add_argument(
        "source", type=Path, help="weekly TOML plan or the saved report.html"
    )
    week.add_argument(
        "--delivery-date",
        metavar="YYYY-MM-DD",
        help="with --out, require the completed audit week before this UTC Monday",
    )
    action = week.add_mutually_exclusive_group()
    action.add_argument(
        "--out", type=Path, help="create the report and assignments in a new directory"
    )
    action.add_argument(
        "--grades", type=Path, help="import a returned weekly grading CSV"
    )
    action.add_argument(
        "--complaint-records",
        type=Path,
        metavar="MANIFEST",
        help="reconcile the sampled cases with dated local register and owner-action exports",
    )
    action.add_argument(
        "--outstanding",
        type=Path,
        metavar="CSV",
        help="write a new blinded packet containing only assignments without a primary return",
    )
    action.add_argument(
        "--digest", action="store_true", help="print an aggregate email digest"
    )
    action.add_argument(
        "--email-to", help="send the digest via your configured TLS SMTP server"
    )
    action.add_argument(
        "--propose", type=Path, help="write a candidate SQL rule into a new directory"
    )
    week.add_argument("--email-from", help="sender address for the requested email")
    week.add_argument("--format", choices=("text", "json"), default="text")
    week.add_argument(
        "--max-reserved",
        type=int,
        help="maximum seats to explore when proposing a rule",
    )
    weekly_report.register_review_arguments(week, action)
    packs.register_arguments(commands)
    planning.register_arguments(commands)
    policy.register_arguments(commands)
    audit_trust.register_arguments(commands)
    seed.register_arguments(commands)
    screen.register_arguments(commands)
    desk = commands.add_parser(
        "desk", help="open the local browser intake for a QA lead"
    )
    desk.add_argument(
        "--out", type=Path, required=True, help="directory for private weekly runs"
    )
    desk.add_argument(
        "--container-port",
        type=int,
        metavar="PORT",
        help="listen on container interfaces; publish only to host 127.0.0.1 on the same port",
    )
    desk.add_argument(
        "--no-browser",
        action="store_true",
        help="print the loopback URL without launching a browser",
    )
    integrations.register_arguments(commands)
    demo = commands.add_parser("demo", help="run a synthetic audit")
    demo.add_argument(
        "--complaints", action="store_true", help="rehearse two weeks of complaints QA"
    )
    demo.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="with --complaints, print the summary or the full comparison JSON",
    )
    demo.add_argument(
        "--out",
        type=Path,
        help="new output directory for the demo",
    )
    compare_parser = commands.add_parser(
        "compare", help="compare two rules and write a report and packet"
    )
    compare_parser.add_argument("definition", type=Path)
    compare_parser.add_argument(
        "--export", type=Path, help="use a separate cutoff export"
    )
    compare_parser.add_argument(
        "--proposal",
        type=Path,
        help="evaluate a saved weekly proposal on a later batch",
    )
    compare_parser.add_argument("--out", type=Path, required=True)
    _result_flags(compare_parser)
    explain = commands.add_parser("explain", help="read a saved evidence snapshot")
    explain.add_argument("report", type=Path)
    _result_flags(explain)
    update = commands.add_parser(
        "refresh", help="merge partial grades or record adjudication"
    )
    update.add_argument("report", type=Path)
    source = update.add_mutually_exclusive_group(required=True)
    source.add_argument("--grades", type=Path, help="returned grading CSV")
    source.add_argument(
        "--adjudicate",
        nargs=3,
        metavar=("ROW", "GRADE", "REASON"),
        help="record an independently resolved grade",
    )
    update.add_argument("--review-id", help="stable identity for an independent return")
    update.add_argument(
        "--review-set", help="current review set shown in the disputed row"
    )
    update.add_argument("--minutes", help="optional time spent on the adjudication")
    update.add_argument(
        "--reviewer-unblinded",
        action="store_true",
        help="record that a reviewer knew which requests switched",
    )
    _result_flags(update)
    check = commands.add_parser(
        "preflight", help="check CSV intake before translating a rule"
    )
    check.add_argument("intake", type=Path)
    check.add_argument(
        "--batches", type=Path, help="separate cutoff manifest for a reusable mapping"
    )
    check.add_argument(
        "--out", type=Path, help="write a reconciled cutoff JSON export when ready"
    )
    _result_flags(check, gate=False)
    trial = commands.add_parser(
        "record-shadow", help="record a completed shadow trial from your queue system"
    )
    trial.add_argument("report", type=Path)
    trial.add_argument("observations", type=Path)
    _result_flags(trial, gate=False)
    pilot = commands.add_parser(
        "benchmark", help="assess a matched pilot record against a saved comparison"
    )
    pilot.add_argument("report", type=Path)
    pilot.add_argument("record", type=Path)
    _result_flags(pilot, gate=False)
    qualify = commands.add_parser("qualify", help="run the shipped qualification cases")
    qualify.add_argument("--case", help="run just this named case")
    return parser


def terminal_text(value: object) -> str:
    """Display one untrusted value without letting it control the terminal."""
    return "".join(
        char if char.isprintable() else repr(char)[1:-1] for char in str(value)
    )


def _print(result: dict, path: Path, *, output_format: str) -> None:
    if output_format == "json":
        print(canonical(result))
        return
    prefix = "Historical report · " if result["legacy_report"] else ""
    if (result.get("weekly_source") or {}).get("status") == "historical_snapshot":
        prefix = "Frozen weekly proposal · " + prefix
    print(
        f"{prefix}{result['recommendation']}  ·  net additional exceptions {signed_range(result['bounds'])}"
    )
    for name, shift in result["shifts"].items():
        if shift["status"] == "Replayable":
            print(
                f"  {terminal_text(name)}: {len(shift['gain'])} gain review, {len(shift['loss'])} lose it; "
                f"net {signed_range(shift['bounds'])}"
            )
    for reason in result["vetoes"] + result["open_items"]:
        print(f"  {terminal_text(reason)}")
    print(f"  {stopping_text(result)}")
    print(f"  {terminal_text(path)}")


def _demo(out: Path | None) -> int:
    if out is None:
        runs = Path("runs")
        runs.mkdir(exist_ok=True)
        out = Path(tempfile.mkdtemp(prefix="demo-", dir=runs)) / "tuesday"
    hold = recommendation(compare(EXAMPLE / "hold.toml"))
    tuesday = compare(EXAMPLE / "comparison.toml")
    middle = recommendation(tuesday)
    shadow = recommendation(compare(EXAMPLE / "shadow.toml"))
    if [item["recommendation"] for item in (hold, middle, shadow)] != [
        "Hold",
        "Inconclusive",
        SHADOW,
    ]:
        raise InputError(
            "the bundled scenarios did not reproduce their expected recommendations"
        )
    report = publish(out, tuesday)
    t = middle["shifts"]["tuesday-am"]
    print(f"HOLD            Candidate A. {terminal_text(hold['vetoes'][0])}")
    print(
        f"INCONCLUSIVE    Candidate B. {len(t['gain'])} gain review, {len(t['loss'])} lose it."
    )
    print(
        f"                Net exceptions sent to review: {signed_range(middle['bounds'])}."
    )
    print(f"                {stopping_text(middle)}")
    print(
        f"                {middle['unknown_count']} open grades plus {middle['pending_cross_checks']} cross-checks."
    )
    print(
        "TRY IN SHADOW   Candidate C. Combined hurdle and safeguards cleared across held-out shifts."
    )
    print(f"Lead report     {terminal_text(report)}")
    print(f"Reviewer CSV    {terminal_text(out / 'grading.csv')}")
    return 0


def preflight(args: argparse.Namespace) -> int:
    result, export = intake.inspect(args.intake, args.batches)
    if result["ready"] and args.out is not None:
        intake.write_export(args.out, export)
    if args.format == "json":
        print(canonical(result))
    else:
        for name, shift in result["shifts"].items():
            print(f"{terminal_text(name)} · {shift['status']}")
            if shift["status"] == "Not ready":
                print(f"  {terminal_text(shift['reason'])}\n  {shift['capture']}")
            else:
                print(f"  {shift['eligible']} eligible · {shift['selected']} selected")
                if shift.get("note"):
                    print(f"  {shift['note']}")
        if args.out is not None and result["ready"]:
            print(f"Export {terminal_text(args.out)}")
    return int(not result["ready"])


def pilot_benchmark(args: argparse.Namespace) -> int:
    result = benchmark.assess(load_report(args.report, recover=True), args.record)
    if args.format == "json":
        print(canonical(result))
        return 0
    print(
        f"{result['evidence'].capitalize()} pilot · {result['decision_date']} · {terminal_text(result['comparator'])}"
    )
    if result["timing_comparable"]:
        print(
            f"Recurring minutes saved: {result['recurring_minutes_saved']:+d} "
            f"(material threshold {result['material_minutes_saved']})"
        )
    else:
        print(
            "Timing remains unqualified. Check matched conditions, complete replay and cross-checks, and the lead’s evidence judgment."
        )
    if result["changes_to_recover_setup"] is not None:
        print(f"Policy changes to recover setup: {result['changes_to_recover_setup']}")
    if result["policy_changes_last_year"] is not None:
        print(
            f"Policy changes recorded in the last 12 months: {result['policy_changes_last_year']}"
        )
    if result["serious_misses_confirmed_by_lead"]:
        print(
            "Confirmed serious misses overlooked by the comparator: "
            + ", ".join(
                terminal_text(case)
                for case in result["serious_misses_confirmed_by_lead"]
            )
        )
    return 0


def weekly_audit(args: argparse.Namespace) -> int:
    if args.delivery_date is not None and args.out is None:
        raise InputError("--delivery-date applies only to a new weekly run with --out")
    if (args.email_to is None) != (args.email_from is None):
        raise InputError("email delivery requires both --email-from and --email-to")
    if args.max_reserved is not None and not args.propose:
        raise InputError("--max-reserved applies only to --propose")
    path = args.source
    state = weekly_report.review_action(args)
    if args.out:
        path = weekly_report.start(path, args.out, delivery_date=args.delivery_date)
    elif args.outstanding is not None:
        export = weekly_report.export_outstanding(path, args.outstanding)
        print(
            canonical(export)
            if args.format == "json"
            else f"{export['primary_outstanding']} assignments await a first grade. Packet: {terminal_text(args.outstanding)}"
        )
        return 0
    elif args.grades:
        state = weekly_report.refresh(path, args.grades, reviewer=args.reviewer)
    elif args.complaint_records:
        state = weekly_report.attach_complaints(path, args.complaint_records)
    elif args.propose:
        result = propose(
            path,
            args.propose,
            max_reserved=1 if args.max_reserved is None else args.max_reserved,
        )
        print(
            canonical(result)
            if args.format == "json"
            else f"Candidate rule: {terminal_text(args.propose / 'candidate.sql')}"
        )
        return 0
    elif args.email_to:
        from .weekly_mail import send_digest

        send_digest(path, args.email_to, args.email_from)
        print("Weekly aggregate digest sent.")
        return 0
    elif args.digest:
        print(weekly_report.digest(path, allow_legacy=args.legacy))
        return 0
    if state is None:
        state = weekly_report.read_state(path, allow_legacy=args.legacy)
    result = weekly.summarize(state)
    if "complaint_records" in state:
        from .complaints import reconcile

        result["complaints"] = reconcile(state)
    result["review"] = weekly_report.review_status(state)
    result["workload"] = weekly_review.workload(state)
    result["verification"] = (
        "team_hmac_verified" if "draw_commitment" in state else "historical_unsigned"
    )
    if args.format == "json":
        print(canonical(result))
    else:
        print(weekly_report.digest(path, allow_legacy=args.legacy))
        print(f"Report: {terminal_text(path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if getattr(args, "require_weekly_source", False) and not args.require_shadow:
            raise InputError("--require-weekly-source requires --require-shadow")
        if args.command == "desk":
            from .desk import serve

            return serve(
                args.out,
                open_browser=not args.no_browser,
                container_port=args.container_port,
            )
        if args.command == "weekly":
            return weekly_audit(args)
        if args.command == "integrations":
            return integrations.execute(args)
        if args.command == "seed":
            return seed.execute(args)
        if args.command == "screen":
            return screen.execute(args)
        if args.command == "pack":
            return packs.execute(args)
        if args.command == "plan-week":
            return planning.execute(args)
        if args.command == "policy-review":
            return policy.execute(args)
        if args.command == "trust":
            return audit_trust.execute(args)
        if args.command == "demo":
            if args.complaints:
                if BUNDLED.is_dir():
                    from ._bundled.examples.complaints_pilot.__main__ import run
                else:
                    from examples.complaints_pilot.__main__ import run

                out = args.out or Path("runs/complaints-pilot")
                with audit_trust.rehearse() as store:
                    result = run(out)
                if args.format == "json":
                    print(canonical(result))
                    return 0
                print("Two-week complaints rehearsal (synthetic)")
                for week in result["weeks"]:
                    print(f"Week of {week['week']}")
                    for method, label in (
                        ("native", "Saved SQL search"),
                        ("telryn", "Telryn"),
                    ):
                        totals = week["methods"][method]
                        print(
                            f"  {label}: {len(totals['owner_accepted_new'])} owner-accepted episodes, "
                            f"{totals['total_staff_minutes']} staff minutes"
                        )
                    print(terminal_text(week["report"]))
                overlap = result["two_week_owner_accepted"]
                print(
                    f"Two-week overlap: {len(overlap['both'])} shared, "
                    f"{len(overlap['native_only'])} SQL only, {len(overlap['telryn_only'])} Telryn only."
                )
                print(
                    f"Staged week-two operator help: {result['week_two_help_minutes']} minutes."
                )
                print(
                    "Selections, time ledger and source references:",
                    terminal_text(out / "comparison.json"),
                )
                first = shlex.quote(str(Path(result["weeks"][0]["report"]).absolute()))
                print("Verify the first week's report with the rehearsal trust store:")
                print(
                    terminal_text(
                        f"TELRYN_TRUST_DIR={shlex.quote(str(store))} telryn weekly {first} --format json"
                    )
                )
                return 0
            if args.format == "json":
                raise InputError("demo --format json requires --complaints")
            return _demo(args.out)
        if args.command == "preflight":
            return preflight(args)
        if args.command == "benchmark":
            return pilot_benchmark(args)
        if args.command == "record-shadow":
            result = recommendation(record_shadow(args.report, args.observations))
            if args.format == "json":
                print(canonical(result))
            else:
                print(
                    f"Recorded shadow trial. Open {terminal_text(args.report)} for observed workload, case mix, and the lead’s decision."
                )
            return 0
        if args.command == "qualify":
            runner = FIXTURE_ROOT / "tests" / "qualification.py"
            command = [sys.executable, "-I", str(runner)]
            if args.case is not None:
                command.append(args.case)
            return subprocess.run(command).returncode
        if args.command == "compare":
            if args.require_weekly_source and args.proposal is None:
                raise InputError("this gate requires a signed weekly proposal")
            state = compare(args.definition, args.export, proposal_path=args.proposal)
            report = publish(args.out, state)
        elif args.command == "explain":
            report = args.report
            state = load_report(report, recover=args.require_shadow)
        else:
            report = args.report
            if args.require_weekly_source and "weekly_proposal" not in load_report(
                report, recover=True
            ):
                raise InputError(
                    "this gate requires a comparison derived from a signed weekly proposal"
                )
            if args.minutes is not None and args.adjudicate is None:
                raise InputError(
                    "--minutes applies to an adjudication; use minutes_spent in a grade return"
                )
            if args.review_id is not None and args.grades is None:
                raise InputError("--review-id applies to --grades")
            if args.review_set is not None and args.adjudicate is None:
                raise InputError("--review-set applies to --adjudicate")
            state = refresh(
                report,
                args.grades,
                args.adjudicate,
                parse_minutes(args.minutes or ""),
                reviewer_unblinded=args.reviewer_unblinded,
                review_id=args.review_id,
                expected_review_set=args.review_set,
            )
        if args.require_weekly_source and "weekly_proposal" not in state:
            raise InputError(
                "this gate requires a comparison derived from a signed weekly proposal"
            )
        result = recommendation(state, require_current=args.require_shadow)
        _print(result, report, output_format=args.format)
        return int(args.require_shadow and result["recommendation"] != SHADOW)
    except (OSError, ValueError, csv.Error, OverflowError) as exc:
        if getattr(args, "format", None) == "json":
            print(
                json.dumps(
                    {"schema": 1, "status": "execution_error", "message": str(exc)}
                )
            )
        else:
            print(f"telryn: {terminal_text(exc)}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("telryn: interrupted", file=sys.stderr)
        return 130

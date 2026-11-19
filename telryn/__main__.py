"""Command-line entry point for Telryn."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

from . import __version__, benchmark, intake
from .contracts import InputError, canonical
from .decision import SHADOW
from .grades import parse_minutes
from .report import load_report, recommendation, signed_range, stopping_text
from .session import compare, publish, record_shadow, refresh

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "refunds"


def _result_flags(parser: argparse.ArgumentParser, *, gate: bool = True) -> None:
    parser.add_argument("--format", choices=("text", "json"), default="text")
    if gate:
        parser.add_argument(
            "--require-shadow",
            action="store_true",
            help="exit 1 unless the recommendation is Try in shadow",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m telryn",
        description="Replay a change to a scheduled human-review queue.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run the three synthetic refund scenarios")
    demo.add_argument(
        "--out",
        type=Path,
        help="new output directory for the Tuesday report and packet",
    )
    compare_parser = commands.add_parser(
        "compare", help="compare two rules and write a report and packet"
    )
    compare_parser.add_argument("definition", type=Path)
    compare_parser.add_argument(
        "--export", type=Path, help="use a separate cutoff export"
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
    result = benchmark.assess(load_report(args.report), args.record)
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "demo":
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
            from tests.qualification import qualify

            return qualify(args.case)
        if args.command == "compare":
            state = compare(args.definition, args.export)
            report = publish(args.out, state)
        elif args.command == "explain":
            report = args.report
            state = load_report(report)
        else:
            report = args.report
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
        result = recommendation(state)
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


if __name__ == "__main__":
    raise SystemExit(main())

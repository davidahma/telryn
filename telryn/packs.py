"""Versioned policy clocks and reviewer rubrics, loaded entirely from local data."""

import json
import re
from pathlib import Path

from . import control, policy
from .contracts import InputError, canonical, fingerprint, sequence, versioned

BUILTINS = Path(__file__).with_name("policy_packs")
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,63}")
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}")


def reference(pack):
    clocks = pack["clock_policy"]
    return f"{clocks['id']}@{clocks['version']}"


def validate(pack):
    versioned(pack, "policy pack")
    policy.fields(
        pack,
        (
            "schema",
            "applicability",
            "sources",
            "rubric_question",
            "categories",
            "clock_policy",
        ),
        "policy pack",
    )
    policy.label(pack["applicability"], "applicability")
    policy.label(pack["rubric_question"], "rubric question")
    sources = sequence(pack["sources"], "policy sources")
    if not 1 <= len(sources) <= 32:
        raise InputError("supply between 1 and 32 policy source references")
    for source in sources:
        policy.label(source, "policy source")
    control.categories(pack["categories"])
    clocks = policy.validate(pack["clock_policy"])
    if not IDENTIFIER.fullmatch(clocks["id"]) or not VERSION.fullmatch(
        clocks["version"]
    ):
        raise InputError("use a lowercase pack ID and a major.minor.patch version")
    return pack


def load(spec, folder):
    if spec.endswith(".json"):
        return validate_local(policy.read_input(folder / spec, "policy pack"))
    name, sep, version = spec.partition("@")
    if not sep or not IDENTIFIER.fullmatch(name) or not VERSION.fullmatch(version):
        raise InputError("select an exact pack ID@version or a local .json file")
    path = BUILTINS / f"{spec}.json"
    if not path.is_file():
        raise InputError(f"unknown policy pack {spec}; use pack list")
    pack = validate(policy.read_input(path, "policy pack"))
    if reference(pack) != spec:
        raise InputError("pack identity differs from the selected built-in version")
    return pack


def validate_local(data):
    pack = validate(data)
    ref = reference(pack)
    if (BUILTINS / f"{ref}.json").is_file() and pack != load(ref, BUILTINS):
        raise InputError(
            "modified pack reuses a bundled ID and version; assign the firm version its own identity"
        )
    return pack


def apply(plan, profile, pack):
    if profile is None:
        raise InputError(
            "a policy pack needs the team's control profile and channel inventory"
        )
    expected = {"rubric": reference(pack), "rubric_question": pack["rubric_question"]}
    for key, value in expected.items():
        if key in plan and plan[key] != value:
            raise InputError(f"{key} differs from the selected policy pack")
        plan[key] = value
    if "categories" in profile and profile["categories"] != pack["categories"]:
        raise InputError("control taxonomy differs from the selected policy pack")
    profile["categories"] = pack["categories"]


def receipt(pack):
    return {
        "reference": reference(pack),
        "sha256": fingerprint(pack),
        "sources": pack["sources"],
        "applicability": pack["applicability"],
    }


def review_rubric(state, candidate):
    """Mark issued classification work separately from changes to dated clocks."""
    old = state.get("policy_pack")
    if not old or old["clock_policy"]["id"] != candidate["clock_policy"]["id"]:
        return False
    digest = fingerprint(candidate)
    proposed_version = candidate["clock_policy"]["version"]
    if old["clock_policy"]["version"] == proposed_version:
        if fingerprint(old) != digest:
            raise InputError("different pack contents reuse the issued version")
        return False
    notices = state.get("pack_reviews", [])
    for prior in notices:
        if prior["review_version"] == proposed_version:
            if prior["pack_digest"] != digest:
                raise InputError("different pack contents reuse a review version")
            return False
    changed = [
        name
        for name in ("rubric_question", "categories", "applicability", "sources")
        if old[name] != candidate[name]
    ]
    if not changed:
        return False
    state.setdefault("pack_reviews", []).append(
        {
            "policy": old["clock_policy"]["id"],
            "issued_version": old["clock_policy"]["version"],
            "review_version": proposed_version,
            "week": state["week"],
            "pack_digest": digest,
            "policy_digest": fingerprint(candidate["clock_policy"]),
            "changed_pack_fields": changed,
        }
    )
    return True


def register_arguments(commands):
    cmd = commands.add_parser(
        "pack", help="inspect versioned local policy and rubric data"
    )
    subs = cmd.add_subparsers(dest="pack_action", required=True)
    subs.add_parser("list", help="list bundled pack versions")
    show = subs.add_parser(
        "show", help="validate and print a complete pack as editable JSON"
    )
    show.add_argument("spec", help="exact ID@version or local .json path")


def execute(args):
    if args.pack_action == "list":
        items = [
            receipt(load(p.stem, BUILTINS)) for p in sorted(BUILTINS.glob("*.json"))
        ]
        print(canonical(items))
    else:
        print(json.dumps(load(args.spec, Path.cwd()), indent=2, ensure_ascii=False))
    return 0

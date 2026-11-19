"""Weekly draw commitments, document authentication, revisions and independent correction."""

import csv
import hashlib
import json
import os
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from telryn import audit_trust
from telryn.contracts import InputError, canonical, fingerprint
from telryn.integrations_coverage import membership
from telryn.weekly import summarize
from telryn.weekly_report import (
    STATE_OPEN,
    adjudicate,
    digest,
    read_state,
    refresh,
    review_status,
    second_review,
    start,
    verified_html,
    verified_packet,
)


class TeamAudit(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.trust = self.root / "vault"
        env = patch.dict(os.environ, {"TELRYN_TRUST_DIR": str(self.trust)})
        env.start()
        self.addCleanup(env.stop)
        self.plan = self.root / "week.toml"
        self.plan.write_text(
            'schema=1\nweek="2026-11-02"\npopulation="cases.csv"\nreviewed="reviewed.csv"\nrubric="esc-v1"\nrubric_question="Was escalation required?"\nscreen_version="flags-v1"\nflagged_sample=4\nunflagged_sample=4\n'
        )
        self.population = self.root / "cases.csv"
        self.population.write_text(
            "case_id,revision,evidence_ref,screen_flag\n"
            + "".join(f"C{i},r1,R{i},{i % 2}\n" for i in range(32))
        )
        (self.root / "reviewed.csv").write_text("case_id\nC1\nC2\n")
        self.report = start(self.plan, self.root / "audit")
        with self.report.with_name("grades.csv").open(
            encoding="utf-8-sig", newline=""
        ) as src:
            self.packet = list(csv.DictReader(src))
        self.case = self.packet[0]["case_id"]

    def returned(
        self, serious="no", minutes=2, disposition=None, idx=0, path="return.csv"
    ):
        row = self.packet[idx].copy()
        row.update(
            serious=serious,
            minutes=str(minutes),
            disposition=disposition or ("none" if serious == "no" else "new"),
            owner="Ops" if serious == "yes" else "",
            action="Review escalation" if serious == "yes" else "",
        )
        out = self.root / path
        with out.open("w", newline="", encoding="utf-8") as fs:
            writer = csv.DictWriter(fs, fieldnames=row)
            writer.writeheader()
            writer.writerow(row)
        return out

    def test_completion_cutoff_follows_each_assignment_and_binds_the_screen(self):
        with self.population.open() as source:
            cases = list(csv.DictReader(source))
        cutoffs = {r["case_id"]: "2026-11-03T12:00:00Z" for r in cases}
        coverage = dict(
            schema=1,
            basis="event-ledger",
            scope="Fixture queue",
            since="2026-11-02",
            until="2026-11-09",
            scanned=32,
            eligible=32,
            open=0,
            unknown=0,
            outside=0,
            still_open=0,
            dimensions=[],
            completed_at=cutoffs,
            membership=membership(cases, cutoffs),
        )
        self.population.with_suffix(".csv.coverage.json").write_text(
            canonical(coverage)
        )
        version = "policy-events:" + "a" * 64
        self.plan.write_text(
            self.plan.read_text().replace("flags-v1", version)
            + 'screen_receipt="screen.json"\n'
        )
        receipt = dict(
            schema=1,
            screen_version=version,
            population_sha256=hashlib.sha256(self.population.read_bytes()).hexdigest(),
        )
        source = self.root / "screen.json"
        source.write_text(canonical(receipt))
        out = self.root / "completion"
        with self.assertRaisesRegex(InputError, "completion cutoffs"):
            start(self.plan, out)
        source.write_text(
            canonical({**receipt, "coverage_sha256": fingerprint(coverage)})
        )
        report = start(self.plan, out)
        state = read_state(report)
        self.assertNotIn("completed_at", state["coverage"])
        self.assertEqual(state["coverage_sha256"], fingerprint(coverage))
        with report.with_name("grades.csv").open(encoding="utf-8-sig") as packet:
            rows = list(csv.DictReader(packet))
        self.assertEqual(len(rows), 8)
        for row in rows:
            self.assertEqual(row["completed_at"], cutoffs[row["case_id"]])
        row = {**rows[0], "serious": "no", "disposition": "none", "minutes": "3"}
        answer = self.root / "cutoff-grade.csv"
        for cutoff in ("2026-11-10T12:00:00Z", rows[0]["completed_at"]):
            with answer.open("w", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=row)
                writer.writeheader()
                writer.writerow({**row, "completed_at": cutoff})
            if cutoff == rows[0]["completed_at"]:
                self.assertEqual(len(refresh(report, answer)["grades"]), 1)
            else:
                with self.assertRaisesRegex(InputError, "frozen"):
                    refresh(report, answer)

    def test_committed_sample_independent_of_directory_order_and_cosmetic_metadata(
        self,
    ):
        first = read_state(self.report)
        header, *rows = self.population.read_text().splitlines()
        self.population.write_text("\n".join([header, *reversed(rows)]) + "\n")
        (self.root / "reviewed.csv").write_text("case_id\nC2\nC1\n")
        another = read_state(start(self.plan, self.root / "second"))
        self.assertEqual(first["draw_commitment"], another["draw_commitment"])
        self.assertEqual(first["cases"], another["cases"])
        self.assertNotEqual(first["publication_id"], another["publication_id"])
        self.plan.write_text(self.plan.read_text().replace("esc-v1", "esc-v2"))
        changed = read_state(start(self.plan, self.root / "third"))
        self.assertEqual(first["seed"], changed["seed"])
        self.assertEqual(first["cases"], changed["cases"])
        self.assertNotEqual(
            first["draw_commitment"]["draw_id"], changed["draw_commitment"]["draw_id"]
        )
        with audit_trust.registry() as (db, key):
            self.assertEqual(db.execute("SELECT count(*) FROM draws").fetchone()[0], 2)
            self.assertEqual(
                audit_trust.key_id(key), first["draw_commitment"]["key_id"]
            )
        self.assertEqual((self.trust / "audit.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.trust.stat().st_mode & 0o777, 0o700)

    def test_authenticates_visible_html_state_and_regenerated_packet(self):
        original = self.report.read_bytes()
        packet = verified_packet(self.report)
        self.report.with_name("grades.csv").write_text("invalid sidecar")
        self.assertEqual(verified_packet(self.report), packet)
        altered = original.replace(b"Confirmed cases", b"Alternate cases", 1)
        self.assertNotEqual(original, altered)
        self.report.write_bytes(altered)
        for reader in (read_state, digest, verified_html, verified_packet):
            with (
                self.subTest(reader=reader),
                self.assertRaisesRegex(InputError, "signature"),
            ):
                reader(self.report)
        state = json.loads(
            original.split(STATE_OPEN.encode())[1].split(b"</script>")[0]
        )["state"]
        state["rubric"] = "changed"
        forged = canonical(
            {"schema": 1, "checksum": fingerprint(state), "state": state}
        )
        self.report.write_text(STATE_OPEN + forged + "</script>")
        with self.assertRaisesRegex(InputError, "unsigned"):
            read_state(self.report)
        with self.assertRaisesRegex(InputError, "historical"):
            read_state(self.report, allow_legacy=True)
        self.report.write_bytes(original)
        with self.assertRaisesRegex(
            InputError, "team (audit )?key|signing key|registry|signature"
        ):
            with patch.dict(
                os.environ, {"TELRYN_TRUST_DIR": str(self.root / "separate")}
            ):
                read_state(self.report)
        self.assertEqual(verified_html(self.report), original)

    def test_partial_trust_store_cannot_silently_reset(self):
        (self.trust / "ledger.sqlite3").unlink()
        with self.assertRaisesRegex(InputError, "incomplete"):
            start(self.plan, self.root / "reset")
        with self.assertRaisesRegex(InputError, "incomplete"):
            read_state(self.report)

    def test_historical_reader_is_explicit_and_read_only(self):
        archive = Path(__file__).parent / "fixtures" / "weekly-v1.html"
        with self.assertRaisesRegex(InputError, "unsigned"):
            read_state(archive)
        self.assertIn("Historical unsigned", digest(archive, allow_legacy=True))
        with self.assertRaisesRegex(InputError, "signature"):
            refresh(archive, self.returned())

    def test_copy_prevents_lost_updates_and_partial_publication(self):
        replica = self.root / "copy.html"
        shutil.copyfile(self.report, replica)
        old = self.report.read_bytes()
        refresh(replica, self.returned(), reviewer="A")
        with self.assertRaisesRegex(InputError, "stale"):
            refresh(self.report, self.returned(idx=1), reviewer="B")
        self.assertEqual(self.report.read_bytes(), old)
        self.assertEqual(len(read_state(replica)["grades"]), 1)
        clean = replica.read_bytes()
        with (
            patch.object(Path, "replace", side_effect=OSError("storage unavailable")),
            self.assertRaises(OSError),
        ):
            refresh(replica, self.returned(idx=1), reviewer="B")
        self.assertEqual(replica.read_bytes(), clean)
        # The committed head rejects the prior file on any other path, even
        # before a reader recovers the original destination.
        older = self.root / "old-replica.html"
        older.write_bytes(clean)
        with self.assertRaisesRegex(InputError, "stale"):
            read_state(older)
        self.assertNotEqual(verified_html(replica), clean)
        self.assertEqual(len(read_state(replica)["grades"]), 2)
        with self.assertRaisesRegex(InputError, "stale"):
            read_state(self.report)

    def test_review_dispute_adjudication_agreement_and_idempotence(self):
        first = self.returned(path="first.csv")
        second = self.returned("yes", 3, path="second.csv")
        refresh(self.report, first, reviewer="Sam")
        with self.assertRaisesRegex(InputError, "independent"):
            second_review(self.report, second, "sam")
        state = second_review(self.report, second, "Lee")
        current = review_status(state)
        self.assertEqual(
            (current["compared"], current["agreed"], current["open"]), (1, 0, 1)
        )
        self.assertEqual(summarize(state)["confirmed_serious"], 0)
        self.assertIsNone(state["grades"][self.case]["serious"])
        issue = current["open_disputes"][0]["dispute_id"]
        for actor in ("sam", "LEE"):
            with self.assertRaisesRegex(InputError, "independent"):
                adjudicate(
                    self.report, self.case, issue, actor, "second", "Policy citation", 4
                )
        with self.assertRaisesRegex(InputError, "matching"):
            adjudicate(
                self.report, self.case, "old", "Kim", "second", "Policy citation", 4
            )
        fixed = adjudicate(
            self.report, self.case, issue, "Kim", "second", "Policy citation", 4
        )
        self.assertTrue(fixed["grades"][self.case]["serious"])
        self.assertEqual(fixed["grades"][self.case]["minutes"], 9)
        self.assertEqual(review_status(fixed)["open"], 0)
        report = self.report.read_bytes()
        refresh(self.report, first, reviewer="Sam")
        second_review(self.report, second, "Lee")
        adjudicate(self.report, self.case, issue, "Kim", "second", "Policy citation", 4)
        self.assertEqual(verified_html(self.report), report)
        with self.assertRaisesRegex(InputError, "already adjudicated"):
            adjudicate(
                self.report, self.case, issue, "Kim", "primary", "Different decision", 9
            )
        # Retain evidence if a later reviewer challenges the adjudicated decision.
        last = second_review(self.report, first, "Taylor")
        followup = review_status(last)["open_disputes"][0]
        self.assertEqual(followup["primary_by"], "Kim")
        self.assertEqual(review_status(last)["compared"], 1)
        adjudicate(
            self.report,
            self.case,
            followup["dispute_id"],
            "Uma",
            "second",
            "Reviewed policy revision",
            1,
        )
        case_history = read_state(self.report)["case_reviews"][self.case]["checks"]
        self.assertEqual(
            [s["decision"]["reviewer"] for s in case_history], ["Kim", "Uma"]
        )
        refresh(self.report, self.returned(idx=1), reviewer="Sam")
        last = second_review(self.report, self.returned(idx=1), "Lee")
        self.assertEqual(review_status(last)["agreement"], 0.5)
        self.assertIn("1 of 2 definite pairs", digest(self.report))
        self.assertNotIn("Uma", digest(self.report))

    def test_different_copies_cannot_commit_competing_changes(self):
        replica = self.root / "copy.html"
        shutil.copyfile(self.report, replica)
        p1 = self.returned(path="a.csv")
        p2 = self.returned(idx=1, path="b.csv")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                pool.submit(refresh, path, packet)
                for path, packet in ((self.report, p1), (replica, p2))
            ]
        self.assertEqual(sum(x.exception() is None for x in results), 1)
        self.assertIn(
            "stale", str(next(x.exception() for x in results if x.exception()))
        )

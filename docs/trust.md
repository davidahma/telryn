# Commit draws and verify weekly evidence

The QA owner initializes a private trust store before the first weekly draw. Its key determines the lottery and signs reports. Its registry records the sampling designs the team committed and the latest revision of each signed report.

```sh
export TELRYN_TRUST_DIR=/path/to/private/team-telryn
python3 -m telryn trust init
python3 -m telryn trust status
python3 -m telryn trust draws
```

Record the printed key ID in a separate, access-controlled team record before viewing assignments. Configure the same store for the CLI, browser desk and scheduled jobs. A first audit initializes the store automatically if one has not been provisioned. The default path is `~/.local/state/telryn/audit`. Only the owning OS account can read its key and database. Run the desk as the audit service account and give graders the blinded CSV and authorized evidence references. A human-readable reviewer name records the operator's attribution. The operator authenticates the reviewer in the team's normal QA system.

## Repeat a week

Telryn canonicalizes the eligible and ordinarily reviewed IDs and combines that frame and the week with the pre-existing secret key. The sampling engine uses the derived seed to rank case IDs privately before taking each group's allocation. Identical membership, group assignment, and allocation produce the same sampled conversations regardless of output path, CSV row order, or the number of times the operator runs the command. Changing the rubric or relabeling an otherwise identical export leaves the lottery order fixed.

Telryn commits each distinct design to the registry before selecting the sample. Review `trust draws` when a frame or policy changes. Keep the authoritative exports and their timestamps to explain exclusions or amendments. Changing the membership, groups or sample budget can change the cases selected. The registry belongs to whoever holds the signing key. A team that needs independent oversight should record the original key ID and periodically retain a read-only copy of the draw register outside the operator's account.

Starting the same design in another output directory creates a separately identified publication with the same sampled cases and a fresh blank grading packet. Give the team one authoritative publication for follow-up. Opening another publication cannot alter the committed sample or replace grades in the original. Copying an existing report retains its publication ID. Once either copy advances, the trust store rejects the older copy. Use the latest file for the next return. If a crash occurs between installing a signed file and committing its revision, the next read can reconcile that signed successor against its recorded parent.

## Verify, retain and share

A version-2 weekly envelope authenticates all HTML bytes before the signature trailer, including the visible tables, evidence, publication ID, prior signature and revision number. Recomputing the embedded state or editing the displayed numbers invalidates the HMAC. The desk verifies the same bytes it serves and regenerates the download packet from authenticated evidence. To verify a file through the CLI, run:

```sh
python3 -m telryn weekly runs/current/report.html --format json
```

A successful read checks both the team HMAC and the local registry's latest known revision. Opening the HTML directly uses the browser's ordinary file viewer. It does not verify either. An external recipient without the team secret can use the team's independently distributed authenticated file digest or signed release, if available. Giving someone an HMAC key gives them signing authority. Back up the entire owner-only store, including the key and registry, to an encrypted location controlled by the team. Stop writers while making a filesystem copy. On another trusted machine, restore the directory with its original permissions and point `TELRYN_TRUST_DIR` to it. Separate restored stores can know different latest revisions. Use one writer service for the team. Configure a new trust directory and record a new key ID when rotating a key. Keep the original store for reading old reports.

Version-1 weekly reports contain a checksum for accidental corruption. Read them with an explicit historical flag. They cannot be updated, used to propose a rule, or sent as a verified digest through the current CLI.

```sh
python3 -m telryn weekly historical/report.html --legacy
```

The signed file retains reviewer identities, reasons, findings and case references. The registry stores design fingerprints, dates and report-head signatures. Include them in the team's retention policy.

## Correct a grade

Keep the original blinded packet. Ask another reviewer to independently grade the disputed cases against the same evidence revision. Each reviewer returns their own copy. Specify identities on the first and second returns. The default first-review identity is the operating-system account if no name is supplied.

```sh
python3 -m telryn weekly runs/current/report.html --grades first.csv --reviewer reviewer-a
python3 -m telryn weekly runs/current/report.html --second-review independent.csv --reviewer reviewer-b
python3 -m telryn weekly runs/current/report.html --format json
```

The report lists the original and second answers. A disagreement about severity or prior handling opens a dispute and makes the effective grade insufficient until adjudication. Partial-evidence ranges incorporate that uncertainty. Agreement on severity is the number of first independent pairs with the same definite yes/no answer divided by all first pairs with two definite answers. The report displays the denominator. This describes the double-graded cases only. Repeated challenges to a case do not inflate the agreement rate.

An independent adjudicator selects the supported answer and records the reason and additional minutes. Copy the case and dispute ID from the report. For example:

```sh
python3 -m telryn weekly runs/current/report.html \
  --adjudicate CASE-123 --dispute "$DISPUTE_ID" --reviewer reviewer-c \
  --accept second --reason "The frozen handoff record confirms referral." --review-minutes 4
```

Telryn preserves both answers, adjudication history and cumulative work. Retrying an accepted return or decision leaves the report revision intact. A further correction requires a new independent reviewer and, if the two answers conflict, another explicit adjudication. The next digest contains aggregate counts and reviewer agreement. Names and reasons stay in the private report.

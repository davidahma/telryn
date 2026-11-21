# Commit draws and verify weekly evidence

The QA owner initializes a private trust store before the first weekly draw. Its key determines the lottery and signs reports. Its registry records the sampling designs the team committed and the latest revision of each signed report.

```sh
export TELRYN_TRUST_DIR=/path/to/private/team-telryn
telryn trust init
telryn trust status
telryn trust draws
```

Record the printed key ID in a separate, access-controlled team record before viewing assignments. Configure the same store for the CLI, browser desk and scheduled jobs. A first audit initializes the store automatically if one has not been provisioned. The default path is `~/.local/state/telryn/audit`. Only the owning OS account can read its key and database. Run the desk as the audit service account and give graders the blinded CSV and authorized evidence references. A human-readable reviewer name records the operator's attribution. The operator authenticates the reviewer in the team's normal QA system.

The installed `telryn demo --complaints` keeps its synthetic draws and signing key in `~/.local/state/telryn/rehearsal`. Set `TELRYN_DEMO_TRUST_DIR` to choose another private rehearsal store. The command prints a verification command with `TELRYN_TRUST_DIR` scoped to the rehearsal read. To verify a rehearsal issued with v0.9.2 or the source example module, use the trust store active when you issued it.

## Repeat a week

Telryn canonicalizes the eligible and ordinarily reviewed IDs and combines that frame and the week with the pre-existing secret key. The sampling engine uses the derived seed to rank case IDs privately before taking each group's allocation. Identical membership, group assignment, and allocation produce the same sampled conversations regardless of output path, CSV row order, or the number of times the operator runs the command. Changing the rubric or relabeling an otherwise identical export leaves the lottery order fixed.

Telryn commits each distinct design to the registry before selecting the sample. Review `trust draws` when a frame or policy changes. Keep the authoritative exports and their timestamps to explain exclusions or amendments. Changing the membership, groups or sample budget can change the cases selected. The registry belongs to whoever holds the signing key. A team that needs independent oversight should record the original key ID and periodically retain a read-only copy of the draw register outside the operator's account.

Starting the same design in another output directory creates a separately identified publication with the same sampled cases and a fresh blank grading packet. Give the team one authoritative publication for follow-up. Opening another publication cannot alter the committed sample or replace grades in the original. Copying an existing report retains its publication ID. Once either copy advances, the trust store rejects the older copy. Use the latest file for the next return. Telryn stages and syncs the new file, then commits its revision and a pending replacement record before installing it. From that commit onward, other copies of the old revision fail verification. Reading the original path finishes a pending replacement using its recorded digests.

Initial issuance records the draw and policy version, publishes the report and packet together, then registers the first report revision. A failed publication leaves the report count unchanged. If issuance stops after the files appear, verifying the signed report completes registration.

## Verify, retain and share

A version-2 weekly envelope authenticates all HTML bytes before the signature trailer, including the visible tables, evidence, publication ID, prior signature and revision number. Recomputing the embedded state or editing the displayed numbers invalidates the HMAC. The desk verifies the same bytes it serves and regenerates the download packet from authenticated evidence. To verify a file through the CLI, run:

```sh
telryn weekly runs/current/report.html --format json
```

A successful read checks both the team HMAC and the local registry's latest known revision. Opening the HTML directly uses the browser's ordinary file viewer. It does not verify either. An external recipient without the team secret can use the team's independently distributed authenticated file digest or signed release, if available. Giving someone an HMAC key gives them signing authority. Back up the entire owner-only store, including the key and registry, to an encrypted location controlled by the team. Stop writers while making a filesystem copy. On another trusted machine, restore the directory with its original permissions and point `TELRYN_TRUST_DIR` to it. Separate restored stores can know different latest revisions. Use one writer service for the team. Configure a new trust directory and record a new key ID when rotating a key. Keep the original store for reading old reports.

Version-1 weekly reports contain a checksum for accidental corruption. Read them with an explicit historical flag. They cannot be updated, used to propose a rule, or sent as a verified digest through the current CLI.

```sh
telryn weekly historical/report.html --legacy
```

The signed file retains reviewer identities, reasons, findings and case references. The registry stores design fingerprints, dates and report-head signatures. Include them in the team's retention policy.

## Correct a grade

Keep the original blinded packet. Ask another reviewer to independently grade the disputed cases against the same evidence revision. Each reviewer returns their own copy. Specify identities on the first and second returns. The default first-review identity is the operating-system account if no name is supplied.

```sh
telryn weekly runs/current/report.html --grades first.csv --reviewer reviewer-a
telryn weekly runs/current/report.html --second-review independent.csv --reviewer reviewer-b
telryn weekly runs/current/report.html --format json
```

The report lists the original and second answers. A disagreement about severity leaves severity unresolved until adjudication. If both reviewers grade a case serious and disagree only about prior handling, the case still counts as confirmed serious. Its contribution to newly discovered findings ranges from zero to one. The case stays in the adjudication queue. When you open an older signed report with the current CLI, the summary applies the same distinction to its recorded votes. The original HTML retains the numbers shown at publication.

Agreement on severity is the number of first independent pairs with the same definite yes/no answer divided by all first pairs with two definite answers. The report displays the denominator. This describes the double-graded cases only. Repeated challenges to a case do not inflate the agreement rate.

An independent adjudicator selects the supported answer and records the reason and additional minutes. Copy the case and dispute ID from the report. For example:

```sh
telryn weekly runs/current/report.html \
  --adjudicate CASE-123 --dispute "$DISPUTE_ID" --reviewer reviewer-c \
  --accept second --reason "The frozen handoff record confirms referral." --review-minutes 4
```

Telryn preserves both answers, adjudication history and cumulative work. Retrying an accepted return or decision leaves the report revision intact. A further correction requires a new independent reviewer and, if the two answers conflict, another explicit adjudication. The next digest contains aggregate counts and reviewer agreement. Names and reasons stay in the private report.

## Verify the path from a weekly grade to a shadow trial

Starting in v0.6, a comparison created with `--proposal` binds the weekly publication and revision into its identity. The team key authenticates its decision evidence, including later grades and shadow observations. The registry keeps the latest revision for that comparison. Each update carries the signature of its predecessor. If a process stops during publication, a verified read at the original path finishes the committed replacement. A stale copy cannot authorize another trial during recovery. Back up the report and the registry together.

Plain `explain` and directly opened HTML remain available when reviewing a historical outcome. To decide whether to start a new weekly-proposal trial, use `explain REPORT --require-shadow --require-weekly-source` with the matching trust store. The gate checks both the comparison and the originating weekly audit. A comparison copied before a later grade or observation can still be read as history. New shadow work, grade updates, and pilot benchmarks use current evidence. Keep the gate's decision revision with the observation. [CLI gate and migration](comparison.md#check-a-weekly-proposal-before-running-a-trial)

After an interrupted update, keep the original directory and its private `.telryn-pending-*` file together. Use `weekly REPORT --format json` for a weekly audit, or `explain REPORT --require-shadow --require-weekly-source` for a proposal comparison. The registry retains the original absolute path, temporary filename and digests. If the old file or the staged revision is missing or differs from the committed digests, recover the matching files from backup at that path. Direct HTML viewing and historical comparison reads show the bytes currently on disk.

Signed weekly reports with a complaint control profile or linked episodes and policy clocks require v0.8.0 or later for verification and updates. They use signed envelope version 3 so an older writer stops before changing the report. Telryn v0.8.0 continues to verify version 2 reports with the original team registry and key. Ordinary audits without the new evidence keep their version 2 envelope.

An audit that pins a policy pack or issues episode clocks with an explicit business-week calendar uses signed envelope version 4. Verify and update it with v0.9.0 or later. The team registry commits the pack identity and report signature before the initial grading packet becomes visible. Verifying a recovered initial report rebinds its signed pack versions when the team restores a registry backup containing its draw. A conflicting pack digest for an existing version fails verification. The stored pack includes the rubric and taxonomy used to grade the cases. A subsequent pack review flags affected reports and preserves their issued evidence. [Policy packs](policy-packs.md)

A complaint report that carries a cited passage from a sampled conversation uses signed envelope version 5. Use v0.9.4 or later to verify or update it. Existing reports keep their earlier envelope until a revision adds a feature that requires a newer one.

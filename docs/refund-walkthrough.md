# Walk through a refund rule change

Run these commands from a clean checkout or unpacked release with CPython
3.12 or newer installed. Each `--out` directory for a Telryn comparison must
be new.

## Analyst

```sh
python3 -m telryn compare examples/refunds/comparison.toml --out runs/tuesday
python3 -m telryn explain runs/tuesday/report.html
cp runs/tuesday/grading.csv runs/tuesday-return.csv
```

Open the lead report and confirm that R-118 gains review and R-073 loses it.
The report shows the 80 shared requests, the 40 requests that move, staffing, the +1
hurdle, and the cross-checks still outstanding. Keep it with the lead.
Share the copied packet and the rubric's allowed evidence sources with the
reviewer. The issued CSV has owner-only permissions. Keep the returned copy
in an access-controlled folder, including when a spreadsheet editor saves a
new file.

## Reviewer

The tutorial uses a local SQLite app to serve evidence. Seed it once:

```sh
python3 -m examples.refunds.app seed --db runs/refunds.sqlite
python3 -m examples.refunds.app inspect --db runs/refunds.sqlite order/R-118@2 refund/R-118@1
```

For R-118 the evidence shows two units delivered and three requested. Enter
`meets` for that row. Use `outside` when the evidence shows no quantity
discrepancy and `insufficient` when the exact permitted evidence is unavailable
or ambiguous. Leave unfinished rows blank. You can optionally log whole
minutes in `minutes_spent`.

For the worked result, inspect and answer the rows referencing R-091, R-101,
R-113, R-114, and R-118. Their answers in that order are `outside`, `meets`,
`meets`, `meets`, and `meets`. Row IDs and order are randomized for each
comparison. In a spreadsheet editor, import and export as UTF-8,
comma-separated CSV. Preserve the headers and all issued identifiers, evidence
references, and questions. Send the partially filled copy back.

## Lead

```sh
python3 -m telryn refresh runs/tuesday/report.html --grades runs/tuesday-return.csv
```

Tuesday now has a range of **+1 to +6** and recommends **Try in shadow**.
A definition covering Tuesday and Saturday is a separate frozen comparison.
Use `examples/refunds/weekend.toml` and a new output directory to see the
combined decision. A Tuesday packet cannot be imported into that comparison.
A missing required Saturday batch leaves the combined decision open except
when a verified operational veto already supports a hold.

## Compare with plain SQL

```sh
python3 -m examples.refunds.app export --db runs/refunds.sqlite --out runs/source-export
python3 -m examples.refunds.baseline --export runs/source-export/cutoff.json --grades runs/source-export/known-grades.json
```

The export reads one database snapshot. Repeating an unchanged export can
reuse its directory. Choose a new directory after the source changes.
The standalone baseline checks the same Tuesday selection and safeguards.
Pass the [worked ledger](../examples/refunds/review-ledger.json) to carry the
SQL comparison through the five returns, a later dispute, and adjudication:

```sh
python3 -m examples.refunds.baseline --export runs/source-export/cutoff.json --grades runs/source-export/known-grades.json --ledger examples/refunds/review-ledger.json
```

Both workflows can reach the same final decision. The [evaluation protocol](evaluation.md)
describes what to measure with an analyst and an operations lead before
drawing conclusions about time savings.

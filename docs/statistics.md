# Plan a weekly sample

Decide what precision would change a decision before a reviewer opens the first conversation. If a lead can afford 200 minutes at four minutes per case, that buys 50 grades. When serious failures are rare, the difference between zero and two findings can leave hundreds of cases in the weekly interval.

Run a preview with the same population, ordinary-review ledger, rubric and group definitions you will use for the draw. This reads the source files and reports an allocation without selecting cases or writing a draw commitment.

```sh
python3 -m telryn plan-week examples/fintech/week.toml \
  --minutes 24 --minutes-per-grade 4 --expected-rate 0.10 \
  --flagged-rate 0.25 --format json
```

Each allocation row gives the residual population, recommended number of grades and assumed prevalence. `expected_interval_width` is the anticipated width in conversations. `scenario_interval` shows the bounds if the sample returns the nearest whole number of positives to its expected count in each group. `zero_findings_upper` gives the upper limit if every assigned grade is negative.

For a residual population of 5,000 in one group, four minutes per review and an assumed 4% rate, the planner produces these results.

| Grading minutes | Grades | Anticipated interval width | 95% range at expected count | Upper limit with zero findings |
| ---: | ---: | ---: | ---: | ---: |
| 200 | 50 | 628 | 25–683 | 353 |
| 400 | 100 | 429 | 56–493 | 179 |
| 1,000 | 250 | 255 | 99–357 | 71 |
| 2,000 | 500 | 172 | 127–300 | 34 |

The expected width averages across the possible sample outcomes for the assumed prevalence. The scenario uses one outcome. Adding more sampled groups can widen individual confidence limits because the report covers all groups jointly.

## Use the recommended allocation

Put these settings at the **end** of the weekly TOML. The `[planning]` section takes precedence over the fixed flagged and unflagged targets when the audit starts. Rates are proportions between 0 and 1. With no rate override for a flag group, the planner uses `expected_rate`. Extra minutes after a full census appear as unused.

```toml
[planning]
minutes = 240
minutes_per_grade = 6
expected_rate = 0.05
flagged_rate = 0.20
unflagged_rate = 0.02
```

The planner gives each nonempty group one review first. It allocates each additional slot to the group that most reduces the anticipated variance of the total, using group size and assumed rate. A half-case adjustment keeps estimated rates of zero or one from removing a group's budget. The forecast then uses the stated rates directly. Assumptions about prevalence and average minutes can be wrong. The planner's `coverage_complete` field shows whether every group fits at least one review. Starting a budget-planned audit requires that coverage. Combine groups or increase the budget when coverage fails.

For the forecast, Telryn rounds the anticipated number of positives in each finite group to a whole count and calculates the hypergeometric distribution of sampled positives. It averages each possible confidence-interval width for short outcome ranges. For wider ranges, it approximates that average at 17 equally spaced probability quantiles. This approximation affects the preview only. The issued report inverts hypergeometric tails using the grades actually returned. For large sample sizes the tail comparison uses bounded integer arithmetic and rounds ambiguous comparisons outwards. [Hypergeometric distribution](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/Hypergeometric.html)

## Break out channel, agent type or product

Add `channel`, `agent_type` or `product` columns to the population CSV. Each value used for grouping must be nonempty printable text. Use stable category names so weekly reports can be interpreted together. At the top level of the weekly TOML, before any section header, specify the dimensions to use.

```toml
group_by = ["channel", "agent_type"]
```

With email and chat handled by both AI and human agents, the sample can have up to eight groups after splitting by the screen flag. Empty combinations need no sample. Keeping a category in the export without listing it in `group_by` leaves it out of the sampling design and report. Counts and percentages declared with `flagged_sample` or `unflagged_sample` remain totals for the corresponding side. Telryn distributes those slots among the selected groups instead of multiplying the budget. The budget planner lets you see when groups have too few reviews to estimate a useful range.

The report gives observed findings and sampling intervals per group. Its overall estimate sums `population × sampled positives / sampled cases` across groups. Screen metrics also use the actual group inclusion probabilities. If a group had zero probability of review, the report withholds the overall estimate. The unsampled group spans zero to its full population. The other groups retain their findings and intervals.

## Read a partly graded audit

Suppose one group has 500 cases and 50 are sampled. Two reviewers identify positives, 45 cases receive negative grades and three lack a determinate answer. The estimated count spans **20–50**. The low endpoint counts all three unknowns as negative. The high endpoint counts all three as positive. The 95% range further includes sampling uncertainty by combining the lower confidence bound for two positives with the upper confidence bound for five.

Telryn performs that calculation in each sampled group and combines the bounds with a Bonferroni correction. This covers pending returns, insufficient evidence and open grading disputes without assuming their outcomes resemble the completed grades. The report preserves individual confirmed findings. It publishes a single point estimate and screen agreement metrics after all assigned cases have determinate grades.

## Test a candidate within the queue budget

The proposal searches the screen flags, each configured category and the observed intersections of those fields. A segment must have a lower bound on its newly discovered serious-case rate above the upper bound for the rest of the residual queue. Both bounds use population weights across sampling groups. A simultaneous Hoeffding bound over the underlying groups protects against choosing a segment after inspecting the same week's outcomes. It keeps uncertainty even for a small census, because a proposal will apply to later arrivals. This comparison assumes roughly independent cases within groups and a similar future mix. Incidents spanning many related tickets or a change to product traffic weaken that assumption. [Hoeffding's result](https://www.tandfonline.com/doi/abs/10.1080/01621459.1963.10500830)

With `--max-reserved N`, the search scores each supported segment by its conservative rate advantage times the number of reserved slots. Reservations are capped at N and at the lower estimated number of new cases in that segment. The SQL also caps the reservation at the actual shift capacity. The default reserves at most one slot. The estimate describes a difference between groups. The frozen rule still needs to replay the real priority order, costs, mandatory reviews and actual displaced cases on a later, disjoint batch before a lead can try it in shadow. [Worked example](../examples/fintech/README.md)

The proposal carries a team-key signature covering the search result, SQL digest and source report revision. Starting a new replay checks that the source revision is still current in the team's registry. Regenerate a proposal if a second review or correction advances that report. A saved v0.4 comparison can still be read as a historical comparison. Its unsigned candidate cannot start a new proposal-gated replay.

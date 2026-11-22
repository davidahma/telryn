# Can the complaint screen find cases a saved search misses?

At 120 reviews each, Telryn found 89 of the 160 authored complaints and a keyword SQL query found 85. They found 57 in common, 32 only in Telryn’s queue and 28 only in SQL’s. In D004, a merchant said the cash the branch counted exceeded the credit in their till. Only Telryn selected it. In D072, a customer paid twice for groceries after the terminal showed a decline. Only SQL selected it.

The paired 95% range for the difference was **five fewer to fifteen more** complaints for Telryn. Both methods found cases the other missed. This comparison leaves the advantage at equal staff time uncertain.

| Method and decision | Cases selected | Complaints found | Precision, 95% range | Recall, 95% range |
| --- | ---: | ---: | --- | --- |
| Telryn, all flagged | 199 | 144 | 72.4% (68.4–76.6%) | 90.0% (85.6–94.4%) |
| SQL, all flagged | 98 | 76 | 77.6% (71.1–84.3%) | 47.5% (40.0–55.0%) |
| Telryn, 120 reviews | 120 | 89 | 74.2% (68.3–80.8%) | 55.6% (51.2–60.6%) |
| SQL, 120 reviews | 120 | 85 | 70.8% (64.2–76.7%) | 53.1% (48.1–57.5%) |

Precision answers how many selected cases met the rubric. Recall answers how many of the 160 authored complaints were selected. Across all flags, Telryn sent 55 noncomplaints for review and missed 16 complaints. SQL sent 22 noncomplaints for review and missed 84 complaints. SQL used its spare 22 review slots to sample from cases below its threshold. Inference over the 320 chats took 2 minutes 44 seconds on the local machine with Ollama 0.13.0 and Llama 3.2 3B Q4_K_M.

## What the fixture tests

Each of 160 situations has two conversations. One customer alleges harm caused by the firm. The other asks for information, accepts a resolution, mentions an unrelated need, or describes a problem caused elsewhere. Both see the same staff opening. The customer turns are individually authored. The five families cover repeat contacts, dissatisfaction after a refund, vulnerability or a need for support, requests for another decision-maker, and indirect descriptions of loss or distress. A need for support alone does not make the contact a complaint under the [example firm policy](../examples/detection_study/policy.txt).

[Source pairs](../examples/detection_study/pairs.tsv) contain the authored labels. The materializer shuffles the chats, issues opaque IDs and writes the answer key separately. Neither detector sees the labels, family or pair IDs. No conversation includes an actual register entry. This study measures detection from chat. The [two-week scorecard audit](../examples/complaints_pilot/README.md) separately tests register and owner evidence.

The policy, corpus, model identity, [SQL](../examples/detection_study/keyword.sql) and [budget](../examples/detection_study/protocol.json) were frozen before the model ran. SQL searches the same customer turns for complaint language, repetition, impact, redress and escalation. It also uses the staff text to identify redress context. Telryn uses the ordinary transcript screen and its unlogged-complaint rule. The local Ollama model returned a passage with each definite answer. The replay checks the passage, speaker, revision and receipt. A matching passage establishes where a prediction came from. Whether the passage supports the conclusion is the classification question tested here.

For the equal-budget comparison, both methods get 15 assumed preparation minutes and 120 two-minute reviews. SQL takes its highest priorities first. Telryn takes flagged cases first. Both break ties and fill any spare places with the same fixed hash order. Review time is modeled, not measured. The model's wall time is reported separately. Both methods had access to the labels only in scoring.

The intervals below resample the 32 **pairs** within each family 4,000 times, keeping both members together and re-ranking at the same budget each time. The tables give the observed result on all 320 cases and a percentile range across those resampled contexts. This fixture deliberately has 50% complaints and short English text. Production precision depends on prevalence, language, queue mix, and evidence quality. The ranges do not estimate performance across a bank’s customers. The reference labels were authored from the rubric; there was no customer adjudication panel.

## Reproduce it

In the release checkout, or after an editable installation, run the commands below. The first one replays the frozen [cited responses](../examples/detection_study/citations.jsonl.gz) offline and checks the [model receipt and input hashes](../examples/detection_study/run.json).

```sh
python -m examples.detection_study.evaluate
python -m examples.detection_study.materialize /tmp/telryn-study-input
telryn screen /tmp/telryn-study-input/chats.jsonl --model llama3.2:latest \
  --policy unlogged-complaint --policy-text examples/detection_study/policy.txt \
  --workers 2 --out /tmp/telryn-study-screen
python -m examples.detection_study.evaluate --screen /tmp/telryn-study-screen
```

Use new output directories and the model weights identified in the receipt. The checked-in responses preserve the observed run independently of future inference-engine changes. An analyst can examine [case selections and counts](../examples/detection_study/results.json), inspect the answer key offline, or replace the corpus and preregister a new comparison for their queue.

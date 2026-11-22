# Evaluation notes

Details behind the results in the README's §3 (claims C2–C4): what re-scoring changed and why, the statement bug
that affected every batch before `v7`, the comparison of `v6` with `v7`, the replies that broke the JSON protocol, and
the re-check of gate 4's third stage. Every number here can be recomputed offline from `flagpilot/results/tacc/`:

```bash
cd flagpilot
python3 scripts/rescore.py results/tacc/v7          # needs a python3 with pycryptodome and gmpy2 first on PATH
python3 scripts/rescore.py results/tacc/v6
python3 scripts/protocol_breaks.py results/tacc/v7
python3 scripts/protocol_breaks.py results/tacc/v6
```

## What re-scoring changed (claim C3)

`scripts/rescore.py` replays each attempt's submissions in the order the agent made them, each with the script
recorded in its trace, through today's verifier. The first submission it accepts is where the attempt would have
ended; with none accepted, the attempt is judged on its last submission, or keeps the budget ending it had.

**`v6`.** All 17 attempts that the verifier of the time ended with `hardcoded_no_material` were correct, and so was
the first submission of an 18th (`crypto-005`, B0), whose repair then failed. Their scripts decode the statement's
ciphertext (`crypto-005`), a base64 string in the statement (`general-022`) or a number in it (`general-019`, whose
only attachment is a placeholder). Two more were correct: the first submission of one (`crypto-058`, B1) prints a flag
whose encoded form is part of the challenge material (it then typed the flag in, which is still rejected), and one
(`crypto-072`, B3) searches for a Caesar key, which the old check undid by shifting every letter of the ciphertext by
the same amount; that attempt then spent its turns on a needless repair. Attempts that never submitted keep their
verdict, and no attempt verified at the time is rejected today.

Until 2026-11-21 the re-scoring paired each attempt's last flag with the last script that had reached a replay, and
missed the `crypto-005` case; it also ran without a GNU-like `zcat`, so one B1 attempt (`forensics-065`) failed on a
Mac. Both are fixed, which moved B0 from 31 to 32 and B1 from 28 to 29.

**`v7`.** Re-scoring changes one B3 attempt (`crypto-072`, the Caesar key search above), which is also the only correct
flag of B0 and B3 the verifier rejected at run time. B1 and B2 ran after the verifier fix and were already judged by it.

**The replays need the cluster's packages.** The TACC jobs carried pycryptodome and gmpy2; the replays run the
`python3` on `PATH`. Without the two packages, every `crypto-079` script fails to import and six verified attempts read
as `script_error` (2026-11-22). `rescore.py` now stops unless that `python3` can import both (`--lenient` goes on).

**Gate 4's third stage (2026-11-22).** Re-scoring all 320 attempts of `v6` and `v7` with the shaped decoy added to
gate 4 changes no verdict: `results/tacc/v6/rescore.csv` and `v7/rescore.csv` are identical with and without it. The
stage closes a gap that tests demonstrate (a format check followed by a hard-coded answer; a fallback taken when the
real file cannot be read as text); none of the recorded attempts used it.

## The statement bug (every batch before `v7`)

The task set's `title` is the first 90 characters of the statement (a display label), and the prompt used it instead
of the full statement. For 22 of the 53 challenges the rest of the statement, often the data itself, never reached the
model: `crypto-086`'s ciphertext, `crypto-072`'s ciphertext, `crypto-074`'s key table, `general-040`'s hex bytes. In
`v6`, 9 of the 20 dev challenges were affected; they were verified in 32 of 72 attempts (44%), against 68 of 88 (77%)
on the other 11, and four of them were never solved. The tiers saw the same statements, so the comparison between
tiers stands, but every absolute rate of `v5` and `v6` is an underestimate. The prompt and the memorization probe now
carry the full statement, and a test checks this for every challenge in the task set.

## `v6` against `v7`

On the same challenges and repeats, as run: B3 rose from 25 to 35 of 40 (10 attempts gained, none lost, exact McNemar
p = 0.002) and B0 from 26 to 33 (10 gained, 3 lost, p = 0.09). Half of the gain at B3, and almost all of it at B0, is
the verifier: with both batches re-scored, B3 rises from 31 to 36 (5 gained, none lost, p = 0.06), B0 from 32 to 33
(4 gained, 3 lost, p = 1.0), B1 from 29 to 33 (4 gained, none lost, p = 0.13) and B2 from 28 to 33 (7 gained, 2 lost,
p = 0.18). All five gains at B3 are on challenges whose statement `v6` had cut (`crypto-072`, `crypto-074` twice,
`crypto-079`, `crypto-093`). B0 gained on `crypto-072` and `crypto-074` but lost `crypto-079` twice and `crypto-093`
once, where its replies broke off in the middle of the JSON. Mean tokens per attempt fell by about 40% (B0 27,061 →
16,148, B3 34,304 → 20,947) and mean turns from 6.38 and 8.05 to 5.47 and 5.90.

Within `v7`, re-scored, each step up the ladder is within chance: B0 to B1 2 gained and 2 lost (p = 1.0), B1 to B2 3
and 3 (p = 1.0), B2 to B3 4 and 1 (p = 0.38), B0 to B3 3 and 0 (p = 0.25). B1 and B2 found a correct flag they could
not back up 1 and 2 times (85.0% and 87.5% raw): `general-019` twice, where a script printed `42` or `0b101010`
instead of the flag and the next one typed the flag in, and `general-024` once, whose last script looked for its
attachment next to itself and in the working directory, not in `challenge/`.

B1 and B2 of `v7` first failed: the TACC home volume ran out of space six minutes into their pipeline. They ran again
from scratch after a clean-up; see [TACC_RELIABILITY.md](TACC_RELIABILITY.md#the-shared-home-volume).

## Replies that broke the JSON protocol (claim C4)

`scripts/protocol_breaks.py` counts, per batch, the replies the agent rejected as protocol breaks:

| Batch | Breaks | Ended by the model (`stop`) | Stopped inside the JSON | After a reply the history showed cut |
|---|---|---|---|---|
| `v7` B0 / B1 / B2 / B3 | 18 / 14 / 11 / 8 | all 51 | 48 | 47 |
| `v6` B0 / B1 / B2 / B3 | 20 / 28 / 19 / 18 | all 85 | 81 | 81 |

None hit the token limit (those are labelled truncation and counted apart). The three `v7` breaks that do not stop
inside the JSON (`crypto-092`) put unescaped quotes inside the `thought` field. In `crypto-079` the replies stopped
while copying an RSA modulus out of an earlier tool output; in `forensics-059` in the middle of a `thought`.

The history keeps the first 2,000 characters of each of the model's own replies. A long reply (copying moduli, lists
of numbers) therefore stays in the conversation as a JSON object that stops in the middle of a string. In `v7`, 47 of
the 51 breaks came after such a reply in the same attempt, and that reply had itself been parsed and run: the model
could write long complete replies until it had been shown one of its own cut off. All 42 parseable `v7` replies longer
than 2,000 characters reached the history that way. This is an association in the records, not a controlled result.

Three switches address it, all off by default so that the default engine is still the one that produced `v7`:

| Switch (CLI / TACC batch) | What changes |
|---|---|
| `--reply-history elide` / `FP_REPLY_HISTORY=elide` | A long reply keeps the same 2,000 characters in the history, shortened in the middle of its longest field with the number of characters left out, so the JSON stays whole (all 42 such `v7` replies fit) |
| `--continue-cut` / `FP_CONTINUE_CUT=1` | A reply that stops inside its JSON is continued once in the same turn; the extra request's tokens are counted and the trace gets a `continued` event |
| `--fence-output` / `FP_FENCE_OUTPUT=1` | Every tool output ends with `--- end of output ---` |

They are tested offline (`tests/test_cut_replies.py`), not yet measured on the cluster. A small measurement reruns
the four challenges that had breaks at B3, four attempts each, once without and once with the switches (two serving
jobs, about an hour of the account's two-job limit; run it only when no one else uses the account), then compares the
breaks per attempt and the verdicts on matched attempts:

```bash
T=crypto-079,crypto-092,crypto-093,forensics-059
FP_BATCH=tacc-dev-v8-off FP_SPLIT=dev FP_TASKS=$T FP_SEEDS=4 FP_ABLATIONS=B3 FP_SAMPLE_SEED=1 \
  deploy/tcloud/pipeline.sh runs/tacc/v8
FP_BATCH=tacc-dev-v8-on FP_SPLIT=dev FP_TASKS=$T FP_SEEDS=4 FP_ABLATIONS=B3 FP_SAMPLE_SEED=1 \
  FP_REPLY_HISTORY=elide FP_CONTINUE_CUT=1 deploy/tcloud/pipeline.sh runs/tacc/v8
python3 scripts/protocol_breaks.py runs/tacc/v8
python3 -m flagpilot pair --a tacc/v8/tacc-dev-v8-off-B3 --b tacc/v8/tacc-dev-v8-on-B3
```

# Evaluation baseline and Track H results

## Frozen picoCTF split

The offline picoCTF task set derived from InterCode contains 53 tasks (43 core and 10 extra General Skills). The original 54-task no-solutions export included `general-041` (InterCode task 41). It requires a live service, so the current dataset builder excludes it. The frozen index preserves the remaining JSONL rows byte for byte.

| Category | Dev | Test | Total |
|---|---:|---:|---:|
| Cryptography | 8 | 8 | 16 |
| Forensics | 6 | 6 | 12 |
| General Skills | 13 | 12 | 25 |
| Total | 27 | 26 | 53 |

[The frozen split](splits-picoctf-53.json) uses the repository's category-stratified generator with seed `0` and a target test fraction of `0.5`. The two sets are disjoint and cover all 53 tasks. Filtering this partition to the core scope leaves 20 dev and 23 test tasks. NYU CTF Bench keeps its own upstream split.

| File | SHA-256 |
|---|---|
| `index/tasks.jsonl` in the no-solutions dataset | `b467958bea9f248aef59dc950a83d0720ab585e55ed0fc10d001865546116b3b` |
| `splits-picoctf-53.json` | `23faaf1c6aa8bf9260b1ba15f505ced4d92a1e3420978ddff3bd4e2046c69cb4` |

Use the no-solutions dataset described in [DATASET.md](DATASET.md). If starting from the original export, remove only the `general-041` row and its task directory. Keep the remaining rows in their original order and encoding. Set `FLAGPILOT_TASKS` to the dataset root. From `flagpilot/`, check both hashes against the table and point the evaluation CLI at the committed split:

```bash
export FLAGPILOT_SPLITS="$PWD/docs/splits-picoctf-53.json"
sha256sum "$FLAGPILOT_TASKS/index/tasks.jsonl" "$FLAGPILOT_SPLITS"
```

Use the dev set for iteration. Reserve the test set for final configurations and use the existing test-run guard.

## Track H smoke test

On 2026-11-20 (Hong Kong time), FlagPilot at `4d7a870` used the hosted `deepseek-flash` model on the bundled synthetic text fixtures. The CLI batch used one worker and one attempt per fixture. A separate B3 Web run repeated `demo-001`. Both entry points used a six-turn base budget and a 1,024-token per-response cap, with replay checks enabled.

| Entry point | Fixture | Verdict | Turns | End-to-end time | Input tokens | Output tokens | Estimated USD |
|---|---|---|---:|---:|---:|---:|---:|
| CLI | `demo-001` | `verified / match` | 2 | 4.46 s | 1,533 | 122 | 0.000209 |
| CLI | `demo-002` | `verified / match` | 3 | 4.18 s | 2,968 | 237 | 0.000286 |
| CLI | `demo-003` | `verified / match` | 3 | 3.57 s | 2,575 | 207 | 0.000247 |
| Web | `demo-001` | `verified / match` | 2 | 4.95 s | 1,547 | 128 | 0.000158 |

All four runs produced encrypted Markdown writeups. The Web download returned HTTP 200 and matched the decrypted artifact. Provider-reported input and output tokens matched the trace totals for each run. The CLI batch used 7,076 input tokens (4,480 cache hits) and 566 output tokens. FlagPilot estimated USD 0.000742 for the CLI batch and USD 0.000158 for the Web run using its configured price table. The provider bill has yet to be reconciled.

These runs establish that the hosted API, replay verdict, writeup generation, and download work on the synthetic fixtures. Formal benchmark results remain part of E1 and E2.

## Track H offline baseline

On 2026-11-20 (Hong Kong time), FlagPilot at `4d7a870` ran one hosted `deepseek-flash` attempt on each of 43 eligible tasks from the frozen 53-task dataset. Five tasks per split were excluded before execution because they involved password or credential recovery, exploitation of cryptographic weaknesses, or reliance on an external resource.

Both splits used B3, four workers, temperature 0.2, a six-turn budget including repairs, a 45-second attempt budget, a 1,024-token response cap, and five-second action and replay timeouts. Pre-submit and material-free replay checks were enabled. The test batch was recorded by the existing test-run guard. Generated commands and replay scripts ran without network access, with read-only attempt files and temporary scratch. Scratch files did not persist between tool calls.

| Split | Coverage | Correct flag (raw) | Replay verified | Input tokens | Output tokens | Estimated USD |
|---|---:|---:|---:|---:|---:|---:|
| Dev | 22/27 | 14/22 (63.6%) | 12/22 (54.5%) | 178,021 | 19,126 | 0.017932 |
| Test | 21/26 | 11/21 (52.4%) | 10/21 (47.6%) | 145,519 | 18,129 | 0.015997 |
| Total | 43/53 | 25/43 (58.1%) | 22/43 (51.2%) | 323,540 | 37,255 | 0.033929 |

The overall 95% Wilson intervals are 43.3%–71.6% for raw accuracy and 36.8%–65.4% for replay verification. The raw-to-verified gap is 3 attempts (7.0% of this subset). The local price table produced the cost estimates. Provider billing reconciliation remains open.

| Category | Coverage | Correct flag | Replay verified |
|---|---:|---:|---:|
| Cryptography | 12/16 | 5 | 5 |
| Forensics | 12/12 | 5 | 4 |
| General Skills | 19/25 | 15 | 13 |

The batch ended with 22 `verified`, 3 `unverified`, 4 `wrong`, and 14 `budget` verdicts.
The other outcomes recorded 14 `max_turns`, 4 `wrong_flag`, 2 `hardcoded_no_material`, and 1 `script_error`.

Verified attempts reached a median end-to-end time of 7.25 s (p95 13.42 s, n=22). Per-turn token totals matched the attempt ledgers. The provider reported usage on 204/204 recorded model turns. The rate denominators use the attempted subset. E1 ablations and the E2 probes and manual review remain open.

<details>
<summary>Per-task verdicts</summary>

| Split | Task | Verdict | Reason | Turns | End-to-end time |
|---|---|---|---|---:|---:|
| Dev | `crypto-005` | `budget` | `max_turns` | 6 | 9.76 s |
| Dev | `crypto-058` | `verified` | `match` | 3 | 14.12 s |
| Dev | `crypto-072` | `wrong` | `wrong_flag` | 6 | 14.70 s |
| Dev | `crypto-074` | `budget` | `max_turns` | 6 | 19.55 s |
| Dev | `crypto-092` | `budget` | `max_turns` | 6 | 18.50 s |
| Dev | `crypto-093` | `verified` | `match` | 6 | 13.46 s |
| Dev | `crypto-097` | `verified` | `match` | 5 | 9.17 s |
| Dev | `forensics-003` | `budget` | `max_turns` | 6 | 20.26 s |
| Dev | `forensics-008` | `verified` | `match` | 6 | 10.76 s |
| Dev | `forensics-059` | `budget` | `max_turns` | 6 | 10.49 s |
| Dev | `forensics-065` | `budget` | `max_turns` | 6 | 16.37 s |
| Dev | `forensics-071` | `unverified` | `script_error` | 6 | 11.34 s |
| Dev | `forensics-094` | `verified` | `match` | 4 | 5.67 s |
| Dev | `general-004` | `verified` | `match` | 2 | 2.44 s |
| Dev | `general-019` | `wrong` | `wrong_flag` | 5 | 8.42 s |
| Dev | `general-022` | `unverified` | `hardcoded_no_material` | 4 | 5.83 s |
| Dev | `general-024` | `verified` | `match` | 3 | 6.25 s |
| Dev | `general-025` | `verified` | `match` | 4 | 5.64 s |
| Dev | `general-026` | `verified` | `match` | 5 | 8.75 s |
| Dev | `general-027` | `verified` | `match` | 4 | 4.94 s |
| Dev | `general-036` | `verified` | `match` | 5 | 7.84 s |
| Dev | `general-037` | `verified` | `match` | 2 | 2.75 s |
| Test | `crypto-055` | `budget` | `max_turns` | 6 | 20.52 s |
| Test | `crypto-056` | `budget` | `max_turns` | 6 | 15.93 s |
| Test | `crypto-080` | `verified` | `match` | 4 | 5.99 s |
| Test | `crypto-086` | `budget` | `max_turns` | 6 | 13.72 s |
| Test | `crypto-090` | `verified` | `match` | 6 | 12.66 s |
| Test | `forensics-002` | `budget` | `max_turns` | 6 | 10.76 s |
| Test | `forensics-014` | `budget` | `max_turns` | 6 | 8.58 s |
| Test | `forensics-061` | `budget` | `max_turns` | 6 | 21.74 s |
| Test | `forensics-064` | `verified` | `match` | 5 | 7.19 s |
| Test | `forensics-067` | `verified` | `match` | 4 | 7.30 s |
| Test | `forensics-070` | `budget` | `max_turns` | 6 | 16.29 s |
| Test | `general-006` | `verified` | `match` | 4 | 9.31 s |
| Test | `general-011` | `verified` | `match` | 2 | 3.17 s |
| Test | `general-017` | `unverified` | `hardcoded_no_material` | 3 | 5.58 s |
| Test | `general-018` | `wrong` | `wrong_flag` | 4 | 7.65 s |
| Test | `general-021` | `verified` | `match` | 2 | 2.83 s |
| Test | `general-023` | `verified` | `match` | 2 | 2.22 s |
| Test | `general-038` | `verified` | `match` | 6 | 9.30 s |
| Test | `general-040` | `wrong` | `wrong_flag` | 3 | 3.94 s |
| Test | `general-060` | `budget` | `max_turns` | 6 | 10.69 s |
| Test | `general-063` | `verified` | `match` | 5 | 9.80 s |

</details>

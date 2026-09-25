# Evaluation: would Signal have surfaced Claude Opus 5.5?

**Short answer: yes.** Replayed as of the morning after the release (2026-09-23 07:00 America/New_York), Claude Opus 5.5 is the #1 candidate by a wide margin. It is sent both with a "Claude" interest and with no interests at all. Its link is the anthropic.com announcement, and its reason line reads `13 sources in 19h (usually 0), matches: Claude`.

The replay uses Hacker News data only, and one threshold (`SCORE_THRESHOLD`) was tuned on the same week of data. Both caveats are explained below.

## The event

The first HN stories appeared on **2026-09-22 at 16:27 UTC (12:27 ET)**. They were two posts titled "Claude Opus 5.5" linking to `https://www.anthropic.com/claude-opus-5-5`, one of which reached 1,797 points. The first morning brief after the release is therefore **2026-09-23 07:00 ET**. Source: Algolia HN Search, `query="Opus 5.5"`.

## Method

1. **Backfill.** `scripts/backfill_hn.py` pulled every HN story created from 2026-09-05 to 2026-09-25 (UTC) from the Algolia API (`search_by_date`, filtered on `created_at_i`). That is 19,871 stories, which produced 9,238 entities and 20,559 mentions. Each story went through the production storage path: `item_from_hn`, entity extraction, mentions, and the `entity_counts` rollup. It ran against a separate local database, not production. `--mark-covered` recorded one `backfill` run per hour so the 14-day baseline counts those hours as observed.
2. **Replay.** `scripts/replay.py` runs the production `score_all` and `select` as of a given time.
3. **Independence.** With HN as the only source, each story counts as the site it links to (the decision Elwin made for this project). anthropic.com, theverge.com and simonwillison.net are therefore three sources. Self-posts count as `news.ycombinator.com`.

To reproduce locally, with a scratch Postgres at `$DATABASE_URL`:

```bash
python -m app.migrate
python -m scripts.backfill_hn --start 2026-09-05 --end 2026-09-25 --mark-covered
python -m scripts.replay --as-of 2026-09-23T07:00:00-04:00 --interests "Claude:1" --target "Claude Opus 5.5"
```

## Results

### Morning of 2026-09-23, interest profile `Claude:1`

```
As of 2026-09-23T07:00:00-04:00  (window ends 2026-09-23T11:00:00+00:00)
Interests: [('Claude', 1.0)]
Candidates with >= 3 origins: 54; threshold 1.0, floor 0.1, top 5

 # sent  score  spike   c24  mean   std   int  entity / reason / link
 1 YES    3.91  14.69  14.7  0.00  0.00  0.17  Opus 5.5
                                            13 sources in 19h (usually 0), matches: Claude
                                            anthropic.com: Claude Opus 5.5  <https://www.anthropic.com/claude-opus-5-5>
 2        0.40   4.00   4.0  0.00  0.00  0.00  Horowitz Andreessen Academy
                                            4 sources in 21h (usually 0)
                                            a16z.news: The Horowitz Andreessen Academy  <https://www.a16z.news/p/introducing-the-horowitz-andreessen>
 3        0.36   2.06  12.7  3.57  3.44  0.07  GPT-6
                                            7 sources in 19h (usually 4), matches: Claude
                                            openai.com: GPT-6 Sol  <https://developers.openai.com/api/docs/models/gpt-6-sol>
 4        0.30   3.00   3.0  0.00  0.00  0.00  OpenClaw
                                            3 sources in 19h (usually 0)
                                            x.com/natfriedman: We built muse from scratch, but it is definitely inspired by OpenClaw  <https://twitter.com/natfriedman/status/2102103707936768130>
 5        0.30   3.00   3.0  0.00  0.00  0.00  Unreal Agent
                                            3 sources in 19h (usually 0)
                                            x.com/unreallabsai: Unreal Agent – frontier performance at 60% cost  <https://twitter.com/unreallabsai/status/2102435462065385775>

Would send 1 item(s): ['Opus 5.5']

Target 'Claude Opus 5.5': rank 1 of 54, score 3.91, spike 14.69, SENT
```

### Same morning, no interests at all (floor only)

```
 1 YES    1.47  14.69  14.7  0.00  0.00  0.00  Opus 5.5
                                            13 sources in 19h (usually 0)
                                            anthropic.com: Claude Opus 5.5  <https://www.anthropic.com/claude-opus-5-5>
 2        0.40   4.00   4.0  0.00  0.00  0.00  Horowitz Andreessen Academy
 ...
Would send 1 item(s): ['Opus 5.5']
Target 'Claude Opus 5.5': rank 1 of 54, score 1.47, spike 14.69, SENT
```

### Control: the morning before the release (2026-09-22 07:00 ET)

Opus 5.5 is not a candidate, since it had no mentions yet. That morning is a quiet day: the best candidate scores 0.51.

### The whole week, highest spikes per morning (no interests)

| Morning (07:00 ET) | Top spikes (origins in 24h, baseline mean/day) |
| --- | --- |
| Sat 09-19 | Denmark 4.0 · Politico 4.0 · Greenland 3.6 |
| Sun 09-20 | AI Force 3.0 · SpaceXAI 3.0 · Jev 2.7 (28 origins, mean 4.3) |
| Mon 09-21 | Jev 2.3 · Politics 1.7 · Flock 0.9 |
| Tue 09-22 | Muse AI 5.1 · GoogleBook 5.0 · AMD 3.4 |
| **Wed 09-23** | **Opus 5.5 14.7 (13 origins, mean 0.0)** · Horowitz Andreessen Academy 4.0 · OpenClaw 3.0 |
| Thu 09-24 | Medicare 2.5 · F-35 2.3 · Australian 2.2 |
| Fri 09-25 | partial day (the backfill ends 09-25 00:00 UTC) |

Opus 5.5's spike is about 3× the largest spike on any other morning that week.

### What each morning would have sent

| Profile | 19 | 20 | 21 | 22 | 23 | 24 | 25 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| none, threshold 1.5 | quiet | quiet | quiet | quiet | quiet (Opus 1.47) | quiet | quiet |
| none, threshold 1.0 | quiet | quiet | quiet | quiet | **Opus 5.5** | quiet | quiet |
| `Claude:1`, either threshold | quiet | quiet | quiet | quiet | **Opus 5.5** | quiet | quiet |
| 7 broad terms¹, either threshold | quiet | SpaceXAI | quiet | quiet | **Opus 5.5** | quiet | quiet |

¹ Claude, Anthropic, OpenAI, Rust, Python, LLM, Apple, each at weight 1.

## Why it ranked

- **Convergence.** In the 19 hours after launch, 13 independent sites were linked from HN about the same entity. They included anthropic.com, artificialanalysis.ai, theverge.com, coderabbit.ai, claude.dev, simonwillison.net and github.com/ninjahawk.
- **Alias merging.** Titles said "Claude Opus 5.5", "Opus 5.5", "Anthropic launches Claude Opus 5.5 …", "Claude Opus 5.5 Intelligence, Performance and Price Analysis (Max)" and "Sol 6 and Opus 5.5 compared …". All of them resolve to one key, `opus 5.5`. Without that merge the count would have split across several entities and none would have reached a comparable spike. `tests/test_extract.py` pins 22 of these real titles.
- **Empty baseline.** The entity had no mentions in the previous 14 days, so the mean and std are 0 and the spike equals the weighted count (14.7).
- **Best link.** The anthropic.com announcement was chosen over news coverage because anthropic.com is in `primary_domains`.

## Caveats

- **Threshold tuning.** `SCORE_THRESHOLD` started at 1.5. At 1.5, the no-interest replay scores Opus 5.5 at 1.47 and the morning is quiet. I lowered it to 1.0 so that an off-interest topic needs a spike of at least 10 (with `INTEREST_FLOOR` at 0.1). That is about twice the largest ordinary spike of the week (5.1). The choice is informed by the same week that contains the target event, so treat it as a starting point and re-tune after a few weeks of real votes. With a "Claude" interest the result does not depend on this change.
- **HN only.** Production also reads RSS feeds, which add origins (more candidates, bigger counts) but no history before launch. This replay does not measure RSS behavior.
- **Many quiet days.** On HN-only data with these thresholds, six of seven mornings are quiet. The brief aims for 3–5 items a day. Production data from RSS plus Elwin's real interest list will change this; revisit `SCORE_THRESHOLD` and `INTEREST_FLOOR` once `/stats` shows the real quiet-day rate.
- **Noise entities.** A few generic words still become entities ("Create", "God"). They have low spikes and no interest match, and none came near the threshold in this week of data.

## Live metrics

Hit rate, quiet-day rate, collect reliability and the `/missed` count are served by `GET /stats` on the deployed service. `FRONTEND_HANDOFF.md` has real output.

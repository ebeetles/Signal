# Signal

Signal is a personal morning brief. It watches free sources (Hacker News plus RSS/Atom feeds: company blogs, changelogs, GitHub releases, YouTube channels) and notices when several **independent** sources start talking about the same thing within hours. Every morning at 07:00 Eastern it sends Elwin 3–5 such items on Telegram. Items he marks 👍 are published through a small read-only API to the "Daily cool stuff" section of his portfolio.

It ranks by **convergence, not keywords**. Stored history defines what "normal" looks like for each entity, and an item is interesting when many independent outlets suddenly mention it at once.

## Status

<!-- STATUS:START -->
All seven milestones are implemented and covered by 232 automated tests, run locally against Postgres. Deployment to Render and live verification are **pending**; this section will list what has been verified on the deployed service.
<!-- STATUS:END -->

## How it works

```
cron-job.org ──GET /health (10 min)──▶ ┌─────────────────────────┐
             ──POST /collect (hourly)─▶ │  FastAPI on Render      │──▶ Supabase Postgres
             ──POST /send (07:00 ET)──▶ │  (free web service)     │
Telegram ─────POST /telegram/webhook──▶ │                         │──▶ Telegram Bot API
Portfolio ────GET /digests, …─────────▶ └─────────────────────────┘
```

1. **Collect** (hourly, `POST /collect`, returns `202`, then works in the background). It takes a Postgres advisory lock and fetches every active source concurrently; one broken feed never fails the run. It stores new items (the unique URL makes this idempotent), extracts entities from titles, recomputes the hourly `entity_counts` for the affected hours, prunes items older than 60 days, and records the run.
2. **Entity extraction** (`app/extract.py`) is deterministic. It takes capitalized phrases of 1–4 tokens and joins version numbers to the name before them. Leading vendor words are dropped before a version, so "Claude Opus 5.5", "Opus 5.5" and "Anthropic Claude Opus 5.5" all become `opus 5.5`. Stoplists, role words ("CEO"), Title Case detection and a manual alias table (`seeds/aliases.yaml`) handle the rest. Candidates that look like ordinary words in Title Case headlines are "weak": they may match an existing entity but never create one.
3. **Independence.** Every item has an *origin*, the outlet that published it. For RSS, that's the domain of the linked page, or the channel for YouTube feeds. For HN, a story counts as **the site it links to**, so three HN stories linking to anthropic.com, theverge.com and simonwillison.net are three sources. Within an hour, an origin's `n` items count `w × (1 + ln n)`, where `w` is the source's `independence_weight`.
4. **Scoring** (`app/scoring.py`). For each entity with at least 3 distinct origins in the last 24 h:
   - `spike = (c_24h − mean_14d) / (std_14d + 1)`, where the baseline uses only hours covered by a successful collect run. A missing hour is missing data, not zero mentions: partly covered days are scaled, and days with fewer than 12 covered hours are skipped.
   - `interest` is the TF-IDF cosine similarity between the entity's recent titles and each interest term, weighted by term weight.
   - `score = spike × (INTEREST_FLOOR + interest)`.
   - Selection: the top 5 above `SCORE_THRESHOLD`. It skips entities sent in the last 3 days unless their spike doubled, and near-duplicates of a better pick. No picks means a quiet day.
   - Best link: a first-party page (primary source, a `primary_domains` site, or a releases/changelog URL); otherwise the link with the most independent weight behind it.
   - Reason line, generated from data: `13 sources in 19h (usually 0), matches: Claude`.
5. **Send** (`POST /send`, 07:00 ET, once per local date). It stores the digest with snapshots of headline, URL and source, so the public archive survives item pruning. It then sends a header message with notification, one silent message per item with 👍/👎 buttons, or "Nothing big today." Finally it snapshots interest weights for the history chart.
6. **Feedback** (`POST /telegram/webhook`). The webhook checks the secret-token header and only accepts updates from `TELEGRAM_CHAT_ID`. Votes are validated against `digest_items`, and a later vote overwrites an earlier one. Learning moves *learned* terms matched by the item by ±`LEARNING_RATE` (capped to 0–5). A first 👍 adds the entity's name, without its version, as a learned term. Manual terms are never changed by learning. The bot also accepts `/add <term> [weight]`, `/remove <term>`, `/interests`, `/missed <text>` and `/help`.

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | none | Keep-warm target; never touches the DB |
| POST | `/collect` | `Authorization: Bearer $CRON_TOKEN` | Start a collect run (202) |
| POST | `/send` | `Authorization: Bearer $CRON_TOKEN` | Build and send today's digest (202, once per day) |
| POST | `/telegram/webhook` | `X-Telegram-Bot-Api-Secret-Token` | Votes and commands |
| GET | `/digests?approved=&limit=&before=` | public | Digests, newest first, id-cursor pagination |
| GET | `/digests/{id}?approved=` | public | One digest |
| GET | `/interests/history?days=` | public | Interest weights over time |
| GET | `/stats` | public | Hit rate, quiet-day rate, collect reliability, missed reports |

Public endpoints allow CORS only from `PORTFOLIO_ORIGIN` (GET only), are rate-limited per client IP (60 requests/min), send `Cache-Control: public, max-age=60`, and return errors as `{"error": {"code", "message", "details?"}}`. Interactive docs are served at `/docs`, and the schema at `/openapi.json`. The frontend contract is in [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md) and is pinned by `tests/test_contract.py`.

## Configuration

Secrets live only in environment variables (Render → Environment; locally, the gitignored `.env`):

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Supabase **session pooler** connection string (IPv4; the direct host is IPv6-only) |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | The only chat the bot responds to |
| `TELEGRAM_WEBHOOK_SECRET` | Must match what `scripts/set_webhook.py` registers |
| `CRON_TOKEN` | Bearer token for `/collect` and `/send` |
| `PORTFOLIO_ORIGIN` | `https://ebeetles.github.io` |
| `TIMEZONE` | `America/New_York` |

Every threshold and window is a named constant in [`app/config.py`](app/config.py): `WINDOW_HOURS`, `BASELINE_DAYS`, `MIN_COVERED_HOURS_PER_DAY`, `MIN_DISTINCT_SOURCES`, `INTEREST_FLOOR`, `SCORE_THRESHOLD`, `TOP_N`, `NO_REPEAT_DAYS`, `REPEAT_SPIKE_MULTIPLIER`, `LEARNING_RATE`, `WEIGHT_CAP`, `MAX_TERMS`, `RETENTION_DAYS`, the rate limit, and so on.

Sources are listed in [`seeds/sources.yaml`](seeds/sources.yaml) and synced into the `sources` table on every collect. Every entry except Hacker News is an example; edit freely. Adding a new source *type* (e.g. Reddit) means adding one module in `app/sources/` with an async `fetch(client, source, ctx)` and registering it in `app/sources/__init__.py`.

## Repository layout

```
app/
  main.py        FastAPI app: ops routes, webhook, CORS, gzip, errors
  api.py         public read-only API, rate limiter
  config.py      env settings + every tunable constant
  db.py          async psycopg pool, advisory lock helper
  migrate.py     applies migrations/*.sql once each (runs in the Render build)
  sources/       hn.py, rss.py, base.py (origins, URL cleanup), registry
  extract.py     entity extraction + alias merging
  collect.py     collect pipeline
  scoring.py     spike, interest match, selection, best link, reason line
  send.py        digest assembly + delivery
  telegram.py    Bot API client (never leaks the token), HTML escaping
  feedback.py    webhook: votes, learning, commands
  schemas.py     Pydantic models (public API contract, Telegram input)
migrations/      plain SQL
seeds/           sources.yaml, aliases.yaml
scripts/         set_webhook.py, get_chat_id.py, backfill_hn.py, replay.py
tests/           232 tests
```

## Development

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
createdb signal_test
TEST_DATABASE_URL=postgresql://localhost/signal_test .venv/bin/python -m pytest
```

The tests never read `.env` and never touch production. To run the app locally against a scratch database:

```bash
DATABASE_URL=postgresql://localhost/signal .venv/bin/python -m app.migrate
DATABASE_URL=postgresql://localhost/signal CRON_TOKEN=dev .venv/bin/uvicorn app.main:app --reload
curl -X POST -H "Authorization: Bearer dev" localhost:8000/collect
```

## Deployment

Manual steps (Supabase, Render, BotFather, the webhook and the three cron-job.org jobs) are in [SETUP.md](SETUP.md). Render builds with `pip install -r requirements.txt && python -m app.migrate` and starts with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. The database connection uses Supabase's session pooler, which supports IPv4 and keeps session-level advisory locks working.

Design choices for the free tiers:

- `/collect` and `/send` return `202` immediately. cron-job.org gives up after 30 s, and a cold Render start takes about a minute.
- `/health` never touches the database, so the 10-minute keep-warm ping stays fast.
- Overlapping or repeated calls are safe. Collect takes an advisory lock and records a `skipped` run. Send is idempotent per local date.
- Hourly collect writes keep the Supabase project from pausing, and storage stays small: derived data only, with raw items pruned after 60 days.
- Idle memory is about 60 MB and peaks around 135 MB when scikit-learn is loaded for TF-IDF, well under Render's 512 MB.

## Evaluation

[EVALUATION.md](EVALUATION.md) replays three weeks of backfilled HN data. On the morning after the Claude Opus 5.5 release (2026-09-23 07:00 ET), Signal ranks it #1 with spike 14.7 from 13 independent sites. That is about 3× any other spike that week, and it would have been sent. The file also covers the caveats, including a threshold tuned on the same week.

## Security

- `/collect` and `/send` require a bearer token; the webhook requires Telegram's secret-token header and ignores other chats. Token comparisons are constant-time.
- Every input is validated with Pydantic (query params, webhook updates, bot commands). SQL is parameterized throughout.
- Fetched text is untrusted. It is HTML-escaped for Telegram, only `http(s)` links are stored, and the public API returns it as plain JSON strings for the frontend to render with `textContent`.
- Secrets live only in environment variables, and `.env` has been gitignored since the first commit. httpx request logging is turned off because Telegram URLs contain the bot token.

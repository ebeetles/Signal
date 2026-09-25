# Signal backend: build brief

You are building the backend for **Signal**, a personal morning brief. Read this whole file before writing code. When something here conflicts with your own preferences, follow this file. When something is unclear or seems wrong, stop and ask Elwin rather than guessing.

## What Signal does

Signal watches a set of free sources, detects when something Elwin cares about suddenly gets attention across several independent sources, and sends him 3–5 items as a Telegram notification every morning at 7am Eastern. Items he approves (thumbs up) are shown publicly in a "Daily cool stuff" section on his portfolio site, which a separate agent will build later from your handoff.

The core idea is **ranking by convergence, not keywords**: when several independent sources start mentioning the same thing within hours, that is an event. Stored history defines what "normal" looks like for each entity.

This is also a class assignment: a backend on Render, integrated with a portfolio site, that justifies itself by keeping secrets server-side, storing data, doing scheduled computation, and validating input.

## Hard constraints

- **$0 to run.** Every component stays on a free tier. No paid APIs, including LLM APIs (no OpenAI, no Claude API). No Render cron jobs, background workers, or Render Postgres (all paid or expiring).
- **Python only** for the backend.
- **Single user.** No accounts, sign-up, or multi-tenancy.
- **Free instance memory is small** (Render free web service). Keep dependencies light. Do not load large ML models. If you want spaCy, measure memory first and ask.
- **v1 sources exclude** Reddit, X, Instagram and TikTok. Reddit may be added later if API access is approved; design the source layer so adding it is one new fetcher.
- Only document work as done after it is deployed and verified. Never describe planned or partially working features as complete, in code comments, READMEs, or the handoff.

## Decided stack

| Piece | Choice |
| --- | --- |
| Web framework | FastAPI + uvicorn, Python 3.12 |
| HTTP client | httpx (async) |
| Feeds | feedparser |
| Database | Supabase free plan (Postgres), accessed with psycopg 3 (or SQLAlchemy Core on psycopg). No Supabase client SDK needed |
| Validation | Pydantic v2 |
| Ranking | Plain Python + scikit-learn (TF-IDF); pandas only if genuinely needed |
| Hosting | Render free web service, deployed from this repo |
| Scheduler | cron-job.org (external, free), configured by Elwin in its web UI |
| Notifications and interest editing | Telegram Bot API, called with httpx (python-telegram-bot is fine if you prefer) |
| Frontend | Not your job. Separate repo, separate agent, built from your handoff |

### Database connection note

Supabase's direct connection string may be IPv6-only. If connecting from Render fails, use Supabase's **session pooler** connection string instead. Verify whichever you choose works from the deployed service, and note it in the README.

## Free-tier behavior you must design around

- **Render free services spin down after 15 minutes without traffic and take about a minute to wake.** cron-job.org's free tier times out requests after **30 seconds**. So:
  - Elwin will configure a keep-warm job hitting `GET /health` every 10 minutes. `/health` must be fast and must not touch the database.
  - `POST /collect` and `POST /send` must return `202 Accepted` immediately and do their work in a background task. Never make the cron request wait for fetching or scoring.
- **Supabase free projects pause after low activity over 7 days.** Hourly collect writes prevent this. Do not add a separate keep-alive.
- **Missed pings leave gaps.** Record every run (see `runs` table). The baseline must treat hours with no successful collect run as missing data, not as zero mentions.
- **cron-job.org disables a job after 25+ consecutive failures.** Endpoints must not error on duplicate or overlapping calls.

## Repository layout (suggested)

```
signal/
  app/
    main.py            # FastAPI app, routes, CORS
    config.py          # settings from env vars, tunable constants
    db.py              # connection pool, helpers
    sources/           # one module per source type (hn.py, rss.py)
    extract.py         # entity extraction + alias normalization
    scoring.py         # spike score, interest match, selection
    collect.py         # collect pipeline
    send.py            # digest assembly + Telegram send
    telegram.py        # Bot API client, webhook handling, commands
    schemas.py         # Pydantic models, including the public API contract
  migrations/          # plain SQL files, applied in order
  seeds/sources.yaml   # source list, edited by Elwin
  scripts/
    set_webhook.py     # registers the Telegram webhook with secret token
    backfill_hn.py     # historical HN backfill for evaluation
  tests/
  README.md
  SETUP.md             # exact manual setup steps for Elwin
  FRONTEND_HANDOFF.md  # you complete this at the end (template provided)
  requirements.txt
  render.yaml          # optional
```

## Environment variables

All secrets live in Render environment variables. Add `.env` to `.gitignore` in the first commit. Never commit secrets, never log them.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Supabase Postgres connection string |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Elwin's chat id; the only chat the bot responds to |
| `TELEGRAM_WEBHOOK_SECRET` | Secret token Telegram sends in `X-Telegram-Bot-Api-Secret-Token` |
| `CRON_TOKEN` | Bearer token required on `/collect` and `/send` |
| `PORTFOLIO_ORIGIN` | The one origin allowed by CORS (e.g. `https://<user>.github.io` or a custom domain; ask Elwin) |
| `TIMEZONE` | `America/New_York` |

## Data model

Write plain SQL migrations. Column names below are guidance; keep the intent.

| Table | Key columns | Purpose |
| --- | --- | --- |
| `sources` | id, name, type (`hn`, `rss`), url, independence_weight, active | What to fetch and how much it counts. Seeded from `seeds/sources.yaml` |
| `items` | id, source_id, url (unique), title, published_at, fetched_at | One row per fetched post; unique url dedupes |
| `entities` | id, name, aliases (text[]), created_at | Things that can trend |
| `mentions` | item_id, entity_id | Which items mention which entities |
| `entity_counts` | entity_id, hour_bucket, weighted_count, distinct_sources | Hourly rollup the spike detector reads |
| `runs` | id, kind (`collect`, `send`), started_at, finished_at, status, error | Every run; used for gap handling and the reliability metric |
| `digests` | id, sent_at, status (`sent`, `quiet`, `failed`) | One per morning, including quiet days |
| `digest_items` | id, digest_id, entity_id, item_id, score, reason, feedback (`up`, `down`, null), feedback_at | What was sent, why, and Elwin's vote |
| `interests` | term, weight, updated_at, origin (`manual`, `learned`) | Interest profile |
| `interest_history` | term, weight, recorded_at | Daily snapshot of weights, for the public chart |
| `missed` | id, text, created_at | Things Elwin reports Signal missed (`/missed` command) |

Retention: delete `items` and `mentions` older than 60 days as part of collect. Keep everything else. Storage must stay well under Supabase's 500 MB free limit; store derived data, not raw feed payloads.

## Sources (v1)

Only sources that are free and need no key.

- **Hacker News**: official public API (`https://hacker-news.firebaseio.com/v0/`), top and new stories each collect run. Fetch item details concurrently but politely (bounded concurrency).
- **RSS/Atom feeds** via feedparser: company blogs and changelogs, GitHub releases (`https://github.com/<owner>/<repo>/releases.atom`), YouTube channels (`https://www.youtube.com/feeds/videos.xml?channel_id=<id>`).

Elwin will supply the actual source list in `seeds/sources.yaml`. Create the file with the schema and a few clearly marked example entries. **Verify every feed URL actually returns a parseable feed before treating it as working**; log and skip broken feeds rather than failing the run. Send a descriptive User-Agent.

Each source has an `independence_weight` so ten posts from one outlet count less than one post each from five different places.

## Pipelines

### Collect (`POST /collect`, hourly)

1. Check `Authorization: Bearer <CRON_TOKEN>`; otherwise 401.
2. Return 202 immediately; run the rest in a background task.
3. Take a Postgres advisory lock (`pg_try_advisory_lock`). If another collect is running, record a skipped run and exit.
4. Fetch all active sources with timeouts. One failing source must not fail the run.
5. Insert new items; the unique url makes this idempotent.
6. Extract entities from new item titles, insert mentions.
7. Update `entity_counts` for affected hours.
8. Prune old items and mentions.
9. Record the run with status and any error.

### Entity extraction

Start simple and deterministic:

- capitalized phrases (1–4 tokens);
- version-like tokens (`5.5`, `v3`, `3.12`) joined to the preceding word(s), so "Opus 5.5" is one entity;
- normalization through an alias table so "Opus 5.5" and "Claude Opus 5.5" merge (store aliases on `entities`, allow manual additions);
- a stoplist for noise ("Show HN", "Ask HN", "New", "The", common words, weekdays).

This is the highest-risk part of the project: if variants don't merge, spikes never form. Write thorough unit tests with real-looking titles.

### Scoring

For each entity, compare the last 24 hours to its baseline over the previous 14 days (only hours covered by successful collect runs):

```
spike(e) = (c_24h(e) - mean_14d(e)) / (std_14d(e) + 1)
```

`c` is the independence-weighted mention count. The +1 keeps brand-new entities from dividing by zero.

- Require at least **3 distinct sources** in the last 24h.
- **Interest match**: TF-IDF similarity between the entity's recent item titles and interest terms, weighted by term weight. Unknown topics get a small floor so something big outside the list can still surface.
- **Final score** = `spike × (floor + interest)`.
- **Selection**: top 5 above a threshold. None above threshold → quiet day.
- **No repeats**: skip entities sent in the last 3 days unless their spike doubled.
- **Best link per item**: prefer a primary source (blog, release, changelog) over aggregators; otherwise the highest-weighted item.
- **Reason line**, generated from data, e.g. `5 sources in 12h (usually 0), matches: Claude`.

Put every threshold and window (14 days, 3 sources, top 5, floor, threshold, learning rate, weight cap) in `config.py` as named constants. They will be tuned.

### Send (`POST /send`, 7am Eastern)

1. Auth and 202 exactly as collect. Guard against a second send on the same day (idempotent per date).
2. Score, select, write `digests` and `digest_items`.
3. Telegram:
   - a header message ("3 things today") **with** notification;
   - one message per item **with `disable_notification: true`**: headline linked to the best URL, the reason line, and an inline keyboard with thumbs up / thumbs down buttons carrying the `digest_items.id` in `callback_data`;
   - quiet day: a single "Nothing big today." message.
4. Escape all fetched text for Telegram's parse mode (or send plain text). Fetched titles are untrusted.
5. Snapshot current interest weights into `interest_history`.

### Telegram webhook (`POST /telegram/webhook`)

- Reject requests without the correct `X-Telegram-Bot-Api-Secret-Token` header.
- Ignore any update not from `TELEGRAM_CHAT_ID`.
- **Callback queries (votes)**: validate `callback_data` refers to an existing `digest_items.id`, record the vote (a later vote overwrites the earlier one), update weights, and call `answerCallbackQuery` so the button stops spinning.
- **Weight learning**: thumbs up raises the weights of interest terms matched by that item; thumbs down lowers them. Small learning rate, capped weights, so one vote nudges rather than rewrites. `manual` terms are never changed by learning.
- **Commands** (this is the interest editor; there is no web editor):
  - `/add <term> [weight]` and `/remove <term>`: validate (term 1–60 chars, weight 0–5, max 100 terms);
  - `/interests`: list current terms and weights;
  - `/missed <text>`: record something Signal failed to surface.
- Note: if the service is asleep, Telegram's delivery waits on the cold start. The keep-warm job should make this rare; test that votes still land after a cold start.

`scripts/set_webhook.py` registers the webhook URL with `secret_token` set.

## Public API (consumed by the portfolio frontend)

The frontend is plain HTML/CSS/JS on GitHub Pages. It will fetch from your API with a **3-second timeout** and hide the section on any error. Build the public endpoints to serve it well:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Keep-warm target. Fast, no DB |
| GET | `/digests?approved=true&limit=&before=` | Recent digests with their items. `approved=true` returns only items Elwin gave a thumbs up, omitting digests left with no items. Paginate with `before` (a date or id cursor). Cap `limit` |
| GET | `/digests/{id}` | One digest |
| GET | `/interests/history?days=` | Interest weights over time, for the archive page chart |

Requirements:

- **CORS**: allow only `PORTFOLIO_ORIGIN`, GET only.
- **Fast and small**: well under the 3-second budget when warm. Only the fields the page needs.
- Each item must include at least: a stable id, headline, url, source name, reason line, the digest date, and the vote. Use ISO 8601 timestamps with timezone.
- Define response shapes as Pydantic models in `schemas.py`; FastAPI's generated OpenAPI then documents them.
- Error responses are JSON with a consistent shape.
- Simple per-IP rate limit on public endpoints.
- Never expose tokens, raw feed payloads, or internal errors.

**Contract test (required):** add a test that calls `/digests?approved=true` against seeded test data and asserts the exact response shape (field names and types). If a later change renames or removes a field, this test must fail.

## Security checklist

- Bearer token on `/collect` and `/send`; secret-token header on the webhook; chat id check.
- Pydantic validation on every input; parameterized SQL only.
- Fetched content is untrusted: escape it wherever it is rendered, never execute or follow it beyond the source's own feed.
- CORS restricted to one origin.
- Secrets only in environment variables; `.env` gitignored from the first commit.

## Evaluation

- **Replay test**: the goal is that Signal would have surfaced the Claude Opus 5.5 release (late September 2026; confirm the exact date) as a morning item. Most feeds only hold recent entries, so `scripts/backfill_hn.py` should use the Algolia HN Search API (`https://hn.algolia.com/api/v1/search_by_date`, filtered by `created_at_i`) to backfill HN data for the surrounding weeks, then run the scorer as of that morning. Report honestly whether it would have ranked, and why.
- **Metrics** (expose via the archive endpoint or a small `/stats` endpoint): hit rate (share of sent items voted up), quiet-day rate, collect reliability (share of expected hourly runs that succeeded), and the count of `/missed` reports.

## Tests

- Entity extraction and alias merging (most tests go here).
- Spike score, including new entities and gap handling.
- Selection: threshold, no-repeat rule, quiet day.
- Collect idempotency (running twice adds nothing) and the advisory lock.
- Webhook auth, chat id filter, vote validation.
- The API contract test above.

## Milestones (in order)

1. **Skeleton**: FastAPI on Render with `/health`, Supabase connected, migrations applied, secrets set. Deployed.
2. **Collector**: HN + RSS ingestion, dedupe, entity extraction, hourly counts, `runs` logging, `/collect` with 202 + background task.
3. **Scorer and sender**: spike score, interest match, selection, Telegram digest via `/send`.
4. **Feedback loop**: webhook, votes, weight learning, commands.
5. **Public API**: `/digests`, `/interests/history`, CORS, contract test.
6. **Evaluation**: HN backfill, replay test, metrics.
7. **Docs and handoff**: README, SETUP.md, FRONTEND_HANDOFF.md.

Commit at each milestone with a clear message. Deploy and check each milestone before moving on.

## SETUP.md: what Elwin does by hand

Write exact, copy-pasteable steps for:

- creating the Supabase project and getting the connection string;
- creating the Render web service (build command, start command such as `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, env vars);
- creating the bot with BotFather, finding his chat id, running `set_webhook.py`;
- the three cron-job.org jobs:
  - keep-warm: `GET /health` every 10 minutes;
  - collect: `POST /collect` hourly, `Authorization: Bearer <CRON_TOKEN>` header;
  - send: `POST /send` daily at 07:00, timezone America/New_York, same header;
  - with failure email notifications turned on.

## Definition of done and the frontend handoff

You are done when every milestone is deployed and verified, and `FRONTEND_HANDOFF.md` is complete.

A template for `FRONTEND_HANDOFF.md` is provided. Fill in every section marked **TO BE FILLED BY BACKEND AGENT**, and follow these rules:

- **Write it from the live, deployed API, not from memory or intentions.** Every sample response must be real output from `curl` against the deployed Render URL.
- **Verify CORS from the real origin**: show a request with `Origin: <PORTFOLIO_ORIGIN>` and the response headers, and one from a different origin being refused.
- Include the real error responses (bad params, unknown id).
- Measure and report typical warm response time for `/digests?approved=true`.
- Save a real response as a mock JSON file the frontend can develop against, and include it verbatim in the handoff.
- If anything is not deployed and working, say so plainly in a "Known limitations" section. Do not describe it as done.
- Do not change the frontend requirements in the template. If the API can't meet one, say so in "Known limitations" and tell Elwin.

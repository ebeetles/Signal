# Signal

Signal is my personal morning brief: every morning, a few tech stories that suddenly got attention, delivered to Telegram.

Most news feeds rank by keywords or popularity. Signal ranks by **convergence**. It watches free sources (Hacker News, company blogs, changelogs, GitHub releases, tech news sites and YouTube channels) and notices when several independent outlets start talking about the same thing within hours. Stored history defines what "normal" looks like for each topic, so a sudden jump stands out, and my interest list decides which jumps I care about. I vote 👍 or 👎 on each item in Telegram; the votes tune my interests, and the 👍 items appear in a "Daily cool stuff" section on my portfolio.

When I replayed it against three weeks of past Hacker News data, it ranked the Claude Opus 5.5 release as the #1 item the morning after launch.

The backend is FastAPI on Render's free tier, with Supabase Postgres, hourly and daily jobs from cron-job.org, and the Telegram Bot API. It is deployed at **https://signal-yfj9.onrender.com** (interactive API docs at [/docs](https://signal-yfj9.onrender.com/docs)). The frontend lives in my portfolio's own repo.

## 1. What the backend does

Every hour, cron-job.org calls `/collect`, which fetches the sources, extracts entities from headlines, and stores hourly counts in Postgres (Supabase). Every morning, `/send` scores each entity by how far its last 24 hours stand out from its 14-day baseline, boosted by my interests, and sends the top items to Telegram. My 👍/👎 votes come back through the Telegram webhook.

| Method | Path | Parameters | Returns |
| --- | --- | --- | --- |
| GET | `/health` | none | `{"status": "ok"}` (keep-warm ping; no database) |
| GET | `/digests` | `approved` (bool, default `false`: only 👍 items), `limit` (1–30 digests, default 10), `before` (int cursor) | `{"digests": [...], "next_before": int or null}` |
| GET | `/digests/{id}` | `approved` (bool) | one digest, or 404 |
| GET | `/interests/history` | `days` (1–365, default 30) | `{"start", "end", "terms": [{"term", "origin", "current_weight", "points": [{"date", "weight"}]}]}` |
| GET | `/stats` | none | hit rate, quiet-day rate, collect reliability, missed-report count |
| POST | `/collect` | bearer token | `202 {"status": "accepted", "job": "collect"}`, then works in the background |
| POST | `/send` | bearer token | `202 {"status": "accepted", "job": "send"}`, at most once per day |
| POST | `/telegram/webhook` | Telegram update JSON | handles votes and the `/add`, `/remove`, `/interests`, `/missed` commands |

Each digest looks like this:

```json
{"id": 1, "date": "2026-09-25", "sent_at": "2026-09-25T19:43:01-04:00", "status": "sent",
 "items": [{"id": 2, "headline": "U.S. appeals court upholds designation of Anthropic as supply chain risk",
            "url": "https://www.cnbc.com/...", "source": "cnbc.com",
            "reason": "5 sources in 8h (no history yet), matches: anthropic, claude",
            "entity": "Anthropic", "digest_date": "2026-09-25", "vote": "up"}]}
```

Errors are JSON: `{"error": {"code": "...", "message": "..."}}`, with status 422 for bad parameters, 404 for an unknown id and 429 when rate-limited. Interactive docs are at `/docs`.

## 2. How the frontend communicates with the backend

The frontend is part of my portfolio, a separate repo on GitHub Pages (`https://ebeetles.github.io/elwin-webpage/`), built from [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md). It only makes public, read-only `GET` requests:

- **Homepage section**, on page load: `GET /digests?approved=true&limit=5`. It shows up to 5 approved items as linked headlines with their source, reason and a relative date. If the request fails or takes longer than 3 seconds, the section stays hidden.
- **Archive page**, on load: `GET /digests?approved=true&limit=10`. A "Load more" button requests the same URL with `&before=<next_before>` until `next_before` is `null`.
- **Interest chart**, on the archive page: `GET /interests/history?days=90`, drawn as one line per term.

Headlines are rendered as plain text, because they come from the web and are untrusted.

## 3. How to set up and run the backend

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
cp .env.example .env                  # then fill in the values below
.venv/bin/python -m app.migrate       # create the tables
.venv/bin/uvicorn app.main:app --reload
.venv/bin/python -m pytest            # tests use a separate local database (TEST_DATABASE_URL)
```

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Postgres connection string (Supabase session pooler in production) |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | My Telegram chat; the bot ignores every other chat |
| `TELEGRAM_WEBHOOK_SECRET` | Secret Telegram sends with each webhook call |
| `CRON_TOKEN` | Bearer token for `/collect` and `/send` |
| `PORTFOLIO_ORIGIN` | The one origin CORS allows: my portfolio, `https://ebeetles.github.io` |
| `TIMEZONE` | `America/New_York` |

No paid API keys are needed; every source is free. On Render, the build command is `pip install -r requirements.txt && python -m app.migrate` and the start command is `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. The account setup I did (Supabase, Render, Telegram, cron-job.org) is in [SETUP.md](SETUP.md).

## 4. How authentication and secrets are handled

- **Secrets stay on the backend.** I keep all tokens in Render's environment variables (locally, in `.env`, which is gitignored). None of them appear in the frontend or in git.
- **The frontend needs no key.** The public endpoints are read-only, allow CORS only from my portfolio's origin, and are rate-limited to 60 requests per minute per IP.
- **Write endpoints need secrets.** `/collect` and `/send` require `Authorization: Bearer <CRON_TOKEN>`, which only cron-job.org has. `/telegram/webhook` requires Telegram's secret-token header, and ignores messages from any chat except mine.
- **Inputs are validated** with Pydantic, SQL is parameterized, and fetched text is escaped before it is sent to Telegram.

---

More detail: [EVALUATION.md](EVALUATION.md) (would Signal have caught the Claude Opus 5.5 release?) · [prompt_log.md](prompt_log.md) (AI tools and prompts used).

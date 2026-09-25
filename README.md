# Signal

Signal is the backend for a personal morning brief. It watches free sources (Hacker News and RSS/Atom feeds) and detects when something suddenly gets attention from several independent sources at once. Every morning at 7:00 Eastern it sends 3–5 items to Telegram. Items marked 👍 appear publicly in the "Daily cool stuff" section of the portfolio site.

Status: under construction. This README describes only what is deployed and verified; see the milestone list below.

## Stack

FastAPI + uvicorn on Python 3.12, httpx, feedparser, psycopg 3 against Supabase Postgres, Pydantic v2 and scikit-learn (TF-IDF). It is hosted on a Render free web service and scheduled by cron-job.org. Everything runs on free tiers.

## Local development

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
DATABASE_URL=postgresql://localhost/signal .venv/bin/python -m app.migrate
.venv/bin/uvicorn app.main:app --reload
```

Manual setup of Supabase, Render, Telegram and cron-job.org is described in [SETUP.md](SETUP.md).

## Milestones

- [ ] 1. Skeleton: `/health` on Render, Supabase connected, migrations applied
- [ ] 2. Collector
- [ ] 3. Scorer and sender
- [ ] 4. Feedback loop
- [ ] 5. Public API
- [ ] 6. Evaluation
- [ ] 7. Docs and handoff

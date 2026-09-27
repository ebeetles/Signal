# Prompt log

Initial design decision and brainstorming full chat with claude chat: https://claude.ai/share/cd4f1280-e623-4031-81ef-8ba91bfe264a

## Tools and models

- **Claude Code** (Anthropic's coding agent, in the Claude desktop app), running **Claude Opus 5.5** (`claude-opus-5-5`). It wrote the backend code, tests, migrations and docs, ran the local and live verification, and filled in `FRONTEND_HANDOFF.md` from the deployed API.

## Key prompts

1. **Kickoff:**
   > "Please carefully read BACKEND_BRIEF.md and follow the instructions to build the backend of the Signal project and fill in the FRONTEND_HANDOFF.md file."

   The brief fixed the stack (FastAPI, Supabase through psycopg, Render, cron-job.org, Telegram), the data model, the pipelines, the spike formula, the security checklist, the milestones and the evaluation target (the Claude Opus 5.5 release).

2. **Clarifying questions the agent asked before coding, and Elwin's answers:**
   - CORS origin → `https://ebeetles.github.io` (the origin of the existing GitHub Pages portfolio).
   - How Hacker News counts toward "3 distinct sources" → each HN story counts as **the site it links to**. This shaped the `origin` column and made the HN-only replay test possible.
   - Pushing to the public repo → yes, push to `main`, one commit per milestone.
   - Account setup → done in parallel. The agent pushed the skeleton and SETUP.md first and kept building locally until `.env` was filled in.

3. **During deployment:**
   - Registering the Telegram webhook → "No, I'll run it myself".
   - Interests → Elwin entered them with `/add` commands in Telegram, which also checked the commands on the live service.
   - A test `/send` on day one → approved, so real approved items existed for the handoff samples.

4. **Follow-ups that changed things:**
   - "can the interests just be anything? like can i just /add ufc" → explained that interests only re-rank what the configured (tech) sources produce, and that sports coverage needs sports feeds in `seeds/sources.yaml`.
   - "i got a 401 for collect" / "i got a 405 Method Not Allowed for send" → troubleshooting the cron-job.org headers and HTTP method. No code changes.
   - "i actually put 8 am in the cron job, is that an issue?" → no code depends on the hour; the docs were updated to 08:00.

## Decisions the agent made and flagged for review

- **Learned terms:** a first 👍 creates a learned term from the entity's name (without its version), since otherwise the brief's `learned` origin would never be used.
- **`SCORE_THRESHOLD` lowered from 1.5 to 1.0** after the replay. This was tuned on the same week that contains the evaluation event; [EVALUATION.md](EVALUATION.md) documents it.
- **`/digests` default:** without `?approved=true` it returns all items, including 👎 ones. The frontend always sends `approved=true`.
- **Quiet-day message:** it uses Telegram's normal notification.

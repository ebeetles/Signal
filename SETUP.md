# Signal setup (manual steps)

These are the steps only you can do, because they need your accounts. Do them in order. Every value marked **secret** goes in two places: Render's Environment tab (production) and the gitignored `.env` file in this repo (local scripts). Never paste secrets into git, chat or issues.

`.env` already contains two generated secrets, `CRON_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`. Copy them into Render as they are.

Local commands below assume you are in the repo root with the virtualenv. If `.venv` doesn't exist yet:

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
```

## 1. Supabase (database)

1. Go to https://supabase.com/dashboard, then **New project**.
   - Name: `signal`
   - Database password: click **Generate a password** and save it somewhere safe (a password manager). If you type your own, use only letters and digits so it needs no URL-encoding.
   - Region: **East US (North Virginia)**, the same area as the Render service.
   - Plan: Free.
2. When the project is ready, click **Connect** in the top bar.
3. Under **Connection string**, choose **Session pooler** (not "Direct connection": Supabase's direct host is IPv6-only and Render's free tier connects over IPv4). Copy the URI. It looks like:
   `postgresql://postgres.<project-ref>:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres`
4. Replace `[YOUR-PASSWORD]` (brackets included) with the password from step 1. This is `DATABASE_URL` (**secret**). Put it in `.env`.

You don't need to create any tables. Render applies `migrations/*.sql` automatically during each deploy.

## 2. Telegram bot

1. In Telegram, open a chat with **@BotFather** and send `/newbot`. Pick a display name (e.g. `Signal`) and a username ending in `bot` (e.g. `elwin_signal_bot`).
2. BotFather replies with a token like `123456789:AA...`. This is `TELEGRAM_BOT_TOKEN` (**secret**). Put it in `.env`.
3. Open a chat with your new bot and send it any message, e.g. `hi`.
4. Find your chat id:

   ```bash
   .venv/bin/python -m scripts.get_chat_id
   ```

   It prints `TELEGRAM_CHAT_ID=<number>`. Put that line in `.env`. The bot only responds to this chat.

Do step 4 **before** registering the webhook in step 4 below: Telegram turns off `getUpdates` once a webhook exists.

## 3. Render (web service)

The repo contains `render.yaml`, so the fastest path is a Blueprint:

1. Go to https://dashboard.render.com, then **New** → **Blueprint**.
2. Connect GitHub if asked, and pick the repository **ebeetles/Signal**.
3. Render reads `render.yaml` and shows one free web service named `signal`. It asks for the `sync: false` values. Paste:
   - `DATABASE_URL`: from Supabase step 4
   - `TELEGRAM_BOT_TOKEN`: from BotFather
   - `TELEGRAM_CHAT_ID`: from `get_chat_id`
   - `TELEGRAM_WEBHOOK_SECRET`: from `.env`
   - `CRON_TOKEN`: from `.env`

   `PORTFOLIO_ORIGIN` (`https://ebeetles.github.io`) and `TIMEZONE` (`America/New_York`) are already filled in.
4. Click **Apply**. The first build installs dependencies and runs the migrations, which takes a few minutes.
5. Copy the service URL shown at the top of the service page (e.g. `https://signal-xxxx.onrender.com`) into `.env` as `RENDER_URL=`.

If you'd rather not use a Blueprint, choose **New** → **Web Service**, pick the repo, and set:

| Setting | Value |
| --- | --- |
| Language | Python 3 |
| Branch | `main` |
| Region | Virginia (US East) |
| Build command | `pip install -r requirements.txt && python -m app.migrate` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Instance type | Free |
| Health check path (Advanced) | `/health` |
| Environment variables | the seven above: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`, `CRON_TOKEN`, `PORTFOLIO_ORIGIN=https://ebeetles.github.io`, `TIMEZONE=America/New_York` |

The Python version comes from `.python-version` (3.12).

Check it's up:

```bash
curl -s https://<your-service>.onrender.com/health
```

It should return `{"status":"ok"}`.

If the build log shows a database connection error, check that you used the **Session pooler** string and replaced the password placeholder.

## 4. Register the Telegram webhook

With `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` and `RENDER_URL` in `.env`:

```bash
.venv/bin/python -m scripts.set_webhook
```

Expected output: `setWebhook -> https://.../telegram/webhook: Webhook was set` and `setMyCommands: ok`. Check it at any time with:

```bash
.venv/bin/python -m scripts.set_webhook --info
```

Then send `/interests` to your bot. It should reply (the list is empty at first). Add a few starting interests, for example:

```
/add Claude 2
/add Anthropic
/add Rust
```

## 5. cron-job.org (scheduler)

Create a free account at https://cron-job.org. Then go to **Settings** and set your timezone to `America/New_York`. Create three jobs with **Create cronjob**:

### Job 1: keep-warm

| Field | Value |
| --- | --- |
| Title | Signal keep-warm |
| URL | `https://<your-service>.onrender.com/health` |
| Execution schedule | Every 10 minutes |
| Advanced → Request method | GET |
| Notifications | On failure: on. (Failed executions trigger an email.) |

### Job 2: collect (hourly)

| Field | Value |
| --- | --- |
| Title | Signal collect |
| URL | `https://<your-service>.onrender.com/collect` |
| Execution schedule | Custom: `50 * * * *` (every hour at minute 50, so fresh data exists before the 08:00 send) |
| Advanced → Request method | POST |
| Advanced → Headers | Key `Authorization`, value `Bearer <CRON_TOKEN>` (the literal word `Bearer`, a space, then the token from `.env`) |
| Advanced → Timezone | America/New_York |
| Notifications | On failure: on |

### Job 3: send (daily)

| Field | Value |
| --- | --- |
| Title | Signal send |
| URL | `https://<your-service>.onrender.com/send` |
| Execution schedule | Every day at 08:00 (any morning time works; the digest date is the local date when it runs) |
| Advanced → Request method | POST |
| Advanced → Headers | Key `Authorization`, value `Bearer <CRON_TOKEN>` |
| Advanced → Timezone | America/New_York |
| Notifications | On failure: on |

Both POST endpoints answer `202 Accepted` right away and do the work in the background, so cron-job.org's 30-second timeout is never an issue once the service is warm. Use each job's **Test run** button once: you should see HTTP 202.

## Done

When all five sections are complete, the `.env` file should have every value filled in. Signal then collects hourly, sends at 08:00 Eastern, and learns from your 👍/👎.

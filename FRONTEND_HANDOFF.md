# Signal frontend handoff

You are adding a "Daily cool stuff" section to Elwin's portfolio site. The site is plain HTML, CSS and JavaScript, hosted on GitHub Pages. There is no build step and no framework; keep it that way.

## Background

Signal is a backend (separate repo, already deployed on Render) that watches tech sources, detects things suddenly getting attention, and sends Elwin a few items on Telegram each morning. He votes thumbs up or down on each. The items he approves appear publicly on his portfolio. Your job is only the portfolio side.

## What to build

1. **The section on the existing homepage**: an empty `<section>` placed where Elwin tells you (ask if unsure), plus one script, `daily.js`, that fills it.
   - Show approved items from the last few days: headline (linked), source name, reason line, and a relative date ("today", "yesterday", "3 days ago").
   - Keep it short: at most about 5 items. Link to the archive page at the bottom.
2. **An archive page, `daily.html`**: older digests (approved items only), paginated with a "load more" button, plus a chart of interest weights over time.
   - The chart should be dependency-free (inline SVG drawn in JS) or use at most one library loaded from a CDN. Ask Elwin before adding a library.
3. **A mock file** for local development: save the sample response below as `daily.mock.json` and add a simple switch (e.g. a `?mock` URL parameter) that makes `daily.js` read the mock instead of the live API.

## Requirements (fixed; do not change)

- **Only approved items.** Always request `approved=true`.
- **No `innerHTML` with fetched data.** Build elements with `document.createElement` and set text with `textContent`. Fetched headlines are untrusted.
- **Links**: set `href` only if the URL starts with `https://` or `http://`; add `rel="noopener noreferrer"` and `target="_blank"`.
- **Fail quietly.** Use `AbortController` with a **3-second timeout**. On timeout, network error, non-2xx status, malformed JSON or an empty result, keep the section hidden. The homepage must never look broken because of this section.
- **Don't block the page.** Load `daily.js` with `defer`; render the rest of the site normally regardless of the fetch.
- **Match the existing site.** Reuse its CSS variables, fonts and spacing. Add minimal new CSS, scoped to the section.
- **Accessible**: a real heading for the section, list markup for items, visible focus states, and sufficient contrast in both light and dark modes if the site supports both.
- **No secrets and no admin features** in the frontend. There is no API key; the public endpoints are read-only.
- **Don't change the API contract.** If you need something the API doesn't provide, stop and tell Elwin; the backend is changed first, then this handoff.

## API details

Everything in this section was captured from the deployed service on 2026-09-25 between 23:43 and 23:47 UTC. Samples are unedited, except that noise headers (`date`, `rndr-id`, `cf-ray`, `alt-svc`, `cf-cache-status`) are left out of the header dumps.

### Base URL

```
https://signal-yfj9.onrender.com
```

This is a single Render free web service behind Cloudflare. Interactive docs are at `/docs` and the machine-readable schema at `/openapi.json`.

### Endpoints used by the frontend

All are `GET`, need no authentication, and return `application/json`.

| Purpose | Request | Parameters | Returns |
| --- | --- | --- | --- |
| Homepage section | `GET /digests?approved=true&limit=5` | see below | `DigestPage` |
| Archive, first page | `GET /digests?approved=true&limit=10` | see below | `DigestPage` |
| Archive, next page | `GET /digests?approved=true&limit=10&before=<next_before>` | see below | `DigestPage` |
| Interest chart | `GET /interests/history?days=90` | `days` | `InterestHistory` |

Parameters of `GET /digests`:

| Param | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `approved` | boolean (`true`/`false`) | `false` | | **Always send `true`.** Only items Elwin voted 👍, and only digests that have at least one. |
| `limit` | integer | `10` | 1–30 | Digests per page, not items. A digest holds 1–5 items. |
| `before` | integer | none | ≥ 1 | Pagination cursor: the `next_before` value from the previous page. |

Parameters of `GET /interests/history`:

| Param | Type | Default | Limits | Meaning |
| --- | --- | --- | --- | --- |
| `days` | integer | `30` | 1–365 | How many days back from today (America/New_York), inclusive |

There is also `GET /digests/{id}?approved=true`, which returns one `DigestOut` (404 if unknown). The page doesn't need it.

### Response schema

**`DigestPage`**

| Field | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `digests` | array of `DigestOut` | no (may be `[]`) | Newest first |
| `next_before` | integer | **yes** | Pass as `?before=` to get the next, older page. **`null` means there are no more pages.** |

**`DigestOut`**

| Field | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `id` | integer | no | Digest id, which is also the pagination cursor |
| `date` | string `YYYY-MM-DD` | no | Local date (America/New_York) of that morning's digest |
| `sent_at` | string, ISO 8601 with UTC offset, e.g. `2026-09-25T19:43:01-04:00` | yes (never null for approved results in practice) | When it was sent |
| `status` | `"sent"` or `"quiet"` | no | With `approved=true`, always `"sent"` |
| `items` | array of `DigestItemOut` | no | In rank order, best first. With `approved=true`, never empty. |

**`DigestItemOut`** (the fields the section renders)

| Field | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `id` | integer | no | Stable id of the item; use it as a React-style key or DOM id |
| `headline` | string | no | Headline of the chosen article. **Untrusted text** fetched from the web: render with `textContent`. May contain typographic quotes or HTML-looking characters. |
| `url` | string | no | Link to the article. Signal only stores `http://` or `https://` URLs, but still check the prefix as the requirements say. |
| `source` | string | no | Display name of where the link is from: a feed name like `"TechCrunch AI"` or a site like `"cnbc.com"` |
| `reason` | string | no | Why it was picked, e.g. `"5 sources in 8h (no history yet), matches: anthropic, claude"`. Show as plain text. |
| `entity` | string | no | The thing that spiked, e.g. `"Anthropic"`. Optional to display. |
| `digest_date` | string `YYYY-MM-DD` | no | Same as the parent digest's `date`. Use it for the relative date ("today", "yesterday", "3 days ago"), computed in the viewer's local time. |
| `vote` | `"up"`, `"down"` or `null` | yes | With `approved=true` it is always `"up"` |

**`InterestHistory`**

| Field | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `start` | string `YYYY-MM-DD` | no | First day of the requested range |
| `end` | string `YYYY-MM-DD` | no | Today (America/New_York) |
| `terms` | array of `InterestSeries` | no (may be `[]`) | Current heaviest terms first; removed terms last |

**`InterestSeries`**

| Field | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `term` | string | no | Interest term as Elwin typed it (1–60 characters, untrusted-ish: use `textContent`) |
| `origin` | `"manual"` or `"learned"` | yes | `null` if the term has since been removed |
| `current_weight` | number, 0–5 | yes | `null` if removed |
| `points` | array of `{ "date": "YYYY-MM-DD", "weight": number }` | no | One point per day that had a snapshot, oldest first. **Days can be missing**; a snapshot is taken at each morning send. Don't assume one point per day. |

**Timestamps and dates.** `date` and `digest_date` are calendar dates in America/New_York. `sent_at` is ISO 8601 with an explicit offset (`-04:00` or `-05:00`); `new Date(sent_at)` parses it correctly.

**Pagination.** Request with `limit`. If `next_before` is a number, request again with `before=<that number>`. Stop when `next_before` is `null`. Pages never overlap: `before` is exclusive and pages are ordered by `id` descending.

### Live sample responses

**Approved digests: the exact request the homepage section should make.**

```
$ curl 'https://signal-yfj9.onrender.com/digests?approved=true&limit=5' -i -H 'Origin: https://ebeetles.github.io'
HTTP/2 200
content-type: application/json
access-control-allow-origin: https://ebeetles.github.io
cache-control: public, max-age=60
server: cloudflare
vary: Origin
vary: Accept-Encoding
x-render-origin-server: uvicorn

{"digests":[{"id":1,"date":"2026-09-25","sent_at":"2026-09-25T19:43:01-04:00","status":"sent","items":[{"id":1,"headline":"Unsecured OpenAI agents posted 53 user images on the internet without the lab’s knowledge","url":"https://techcrunch.com/2026/09/25/unsecured-openai-agents-posted-53-user-images-on-the-internet-without-the-labs-knowledge/","source":"TechCrunch AI","reason":"7 sources in 15h (no history yet), matches: openAI","entity":"OpenAI","digest_date":"2026-09-25","vote":"up"},{"id":2,"headline":"U.S. appeals court upholds designation of Anthropic as supply chain risk","url":"https://www.cnbc.com/2026/09/25/pentagon-anthropic-ai-risk-appeals-court.html","source":"cnbc.com","reason":"5 sources in 8h (no history yet), matches: anthropic, claude","entity":"Anthropic","digest_date":"2026-09-25","vote":"up"}]}],"next_before":null}
```

**Archive, first page** (`limit=1` so the cursor shows):

```
$ curl 'https://signal-yfj9.onrender.com/digests?approved=true&limit=1'
{"digests":[{"id":1,"date":"2026-09-25","sent_at":"2026-09-25T19:43:01-04:00","status":"sent","items":[{"id":1,"headline":"Unsecured OpenAI agents posted 53 user images on the internet without the lab’s knowledge","url":"https://techcrunch.com/2026/09/25/unsecured-openai-agents-posted-53-user-images-on-the-internet-without-the-labs-knowledge/","source":"TechCrunch AI","reason":"7 sources in 15h (no history yet), matches: openAI","entity":"OpenAI","digest_date":"2026-09-25","vote":"up"},{"id":2,"headline":"U.S. appeals court upholds designation of Anthropic as supply chain risk","url":"https://www.cnbc.com/2026/09/25/pentagon-anthropic-ai-risk-appeals-court.html","source":"cnbc.com","reason":"5 sources in 8h (no history yet), matches: anthropic, claude","entity":"Anthropic","digest_date":"2026-09-25","vote":"up"}]}],"next_before":null}
```

`next_before` is `null` because only one digest exists so far (see Known limitations). When there are more, the next page is requested like this:

```
$ curl 'https://signal-yfj9.onrender.com/digests?approved=true&limit=1&before=<next_before from the previous page>'
```

That pattern is covered by `tests/test_api_public.py::test_pagination_reaches_end_without_duplicates`.

**Interest history:**

```
$ curl 'https://signal-yfj9.onrender.com/interests/history?days=30'
{"start":"2026-08-27","end":"2026-09-25","terms":[{"term":"anthropic","origin":"manual","current_weight":1.0,"points":[{"date":"2026-09-25","weight":1.0}]},{"term":"claude","origin":"manual","current_weight":1.0,"points":[{"date":"2026-09-25","weight":1.0}]},{"term":"gemini","origin":"manual","current_weight":1.0,"points":[{"date":"2026-09-25","weight":1.0}]},{"term":"openAI","origin":"manual","current_weight":1.0,"points":[{"date":"2026-09-25","weight":1.0}]}]}
```

### Mock file

Save this as `daily.mock.json`. It is the real `/digests?approved=true` response shown above, pretty-printed and otherwise unchanged. The same file is in the backend repo at `docs/daily.mock.json`.

```json
{
  "digests": [
    {
      "id": 1,
      "date": "2026-09-25",
      "sent_at": "2026-09-25T19:43:01-04:00",
      "status": "sent",
      "items": [
        {
          "id": 1,
          "headline": "Unsecured OpenAI agents posted 53 user images on the internet without the lab’s knowledge",
          "url": "https://techcrunch.com/2026/09/25/unsecured-openai-agents-posted-53-user-images-on-the-internet-without-the-labs-knowledge/",
          "source": "TechCrunch AI",
          "reason": "7 sources in 15h (no history yet), matches: openAI",
          "entity": "OpenAI",
          "digest_date": "2026-09-25",
          "vote": "up"
        },
        {
          "id": 2,
          "headline": "U.S. appeals court upholds designation of Anthropic as supply chain risk",
          "url": "https://www.cnbc.com/2026/09/25/pentagon-anthropic-ai-risk-appeals-court.html",
          "source": "cnbc.com",
          "reason": "5 sources in 8h (no history yet), matches: anthropic, claude",
          "entity": "Anthropic",
          "digest_date": "2026-09-25",
          "vote": "up"
        }
      ]
    }
  ],
  "next_before": null
}
```

For the chart's mock mode, the interest-history response above can be saved as `daily-history.mock.json` in the same way.

### Error responses

Every error has the same JSON shape:

```json
{"error": {"code": "<machine code>", "message": "<human message>", "details": [{"field": "<param>", "message": "<why>"}]}}
```

`details` appears only on `invalid_request`. The codes are `invalid_request` (422), `not_found` (404), `rate_limited` (429), `unavailable` (503, database unreachable) and `internal_error` (500). For the section, treat any non-2xx status as "hide".

Invalid parameters (real output):

```
$ curl -i 'https://signal-yfj9.onrender.com/digests?approved=true&limit=100'
HTTP/2 422
content-type: application/json

{"error":{"code":"invalid_request","message":"Invalid request parameters","details":[{"field":"limit","message":"Input should be less than or equal to 30"}]}}

$ curl 'https://signal-yfj9.onrender.com/digests?approved=maybe'
{"error":{"code":"invalid_request","message":"Invalid request parameters","details":[{"field":"approved","message":"Input should be a valid boolean, unable to interpret input"}]}}

$ curl 'https://signal-yfj9.onrender.com/digests?before=abc'
{"error":{"code":"invalid_request","message":"Invalid request parameters","details":[{"field":"before","message":"Input should be a valid integer, unable to parse string as an integer"}]}}

$ curl 'https://signal-yfj9.onrender.com/interests/history?days=0'
{"error":{"code":"invalid_request","message":"Invalid request parameters","details":[{"field":"days","message":"Input should be greater than or equal to 1"}]}}
```

Unknown id:

```
$ curl -i 'https://signal-yfj9.onrender.com/digests/999999'
HTTP/2 404
content-type: application/json

{"error":{"code":"not_found","message":"Digest 999999 not found"}}
```

Rate-limited request. The limit is 60 requests per 60 seconds per client IP, on all public endpoints. A page load makes one or two requests, so visitors won't hit it.

```
$ for i in $(seq 60); do curl -s -o /dev/null 'https://signal-yfj9.onrender.com/stats'; done
$ curl -i 'https://signal-yfj9.onrender.com/digests?approved=true'
HTTP/2 429
content-type: application/json
retry-after: 43
server: cloudflare

{"error":{"code":"rate_limited","message":"Too many requests; limit is 60 per 60s"}}
```

### CORS

Allowed origin: **`https://ebeetles.github.io`** (the origin of `https://ebeetles.github.io/elwin-webpage/`). Only `GET` is allowed, with no credentials. The frontend's plain `fetch(url, { signal })` is a simple request and needs no preflight.

From the portfolio origin, the allow header is present:

```
$ curl -s -o /dev/null -D - 'https://signal-yfj9.onrender.com/digests?approved=true' -H 'Origin: https://ebeetles.github.io'
HTTP/2 200
content-type: application/json
access-control-allow-origin: https://ebeetles.github.io
cache-control: public, max-age=60
server: cloudflare
vary: Origin
vary: Accept-Encoding
x-render-origin-server: uvicorn
```

From another origin, there is no `access-control-allow-origin` header, so the browser blocks the response:

```
$ curl -s -o /dev/null -D - 'https://signal-yfj9.onrender.com/digests?approved=true' -H 'Origin: https://evil.example.com'
HTTP/2 200
content-type: application/json
cache-control: public, max-age=60
server: cloudflare
vary: Origin
vary: Accept-Encoding
x-render-origin-server: uvicorn
```

A preflight from another origin is refused:

```
$ curl -si -X OPTIONS 'https://signal-yfj9.onrender.com/digests' -H 'Origin: https://evil.example.com' -H 'Access-Control-Request-Method: GET'
HTTP/2 400
content-type: text/plain; charset=utf-8
access-control-allow-methods: GET
access-control-max-age: 600

Disallowed CORS origin
```

Local development from `http://localhost` or `file://` is **not** an allowed origin, so use the mock mode there.

### Performance

The homepage request, `GET /digests?approved=true`, was measured with 20 consecutive warm requests from Philadelphia (Cloudflare PHL edge):

```
$ for i in $(seq 20); do curl -s -o /dev/null -w '%{http_code} %{time_total}s %{size_download}B\n' 'https://signal-yfj9.onrender.com/digests?approved=true'; done
200 0.092784s 848B
200 0.090972s 848B
200 0.108189s 848B
200 0.097367s 848B
200 0.103632s 848B
200 0.099648s 848B
200 0.087953s 848B
200 0.085583s 848B
200 0.088019s 848B
200 0.100631s 848B
200 0.094759s 848B
200 0.090260s 848B
200 0.107835s 848B
200 0.088911s 848B
200 0.123423s 848B
200 0.088566s 848B
200 0.087645s 848B
200 0.094345s 848B
200 0.097166s 848B
200 0.091749s 848B
```

- **Warm response time: about 0.09–0.12 s in total (median ≈ 0.09 s)**, far inside the 3-second budget.
- **Size: 848 bytes uncompressed** for one digest with two items. Responses over 1 KB are gzip-compressed (`Accept-Encoding: gzip`, which browsers send automatically). Expect roughly 400 bytes per item.
- `Cache-Control: public, max-age=60`: the browser may reuse a response for up to a minute.
- **Cold start.** Render's free tier stops the service after 15 minutes without traffic, and waking it takes about a minute. A cron job pings `/health` every 10 minutes to prevent that, so the service should always be warm. If it isn't, the first request takes longer than 3 seconds, the frontend's `AbortController` fires, and the section stays hidden for that page view. That is the intended behavior; don't retry in a loop.

### Known limitations

- **Only one digest exists yet** (2026-09-25, the first live send, with 2 approved items). A real response with a second page couldn't be captured, which is why `next_before` is `null` in every sample above. Pagination is covered by automated tests, and new digests arrive every morning at 07:00 America/New_York, so the archive grows daily. Some mornings are "quiet" (nothing sent); those never appear with `approved=true`.
- **Interest history has a single day of points** so far (one snapshot per morning send). The chart must handle 0 or 1 points per term, and missing days.
- **`reason` text will change as history builds.** For the first 14 days it says "(no history yet)"; after that it says "(usually N)". Treat it as opaque display text.
- **The preflight rejection body is plain text, not JSON** (`Disallowed CORS origin`). It comes from the CORS layer and only affects preflights from other origins, which this frontend never sends.
- **Items have no article publish time.** The relative date comes from `digest_date` (the morning the item was sent), not from when the article was published.
- The cold-start time was not measured directly, since the keep-warm job keeps the service up. The figure above is Render's documented behavior.

## Acceptance checklist (frontend agent)

- [ ] Section renders real approved items from the live API.
- [ ] Section renders from `daily.mock.json` in mock mode.
- [ ] With the API unreachable (e.g. a wrong base URL), the section stays hidden and the page has no visible errors.
- [ ] With a forced 3-second delay, the request aborts and the section stays hidden.
- [ ] A headline containing `<script>` or HTML tags renders as plain text.
- [ ] Archive page paginates to the end without duplicates.
- [ ] Chart renders from interest history, and degrades gracefully with little or no data.
- [ ] Looks consistent with the rest of the site on mobile and desktop.
- [ ] No console errors on the homepage.

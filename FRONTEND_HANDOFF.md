# Signal frontend handoff

> **Status: TEMPLATE.** The backend agent completes every section marked **TO BE FILLED BY BACKEND AGENT** from the live, deployed API, then removes this note. Until then, nothing in the API sections is guaranteed. Frontend agent: if you still see this note, stop and tell Elwin the handoff isn't finished.

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

### Base URL

**TO BE FILLED BY BACKEND AGENT**: the deployed Render URL.

### Endpoints used by the frontend

**TO BE FILLED BY BACKEND AGENT**: a table of the exact endpoints, query parameters (with types, defaults and limits) and what each returns. At minimum: approved digests for the section, paginated digests for the archive, and interest history for the chart.

### Response schema

**TO BE FILLED BY BACKEND AGENT**: every field the frontend may use, with its type, whether it can be null, and its meaning. Timestamps and their timezone format. How pagination works (which field to pass back as the cursor, and how the end is signaled).

### Live sample responses

**TO BE FILLED BY BACKEND AGENT**: real `curl` commands against the deployed URL and their real output, unedited except for truncating long arrays (mark where truncated). Include:

- approved digests (the exact request the homepage section should make);
- one page of the archive and the request for the next page;
- interest history.

### Mock file

**TO BE FILLED BY BACKEND AGENT**: the exact contents for `daily.mock.json`, taken from a real response.

### Error responses

**TO BE FILLED BY BACKEND AGENT**: real output for invalid parameters, an unknown id, and a rate-limited request. The error JSON shape.

### CORS

**TO BE FILLED BY BACKEND AGENT**: the allowed origin, and real verification output: a request with the portfolio's `Origin` header showing the `Access-Control-Allow-Origin` response header, and a request from a different origin showing it is refused.

### Performance

**TO BE FILLED BY BACKEND AGENT**: measured warm response time for the homepage request, and response size. What happens on a cold start (the service is kept warm, but say what the frontend should expect if it isn't).

### Known limitations

**TO BE FILLED BY BACKEND AGENT**: anything not deployed, not working, or behaving differently from this document. If there is nothing, write "None".

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

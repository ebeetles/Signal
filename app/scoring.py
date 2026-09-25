"""Spike score, interest match, and selection.

For an as-of time T the window is the 24 hours before T (aligned to the
hour). Each entity's weighted count in the window is compared with the same
count on each of the previous 14 days:

    spike(e) = (c_24h(e) - mean_14d(e)) / (std_14d(e) + 1)

Baseline days only use hours covered by a successful collect run. A day
with fewer than MIN_COVERED_HOURS_PER_DAY covered hours is skipped; a
partly covered day is scaled up to 24 hours. Missing hours are missing
data, never zero mentions.

The pure functions at the top are unit-tested directly; the async loaders
at the bottom read the database.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import psycopg

from app.config import (
    BASELINE_DAYS,
    CANDIDATE_LIMIT,
    INTEREST_FLOOR,
    MATCH_MIN,
    MIN_COVERED_HOURS_PER_DAY,
    MIN_DISTINCT_SOURCES,
    NO_REPEAT_DAYS,
    OVERLAP_MAX,
    REPEAT_SPIKE_MULTIPLIER,
    SCORE_THRESHOLD,
    TOP_N,
    WINDOW_HOURS,
)

HOUR = timedelta(hours=1)
HN_ORIGIN = "news.ycombinator.com"


# --- data -------------------------------------------------------------------


@dataclass(frozen=True)
class ItemRow:
    id: int
    title: str
    link_url: str
    origin: str
    published_at: datetime
    source_name: str
    source_type: str
    is_primary: bool
    weight: float

    @property
    def display_source(self) -> str:
        """HN stories are credited to the site they link to."""
        if self.source_type == "hn" and self.origin != HN_ORIGIN:
            return self.origin
        return self.source_name


@dataclass
class Scored:
    entity_id: int
    name: str
    items: list[ItemRow]
    n_origins: int
    first_seen: datetime
    c24: float = 0.0
    mean: float = 0.0
    std: float = 0.0
    baseline_days: int = 0
    spike: float = 0.0
    interest: float = 0.0
    matched: list[str] = field(default_factory=list)
    score: float = 0.0
    best: ItemRow | None = None
    reason: str = ""


# --- pure functions ---------------------------------------------------------------


def floor_hour(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def window_bounds(as_of: datetime) -> tuple[datetime, datetime, datetime]:
    """(baseline_start, window_start, window_end)."""
    end = floor_hour(as_of)
    start = end - timedelta(hours=WINDOW_HOURS)
    return start - timedelta(days=BASELINE_DAYS), start, end


def spike_stats(
    hourly: dict[datetime, float], covered: set[datetime], window_end: datetime
) -> tuple[float, float, float, int]:
    """(c_24h, mean_14d, std_14d, baseline days used) for one entity.

    hourly: hour bucket -> weighted count (hours without mentions absent).
    covered: hour buckets that had a successful collect run.
    """
    window_start = window_end - timedelta(hours=WINDOW_HOURS)
    c24 = sum(v for h, v in hourly.items() if window_start <= h < window_end)
    daily: list[float] = []
    for k in range(1, BASELINE_DAYS + 1):
        day_end = window_start - timedelta(hours=WINDOW_HOURS * (k - 1))
        hours = [day_end - HOUR * (i + 1) for i in range(WINDOW_HOURS)]
        good = [h for h in hours if h in covered]
        if len(good) < MIN_COVERED_HOURS_PER_DAY:
            continue
        total = sum(hourly.get(h, 0.0) for h in good)
        daily.append(total * WINDOW_HOURS / len(good))
    if not daily:
        return c24, 0.0, 0.0, 0
    mean = sum(daily) / len(daily)
    std = math.sqrt(sum((d - mean) ** 2 for d in daily) / len(daily))
    return c24, mean, std, len(daily)


def spike_score(c24: float, mean: float, std: float) -> float:
    return (c24 - mean) / (std + 1.0)


_TOKEN = r"(?u)[\w][\w.+#]*[\w+#]|\w"


def _prep(text: str) -> str:
    return re.sub(r"[-‐‑]", " ", text.lower())


def interest_scores(docs: dict[int, str], terms: list[tuple[str, float]]) -> dict[int, tuple[float, list[str]]]:
    """TF-IDF cosine similarity between each doc and each interest term,
    weighted by term weight. Returns id -> (interest, matched terms)."""
    active = [(t, w) for t, w in terms if w > 0 and t.strip()]
    if not docs or not active:
        return {i: (0.0, []) for i in docs}
    from sklearn.feature_extraction.text import TfidfVectorizer  # imported lazily: keeps idle memory low

    ids = list(docs)
    corpus = [_prep(docs[i]) for i in ids] + [_prep(t) for t, _ in active]
    vec = TfidfVectorizer(token_pattern=_TOKEN, ngram_range=(1, 2), sublinear_tf=True)
    matrix = vec.fit_transform(corpus)
    sims = (matrix[: len(ids)] @ matrix[len(ids) :].T).toarray()
    out: dict[int, tuple[float, list[str]]] = {}
    for row, eid in enumerate(ids):
        contrib = [(w * float(sims[row, col]), t) for col, (t, w) in enumerate(active)]
        total = sum(c for c, _ in contrib)
        matched = [t for c, t in sorted(contrib, reverse=True) if c >= MATCH_MIN]
        out[eid] = (total, matched)
    return out


_PRIMARY_PATH = re.compile(r"/(releases?|changelog|release-notes)(/|$)", re.I)


def is_primary_link(item: ItemRow, primary_domains: set[str]) -> bool:
    if item.is_primary:
        return True
    host = (urlsplit(item.link_url).hostname or "").lower().removeprefix("www.")
    if any(host == d or host.endswith("." + d) for d in primary_domains):
        return True
    return bool(_PRIMARY_PATH.search(urlsplit(item.link_url).path))


def best_item(items: list[ItemRow], primary_domains: set[str]) -> ItemRow:
    """Prefer a first-party link (earliest); otherwise the link most
    independent weight points at (earliest on ties)."""
    primary = [i for i in items if is_primary_link(i, primary_domains)]
    if primary:
        return min(primary, key=lambda i: (i.published_at, -i.weight, i.id))
    weight_by_link: dict[str, float] = {}
    for i in items:
        weight_by_link[i.link_url] = weight_by_link.get(i.link_url, 0.0) + i.weight
    return min(items, key=lambda i: (-weight_by_link[i.link_url], -i.weight, i.published_at, i.id))


def reason_line(s: Scored, window_end: datetime) -> str:
    hours = max(1, min(WINDOW_HOURS, math.ceil((window_end - s.first_seen) / HOUR)))
    usual = f"usually {round(s.mean)}" if s.baseline_days else "no history yet"
    line = f"{s.n_origins} sources in {hours}h ({usual})"
    if s.matched:
        line += ", matches: " + ", ".join(s.matched[:3])
    return line


def overlap(a: Scored, b: Scored) -> float:
    ia, ib = {i.id for i in a.items}, {i.id for i in b.items}
    if not ia or not ib:
        return 0.0
    return len(ia & ib) / min(len(ia), len(ib))


def select(candidates: list[Scored], recent_spikes: dict[int, float]) -> list[Scored]:
    """Top TOP_N above SCORE_THRESHOLD, skipping recent repeats (unless the
    spike at least doubled) and near-duplicates of a better pick."""
    picked: list[Scored] = []
    for c in sorted(candidates, key=lambda s: (-s.score, s.entity_id)):
        if c.score < SCORE_THRESHOLD or len(picked) >= TOP_N:
            break
        prev = recent_spikes.get(c.entity_id)
        if prev is not None and c.spike < REPEAT_SPIKE_MULTIPLIER * prev:
            continue
        if any(overlap(c, p) > OVERLAP_MAX for p in picked):
            continue
        picked.append(c)
    return picked


# --- database loaders ---------------------------------------------------------------


async def covered_hours(conn: psycopg.AsyncConnection, start: datetime, end: datetime) -> set[datetime]:
    rows = await (
        await conn.execute(
            """
            select distinct date_trunc('hour', started_at) as h from runs
            where kind in ('collect', 'backfill') and status in ('success', 'partial')
              and started_at >= %s and started_at < %s
            """,
            (start, end),
        )
    ).fetchall()
    return {r["h"] for r in rows}


async def load_candidates(conn: psycopg.AsyncConnection, start: datetime, end: datetime) -> list[Scored]:
    rows = await (
        await conn.execute(
            """
            select m.entity_id, e.name, count(distinct i.origin) as n_origins,
                   min(i.published_at) as first_seen, count(*) as n_items
            from mentions m
            join items i on i.id = m.item_id
            join entities e on e.id = m.entity_id
            where i.published_at >= %(start)s and i.published_at < %(end)s
            group by m.entity_id, e.name
            having count(distinct i.origin) >= %(min)s
            order by count(distinct i.origin) desc, count(*) desc
            limit %(limit)s
            """,
            {"start": start, "end": end, "min": MIN_DISTINCT_SOURCES, "limit": CANDIDATE_LIMIT},
        )
    ).fetchall()
    cands = {
        r["entity_id"]: Scored(r["entity_id"], r["name"], [], r["n_origins"], r["first_seen"]) for r in rows
    }
    if not cands:
        return []
    item_rows = await (
        await conn.execute(
            """
            select m.entity_id, i.id, i.title, i.link_url, i.origin, i.published_at,
                   s.name as source_name, s.type as source_type, s.is_primary,
                   s.independence_weight as weight
            from mentions m
            join items i on i.id = m.item_id
            join sources s on s.id = i.source_id
            where m.entity_id = any(%s) and i.published_at >= %s and i.published_at < %s
            order by i.published_at
            """,
            (list(cands), start, end),
        )
    ).fetchall()
    for r in item_rows:
        eid = r.pop("entity_id")
        cands[eid].items.append(ItemRow(**r))
    return list(cands.values())


async def load_hourly(conn, entity_ids: list[int], start: datetime, end: datetime) -> dict[int, dict[datetime, float]]:
    rows = await (
        await conn.execute(
            """
            select entity_id, hour_bucket, weighted_count from entity_counts
            where entity_id = any(%s) and hour_bucket >= %s and hour_bucket < %s
            """,
            (entity_ids, start, end),
        )
    ).fetchall()
    out: dict[int, dict[datetime, float]] = {}
    for r in rows:
        out.setdefault(r["entity_id"], {})[r["hour_bucket"]] = float(r["weighted_count"])
    return out


async def load_interests(conn) -> list[tuple[str, float]]:
    rows = await (await conn.execute("select term, weight from interests order by term")).fetchall()
    return [(r["term"], float(r["weight"])) for r in rows]


async def recent_spikes(conn, digest_date, days: int = NO_REPEAT_DAYS) -> dict[int, float]:
    rows = await (
        await conn.execute(
            """
            select di.entity_id, max(di.spike) as spike
            from digest_items di join digests d on d.id = di.digest_id
            where d.status = 'sent' and d.digest_date >= %s and d.digest_date < %s
              and di.entity_id is not null
            group by di.entity_id
            """,
            (digest_date - timedelta(days=days), digest_date),
        )
    ).fetchall()
    return {r["entity_id"]: float(r["spike"]) for r in rows}


async def score_all(
    conn: psycopg.AsyncConnection,
    as_of: datetime,
    primary_domains: set[str] | None = None,
    interests: list[tuple[str, float]] | None = None,
) -> tuple[list[Scored], datetime]:
    """Score every candidate entity as of `as_of`. Returns (scored, window_end)."""
    baseline_start, start, end = window_bounds(as_of)
    cands = await load_candidates(conn, start, end)
    if not cands:
        return [], end
    hourly = await load_hourly(conn, [c.entity_id for c in cands], baseline_start, end)
    covered = await covered_hours(conn, baseline_start, end)
    if interests is None:
        interests = await load_interests(conn)
    primary_domains = primary_domains if primary_domains is not None else set()

    docs = {c.entity_id: " ".join([c.name] + [i.title for i in c.items]) for c in cands}
    matches = interest_scores(docs, interests)
    for c in cands:
        c.c24, c.mean, c.std, c.baseline_days = spike_stats(hourly.get(c.entity_id, {}), covered, end)
        c.spike = spike_score(c.c24, c.mean, c.std)
        c.interest, c.matched = matches.get(c.entity_id, (0.0, []))
        c.score = c.spike * (INTEREST_FLOOR + c.interest)
        c.best = best_item(c.items, primary_domains)
        c.reason = reason_line(c, end)
    cands.sort(key=lambda s: (-s.score, s.entity_id))
    return cands, end

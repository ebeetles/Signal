"""Run the scorer as of a past morning and show what Signal would have sent.

Usage:
    python -m scripts.replay --as-of 2026-09-23T07:00:00-04:00 [--interests "Claude:1,Rust:1"] \
        [--target "opus 5.5"] [--top 15] [--database-url URL]

Uses the real scoring and selection code (no no-repeat history). Without
--interests it uses the interests table. --target names an entity key to
report on explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from app.config import INTEREST_FLOOR, MIN_DISTINCT_SOURCES, SCORE_THRESHOLD, TOP_N
from app.extract import canonical_key_for_phrase
from app.scoring import score_all, select
from app.send import load_primary_domains


def parse_interests(text: str | None) -> list[tuple[str, float]] | None:
    if text is None:
        return None
    out = []
    for part in text.split(","):
        if part.strip():
            term, _, weight = part.partition(":")
            out.append((term.strip(), float(weight or 1)))
    return out


async def replay(database_url: str, as_of: datetime, interests, target: str | None, top: int) -> dict:
    async with await psycopg.AsyncConnection.connect(
        database_url, prepare_threshold=None, row_factory=dict_row
    ) as conn:
        await conn.execute("set time zone 'UTC'")
        scored, end = await score_all(conn, as_of, load_primary_domains(), interests)
        picks = select(scored, {})
        target_key = canonical_key_for_phrase(target) if target else None
        target_row = None
        if target_key:
            target_row = await (
                await conn.execute("select id, name from entities where norm = %s", (target_key,))
            ).fetchone()

    print(f"As of {as_of.isoformat()}  (window ends {end.isoformat()})")
    print(f"Interests: {interests if interests is not None else 'from database'}")
    print(f"Candidates with >= {MIN_DISTINCT_SOURCES} origins: {len(scored)}; "
          f"threshold {SCORE_THRESHOLD}, floor {INTEREST_FLOOR}, top {TOP_N}\n")
    print(f"{'#':>2} {'sent':4} {'score':>6} {'spike':>6} {'c24':>5} {'mean':>5} {'std':>5} {'int':>5}  entity / reason / link")
    picked_ids = {p.entity_id for p in picks}
    for n, s in enumerate(scored[:top], start=1):
        mark = "YES" if s.entity_id in picked_ids else ""
        print(f"{n:>2} {mark:4} {s.score:6.2f} {s.spike:6.2f} {s.c24:5.1f} {s.mean:5.2f} {s.std:5.2f} {s.interest:5.2f}  "
              f"{s.name}")
        print(f"{'':44}{s.reason}")
        print(f"{'':44}{s.best.display_source}: {s.best.title[:70]}  <{s.best.link_url}>")
    print(f"\nWould send {len(picks)} item(s): {[p.name for p in picks] or 'quiet day'}")

    result = {"picks": [p.name for p in picks], "target_rank": None, "target_sent": False}
    if target_key:
        rank = next((i for i, s in enumerate(scored, start=1) if target_row and s.entity_id == target_row["id"]), None)
        result["target_rank"] = rank
        result["target_sent"] = bool(target_row and target_row["id"] in picked_ids)
        if rank is None:
            print(f"\nTarget {target!r} (key {target_key!r}): not a candidate "
                  f"({'no such entity' if not target_row else 'fewer than ' + str(MIN_DISTINCT_SOURCES) + ' origins in window'})")
        else:
            s = scored[rank - 1]
            print(f"\nTarget {target!r}: rank {rank} of {len(scored)}, score {s.score:.2f}, "
                  f"spike {s.spike:.2f}, {'SENT' if result['target_sent'] else 'not sent'}")
    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", required=True, help="ISO timestamp with offset, e.g. 2026-09-23T07:00:00-04:00")
    ap.add_argument("--interests", help='e.g. "Claude:1,Rust:1"; omit to use the interests table; "" for none')
    ap.add_argument("--target", help="entity to report on, e.g. 'Claude Opus 5.5'")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args(argv)
    if not args.database_url:
        print("Set DATABASE_URL or pass --database-url")
        return 1
    as_of = datetime.fromisoformat(args.as_of)
    asyncio.run(replay(args.database_url, as_of, parse_interests(args.interests), args.target, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

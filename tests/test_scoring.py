"""Spike score (incl. new entities and gaps), interest match, selection, best link."""

from datetime import timedelta

import pytest

from app import config
from app.scoring import (
    ItemRow,
    Scored,
    best_item,
    interest_scores,
    reason_line,
    select,
    spike_score,
    spike_stats,
    window_bounds,
)
from tests.helpers import utc

END = utc(2026, 9, 23, 11)  # 07:00 ET
H = timedelta(hours=1)


def all_hours(days=15):
    return {END - H * (i + 1) for i in range(24 * days)}


# --- spike ----------------------------------------------------------------


def test_window_bounds_align_to_hour():
    base, start, end = window_bounds(utc(2026, 9, 23, 11, 0, 42))
    assert end == END and start == END - 24 * H and base == start - timedelta(days=14)


def test_new_entity_spike_equals_count():
    hourly = {END - 2 * H: 3.0, END - 5 * H: 2.0}
    c24, mean, std, days = spike_stats(hourly, all_hours(), END)
    assert (c24, mean, std, days) == (5.0, 0.0, 0.0, 14)
    assert spike_score(c24, mean, std) == 5.0


def test_no_history_at_all_means_zero_baseline():
    c24, mean, std, days = spike_stats({END - H: 4.0}, set(), END)
    assert (c24, mean, std, days) == (4.0, 0.0, 0.0, 0)


def test_steady_entity_has_no_spike():
    # 2 mentions every day at the same hour, including today
    hourly = {END - H * (3 + 24 * d): 2.0 for d in range(15)}
    c24, mean, std, days = spike_stats(hourly, all_hours(), END)
    assert c24 == 2.0 and mean == pytest.approx(2.0) and std == pytest.approx(0.0)
    assert spike_score(c24, mean, std) == pytest.approx(0.0)


def test_variance_lowers_spike():
    calm = {END - H * (3 + 24 * d): 2.0 for d in range(1, 15)}
    noisy = {END - H * (3 + 24 * d): (0.0 if d % 2 else 4.0) for d in range(1, 15)}
    for h in (calm, noisy):
        h[END - 2 * H] = 10.0
    s_calm = spike_score(*spike_stats(calm, all_hours(), END)[:3])
    s_noisy = spike_score(*spike_stats(noisy, all_hours(), END)[:3])
    assert s_calm > s_noisy


def test_uncovered_hours_are_missing_not_zero():
    covered = all_hours()
    day1 = [END - 24 * H - H * (i + 1) for i in range(24)]  # the day before the window
    # day 1 had a 4-hour outage; mentions were only seen in covered hours
    for h in day1[:4]:
        covered.discard(h)
    hourly = {h: 1.0 for h in day1[4:]}  # 20 covered hours with 1 mention each
    _, mean_gap, _, days = spike_stats(hourly, covered, END)
    # day 1 is scaled to 24h (20 * 24/20 = 24); other 13 days are 0
    assert days == 14
    assert mean_gap == pytest.approx(24 / 14)


def test_mostly_uncovered_day_is_skipped():
    covered = all_hours()
    day1 = [END - 24 * H - H * (i + 1) for i in range(24)]
    for h in day1[:13]:  # only 11 covered hours left < MIN_COVERED_HOURS_PER_DAY
        covered.discard(h)
    hourly = {h: 5.0 for h in day1}
    _, mean, _, days = spike_stats(hourly, covered, END)
    assert days == 13 and mean == 0.0


def test_mentions_in_uncovered_hours_ignored_in_baseline():
    covered = all_hours() - {END - 30 * H}
    _, mean, _, _ = spike_stats({END - 30 * H: 50.0}, covered, END)
    assert mean == 0.0


def test_window_edges():
    hourly = {END: 100.0, END - 24 * H: 1.0, END - 25 * H: 7.0}
    c24, *_ = spike_stats(hourly, all_hours(), END)
    assert c24 == 1.0  # END itself is outside; window_start is inside


# --- interest match ----------------------------------------------------------------


def test_interest_match_prefers_matching_topic():
    docs = {
        1: "Claude Opus 5.5 Anthropic launches Claude Opus 5.5 Opus 5.5 benchmarks",
        2: "Rust 1.90 released Announcing Rust 1.90",
        3: "Ukraine news Ukraine talks",
    }
    out = interest_scores(docs, [("Claude", 1.0), ("Rust", 1.0)])
    assert out[1][0] > 0 and out[1][1] == ["Claude"]
    assert out[2][0] > 0 and out[2][1] == ["Rust"]
    assert out[3] == (0.0, [])


def test_interest_weight_scales_match():
    docs = {1: "Claude Opus 5.5 is out", 2: "Rust 1.90 is out"}
    low = interest_scores(docs, [("Claude", 1.0)])[1][0]
    high = interest_scores(docs, [("Claude", 3.0)])[1][0]
    assert high == pytest.approx(3 * low)


def test_multiword_and_versioned_terms():
    docs = {1: "Claude Code 2.1 adds hooks; Claude Code plugins", 2: "Claude Opus 5.5 GPT-5.5 compared"}
    out = interest_scores(docs, [("Claude Code", 1.0), ("GPT-5.5", 1.0)])
    assert out[1][1] == ["Claude Code"]
    assert "GPT-5.5" in out[2][1]


def test_zero_weight_and_no_terms():
    docs = {1: "Claude Opus 5.5"}
    assert interest_scores(docs, []) == {1: (0.0, [])}
    assert interest_scores(docs, [("Claude", 0.0)]) == {1: (0.0, [])}


# --- selection ----------------------------------------------------------------


def mk(eid, score, spike=None, items=()):
    s = Scored(entity_id=eid, name=f"E{eid}", items=[_item(i) for i in items], n_origins=3,
               first_seen=END - 5 * H)
    s.score, s.spike = score, spike if spike is not None else score
    return s


def _item(i, **kw):
    base = dict(id=i, title=f"t{i}", link_url=f"https://site{i}.com/x", origin=f"site{i}.com",
                published_at=END - H * (10 - i % 10), source_name="Hacker News", source_type="hn",
                is_primary=False, weight=1.0)
    base.update(kw)
    return ItemRow(**base)


def test_select_threshold_and_top_n(monkeypatch):
    monkeypatch.setattr("app.scoring.SCORE_THRESHOLD", 1.0)
    monkeypatch.setattr("app.scoring.TOP_N", 2)
    cands = [mk(1, 5, items=[1]), mk(2, 0.5, items=[2]), mk(3, 3, items=[3]), mk(4, 2, items=[4])]
    assert [s.entity_id for s in select(cands, {})] == [1, 3]


def test_quiet_day_when_nothing_clears_threshold(monkeypatch):
    monkeypatch.setattr("app.scoring.SCORE_THRESHOLD", 10.0)
    assert select([mk(1, 5, items=[1]), mk(2, 9.9, items=[2])], {}) == []


def test_no_repeat_unless_spike_doubled(monkeypatch):
    monkeypatch.setattr("app.scoring.SCORE_THRESHOLD", 1.0)
    cands = [mk(1, 5, spike=5, items=[1]), mk(2, 4, spike=4, items=[2])]
    assert [s.entity_id for s in select(cands, {1: 3.0})] == [2]  # 5 < 2 * 3
    assert [s.entity_id for s in select(cands, {1: 2.5})] == [1, 2]  # 5 >= 2 * 2.5


def test_overlapping_entities_are_deduped(monkeypatch):
    monkeypatch.setattr("app.scoring.SCORE_THRESHOLD", 1.0)
    opus = mk(1, 9, items=[1, 2, 3, 4])
    anthropic = mk(2, 5, items=[1, 2, 3, 9])  # 3 of 4 items shared with a better pick
    other = mk(3, 4, items=[7, 8])
    assert [s.entity_id for s in select([opus, anthropic, other], {})] == [1, 3]


# --- best link and reason ----------------------------------------------------------------


def test_best_item_prefers_primary_source():
    news = _item(1, title="News story", published_at=END - 9 * H)
    blog = _item(2, title="Introducing X", is_primary=True, source_name="Blog", source_type="rss",
                 published_at=END - 5 * H)
    assert best_item([news, blog], set()).id == 2


def test_best_item_primary_domain_via_hn():
    verge = _item(1, link_url="https://www.theverge.com/x", origin="theverge.com")
    anth = _item(2, link_url="https://www.anthropic.com/claude-opus-5-5", origin="anthropic.com")
    assert best_item([verge, anth], {"anthropic.com"}).id == 2
    assert best_item([verge, anth], set()).id in (1, 2)


def test_best_item_release_page_is_primary():
    news = _item(1)
    rel = _item(2, link_url="https://github.com/vllm-project/vllm/releases/tag/v1.0.0")
    assert best_item([news, rel], set()).id == 2


def test_best_item_most_linked_when_no_primary():
    a1 = _item(1, link_url="https://a.com/story", weight=1.0)
    a2 = _item(2, link_url="https://a.com/story", weight=0.8)
    b = _item(3, link_url="https://b.com/other", weight=1.0)
    assert best_item([a1, a2, b], set()).link_url == "https://a.com/story"


def test_reason_line():
    s = mk(1, 5, items=[1])
    s.n_origins, s.mean, s.baseline_days, s.first_seen = 5, 0.2, 14, END - timedelta(hours=11, minutes=30)
    s.matched = ["Claude", "Anthropic"]
    assert reason_line(s, END) == "5 sources in 12h (usually 0), matches: Claude, Anthropic"
    s.matched, s.baseline_days = [], 0
    assert reason_line(s, END) == "5 sources in 12h (no history yet)"


def test_config_constants_exist():
    for name in ("WINDOW_HOURS", "BASELINE_DAYS", "MIN_DISTINCT_SOURCES", "TOP_N", "INTEREST_FLOOR",
                 "SCORE_THRESHOLD", "LEARNING_RATE", "WEIGHT_CAP", "NO_REPEAT_DAYS"):
        assert hasattr(config, name)

"""Entity extraction and alias merging. Titles are real or realistic headlines."""

import pytest

from app.extract import AliasTable, base_name, canonical_key_for_phrase, clean_title, extract


def keys(title, aliases=None, include_weak=True):
    return [c.key for c in extract(title, aliases) if include_weak or not c.weak]


def strong_keys(title, aliases=None):
    return keys(title, aliases, include_weak=False)


# --- the Opus 5.5 release: every variant must merge -------------------------

OPUS_TITLES = [
    "Claude Opus 5.5",
    "Claude Opus 5.5 Intelligence, Performance and Price Analysis (Max)",
    "Claude Opus 5.5 for code review: More catches, different misses",
    "Anthropic launches Claude Opus 5.5 with stricter safeguards for cybersecurity",
    "Getting the most out of Opus 5.5 in Claude and Claude Code",
    "Show HN: Livenerf – a benchmark for whether Opus 5.5 gets nerfed",
    "Claude Opus 5.5 – Pelican Game",
    "Claude Opus 5.5, GPT-6 Sol, GPT-6 Luna, and a new price war",
    "Claude Opus 5.5: Things People Created",
    "Opus 5.5 Scores 75.6% on Part Catalog Bench",
    "Sol 6 and Opus 5.5 compared on an agentic CAD harness",
    "Drawing a portrait with the traveling salesman problem by Opus 5.5",
    "2.5D parallax interactive battle scene by Opus 5.5",
    "Opus 5.5 refuses basic molecular biology questions",
    "Bosphore 1819: Reviving a 200-Year-Old Map of Istanbul with Opus 5.5",
    "Opus 5.5 is good at explainer videos",
    "Show HN: Clippy's Revenge – Made by Opus 5.5 [video]",
    "I got Opus 5.5 to make a whimsical video ad for my side hustle",
    "Claude Opus 5.5 AI: An Incredible Leap Forward",
    "Anthropic Claude Opus 5.5 is now generally available",
    "Introducing Claude Opus 5.5",
    "Anthropic's Claude Opus 5.5 tops SWE-bench",
    "claude opus 5.5 thoughts",  # all lowercase: no capitalization signal
]


@pytest.mark.parametrize("title", OPUS_TITLES[:-1])
def test_opus_variants_merge_to_one_strong_key(title):
    cands = {c.key: c for c in extract(title)}
    assert "opus 5.5" in cands, title
    assert not cands["opus 5.5"].weak


def test_all_lowercase_title_yields_nothing_without_alias():
    assert keys("claude opus 5.5 thoughts") == []


def test_all_lowercase_title_matches_manual_alias():
    table = AliasTable.build({"Claude Opus 5.5": ["claude opus 5.5", "opus5.5"]})
    assert "opus 5.5" in keys("claude opus 5.5 thoughts", table)
    assert "opus 5.5" in keys("benchmarks for opus5.5 are in", table)


def test_opus_display_keeps_surface_form():
    [cand] = [c for c in extract("Anthropic launches Claude Opus 5.5 today") if c.key == "opus 5.5"]
    assert cand.display == "Claude Opus 5.5"


# --- versions ----------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("OpenAI's GPT-5.5 beats Google Gemini 3 Pro on ARC-AGI", ["openai", "gpt 5.5", "gemini 3", "arc agi"]),
        ("OpenAI GPT-5.5 system card", ["gpt 5.5"]),
        ("GPT 5.5 is out", ["gpt 5.5"]),
        ("GPT5.5 first impressions", ["gpt 5.5"]),
        ("Gemini 3 Pro vs Gemini 3 Flash", ["gemini 3"]),
        ("Google Gemini 3", ["gemini 3"]),
        ("DeepSeek-V3.2 is out", ["deepseek 3.2"]),
        ("DeepSeek V3.2 technical report", ["deepseek 3.2"]),
        ("Qwen3-Coder: Agentic coding in the world", ["qwen 3"]),
        ("Qwen 3 released under Apache 2.0", ["qwen 3", "apache 2"]),
        ("Claude 3.5 Sonnet vs GPT-4o", ["sonnet 3.5", "gpt 4o"]),
        ("Claude Sonnet 3.5 is still my favorite", ["sonnet 3.5"]),
        ("Claude 5 rumors", ["claude 5"]),
        ("Python 3.14.0 released", ["python 3.14"]),
        ("What's new in Python 3.14", ["python 3.14"]),
        ("Rust 1.90.0 is out", ["rust 1.90"]),
        ("Announcing Rust 1.90", ["rust 1.90"]),
        ("Next.js 16 is here", ["next.js 16"]),
        ("iOS 26.1 fixes battery drain", ["ios 26.1"]),
        ("Microsoft Windows 11 gets a new start menu", ["windows 11"]),
        ("Windows 11 24H2 rollout paused", ["windows 11"]),
        ("Meta Llama 4 Scout benchmarks", ["llama 4"]),
        ("Llama 4 Scout and Maverick released", ["llama 4"]),
        ("Mistral Large 3 is open weights", ["mistral large 3"]),
        ("Claude Code 2.1 adds hooks", ["claude code 2.1"]),
        ("Apple M5 MacBook Pro review: faster than M4 Max", ["m 5", "macbook pro", "m 4"]),
        ("Nvidia H100 prices drop", ["h 100"]),
        ("vLLM v1.0.0 released", ["vllm 1"]),
        ("vLLM 1.0 is here", ["vllm 1"]),
    ],
)
def test_versioned_entities(title, expected):
    got = strong_keys(title)
    for key in expected:
        assert key in got, (title, got)


def test_version_normalization_merges_trailing_zeros():
    assert canonical_key_for_phrase("Python 3.14.0") == canonical_key_for_phrase("Python 3.14")
    assert canonical_key_for_phrase("vLLM v1.0.0") == canonical_key_for_phrase("vLLM 1.0")


def test_distinct_versions_stay_distinct():
    assert canonical_key_for_phrase("GPT-5") != canonical_key_for_phrase("GPT-5.5")
    assert canonical_key_for_phrase("Claude Opus 5.5") != canonical_key_for_phrase("Claude Sonnet 5.5")


def test_bare_version_is_not_an_entity():
    assert keys("v2.1.283") == []
    assert keys("2.5D parallax interactive battle scene") == []
    assert keys("Top 10 tools of 2026") == []


# --- noise and stoplist ---------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Ask HN: Who is hiring? (September 2026)",
        "Ask HN: What are you working on?",
        "Tell HN: I got laid off today",
        "Show HN: A tool I built",
        "How to write a good README",
        "Why we stopped using microservices",
        "The best way to learn",
        "New AI API for LLM apps",
        "Monday Tuesday Wednesday",
    ],
)
def test_noise_titles_yield_no_strong_entities(title):
    assert strong_keys(title) == [], (title, strong_keys(title))


def test_hn_prefixes_and_tags_are_stripped():
    assert clean_title("Show HN: Livenerf – a benchmark [video]") == "Livenerf – a benchmark"
    assert clean_title("Launch HN: Foobar (YC W24) – observability") == "Foobar  – observability"
    assert clean_title("The Unix philosophy (1994)") == "The Unix philosophy"
    assert clean_title("Attention is all you need [pdf]") == "Attention is all you need"


def test_generic_acronyms_are_dropped_but_real_ones_kept():
    got = strong_keys("The FTC and NASA both use AI and an API")
    assert "ftc" in got and "nasa" in got
    assert "ai" not in got and "api" not in got


def test_role_words_split_names():
    assert strong_keys("FTC Chairman Andrew Ferguson says he resists anthropomorphizing") == [
        "ftc",
        "andrew ferguson",
    ]


def test_possessive_splits_runs():
    assert strong_keys("We tried OpenAI's Codex and Google's Jules") == ["openai", "codex", "google", "jules"]


def test_sentence_start_common_word_is_dropped():
    assert strong_keys("Introducing Gemini 3.8 Live with Live Avatar")[0] == "gemini 3.8"
    assert "introducing" not in keys("Introducing Claude Opus 5.5")


def test_sentence_start_single_word_is_weak():
    [cand] = [c for c in extract("Anthropic launches a thing") if c.key == "anthropic"]
    assert cand.weak


def test_mid_sentence_names_are_strong():
    got = strong_keys("We moved from Postgres to SQLite and saved $40k a year")
    assert got == ["postgres", "sqlite"]


def test_multiword_names_mid_sentence():
    assert "microsoft teams" in strong_keys("Microsoft Teams outage hits Europe")
    assert "europe" in strong_keys("Microsoft Teams outage hits Europe")
    assert "google cloud" in strong_keys("We left AWS for Google Cloud last year")


# --- Title Case headlines ---------------------------------------------------------


def test_title_case_plain_words_are_weak():
    cands = {c.key: c for c in extract("Thieves Stole 'Nvidia' Trailers. They Got 20 Tons of Sand")}
    assert "sand" in cands and cands["sand"].weak
    assert "thieves" in cands and cands["thieves"].weak
    assert not any(not c.weak for c in cands.values())


def test_title_case_distinctive_tokens_are_strong():
    got = strong_keys("Apple Announces New iPhone 18 Pro With Better Camera")
    assert got == ["iphone 18"]
    got = strong_keys("OpenAI Unveils GPT-6 Sol And GPT-6 Luna")
    assert got == ["openai", "gpt 6"]


def test_title_case_split_at_common_words():
    got = keys("Claude Opus 5.5 Intelligence, Performance and Price Analysis (Max)")
    assert got == ["opus 5.5"]


def test_title_case_suffix_heuristic():
    got = keys("Bosphore 1819: Reviving a 200-Year-Old Map of Istanbul with Opus 5.5")
    assert "reviving" not in got
    assert "opus 5.5" in got


def test_all_caps_headline():
    got = strong_keys("BREAKING: OPENAI RELEASES GPT-6")
    assert "gpt 6" in got and "openai" in got


# --- aliases ---------------------------------------------------------------


def test_alias_merges_different_names():
    table = AliasTable.build({"Claude Code": ["claude-code", "Claude Code CLI"]})
    assert "claude code" in keys("I switched to claude-code last week", table)
    assert "claude code" in keys("Claude Code CLI now supports plugins", table)


def test_alias_resolves_extracted_key():
    table = AliasTable.build({"Kubernetes": ["K8s"]})
    assert keys("Running K8s at home", table) == ["kubernetes"]


def test_alias_makes_weak_candidate_strong():
    table = AliasTable.build({"Anthropic": ["Anthropic PBC"]})
    [cand] = [c for c in extract("Anthropic launches a thing", table) if c.key == "anthropic"]
    assert not cand.weak


def test_alias_table_self_alias_maps_to_itself():
    table = AliasTable.build({"Rust": ["rust", "Rust"]})
    assert table.to_key == {"rust": "rust"}


# --- misc ---------------------------------------------------------------


def test_dedupes_within_title():
    assert keys("Gemini 3 vs Gemini 3 Pro vs Google Gemini 3") == ["gemini 3"]


def test_unicode_and_quotes():
    got = strong_keys("Zürich’s ETH releases Apertus 2 — open model")
    assert "apertus 2" in got


def test_empty_and_garbage():
    assert extract("") == []
    assert extract("   ") == []
    assert extract("!!! ??? ...") == []
    assert extract("<script>alert(1)</script>") == [] or all(
        "<" not in c.display for c in extract("<script>alert(1)</script>")
    )


def test_base_name():
    assert base_name("Claude Opus 5.5") == "Claude Opus"
    assert base_name("GPT-5.5") == "GPT"
    assert base_name("Rust Foundation") == "Rust Foundation"


def test_unicode_hyphens_join_versions():
    # OpenAI's feed uses U+2011 NON-BREAKING HYPHEN: "GPT\u20116 Astra"
    assert strong_keys("Hex turns analysis into reports with GPT\u20116 Astra") == ["gpt 6"]
    assert "gpt 5.6" in strong_keys("The builder\u2019s guide to GPT\u20115.6")


def test_dotted_country_abbreviations_are_generic():
    assert strong_keys("U.S. appeals court upholds designation of Anthropic") == ["anthropic"]

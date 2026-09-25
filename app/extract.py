"""Deterministic entity extraction from headlines.

Pipeline for one title:

1. Clean: strip "Show HN:"-style prefixes, "(YC W24)", and trailing tags
   like "[pdf]" or "(2019)".
2. Tokenize, remembering the punctuation between tokens. Hyphens between a
   name and a number are split ("GPT-5.5" -> GPT, 5.5), and so is a number
   glued to a capitalized name ("Qwen3" -> Qwen, 3).
3. Group consecutive capitalized tokens (plus version numbers that follow
   them) into runs. Stopwords, role words ("CEO", "Chairman") and
   punctuation end a run.
4. Headlines Written In Title Case carry little capitalization signal, so in
   that mode runs are also split at common English words.
5. Turn each run into a canonical key:
   - with a version: name words + version. Leading vendor words are dropped
     ("Claude Opus 5.5" -> "opus 5.5", "Google Gemini 3 Pro" -> "gemini 3")
     and qualifiers after the version are ignored;
   - without a version: the lowercased phrase (1-4 tokens).
6. Map keys through the manual alias table.

Candidates are "strong" or "weak". Weak ones (plain words in Title Case
headlines, a single ordinary-looking word at the start of a sentence) may
only match entities that already exist; they never create new ones. That
keeps "Trailers" and "Sand" out of the entity table.

Everything here is pure (no I/O) so it can be tested exhaustively.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --- word lists ---------------------------------------------------------------

# Function words: end a run, never part of an entity.
STOPWORDS = frozenset(
    """
    a an and or nor but the of for to in on at by with from as into onto over under
    via vs versus than then so if is are was were be been being am do does did done
    has have had having it its this that these those there here what which who
    whom whose why how when where while not no yes all any some each every both
    either neither can could will would should shall may might must i me my mine
    we us our ours you your yours he him his she her hers they them their theirs
    up down out off about above below after before during until since per
    just also only too very more most less least much many such own same other
    another again ever never now still yet already even really
    """.split()
)

# People's roles: end a run so "FTC Chairman Andrew Ferguson" -> FTC | Andrew Ferguson.
ROLE_WORDS = frozenset(
    """
    ceo cto cfo coo ciso founder cofounder co-founder chairman chairwoman chair
    president vice minister senator rep representative governor mayor judge
    director head chief secretary commissioner spokesperson spokesman
    professor prof dr mr mrs ms sir lord
    """.split()
)

# Common headline words. Dropped at the start of a sentence, and treated as
# run breaks in Title Case headlines. Brand names are deliberately absent
# (apple, windows, meta, chrome, pixel, ...).
COMMON_WORDS = frozenset(
    """
    new newer newest latest first last next best better worst worse good great big
    bigger biggest small smaller large larger huge tiny top free open cheap cheaper
    fast faster slow slower simple easy hard real true false full whole old young
    early late major minor key main modern classic incredible amazing awesome
    important interesting strange weird wild quick short long high low deep
    announce announces announced announcing announcement launch launches launched
    launching release releases released releasing introduce introduces introduced
    introducing unveil unveils unveiled reveal reveals revealed ship ships shipped
    shipping build builds built building make makes made making use uses used using
    get gets got getting add adds added adding bring brings brought take takes took
    give gives gave show shows showed shown see sees saw seen find finds found say
    says said tell tells told ask asks asked think thinks thought know knows knew
    want wants need needs needed try tries tried work works worked working run runs
    running ran start starts started stop stops stopped keep keeps kept let lets
    help helps helped move moves moved turn turns turned become becomes became
    beat beats cut cuts hit hits set sets win wins won lose loses lost fail
    fails failed break breaks broke fix fixes fixed update updates updated
    upgrade upgrades expand expands expanded raise raises raised sue sues sued ban
    bans banned block blocks blocked buy buys bought sell sells sold pay pays paid
    plan plans planned delay delays delayed kill kills killed drop drops dropped
    opens opened close closes closed join joins joined leave leaves left
    write writes wrote written read reads learn learns learned
    explain explains explained compare compares compared review reviews reviewed
    test tests tested testing measure measures measured score scores scored
    benchmark benchmarks rank ranks ranked lead leads led top tops topped
    stole steal steals stolen go goes went gone come comes came call calls called
    look looks looked feel feels felt play plays played live lives lived die dies
    died grow grows grew fall falls fell rise rises rose hire hires hired fire
    fires fired warn warns warned claim claims claimed deny denies denied
    deep dive guide guides tutorial tutorials tips tricks lessons thoughts notes
    intro introduction overview analysis report reports study studies survey
    paper papers research data results result story stories case cases
    part chapter episode edition issue volume vol series season day days week weeks
    weekly month months monthly year years yearly daily today tonight tomorrow
    yesterday hour hours minute minutes time times era age future past present
    people person man woman men women kid kids world life home house city country
    company companies startup startups team teams industry market markets business
    product products service services feature features tool tools app apps
    software hardware code model models system systems platform network internet
    web site website page blog post posts video videos search podcast newsletter book
    books game games music movie movies film films art design image images photo
    photos camera phone phones device devices computer computers chip chips
    engine engines agent agents assistant bot bots browser language languages
    framework library database server servers cloud security privacy safety
    policy law laws rule rules court government state states federal
    national public private office job jobs worker workers price prices
    cost costs money deal deals fund funds funding round investor investors
    billion million thousand hundred percent ton tons
    way ways thing things lot lots number numbers point points fact facts idea
    ideas problem problems question questions answer answers reason reasons change
    changes step steps level levels line lines list lists map maps side end
    power energy space science math history health food water war peace
    intelligence performance analysis revenge leap forward bench catalog
    pro max mini ultra plus lite nano turbo flash preview beta alpha stable
    edition release candidate rc lts instant thinking
    hello goodbye welcome thanks farewell meet meets
    breaking exclusive official finally inside behind beyond toward towards across
    against without within around through among between
    here's there's what's that's let's i'm we're you're don't can't won't
    isn't aren't doesn't didn't
    monday tuesday wednesday thursday friday saturday sunday
    mon tue tues wed thu thur thurs fri sat sun
    january february march april may june july august september october november
    december jan feb mar apr jun jul aug sep sept oct nov dec
    """.split()
)

# Generic acronyms and shorthand that would otherwise become entities.
GENERIC_ACRONYMS = frozenset(
    """
    ai ml llm llms api apis ceo cto cfo coo gpu gpus cpu cpus ui ux os pdf html css
    json xml http https url urls usb tv pc pcs vr ar it us usa uk un agi saas ipo
    faq tldr diy fyi psa ama wip oss cli ide sdk hn yc rss dns vpn ssh tls ssl
    q&a r&d u.s u.k e.u lol omg imo imho tbh ok eta asap pr prs mvp roi kpi hr qa cad readme todo
    """.split()
)

# Leading brand words often dropped before a versioned product name:
# "Claude Opus 5.5" == "Opus 5.5", "Google Gemini 3" == "Gemini 3".
VENDOR_PREFIXES = frozenset(
    """
    anthropic claude openai chatgpt google alphabet deepmind meta facebook
    microsoft apple amazon aws nvidia intel amd mistral xai alibaba deepseek
    github huggingface samsung ibm oracle
    """.split()
)

# Words after a version that name a variant of the same thing.
QUALIFIERS = frozenset(
    """
    pro max mini ultra plus lite nano turbo flash preview beta alpha stable rc lts
    instant thinking live edition release candidate update model models ai
    """.split()
)

# In Title Case mode, words with these endings (and at least this length) are
# almost always ordinary words: "Reviving", "Performance", "Accessible".
_COMMON_SUFFIXES = (
    ("ings", 6), ("ing", 7), ("ed", 6), ("ly", 5), ("tion", 6), ("tions", 7),
    ("sion", 6), ("sions", 7), ("ness", 6), ("ment", 6), ("ments", 7),
    ("ity", 6), ("ities", 7), ("ive", 7), ("ives", 8), ("able", 7), ("ible", 7),
    ("ful", 6), ("less", 7), ("ous", 6), ("ance", 7), ("ence", 7), ("ers", 7),
)

_HN_PREFIX = re.compile(r"^\s*(show|ask|tell|launch)\s+hn\s*[:\-–—]\s*", re.I)
_YC_TAG = re.compile(r"\(\s*YC\s+[A-Z]\d{2}\s*\)", re.I)
_TRAILING_TAG = re.compile(r"\s*[\[(](?:pdf|video|audio|slides|paywall|\d{4})[\])]\s*$", re.I)
_TOKEN = re.compile(r"[^\W_](?:[\w.+#&'/-]*[\w+#])?")
# 5.5, v3, 3.12, 2026, and model-style suffixes like 4o or 3n
_VERSION = re.compile(r"^[vV]?\d+(?:\.\d+)*[a-z]?$")
_GLUED_VERSION = re.compile(r"^([A-Za-z]+)(\d+(?:\.\d+)*)$")
_CLAUSE_BREAK = re.compile(r"[:.!?|–—]|\s-\s")

MAX_PHRASE_TOKENS = 4


@dataclass(frozen=True)
class Token:
    text: str  # surface text, possessive removed
    start: int
    end: int
    gap: str  # non-space characters between the previous token and this one
    ends_run: bool = False  # possessive: "OpenAI's GPT-5" -> OpenAI | GPT-5

    @property
    def brk(self) -> bool:
        return bool(self.gap)

    @property
    def low(self) -> str:
        return self.text.lower()

    @property
    def is_version(self) -> bool:
        return bool(_VERSION.match(self.text))

    @property
    def is_cap(self) -> bool:
        return any(c.isupper() for c in self.text) and any(c.isalpha() for c in self.text)

    @property
    def is_strong(self) -> bool:
        """Distinctive on its own: iPhone, OpenAI, NASA, 5.5."""
        t = self.text
        if self.is_version:
            return True
        letters = [c for c in t if c.isalpha()]
        if len(letters) >= 2 and all(c.isupper() for c in letters):
            return self.low not in GENERIC_ACRONYMS
        return any(c.isupper() for c in t[1:]) or any(c.isdigit() for c in t)


@dataclass(frozen=True)
class Candidate:
    key: str  # canonical key, e.g. "opus 5.5"
    display: str  # surface form, e.g. "Claude Opus 5.5"
    weak: bool = False  # may match an existing entity but never create one


@dataclass
class AliasTable:
    """Manual aliases: normalized alias phrase -> canonical key."""

    to_key: dict[str, str] = field(default_factory=dict)
    max_ngram: int = 1

    @classmethod
    def build(cls, mapping: dict[str, list[str]] | None) -> "AliasTable":
        table = cls()
        for canonical, aliases in (mapping or {}).items():
            target = canonical_key_for_phrase(str(canonical))
            if not target:
                continue
            # the canonical name itself also matches in lowercase ("claude code")
            table.add(normalize_phrase(str(canonical)), target)
            for alias in aliases or []:
                akey = normalize_phrase(str(alias))
                if akey:
                    table.add(akey, target)
        return table

    def add(self, alias_key: str, target: str) -> None:
        self.to_key[alias_key] = target
        self.max_ngram = max(self.max_ngram, len(alias_key.split()))


# --- tokenizing ---------------------------------------------------------------


_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u00ad": "", "\u2212": "-"})


def clean_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title or "").translate(_HYPHENS)
    title = title.replace("’", "'").replace("‘", "'")
    title = _HN_PREFIX.sub("", title.strip())
    title = _YC_TAG.sub("", title)
    for _ in range(2):
        title = _TRAILING_TAG.sub("", title)
    return title.strip()


def _split_token(raw: str) -> list[str]:
    """GPT-5.5 -> [GPT, 5.5]; Qwen3-Coder -> [Qwen, 3, Coder]; e/acc stays whole."""
    out: list[str] = []
    for part in re.split(r"-(?=\w)", raw):
        if not part:
            continue
        m = _GLUED_VERSION.match(part)
        if m and not _VERSION.match(part) and any(c.isupper() for c in m.group(1)):
            out.extend([m.group(1), m.group(2)])
        else:
            out.append(part)
    return out


def tokenize(title: str) -> list[Token]:
    tokens: list[Token] = []
    prev_end = 0
    for m in _TOKEN.finditer(title):
        gap = title[prev_end : m.start()].strip()
        prev_end = m.end()
        raw = m.group(0)
        ends_run = False
        if len(raw) > 2 and raw[-2:].lower() == "'s":
            raw, ends_run = raw[:-2], True
        offset = m.start()
        pieces = _split_token(raw)
        for i, piece in enumerate(pieces):
            start = title.find(piece, offset)
            start = start if start >= 0 else offset
            offset = start + len(piece)
            tokens.append(
                Token(
                    text=piece,
                    start=start,
                    end=offset,
                    gap=gap if i == 0 else "",
                    ends_run=ends_run and i == len(pieces) - 1,
                )
            )
    return tokens


def normalize_version(text: str) -> str:
    v = text.lower()
    if v.startswith("v"):
        v = v[1:]
    parts = v.split(".")
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def _norm(tok: Token | str) -> str:
    text = tok.text if isinstance(tok, Token) else tok
    if isinstance(tok, Token) and tok.is_version:
        return normalize_version(text)
    return text.lower()


def is_title_case(tokens: list[Token]) -> bool:
    """Headline Style Capitalization: (nearly) every content word capitalized."""
    words = [t for t in tokens[1:] if t.text.isalpha() and t.low not in STOPWORDS and len(t.text) > 1]
    if len(words) < 3:
        return False
    capped = sum(1 for t in words if t.is_cap)
    return capped / len(words) >= 0.9


def _is_common(low: str, title_case: bool) -> bool:
    if low in COMMON_WORDS or low in STOPWORDS:
        return True
    if title_case and low.isalpha():
        return any(low.endswith(suf) and len(low) >= n for suf, n in _COMMON_SUFFIXES)
    return False


# --- runs and keys --------------------------------------------------------------

Run = list[tuple[int, Token]]


def _runs(tokens: list[Token]) -> list[Run]:
    """Consecutive capitalized tokens; a version may continue a run, never start one."""
    runs: list[Run] = []
    cur: Run = []
    for i, tok in enumerate(tokens):
        if tok.brk and cur:
            runs.append(cur)
            cur = []
        joinable = tok.is_version or (tok.is_cap and tok.low not in STOPWORDS and tok.low not in ROLE_WORDS)
        if joinable and (cur or not tok.is_version):
            cur.append((i, tok))
        elif cur:
            runs.append(cur)
            cur = []
        if tok.ends_run and cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def _split_at_common(run: Run) -> list[Run]:
    parts: list[Run] = []
    cur: Run = []
    for i, tok in run:
        if not tok.is_version and _is_common(tok.low, title_case=True):
            if cur:
                parts.append(cur)
            cur = []
        else:
            cur.append((i, tok))
    if cur:
        parts.append(cur)
    return parts


def _versioned_key(names: list[str], version: str, after: list[str]) -> tuple[str | None, bool]:
    """Key for name tokens + version. Returns (key, whether after[0] became the name)."""
    trimmed = list(names)
    while len(trimmed) > 1 and trimmed[0] in VENDOR_PREFIXES and trimmed[1] not in COMMON_WORDS:
        trimmed.pop(0)
    if (
        len(trimmed) == 1
        and trimmed[0] in VENDOR_PREFIXES
        and after
        and after[0].isalpha()
        and after[0] not in QUALIFIERS
        and after[0] not in COMMON_WORDS
    ):
        return f"{after[0]} {version}", True  # "Claude 3.5 Sonnet" -> "sonnet 3.5"
    trimmed = trimmed[-2:]
    if not trimmed:
        return None, False
    return " ".join(trimmed + [version]), False


def _key_for(toks: list[Token]) -> tuple[str | None, int, int]:
    """(canonical key, first, last) where toks[first:last+1] is the display form.

    Versioned: the name is the token right before the version plus any
    uncommon tokens before it, so "Apple Announces New iPhone 18" -> "iphone 18"
    but "Mistral Large 3" -> "mistral large 3".
    """
    for idx, tok in enumerate(toks):
        if tok.is_version and idx > 0 and not toks[idx - 1].is_version:
            first = idx - 1
            while first > 0 and not toks[first - 1].is_version and not _is_common(toks[first - 1].low, False):
                first -= 1
            names = [_norm(t) for t in toks[first:idx]]
            after = [_norm(t) for t in toks[idx + 1 :]]
            key, used_after = _versioned_key(names, _norm(tok), after)
            return key, first, idx + 1 if used_after else idx
    if len(toks) > MAX_PHRASE_TOKENS:
        return None, 0, len(toks) - 1
    return " ".join(_norm(t) for t in toks), 0, len(toks) - 1


def _acceptable(key: str) -> bool:
    words = [w for w in key.split() if not _VERSION.match(w)]
    if not words:
        return False
    has_version = len(words) < len(key.split())
    # "m 5" (Apple M5) is fine; a lone "x" is not
    if sum(c.isalpha() for c in key) < (1 if has_version else 2):
        return False
    if len(key.split()) == 1 and (key in GENERIC_ACRONYMS or key in ROLE_WORDS):
        return False
    return not all(w in COMMON_WORDS or w in STOPWORDS or w in GENERIC_ACRONYMS for w in words)


def normalize_phrase(phrase: str) -> str:
    """Lowercased token form of a phrase, without canonical reduction."""
    return " ".join(_norm(t) for t in tokenize(clean_title(phrase)))


def canonical_key_for_phrase(phrase: str) -> str:
    """Canonical key of a whole phrase, treated as one run ('Claude Opus 5.5' -> 'opus 5.5')."""
    toks = tokenize(clean_title(phrase))
    if not toks:
        return ""
    key, _, _ = _key_for(toks)
    return key or " ".join(_norm(t) for t in toks)


# --- main entry point -----------------------------------------------------------


def extract(title: str, aliases: AliasTable | None = None) -> list[Candidate]:
    """Entity candidates in one headline, deduplicated by key, in title order."""
    aliases = aliases or AliasTable()
    text = clean_title(title)
    tokens = tokenize(text)
    if not tokens:
        return []
    title_case = is_title_case(tokens)
    found: dict[str, Candidate] = {}

    def emit(run: Run, clause_start: bool) -> None:
        toks = [t for _, t in run]
        while toks and (toks[0].is_version or toks[0].low in STOPWORDS):
            toks.pop(0)
        while toks and toks[-1].low in STOPWORDS:
            toks.pop()
        if not toks:
            return
        key, first, last = _key_for(toks)
        if key is None:
            # an over-long run with no version: salvage its parts
            for part in _split_at_common([(0, t) for t in toks]):
                if len(part) < len(toks):
                    emit(part, clause_start=False)
            return
        if first > 0:
            # "Apple Announces New iPhone 18": the words before the name
            for part in _split_at_common([(0, t) for t in toks[:first]]):
                emit(part, clause_start=clause_start and part[0][1] is toks[0])
        surface = " ".join(_norm(t) for t in toks[first : last + 1])
        key = aliases.to_key.get(surface) or aliases.to_key.get(key) or key
        if not _acceptable(key):
            return
        strong = any(t.is_strong for t in toks[first : last + 1])
        weak = not strong and (title_case or (clause_start and first == 0 and last == 0))
        if key in aliases.to_key.values():
            weak = False
        display = text[toks[first].start : toks[last].end]
        prev = found.get(key)
        if prev is None or (prev.weak and not weak):
            found[key] = Candidate(key=key, display=display, weak=weak)
        # "Apple M5 MacBook Pro": the words after the version can name a second thing
        rest = toks[last + 1 :]
        while rest and (rest[0].low in QUALIFIERS or rest[0].is_version):
            rest = rest[1:]
        if rest and any(t.is_strong for t in rest):
            emit([(0, t) for t in rest], clause_start=False)

    for run in _runs(tokens):
        first_idx, first = run[0]
        clause_start = first_idx == 0 or bool(_CLAUSE_BREAK.search(first.gap))
        if clause_start and not first.is_strong and _is_common(first.low, title_case=True):
            run = run[1:]  # "Introducing Claude Opus 5.5" -> "Claude Opus 5.5"
            clause_start = False
        if not run:
            continue
        pieces = _split_at_common(run) if title_case else [run]
        for n, piece in enumerate(pieces):
            emit(piece, clause_start=clause_start and n == 0 and piece[0][0] == first_idx)

    # Manual aliases can name things capitalization misses (e.g. "vllm").
    if aliases.to_key:
        norms = [_norm(t) for t in tokens]
        for n in range(1, aliases.max_ngram + 1):
            for i in range(len(norms) - n + 1):
                target = aliases.to_key.get(" ".join(norms[i : i + n]))
                if target and (target not in found or found[target].weak):
                    found[target] = Candidate(
                        key=target, display=text[tokens[i].start : tokens[i + n - 1].end]
                    )
    return list(found.values())


def base_name(display: str) -> str:
    """Display name without its version: 'Claude Opus 5.5' -> 'Claude Opus'."""
    kept: list[str] = []
    for tok in tokenize(clean_title(display)):
        if tok.is_version:
            break
        kept.append(tok.text)
    return " ".join(kept).strip() or display.strip()

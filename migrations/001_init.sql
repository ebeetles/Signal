-- Signal schema v1.

create table sources (
    id serial primary key,
    name text not null unique,
    type text not null check (type in ('hn', 'rss')),
    url text not null,
    independence_weight real not null default 1.0
        check (independence_weight > 0 and independence_weight <= 5),
    -- first-party source (company blog, release notes, changelog): preferred as the link
    is_primary boolean not null default false,
    -- optional override of the independence key for every item of this source
    origin text,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table items (
    id bigserial primary key,
    source_id int not null references sources (id) on delete cascade,
    url text not null unique,        -- dedupe key (for HN: the HN item permalink)
    link_url text not null,          -- where a reader goes (for HN: the linked article)
    origin text not null,            -- independence key, e.g. 'anthropic.com', 'github.com/vllm-project'
    title text not null,
    published_at timestamptz not null,
    fetched_at timestamptz not null default now()
);
create index items_published_at_idx on items (published_at);

create table entities (
    id bigserial primary key,
    name text not null,              -- display form, e.g. 'Claude Opus 5.5'
    norm text not null unique,       -- canonical key, e.g. 'opus 5.5'
    aliases text[] not null default '{}',  -- extra normalized keys that map here (manual)
    created_at timestamptz not null default now()
);

create table mentions (
    item_id bigint not null references items (id) on delete cascade,
    entity_id bigint not null references entities (id) on delete cascade,
    primary key (item_id, entity_id)
);
create index mentions_entity_idx on mentions (entity_id);

create table entity_counts (
    entity_id bigint not null references entities (id) on delete cascade,
    hour_bucket timestamptz not null,
    weighted_count real not null,
    distinct_sources int not null,
    primary key (entity_id, hour_bucket)
);
create index entity_counts_hour_idx on entity_counts (hour_bucket);

create table runs (
    id bigserial primary key,
    kind text not null check (kind in ('collect', 'send', 'backfill')),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    status text not null default 'running'
        check (status in ('running', 'success', 'partial', 'skipped', 'failed')),
    error text,
    stats jsonb not null default '{}'
);
create index runs_kind_started_idx on runs (kind, started_at);

create table digests (
    id bigserial primary key,
    digest_date date not null unique,  -- local date (TIMEZONE) of the morning brief
    sent_at timestamptz,
    status text not null default 'pending'
        check (status in ('pending', 'sent', 'quiet', 'failed')),
    created_at timestamptz not null default now()
);

create table digest_items (
    id bigserial primary key,
    digest_id bigint not null references digests (id) on delete cascade,
    entity_id bigint references entities (id) on delete set null,
    item_id bigint references items (id) on delete set null,
    rank int not null,
    score real not null,
    spike real not null,
    reason text not null,
    -- snapshots, so the public archive survives item pruning
    headline text not null,
    url text not null,
    source_name text not null,
    entity_name text not null,
    matched_terms text[] not null default '{}',
    feedback text check (feedback in ('up', 'down')),
    feedback_at timestamptz
);
create index digest_items_digest_idx on digest_items (digest_id);
create index digest_items_entity_idx on digest_items (entity_id);

create table interests (
    term text primary key check (char_length(term) between 1 and 60),
    weight real not null check (weight >= 0 and weight <= 5),
    origin text not null check (origin in ('manual', 'learned')),
    updated_at timestamptz not null default now()
);
create unique index interests_term_lower_idx on interests (lower(term));

create table interest_history (
    term text not null,
    weight real not null,
    day date not null,
    recorded_at timestamptz not null default now(),
    primary key (term, day)
);

create table missed (
    id bigserial primary key,
    text text not null check (char_length(text) between 1 and 500),
    created_at timestamptz not null default now()
);

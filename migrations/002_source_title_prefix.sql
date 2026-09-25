-- Optional text prepended to entry titles from a source, e.g. "vLLM" for a
-- GitHub releases feed whose entries are titled just "v1.0.0".
alter table sources add column if not exists title_prefix text;

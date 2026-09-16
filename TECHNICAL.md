---
Title        wikigen technical reference
Purpose      Operational reference — install, every CLI flag, input formats, environment variables, outputs and known limitations.
Author       Jennifer Naomi Nguyen
Canonical    ~/Bootwitch/Projects/wikigen/TECHNICAL.md — authoritative
Updated      2026-09-16
Dependencies Python 3.9+; networkx>=3.0, matplotlib>=3.7, numpy>=1.24, Pillow>=10.0, and anthropic>=0.40 (only if not using `--no-api`). ANTHROPIC_API_KEY for concept extraction.
---

# Technical reference

[README.md](README.md) is the introduction and [ARCHITECTURE.md](ARCHITECTURE.md)
explains the pipeline. This is the operational document.

---

## Install

```bash
pip install -r requirements.txt
```

| Package | Needed for |
|---|---|
| `networkx>=3.0` | graph construction and layout |
| `matplotlib>=3.7` | PNG and GIF rendering |
| `numpy>=1.24` | layout maths |
| `Pillow>=10.0` | GIF assembly |
| `anthropic>=0.40` | concept extraction — **optional**, omit and use `--no-api` |

Python 3.9 or newer. There is no `setup.py`, no packaging and no test suite;
run the scripts directly.

---

## Quick check, no API key

```bash
python src/wikigen.py --no-api --html
```

This proves the pipeline runs end to end. **It produces unconnected dots with
zero edges** — demo mode derives one node name per file from its filename, and
every filename is different, so no two conversations can share a concept. It is
a smoke test, not a preview.

---

## The real run

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python src/wikigen.py --animated
```

---

## CLI reference

```
--dir DIR            Directory of conversation .txt files (default: chat_history)
--html               Standalone interactive HTML
--static             Static PNG
--animated           Animated GIF, concepts appearing chronologically
--no-api             Skip the Claude API; derive concepts from filenames
--model MODEL        Model for concept extraction
                     (default: claude-haiku-4-5-20251001)
--consolidate        Merge near-synonymous concepts in a second pass before graphing
--save-concepts FILE Write extracted concepts to a JSON file, for comparing runs
--before DATE        Only sessions whose filename sorts before this (e.g. '2026-05')
--project SUB        Only read Claude Code project dirs whose name contains SUB
                     (e.g. 'Anthropic'). Omitted: every project is read
--out DIR            Output directory (default: .)
```

### Notes on the less obvious flags

**`--consolidate`** costs one extra model call and merges concept names that
mean the same thing. Use it on any corpus large enough that the graph looks like
a pile of overlapping labels — that symptom is near-synonyms stacking, and it is
the only thing that fixes it. Conservative by design: genuine synonyms only,
anything doubtful is left alone. If the call fails the run continues with
unmerged concepts and prints a warning.

**`--model`** overrides the extraction model. Different models pick genuinely
different concepts; `out/concepts-haiku.json` and `out/concepts-opus.json` in
this repository are two such runs, with `out/graph-haiku.png` and
`out/graph-opus.png` as the resulting graphs.

**`--save-concepts`** writes the extracted concepts to JSON so two runs can be
compared without re-extracting. Pair it with `--model` to see how a model choice
changes the graph.

**`--project`** matters more than it looks. Without it, **every** Claude Code
project directory is read, so unrelated work appears in the graph.

**`--before`** narrows to a stretch of time — useful for watching one period
rather than everything at once.

---

## Input sources

Both are read together in a single run.

### `chat_history/*.txt`

Written by `src/chat.py`. Plain text, one file per conversation, `ROLE: content`
blocks separated by blank lines. Change the directory with `--dir`.

### `~/.claude/projects/**/*.jsonl`

Claude Code session transcripts, read automatically from their standard
location. Every project directory is scanned unless `--project` narrows it.

### Filtering

`is_noisy` drops a conversation before it reaches the API when either:

- more than **55%** of its lines match the noise pattern (terminal output, error logs), or
- it has fewer than **500 characters** of content.

This runs before extraction, so noisy conversations cost nothing.

---

## `src/chat.py`

A 52-line terminal chat client whose purpose is to build the corpus:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python src/chat.py          # writes chat_history/<timestamp>.txt
```

Type `quit`, `exit` or `bye` to leave. `chat_history/` is gitignored.

Three things to know before relying on it:

- It **rewrites the entire transcript file after every turn**, under a filename stamped with the current time. A long conversation therefore leaves one file per turn, not one file per conversation, and re-writes grow with conversation length.
- It pins `claude-3-5-sonnet-20241022`, which is old. Nothing reads that constant except this script; change it freely.
- There is no error handling around the API call — a failed request ends the session.

It is a corpus generator, not a chat application. `claude-chat-mini` is the
project that actually cares about being a chat client.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | concept extraction and `chat.py`. Not needed with `--no-api` |
| `WIKIGEN_CREDIT` | name shown on generated HTML. Unset means no credit line |

```bash
WIKIGEN_CREDIT="Your Name" python src/wikigen.py --html
```

Generated pages carry no author by default on purpose — a hardcoded name would
appear on every graph anyone produced.

---

## Outputs

| Flag | File | Contents |
|---|---|---|
| `--html` | standalone `.html` | self-contained interactive page — pan, zoom, click a concept |
| `--static` | `.png` | the full graph |
| `--animated` | `.gif` | concepts appearing in chronological order |

Written to `--out` (default: the current directory). The committed `out/`
directory holds example artifacts from two model runs.

Node colour and size encode **mention count**; edges encode co-occurrence weight.
The layout seed is fixed at 42, so the same corpus renders identically across
runs — keep that in mind when comparing two outputs, and remember that a
difference you see is a real difference in the concepts, not layout noise.

---

## Known limitations

**`--no-api` produces a graph with no edges.** By construction, not a bug. See
above.

**Concepts are extracted per conversation, in isolation.** The same idea arrives
under different names, which is what `--consolidate` exists to repair. Without
it, a large corpus produces visually stacked duplicate nodes.

**Temperature handling is a denylist.** `_accepts_temperature` matches older
model families (`haiku-4`, `sonnet-4`, `opus-4`) and passes `temperature: 0.2`
via `extra_body` only for those. An unrecognised model is assumed new and the
parameter is dropped — deliberate, so a future model id fails safe. If a model
that *does* want temperature is unrecognised, it silently runs at the default
instead.

**No test suite, no packaging, no pinned lockfile.** `requirements.txt` uses
floors (`>=`), not pins.

**Everything is in memory.** One extraction call per conversation, all
conversations held at once, and no caching between runs — re-running re-extracts
and re-pays unless you used `--save-concepts`.

**No rate limiting or retry.** Extraction calls go out one after another; a
transient API failure on one conversation is caught per-conversation, but there
is no backoff.

**`chat.py` writes one file per turn.** See above.

**Outputs contain personal content.** Concept names and conversation names are
derived from your transcripts. Treat a generated graph as personal data.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Graph is unconnected dots | `--no-api` demo mode, or a corpus with no shared concepts |
| Pile of overlapping labels | near-synonymous concept names — re-run with `--consolidate` |
| Unrelated projects in the graph | every Claude Code project dir is read; narrow with `--project` |
| Conversations missing from the graph | `is_noisy` dropped them (>55% log lines, or <500 chars) |
| `temperature is deprecated for this model` | a model matched the denylist but no longer accepts it — update `_accepts_temperature` |
| `ModuleNotFoundError: anthropic` | install it, or run with `--no-api` |
| Empty frame / "No concepts yet" | nothing survived loading and filtering |

---

## License

MIT — see [LICENSE](LICENSE). **Jennifer Naomi Nguyen**, with **Claude** as contributor.

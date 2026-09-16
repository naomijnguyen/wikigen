---
Title        wikigen — concept graph from your conversation history
Purpose      The introduction: what it builds, how to run it, and where the conversations come from. Start here.
Author       Jennifer Naomi Nguyen
Canonical    ~/Bootwitch/Projects/wikigen — authoritative. Older copies of wikigen.py lived under the former ~/Projects/Anthropic/interpretability/attractor/components and did not move to the current project home. A drifted snapshot does still exist at ~/Bootwitch/Projects/oo/src/wikigen.py; this repository is live.
Updated      2026-09-16
Dependencies Python 3.9+; networkx, matplotlib, numpy, Pillow, and anthropic (unless using --no-api). ANTHROPIC_API_KEY for concept extraction.
---

# wikigen

> Builds a concept graph from your conversation history — and animates it appearing over time.

You have months of conversations sitting in text files. Somewhere in them is a
record of what you were actually thinking about, and how one idea led to the
next. `wikigen` extracts that: it reads the transcripts, asks Claude for the
few concepts each conversation was really about, links conversations that share
concepts, and renders the result.

Three outputs, same graph:

| Flag | Output |
|------|--------|
| `--html` | Standalone interactive page — pan, zoom, click a concept |
| `--static` | PNG still of the full graph |
| `--animated` | GIF of concepts appearing in chronological order |

The GIF is the one worth running first. Watching clusters form — a topic you
touched once in month one going quiet, then suddenly connecting to something
from month six — is a different experience from reading a list of what you did.

---

## Documentation

| Document | What it covers |
|---|---|
| **README.md** (this file) | What it builds, how to run it, where conversations come from |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The pipeline stage by stage, and the layout and consolidation decisions |
| [TECHNICAL.md](TECHNICAL.md) | Install, every flag, input formats, outputs, known limitations |

---

## Try it without an API key

```bash
pip install -r requirements.txt
python src/wikigen.py --no-api --html
```

`--no-api` skips concept extraction entirely and derives one node name per file
from its filename. Be warned what that looks like: since every filename is
different, you get **unconnected dots — zero edges**. It proves the pipeline
runs end to end and renders, but it shows you none of what the tool is for. The
graph only becomes a graph when Claude extracts concepts, because that's what
lets two different conversations share a node.

---

## The real thing

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python src/wikigen.py --animated
```

Each conversation goes to Claude Haiku, which returns the 3–5 most distinct
concepts in it — "ideas, techniques, frameworks, questions, or insights that
would be worth their own wiki article." Concepts become nodes; two
conversations that share a concept become connected. Node weight is how often a
concept recurs, which is what drives colour and size.

A `is_noisy` filter drops conversations that are mostly boilerplate before they
reach the API, so you aren't paying to extract concepts from a stack trace.

### Options

```
--dir DIR            Directory of conversation .txt files (default: chat_history)
--html               Standalone interactive HTML
--static             Static PNG
--animated           Animated GIF, concepts appearing chronologically
--no-api             Skip the Claude API; derive concepts from filenames
--model MODEL        Model for concept extraction
                     (default: claude-haiku-4-5-20251001)
--consolidate        Merge near-synonymous concepts in a second pass before graphing
--save-concepts FILE Write the extracted concepts to JSON, for comparing runs
--before DATE        Only sessions whose filename sorts before this (e.g. '2026-05')
--project SUB        Only read Claude Code project dirs whose name contains SUB
                     (e.g. 'Anthropic'). Omitted: every project is read
--out DIR            Output directory (default: .)
```

`--before` is for watching a specific stretch of time rather than everything at
once.

**`--consolidate` is the one to reach for when the graph looks like a pile of
overlapping labels.** Each conversation is analysed on its own, so the same idea
comes back phrased four different ways — "local JSON state", "…persistence",
"…storage", "…management" — and because those share neighbours the layout
correctly stacks them into an unreadable heap. A second pass merges genuine
synonyms before the graph is built, which is the only place the fix can go.

**`--model` and `--save-concepts` go together.** Different models pick genuinely
different concepts; saving them lets you compare two runs without re-extracting.
`out/` holds example artifacts from a Haiku run and an Opus run.

---

## Where conversations come from

Two sources, read together:

- **`chat_history/*.txt`** — what `src/chat.py` writes. It's a small terminal
  chat client that timestamps and saves every conversation without being asked,
  which is the entire reason a corpus exists to graph.
- **`~/.claude/projects/**/*.jsonl`** — Claude Code session transcripts, read
  automatically. Every project directory is scanned by default, so use
  `--project Anthropic` to narrow it to one; otherwise unrelated work shows up
  in the graph.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python src/chat.py          # writes chat_history/<timestamp>.txt
```

`chat_history/` is gitignored. Transcripts are personal; keep them that way.

---

## Crediting

Generated pages carry no author by default — a name hardcoded into the
template would appear on every graph anyone produced. Set `WIKIGEN_CREDIT` to
put your own on yours:

```bash
WIKIGEN_CREDIT="Your Name" python src/wikigen.py --html
```

## Requirements

Python 3.9+, plus `networkx`, `matplotlib`, `numpy`, `Pillow`, and `anthropic`
(the last only if you're not using `--no-api`). All in `requirements.txt`.

Concept extraction passes `temperature` via `extra_body` — the Anthropic SDK
1.x removed it from the `messages.create()` signature, though the API still
honours it.

---

## License

MIT — see [LICENSE](LICENSE).

**Jennifer Naomi Nguyen**, with **Claude** as contributor.

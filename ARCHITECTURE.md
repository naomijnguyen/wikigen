---
Title        wikigen architecture
Purpose      The shape of the pipeline — how transcripts become a concept graph, and the decisions behind extraction, consolidation, layout and rendering.
Author       Jennifer Naomi Nguyen
Canonical    ~/Projects/Anthropic/wikigen/ARCHITECTURE.md — authoritative. Copies of `wikigen.py` also exist under ~/Projects/Anthropic/interpretability/attractor/components; this repository is the live one.
Updated      2026-09-13
Dependencies none to read. To run what it describes: Python 3.9+, networkx, matplotlib, numpy, Pillow, and anthropic (unless using `--no-api`).
---

# Architecture

[README.md](README.md) is the introduction; [TECHNICAL.md](TECHNICAL.md) is the
operational reference. This document is about **structure**: the stages of the
pipeline and why each is shaped the way it is.

Two files, and they are barely related:

| File | Lines | Role |
|---|---|---|
| `src/wikigen.py` | 1020 | the entire pipeline — extraction, graph, layout, three renderers |
| `src/chat.py` | 52 | a small terminal chat client that writes the corpus `wikigen` reads |

`chat.py` is not a dependency of `wikigen.py`. It is the reason a corpus exists
at all: it timestamps and saves every conversation without being asked.

---

## The pipeline

```
  sources                    chat_history/*.txt        ~/.claude/projects/**/*.jsonl
                                      \                   /
                                       v                 v
  [ load ]            load_conversation()        load_jsonl_session()
                                       \                 /
                                        v               v
  [ filter ]                        is_noisy()  — drops boilerplate BEFORE the API
                                            |
                                            v
  [ extract ]              extract_concepts()  — one Claude call per conversation
                                            |          3-5 concepts each
                                            v
  [ merge ]        consolidate_concepts()  — OPTIONAL second pass, --consolidate
                                            |
                                            v
  [ graph ]                        build_graph()  — networkx
                                            |     nodes = concepts, weight = mentions
                                            v     edges = co-occurrence
  [ layout ]                     compute_layout()  — per-component spring + box packing
                                            |
                        +-------------------+-------------------+
                        v                   v                   v
               render_html()        render_static()       render_gif()
                 standalone              PNG                  GIF
```

Every stage is a plain function over plain data. A conversation is
`{"name", "concepts", "date"}` throughout, which is why demo mode can
substitute `concepts_from_filename` for the whole extraction stage without
touching anything downstream.

---

## Why a concept graph and not a topic list

The unit of the graph is the **concept**, not the conversation. Conversations
are the *edges*' reason for existing, never nodes themselves.

That is the decision everything else follows from. Two conversations that share
a concept become connected **through that concept**, so the graph answers "what
did I keep coming back to, and what did it connect to?" rather than "what did I
talk about on Tuesday". Node weight is mention count — how often a concept
recurs — which is what drives colour and size.

The consequence shows up starkly in demo mode: `--no-api` derives one node name
per file from its filename, and because every filename is different you get
**unconnected dots with zero edges**. It proves the pipeline runs end to end and
renders. It shows none of what the tool is for. The graph only becomes a graph
when extraction finds the *same* concept in two different conversations.

---

## Extraction

One Claude call per conversation, asking for the 3–5 most distinct concepts —
"ideas, techniques, frameworks, questions, or insights that would be worth
their own wiki article." Default model is Haiku (`DEFAULT_MODEL`), overridable
with `--model`.

**`is_noisy` runs before the API call, not after.** A conversation that is more
than 55% terminal output or error logs, or under 500 characters of real content,
is dropped. The ordering is the point: you should not pay to extract concepts
from a stack trace.

**`_accepts_temperature` is a denylist, not an allowlist.** Temperature is
passed via `extra_body` because the Anthropic SDK 1.x removed it from the
`messages.create()` signature, while the API still honours it for older
families. But newer models reject the parameter outright. Matching a denylist of
older families (`haiku-4`, `sonnet-4`, `opus-4`) means an **unrecognised model
is assumed new and the parameter is dropped**, so a future model id fails safe
rather than failing the request. An allowlist would break on every new release.

Responses are read by finding the first block whose `type` is `text`, never
`content[0]` — reasoning models return a thinking block first.

---

## Consolidation, and the problem it solves

`--consolidate` adds a second pass that merges near-synonymous concept names
into canonical ones, returning `{original: canonical}` for only the names that
change.

The problem is structural, not cosmetic. **Each conversation is analysed in
isolation**, so the same idea comes back phrased differently every time — "local
JSON state", "…persistence", "…storage" and "…management" are four nodes for one
idea. Because they share neighbours, the layout engine *correctly* stacks them
on top of each other, and the graph becomes a pile of overlapping labels.

No amount of layout tuning fixes this. The duplicates have to be merged **before
the graph is built**, which is why consolidation sits between extraction and
`build_graph` rather than anywhere later.

The prompt is deliberately conservative: merge genuine synonyms, leave anything
doubtful alone, and it is told explicitly that "model routing" and "model
consistency" are *not* the same idea. A failed consolidation call is caught and
the run continues with unmerged concepts — a degraded graph beats no graph.

It is opt-in because it costs an extra call and the merge is a judgement the
tool should not make silently.

---

## Layout

`compute_layout` is where most of the visual quality lives, and it is not a
plain `spring_layout` call.

**One conversation per cluster means the graph is usually disconnected.** Run
`nx.spring_layout` over a disconnected graph and it flings whole components
apart with nothing between them: each cluster collapses into an unreadable knot
in a mostly-empty frame.

So each connected component is laid out on its own and the resulting boxes are
packed. The box size scales with **√(node count)**, which is the non-obvious
part: a uniform grid gives a forty-node cluster the same canvas as a three-node
one, compressing the big one far harder. The small clusters look fine and the
cluster that matters most is the illegible one.

The layout seed is fixed at 42, so the same corpus renders the same way twice —
worth keeping when comparing two runs.

Single-node and empty graphs are special-cased rather than allowed to divide by
zero.

---

## Three renderers, one graph

| Renderer | Output | Notes |
|---|---|---|
| `render_html` | standalone interactive page | self-contained; pan, zoom, click a concept |
| `render_static` | PNG | the whole graph at once |
| `render_gif` | animated GIF | concepts appearing in chronological order |

All three consume the same `networkx` graph and the same layout, so they cannot
disagree about structure. `draw_graph` is shared between the matplotlib
renderers and handles the empty case by drawing "No concepts yet" rather than an
empty frame.

The GIF is the output worth running first, and the reason the tool exists in
this form: watching clusters form over time — a topic touched once in month one
going quiet, then connecting to something from month six — is a different
experience from reading a list.

The colour palette deliberately mirrors the Attractor's `AttractorView`, so
graphs from the two projects read as the same system.

---

## Crediting

Generated HTML carries **no author by default**. `WIKIGEN_CREDIT` sets one.

This is a deliberate choice with a one-line justification in the source: a name
hardcoded into the template would appear on every graph anyone produced, which
is wrong for a tool meant to be handed to other people.

---

## Privacy

Transcripts are personal. `chat_history/` is gitignored, and the tool reads
`~/.claude/projects/**/*.jsonl` — every project directory by default, which is
why `--project` exists. Without it, unrelated work shows up in the graph.

Nothing is uploaded except the conversation text sent to the extraction model,
and `is_noisy` reduces even that. Outputs (`--html`, `--static`, `--animated`)
contain concept names and conversation names, so treat a generated graph as
derived personal data before sharing it.

---

## License

MIT — see [LICENSE](LICENSE). **Jennifer Naomi Nguyen**, with **Claude** as contributor.

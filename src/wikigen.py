#!/usr/bin/env python3
"""
wikigen.py — Concept-driven wiki graph from multiple conversations.

Reads chat_history/*.txt and Claude Code ~/.claude/projects/ sessions,
calls Claude to extract key concepts, builds a knowledge graph, and outputs:
  - wiki_graph.html     (standalone interactive, default)
  - wiki_graph.png      (static image)
  - wiki_evolution.gif  (animated, concepts appearing chronologically)

Usage:
    python src/wikigen.py                # interactive HTML (default)
    python src/wikigen.py --html         # HTML only
    python src/wikigen.py --static       # PNG only
    python src/wikigen.py --animated     # GIF only
    python src/wikigen.py --no-api       # skip Claude API (demo mode)
    python src/wikigen.py --before 2026  # only sessions before a date string
"""

from __future__ import annotations

import os
import sys
import json
import glob
import math
import argparse
import textwrap
from pathlib import Path

# Optional: only needed for concept extraction. --no-api runs without it.
try:
    import anthropic
except ImportError:
    anthropic = None

try:
    import networkx as nx
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib.patches import FancyArrowPatch
    from PIL import Image
    import io
    import numpy as np
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install networkx matplotlib pillow numpy")
    sys.exit(1)


# ── Color palette (mirrors AttractorView.md) ─────────────────────────────────

BG_COLOR = "#0d0d14"
EDGE_COLOR = "#2a2a3a"

def weight_to_rgb(weight: float) -> tuple:
    """Steel blue (rare) → teal → amber → coral → violet (dominant)."""
    stops = [
        (0.00, (80,  120, 180)),
        (0.30, (100, 160, 180)),
        (0.55, (200, 170,  80)),
        (0.75, (220, 130,  90)),
        (1.00, (180, 120, 220)),
    ]
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if weight <= t1:
            t = (weight - t0) / (t1 - t0)
            r = int(c0[0] + (c1[0] - c0[0]) * t)
            g = int(c0[1] + (c1[1] - c0[1]) * t)
            b = int(c0[2] + (c1[2] - c0[2]) * t)
            return (r / 255, g / 255, b / 255)
    return (180 / 255, 120 / 255, 220 / 255)


# ── Conversation parsing ──────────────────────────────────────────────────────

def load_conversation(path: str) -> list[dict]:
    """Parse a chat_history .txt file into [{role, content}] messages."""
    messages = []
    current_role = None
    current_lines = []

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("USER:") or line.startswith("ASSISTANT:"):
                if current_role and current_lines:
                    messages.append({
                        "role": current_role.lower(),
                        "content": " ".join(current_lines).strip(),
                    })
                    current_lines = []
                parts = line.split(":", 1)
                current_role = parts[0].strip()
                rest = parts[1].strip() if len(parts) > 1 else ""
                if rest:
                    current_lines.append(rest)
            else:
                stripped = line.strip()
                if stripped and current_role:
                    current_lines.append(stripped)

    if current_role and current_lines:
        messages.append({
            "role": current_role.lower(),
            "content": " ".join(current_lines).strip(),
        })

    return messages


# ── Concept extraction ────────────────────────────────────────────────────────

def extract_concepts(client: anthropic.Anthropic, messages: list[dict], conversation_name: str) -> list[str]:
    """Ask Claude for 3-5 key concepts from a conversation. Returns concept names."""
    if not messages:
        return []

    truncated = [
        {"role": m["role"], "content": m["content"][:800] + ("..." if len(m["content"]) > 800 else "")}
        for m in messages[:40]
    ]

    system = """You are a knowledge curator. Identify the 3-5 most distinct, memorable concepts
from this conversation — things like ideas, techniques, frameworks, questions, or insights
that would be worth their own wiki article.

Return ONLY a JSON array of short concept names (2-5 words each). No explanation, no markdown.
Example: ["context window optimization", "residual stream", "attention heads"]

If the conversation has no notable concepts, return []."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            # SDK 1.x removed temperature/top_p/top_k from the messages.create()
            # signature (TypeError if passed). The API still honours them on
            # Haiku 4.5, so pass it through extra_body — low temperature keeps
            # concept extraction consistent across runs.
            extra_body={"temperature": 0.2},
            system=system,
            messages=truncated + [{"role": "user", "content": "List the key concepts from this conversation."}],
        )
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        concepts = json.loads(text)
        if isinstance(concepts, list):
            return [str(c).strip() for c in concepts if c][:5]
    except Exception as e:
        print(f"  [warn] concept extraction failed for {conversation_name}: {e}")

    return []


def load_jsonl_session(path: str) -> list[dict]:
    """Parse a Claude Code .jsonl session file into [{role, content}] messages."""
    messages = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    for role in ("user", "assistant"):
                        if d.get("type") != role or "message" not in d:
                            continue
                        msg = d["message"]
                        if not isinstance(msg, dict):
                            continue
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text", "").strip()
                                    if len(text) > 30:
                                        messages.append({"role": role, "content": text})
                        elif isinstance(content, str) and len(content.strip()) > 30:
                            messages.append({"role": role, "content": content.strip()})
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return messages


def concepts_from_filename(path: str) -> list[str]:
    """Demo mode: derive fake concepts from the filename."""
    stem = Path(path).stem.replace("conversation_", "").replace("_", " ")
    return [f"concept from {stem[:20]}"]


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph(conversations: list[dict]) -> nx.Graph:
    """
    conversations: [{"name": str, "concepts": [str], "date": str}]
    Returns a weighted graph. Node weight = mention count. Edge weight = co-occurrence.
    """
    G = nx.Graph()

    for conv in conversations:
        concepts = conv["concepts"]
        for c in concepts:
            if G.has_node(c):
                G.nodes[c]["count"] += 1
                G.nodes[c]["conversations"].append(conv["name"])
            else:
                G.add_node(c, count=1, conversations=[conv["name"]])

        # Connect concepts that appeared together
        for i in range(len(concepts)):
            for j in range(i + 1, len(concepts)):
                a, b = concepts[i], concepts[j]
                if G.has_edge(a, b):
                    G[a][b]["weight"] += 1
                else:
                    G.add_edge(a, b, weight=1)

    return G


# ── Drawing ───────────────────────────────────────────────────────────────────

def draw_graph(G: nx.Graph, ax: plt.Axes, pos: dict, highlight_nodes: set = None,
               title: str = "", alpha_scale: float = 1.0):
    ax.set_facecolor(BG_COLOR)

    if len(G.nodes) == 0:
        ax.text(0.5, 0.5, "No concepts yet", ha="center", va="center",
                color="#555566", fontsize=14, transform=ax.transAxes)
        return

    max_count = max(d["count"] for _, d in G.nodes(data=True)) or 1
    max_weight = max((d["weight"] for _, _, d in G.edges(data=True)), default=1)

    # Edges
    for u, v, data in G.edges(data=True):
        if u not in pos or v not in pos:
            continue
        w = data["weight"] / max_weight
        lw = 0.5 + w * 2.5
        alpha = 0.15 + w * 0.4
        ax.plot(
            [pos[u][0], pos[v][0]], [pos[u][1], pos[v][1]],
            color=EDGE_COLOR, linewidth=lw, alpha=alpha * alpha_scale, zorder=1
        )

    # Nodes
    for node, data in G.nodes(data=True):
        if node not in pos:
            continue
        x, y = pos[node]
        count = data["count"]
        weight = count / max_count
        color = weight_to_rgb(weight)
        radius = 120 + count * 60
        is_new = highlight_nodes and node in highlight_nodes

        # Glow for new/prominent nodes
        if is_new or weight > 0.6:
            glow_alpha = 0.25 if not is_new else 0.4
            ax.scatter(x, y, s=radius * 3.5, c=[color], alpha=glow_alpha * alpha_scale,
                       zorder=2, linewidths=0)

        ax.scatter(x, y, s=radius, c=[color], alpha=0.9 * alpha_scale,
                   zorder=3, linewidths=0)

    # Labels
    for node, data in G.nodes(data=True):
        if node not in pos:
            continue
        x, y = pos[node]
        count = data["count"]
        weight = count / max_count
        color = weight_to_rgb(weight)
        label = "\n".join(textwrap.wrap(node, width=18))
        fontsize = 7 + min(count * 1.5, 5)
        ax.text(x, y, label, ha="center", va="center", fontsize=fontsize,
                color="white", fontweight="bold" if weight > 0.5 else "normal",
                alpha=0.95 * alpha_scale, zorder=4,
                path_effects=[pe.withStroke(linewidth=2, foreground=BG_COLOR)])

    if title:
        ax.set_title(title, color="#aaaacc", fontsize=10, pad=8, loc="left")


def compute_layout(G: nx.Graph, seed: int = 42) -> dict:
    if len(G.nodes) == 0:
        return {}
    if len(G.nodes) == 1:
        return {list(G.nodes)[0]: (0.5, 0.5)}

    components = sorted(nx.connected_components(G), key=len, reverse=True)

    def _spring(g, spread=1.5):
        k = spread / (len(g.nodes) ** 0.5)
        return nx.spring_layout(g, k=k, iterations=80, seed=seed)

    if len(components) == 1:
        return _spring(G)

    # One conversation per cluster means the graph is usually disconnected.
    # spring_layout flings whole components apart with nothing between them,
    # so each cluster collapses into an unreadable knot in a mostly-empty
    # frame. Lay each component out on its own, normalise it into a unit box,
    # then pack the boxes into a grid so every cluster gets equal room.
    cols = math.ceil(math.sqrt(len(components)))
    pos = {}
    for i, comp in enumerate(components):
        sub = G.subgraph(comp)
        if len(sub.nodes) == 1:
            sub_pos = {next(iter(sub.nodes)): (0.0, 0.0)}
        else:
            # Wider spread than the single-component case: each cluster is
            # normalised into its own cell, so pushing nodes apart here buys
            # label separation without costing canvas space.
            sub_pos = _spring(sub, spread=2.6)

        xs = [float(p[0]) for p in sub_pos.values()]
        ys = [float(p[1]) for p in sub_pos.values()]
        span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
        cx = (max(xs) + min(xs)) / 2.0
        cy = (max(ys) + min(ys)) / 2.0

        col, row = i % cols, i // cols
        for node, (x, y) in sub_pos.items():
            pos[node] = ((float(x) - cx) / span * 0.78 + col * 1.25,
                         (float(y) - cy) / span * 0.78 - row * 1.25)
    return pos


# ── Static PNG ────────────────────────────────────────────────────────────────

def render_static(G: nx.Graph, output_path: str, subtitle: str = ""):
    fig, ax = plt.subplots(figsize=(12, 8), facecolor=BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_axis_off()

    pos = compute_layout(G)
    draw_graph(G, ax, pos, title="")

    # Reserve space at the top so the "concept wiki" header never lands on the
    # first cluster's labels. Autoscaled limits fit the nodes exactly, which
    # puts the top row directly under the title.
    if pos:
        ys = [p[1] for p in pos.values()]
        xs = [p[0] for p in pos.values()]
        yspan = (max(ys) - min(ys)) or 1.0
        xspan = (max(xs) - min(xs)) or 1.0
        ax.set_ylim(min(ys) - 0.10 * yspan, max(ys) + 0.34 * yspan)
        ax.set_xlim(min(xs) - 0.08 * xspan, max(xs) + 0.08 * xspan)

    node_count = len(G.nodes)
    edge_count = len(G.edges)
    fig.text(0.02, 0.97, "concept wiki", color="#8888aa", fontsize=18,
             fontweight="bold", va="top", fontfamily="monospace")
    fig.text(0.02, 0.93, f"{node_count} concepts · {edge_count} connections",
             color="#555566", fontsize=10, va="top")
    if subtitle:
        fig.text(0.02, 0.90, subtitle, color="#445566", fontsize=9, va="top")

    plt.tight_layout(pad=0.5)
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print(f"Saved: {output_path}")


# ── Animated GIF ──────────────────────────────────────────────────────────────

def render_gif(conversations: list[dict], all_G: nx.Graph, output_path: str):
    pos = compute_layout(all_G)
    frames = []
    cumulative_G = nx.Graph()
    prev_nodes = set()

    # Build frames: one per conversation + a final hold
    for i, conv in enumerate(conversations):
        if not conv["concepts"]:
            continue

        for c in conv["concepts"]:
            if cumulative_G.has_node(c):
                cumulative_G.nodes[c]["count"] += 1
                cumulative_G.nodes[c]["conversations"].append(conv["name"])
            else:
                cumulative_G.add_node(c, count=1, conversations=[conv["name"]])

        for a_idx in range(len(conv["concepts"])):
            for b_idx in range(a_idx + 1, len(conv["concepts"])):
                a, b = conv["concepts"][a_idx], conv["concepts"][b_idx]
                if cumulative_G.has_edge(a, b):
                    cumulative_G[a][b]["weight"] += 1
                else:
                    cumulative_G.add_edge(a, b, weight=1)

        new_nodes = set(conv["concepts"]) - prev_nodes
        sub_pos = {n: p for n, p in pos.items() if n in cumulative_G.nodes}

        label = conv.get("date", f"conversation {i + 1}")

        fig, ax = plt.subplots(figsize=(9, 6), facecolor=BG_COLOR)
        ax.set_facecolor(BG_COLOR)
        ax.set_axis_off()

        draw_graph(cumulative_G, ax, sub_pos, highlight_nodes=new_nodes,
                   title=f"↳ {label}")

        fig.text(0.02, 0.97, "concept wiki", color="#8888aa", fontsize=14,
                 fontweight="bold", va="top", fontfamily="monospace")
        fig.text(0.02, 0.93,
                 f"{len(cumulative_G.nodes)} concepts · conversation {i + 1}/{len(conversations)}",
                 color="#555566", fontsize=9, va="top")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor=BG_COLOR)
        plt.close(fig)
        buf.seek(0)
        frame = Image.open(buf).convert("RGB")
        frames.append(frame)
        prev_nodes = set(cumulative_G.nodes)

    if not frames:
        print("No frames to animate.")
        return

    # Resize all frames to the same size
    w = max(f.width for f in frames)
    h = max(f.height for f in frames)
    frames = [f.resize((w, h), Image.LANCZOS) for f in frames]

    # Hold the final frame longer
    hold = 6
    all_frames = frames + [frames[-1]] * hold

    durations = [800] * len(frames) + [2500] * hold

    all_frames[0].save(
        output_path,
        save_all=True,
        append_images=all_frames[1:],
        optimize=False,
        duration=durations,
        loop=0,
    )
    print(f"Saved: {output_path}")


# ── Noise filter ─────────────────────────────────────────────────────────────

_NOISE_PATTERNS = [
    r"✘ \[ERROR\]", r"npx wrangler", r"wrangler deploy", r"exit code",
    r"\$ npm ", r"node_modules", r"stack trace", r"TypeError:", r"SyntaxError:",
    r"at Object\.", r"at Module\.", r"ENOENT", r"EACCES",
    r"^\s*%\s", r"naomijnguyen@", r"MacBook",
]

import re as _re
_NOISE_RE = _re.compile("|".join(_NOISE_PATTERNS), _re.IGNORECASE | _re.MULTILINE)

def is_noisy(messages: list[dict], threshold: float = 0.55) -> bool:
    """Return True if the conversation is mostly terminal output / error logs."""
    if not messages:
        return True
    all_text = "\n".join(m["content"] for m in messages)
    noise_lines = len(_NOISE_RE.findall(all_text))
    total_lines = max(all_text.count("\n"), 1)
    total_chars = len(all_text)
    # Also skip very short conversations (< 500 chars of real content)
    if total_chars < 500:
        return True
    return (noise_lines / total_lines) > threshold


# ── HTML output ───────────────────────────────────────────────────────────────

def render_html(conversations: list[dict], G, output_path: str):
    """Render a self-contained interactive HTML with the force-directed concept graph."""

    # Build node/edge data for JS
    max_count = max((G.nodes[n]["count"] for n in G.nodes), default=1)

    nodes_js = []
    for node in G.nodes:
        d = G.nodes[node]
        weight = d["count"] / max_count
        convs  = d.get("conversations", [])
        nodes_js.append({
            "id":     node,
            "label":  node,
            "weight": round(weight, 3),
            "count":  d["count"],
            "convs":  convs,
        })

    edges_js = []
    for u, v, data in G.edges(data=True):
        edges_js.append({"from": u, "to": v, "weight": data.get("weight", 1)})

    # Build provenance table rows
    rows_html = ""
    for conv in conversations:
        if conv["concepts"]:
            label = conv.get("date", conv["name"])
            concepts_str = " · ".join(conv["concepts"])
            rows_html += f'<tr><td>{label}</td><td>{concepts_str}</td></tr>\n'

    nodes_json = json.dumps(nodes_js, ensure_ascii=False)
    edges_json = json.dumps(edges_js, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>concept wiki — oo</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0d0d14;--bg2:#10101a;--surface:#14141f;--border:#1e1e2e;
  --muted:#3a3a52;--dim:#6666aa;--text:#b0b0cc;--bright:#e8e8ff;
}}
html,body{{background:var(--bg);color:var(--text);font-family:'SF Mono','Fira Code',monospace;overflow-x:hidden}}
#hero{{position:relative;width:100%;height:100svh;min-height:480px}}
canvas{{display:block;width:100%;height:100%}}
#overlay{{position:absolute;top:0;left:0;right:0;padding:2rem 2.5rem;pointer-events:none;display:flex;justify-content:space-between;align-items:flex-start}}
#title h1{{font-size:clamp(1.4rem,3vw,2.2rem);font-weight:700;color:var(--bright);letter-spacing:-.02em}}
#title p{{font-size:clamp(.7rem,1.5vw,.85rem);color:var(--dim);letter-spacing:.04em;margin-top:.3rem}}
#meta{{text-align:right;font-size:.72rem;color:var(--muted);line-height:1.7}}
#meta a{{color:var(--dim);text-decoration:none}}
#meta a:hover{{color:var(--text)}}
#tip{{position:absolute;background:rgba(14,14,22,.95);border:1px solid var(--border);border-radius:6px;padding:.6rem .9rem;font-size:.75rem;color:var(--text);pointer-events:none;max-width:260px;line-height:1.5;opacity:0;transition:opacity .15s;z-index:10;backdrop-filter:blur(8px)}}
#tip.on{{opacity:1}}
#tip strong{{color:var(--bright);display:block;margin-bottom:.2rem}}
section{{max-width:860px;margin:0 auto;padding:4rem 2rem}}
hr{{border:none;border-top:1px solid var(--border);margin:0}}
h2{{font-size:.75rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);margin-bottom:1.5rem}}
.prose p{{font-size:clamp(.85rem,1.8vw,.95rem);line-height:1.85;color:var(--text);margin-bottom:1.2rem;font-family:-apple-system,'Segoe UI',sans-serif}}
.prose em{{color:var(--bright);font-style:italic}}
table{{width:100%;border-collapse:collapse;font-size:.78rem}}
th{{text-align:left;color:var(--dim);padding:.4rem .8rem;border-bottom:1px solid var(--border)}}
td{{padding:.5rem .8rem;border-bottom:1px solid var(--border);color:var(--text);font-family:-apple-system,'Segoe UI',sans-serif;vertical-align:top}}
td:first-child{{color:var(--muted);white-space:nowrap;font-family:monospace}}
.chips{{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.5rem}}
.chip{{font-size:.72rem;padding:.25rem .7rem;border-radius:999px;border:1px solid var(--border);color:var(--dim);background:var(--surface)}}
footer{{border-top:1px solid var(--border);padding:2rem;text-align:center;font-size:.75rem;color:var(--muted);line-height:1.8}}
footer a{{color:var(--dim);text-decoration:none}}
</style>
</head>
<body>

<section id="hero">
<canvas id="c"></canvas>
<div id="overlay">
  <div id="title">
    <h1>concept wiki</h1>
    <p>18 months of conversations with Claude — what we actually built, mapped</p>
  </div>
  <div id="meta">
    <a href="https://github.com/njarm23/oo" target="_blank" rel="noopener">github.com/njarm23/oo</a><br>
    Jennifer Nguyen Armstrong<br>
    &amp; Claude (Anthropic) · 2025–2026
  </div>
</div>
<div id="tip"></div>
</section>

<hr/>
<section class="prose">
<h2>origin</h2>
<p>It started with a lot of questions: <em>what's a transformer? What is AI? How do you generate responses?
Why do you remember some things so well? What's a residual stream? What's a context window?</em></p>
<p>Over eighteen months, I learned about AI out of curiosity about LLMs and how they responded to a question
so fluently, with such depth, and an interesting tone and recall. These questions pulled me into transformers,
context windows, RLHF, attention heads, logits, matrix multiplication, chips, and layers and
mixture-of-experts designs.</p>
<p>That led to wikis for pulling up state: <em>here's what we have, here's what we were doing, here are the
open questions.</em> Versioned updates that don't eat the whole context window. Handoff notes between
instances. Routing to save on costs. Summaries and structure that let ideas evolve over time without
needing to restart the conversation.</p>
<p>The most engineer thing I've ever done: mistyping <code>npx wrangler deploy</code> so many times
that I did the hardest possible thing just to save 300 minutes, 3e6 typos, and humiliation over the
thing I'm bad at: writing my own code. In doing so, I accidentally ended up with persistent autonomous agents.</p>
</section>

<hr/>
<section>
<h2>provenance — concepts by conversation</h2>
<table>
<thead><tr><th>conversation</th><th>concepts extracted</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
</section>

<hr/>
<section>
<h2>tech stack</h2>
<div class="chips">
  <span class="chip">Cloudflare Workers</span>
  <span class="chip">KV + D1 storage</span>
  <span class="chip">Claude (Anthropic API)</span>
  <span class="chip">React</span>
  <span class="chip">HTML Canvas</span>
  <span class="chip">Python</span>
  <span class="chip">Bash</span>
</div>
</section>

<footer>
  <a href="https://github.com/njarm23/oo" target="_blank" rel="noopener">github.com/njarm23/oo</a>
  &nbsp;·&nbsp; Jennifer Nguyen Armstrong &amp; Claude (Anthropic) &nbsp;·&nbsp; 2025–2026
</footer>

<script>
const NODES = {nodes_json};
const EDGES = {edges_json};

function weightToColor(w) {{
  if (w < .30) return {{r:80, g:120,b:180}};
  if (w < .50) return {{r:100,g:160,b:180}};
  if (w < .70) return {{r:200,g:170,b:80}};
  if (w < .85) return {{r:220,g:130,b:90}};
  return              {{r:180,g:120,b:220}};
}}
function rgba({{r,g,b}},a=1){{return `rgba(${{r|0}},${{g|0}},${{b|0}},${{a}})`}}
function lerp(a,b,t){{return a+(b-a)*t}}
function lerpC(a,b,t){{return{{r:lerp(a.r,b.r,t),g:lerp(a.g,b.g,t),b:lerp(a.b,b.b,t)}}}}

class Sim {{
  constructor(nodes,edges,w,h) {{
    this.w=w; this.h=h;
    this.nodes=nodes.map((n,i)=>{{
      const r=12+n.weight*22;
      return{{...n,
        x:w/2+Math.cos(i*2*Math.PI/nodes.length)*Math.min(w,h)*.3,
        y:h/2+Math.sin(i*2*Math.PI/nodes.length)*Math.min(w,h)*.3,
        vx:0,vy:0,radius:r}};
    }});
    this.edges=edges; this.alpha=1;
  }}
  tick() {{
    if(this.alpha<.001) return false;
    const {{nodes,edges,w,h}}=this;
    const cx=w/2,cy=h/2;
    for(let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++) {{
      let dx=nodes[j].x-nodes[i].x,dy=nodes[j].y-nodes[i].y;
      let d=Math.sqrt(dx*dx+dy*dy)||1;
      const md=(nodes[i].radius+nodes[j].radius)*2.5;
      const f=Math.max(0,(md-d)/d)*2+150/(d*d);
      const fx=dx/d*f,fy=dy/d*f;
      nodes[i].vx-=fx;nodes[i].vy-=fy;nodes[j].vx+=fx;nodes[j].vy+=fy;
    }}
    for(const e of edges) {{
      const a=nodes.find(n=>n.id===e.from),b=nodes.find(n=>n.id===e.to);
      if(!a||!b) continue;
      const dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1;
      const t=(a.radius+b.radius)*3.2,f=(d-t)*.018;
      const fx=dx/d*f,fy=dy/d*f;
      a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy;
    }}
    for(const n of nodes) {{
      n.vx+=(cx-n.x)*.004;n.vy+=(cy-n.y)*.004;
      n.vx*=.85;n.vy*=.85;
      n.x+=n.vx*this.alpha;n.y+=n.vy*this.alpha;
      const p=n.radius+12;
      n.x=Math.max(p,Math.min(w-p,n.x));n.y=Math.max(p,Math.min(h-p,n.y));
    }}
    this.alpha*=.995; return true;
  }}
  resize(w,h) {{
    const sx=w/this.w,sy=h/this.h;
    this.w=w;this.h=h;
    for(const n of this.nodes){{n.x*=sx;n.y*=sy}}
  }}
}}

const canvas=document.getElementById('c');
const ctx=canvas.getContext('2d');
const tip=document.getElementById('tip');
let sim,hov=null,settled=false;

function init() {{ sim=new Sim(NODES,EDGES,canvas.width,canvas.height); }}

function resize() {{
  const r=canvas.parentElement.getBoundingClientRect();
  const dpr=window.devicePixelRatio||1;
  canvas.width=r.width*dpr; canvas.height=r.height*dpr;
  canvas.style.width=r.width+'px'; canvas.style.height=r.height+'px';
  ctx.scale(dpr,dpr);
  if(sim) sim.resize(r.width,r.height); else init();
}}

function draw() {{
  const W=canvas.width/(window.devicePixelRatio||1);
  const H=canvas.height/(window.devicePixelRatio||1);
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#0d0d14';ctx.fillRect(0,0,W,H);
  const ns=sim.nodes;
  for(const [fi,ti] of EDGES.map(e=>[ns.find(n=>n.id===e.from),ns.find(n=>n.id===e.to)])) {{
    if(!fi||!ti) continue;
    const ih=hov&&(hov.id===fi.id||hov.id===ti.id);
    ctx.beginPath();ctx.moveTo(fi.x,fi.y);ctx.lineTo(ti.x,ti.y);
    ctx.strokeStyle=ih?'rgba(120,100,180,.45)':'rgba(40,40,64,.55)';
    ctx.lineWidth=ih?1.5:.8;ctx.stroke();
  }}
  for(const n of ns) {{
    const c=weightToColor(n.weight),ih=hov&&hov.id===n.id,r=n.radius;
    const g=ctx.createRadialGradient(n.x,n.y,r*.5,n.x,n.y,r*2.8);
    g.addColorStop(0,rgba(c,ih?.35:(n.weight>.8?.18:.08)));g.addColorStop(1,rgba(c,0));
    ctx.beginPath();ctx.arc(n.x,n.y,r*2.8,0,Math.PI*2);ctx.fillStyle=g;ctx.fill();
    ctx.beginPath();ctx.arc(n.x,n.y,r,0,Math.PI*2);ctx.fillStyle=rgba(c,.88);ctx.fill();
    if(ih){{ctx.strokeStyle=rgba(lerpC(c,{{r:255,g:255,b:255}},.4),.9);ctx.lineWidth=1.5;ctx.stroke()}}
    const words=n.label.split(' '),lines=[];let cur=words[0];
    for(let i=1;i<words.length;i++){{
      if((cur+' '+words[i]).length>14){{lines.push(cur);cur=words[i]}}else cur+=' '+words[i];
    }}
    lines.push(cur);
    const fs=9+n.weight*3.5,lh=fs+2,th=lines.length*lh;
    ctx.font=`${{n.weight>.75?600:400}} ${{fs}}px 'SF Mono',monospace`;
    ctx.textAlign='center';ctx.textBaseline='middle';
    for(let i=0;i<lines.length;i++) {{
      const ly=n.y-th/2+i*lh+lh/2;
      ctx.fillStyle='rgba(13,13,20,.75)';ctx.fillText(lines[i],n.x+.5,ly+.5);
      ctx.fillStyle=ih?'rgba(240,240,255,.98)':'rgba(200,200,230,.9)';ctx.fillText(lines[i],n.x,ly);
    }}
  }}
}}

function find(x,y){{
  if(!sim) return null;
  for(const n of sim.nodes){{const dx=n.x-x,dy=n.y-y;if(Math.sqrt(dx*dx+dy*dy)<n.radius+8)return n}}
  return null;
}}

canvas.addEventListener('mousemove',e=>{{
  const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
  hov=find(x,y);
  if(hov){{
    const convList=hov.convs&&hov.convs.length?`<span style="font-size:.7rem;color:#555577;display:block;margin-top:.3rem">${{hov.convs.length}} conversation${{hov.convs.length>1?'s':''}}</span>`:'';
    tip.innerHTML=`<strong>${{hov.label}}</strong>appeared ${{hov.count}}x${{convList}}`;
    tip.classList.add('on');
    tip.style.left=Math.min(e.clientX+14,window.innerWidth-280)+'px';
    tip.style.top=Math.max(e.clientY-10,8)+'px';
  }} else tip.classList.remove('on');
}});
canvas.addEventListener('mouseleave',()=>{{hov=null;tip.classList.remove('on')}});

function tick(){{
  if(sim){{
    if(!settled)settled=!sim.tick();
    else for(const n of sim.nodes){{n.x+=(Math.random()-.5)*.15;n.y+=(Math.random()-.5)*.15}}
    draw();
  }}
  requestAnimationFrame(tick);
}}

window.addEventListener('resize',resize);
resize();tick();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate concept wiki graph from conversations")
    parser.add_argument("--dir", default="chat_history", help="Directory of conversation .txt files")
    parser.add_argument("--html",     action="store_true", help="Output standalone interactive HTML")
    parser.add_argument("--static",   action="store_true", help="Output static PNG")
    parser.add_argument("--animated", action="store_true", help="Output animated GIF")
    parser.add_argument("--no-api",   action="store_true", help="Skip Claude API (demo mode)")
    parser.add_argument("--before",   default="", help="Only include sessions whose filename contains a date string before this (e.g. '2026-05')")
    parser.add_argument("--out", default=".", help="Output directory for generated files")
    parser.add_argument("--project", default="", help="Only include Claude Code session dirs whose name contains this substring (e.g. 'Anthropic')")
    args = parser.parse_args()

    # Default: HTML. Only skip HTML if explicit image flags given.
    any_explicit = args.html or args.static or args.animated
    do_html     = args.html     or not any_explicit
    do_static   = args.static
    do_animated = args.animated

    # Find conversation files — .txt from chat.py, or .jsonl from Claude Code sessions
    txt_files  = sorted(glob.glob(os.path.join(args.dir, "*.txt")))
    jsonl_files = sorted(glob.glob(os.path.join(args.dir, "*.jsonl")))

    # Also auto-discover Claude Code session files.
    # Previously hardcoded to ~/.claude/projects/-Users-<user>-Documents-GitHub-oo,
    # which stopped existing when oo moved out of ~/Documents/GitHub. Scan every
    # project directory instead; --project filters to matching ones.
    claude_root = os.path.expanduser("~/.claude/projects")
    if os.path.isdir(claude_root):
        for proj in sorted(glob.glob(os.path.join(claude_root, "*"))):
            if not os.path.isdir(proj):
                continue
            if args.project and args.project not in os.path.basename(proj):
                continue
            for f in sorted(glob.glob(os.path.join(proj, "*.jsonl"))):
                if f not in jsonl_files:
                    jsonl_files.append(f)

    files = txt_files + jsonl_files
    if not files:
        print(f"No conversation files found in {args.dir}/ or Claude Code sessions.")
        print("Run chat.py first, or use --no-api for demo mode.")
        sys.exit(0)

    print(f"Found {len(txt_files)} chat.py conversation(s) + {len(jsonl_files)} Claude Code session(s)")

    # Set up Claude client
    client = None
    if not args.no_api:
        if anthropic is None:
            print("Concept extraction needs the anthropic package: pip install anthropic")
            print("Or run with --no-api to derive concepts from filenames instead.")
            sys.exit(1)
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ANTHROPIC_API_KEY not set. Run with --no-api for demo mode.")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)

    # Apply --before filter
    if args.before:
        files = [f for f in files if args.before not in Path(f).name or Path(f).name < args.before]

    # Process each conversation
    conversations = []
    skipped = 0
    for path in files:
        name = Path(path).stem
        date = name.replace("conversation_", "").replace("_", " ")

        if args.no_api:
            concepts = concepts_from_filename(path)
        else:
            if path.endswith(".jsonl"):
                messages = load_jsonl_session(path)
            else:
                messages = load_conversation(path)

            if is_noisy(messages):
                skipped += 1
                continue

            print(f"  Processing: {name}")
            concepts = extract_concepts(client, messages, name)

        print(f"    Concepts: {concepts or '(none)'}")
        conversations.append({"name": name, "date": date, "concepts": concepts})

    if skipped:
        print(f"  (skipped {skipped} noisy/short session(s) — mostly error logs or terminal output)")

    # Build full graph
    G = build_graph(conversations)
    print(f"\nGraph: {len(G.nodes)} concepts, {len(G.edges)} connections")

    if len(G.nodes) == 0:
        print("No concepts extracted. Try --no-api for a demo, or check your conversation files.")
        sys.exit(0)

    os.makedirs(args.out, exist_ok=True)

    if do_html:
        out_html = os.path.join(args.out, "wiki_graph.html")
        render_html(conversations, G, out_html)

    if do_static:
        out_png = os.path.join(args.out, "wiki_graph.png")
        subtitle = f"from {len(conversations)} conversation(s)"
        render_static(G, out_png, subtitle=subtitle)

    if do_animated:
        out_gif = os.path.join(args.out, "wiki_evolution.gif")
        render_gif(conversations, G, out_gif)

    print("\nDone.")
    if do_html:
        print(f"  Open: {os.path.abspath(out_html)}")
        print("  Or deploy it: npx wrangler pages deploy <dir>")
    if do_static:
        print("  Embed: ![concept wiki](wiki_graph.png)")
    if do_animated:
        print("  Embed: ![concept wiki evolution](wiki_evolution.gif)")


if __name__ == "__main__":
    main()

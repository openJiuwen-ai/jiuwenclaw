---
name: dovescore-evaluator
version: 0.1.0
author: Danna Zheng
description: Evaluate long-form information alignment between source and target text with DoveScore, including factual accuracy, descriptive facts, event extraction, and event-order consistency. Use when checking whether generated, summarized, rewritten, or reordered text preserves source facts and temporal or causal sequence.
tags: [evaluation, factuality, alignment, dovescore]
allowed_tools: [bash, read_file, write_file]
---

# DoveScore Evaluator

Use this skill when a user asks whether a target text is faithful to a source text and event order matters. DoveScore is best for long-form information alignment, montage-style lies, narrative rewrites, summaries, biographies, reports, timelines, and other outputs where true facts can still become misleading if reordered.

## Requirements

DoveScore is an optional JiuwenClaw dependency. If it is not installed, install JiuwenClaw with:

```bash
pip install -e ".[dovescore]"
```

For local development from a DoveScore checkout, this is also acceptable:

```bash
pip install -e /path/to/DoveScore
```

Set the API key as `OPENAI_API_KEY` or pass it with `--api-key`. The default backbone is `gpt-4o-mini`.

## Workflow

1. Get both inputs from the user: the reference `source` text and the `target` text to evaluate.
2. Prefer file input for long text. Save or use existing files, then run:

```bash
python scripts/run_dovescore.py --source-file source.txt --target-file target.txt
```

3. For short text, direct arguments are acceptable:

```bash
python scripts/run_dovescore.py --source "source text" --target "target text"
```

4. Report the overall score first, then explain event accuracy, order consistency, descriptive accuracy, and any extracted facts that clarify the judgment.
5. If the user needs machine-readable output, pass `--output result.json`.

## Output Fields

See `references/usage.md` when you need detailed interpretation guidance or troubleshooting notes.

Core fields:

- `total_score`: overall alignment score.
- `event_score`: factual correctness of event facts.
- `order_score`: consistency of verified event order between source and target.
- `descriptive_score`: factual correctness of descriptive facts.
- `events` and `descriptives`: extracted facts used for scoring.

## Boundaries

Do not present DoveScore as a general semantic similarity metric. It evaluates source-target information alignment and is especially useful when temporal, causal, or ordered-event consistency is part of the question.

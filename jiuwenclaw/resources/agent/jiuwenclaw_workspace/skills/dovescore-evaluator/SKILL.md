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

## 功能概述

DoveScore Evaluator 用于评估源文本与目标文本之间的长文本信息对齐情况。它不仅检查目标文本中的事实是否被源文本支持，还会关注事件顺序是否一致，适合用于摘要、改写、时间线、新闻报道、人物传记和其他包含事件链条的长文本评估。

该 skill 会调用 DoveScore 输出整体分数、事件事实准确率、事件顺序一致性、描述性事实准确率，以及用于评分的事件和描述性事实列表。

## 配置方式

DoveScore 作为 JiuwenClaw 的可选依赖提供。需要使用该 skill 时，在 JiuwenClaw 仓库根目录安装：

```bash
pip install -e ".[dovescore]"
```

随后配置 OpenAI API key：

```bash
export OPENAI_API_KEY="your-api-key"
```

默认模型为 `gpt-4o-mini`，也可以在运行 `scripts/run_dovescore.py` 时通过 `--backbone` 指定其他模型。

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

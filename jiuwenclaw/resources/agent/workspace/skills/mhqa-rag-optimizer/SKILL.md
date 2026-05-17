---
name: mhqa-rag-optimizer
version: 1.0.0
author: wenyu-huang
description: Improve multi-hop question answering accuracy by reordering retrieved documents in forward reasoning-chain order before passing them to the LM. Based on findings from "Masking in Multi-hop QA" (ACL 2025). Use when a question requires synthesising evidence from multiple sources or when RAG answer quality is poor on compositional questions.
tags: [rag, multi-hop, qa, reasoning, document-ordering]
allowed_tools: [webSearch, readFile]
---

# MHQA RAG Optimizer

Use this skill when a user asks a complex question that requires reasoning across two or more retrieved documents (multi-hop QA), or when RAG answer quality is low on compositional or chained questions. The skill applies context-ordering heuristics derived from research on how the causal mask in decoder-only LMs limits cross-document reasoning.

## 背景与原理

本 skill 基于 ACL 2025 论文《Masking in Multi-hop QA: An Analysis of How Language Models Perform with Context Permutation》（Wenyu Huang et al.）的核心发现，将其转化为可在 JiuwenClaw RAG 流程中直接使用的提示与上下文编排策略。

**核心发现：**

1. **文档顺序影响答案质量**：将检索文档按推理链顺序排列（第一跳文档在前，末跳文档在后）可显著提升模型的准确率。
2. **gold 文档之间的距离越近越好**：无关噪声文档应推至上下文两端，减少 gold 文档之间的间隔。

## When to Use

- User asks a question that requires chaining information from 2+ documents or sources (e.g. "What is the nationality of the director of [Film X]?")
- Agent has retrieved multiple documents and needs to synthesise them to answer
- Answer quality is poor or inconsistent on multi-hop or compositional questions

## Workflow

1. **Decompose the question** into ordered sub-questions (hops). Identify which piece of information must be found first to unlock the next.

2. **Retrieve documents** for each hop. For each sub-question, retrieve the most relevant document or passage.

3. **Reorder documents in forward reasoning-chain order**: place the document answering the 1st-hop question first, followed by 2nd-hop, and so on. Push noise/irrelevant documents to the beginning or end of the context, away from the gold documents.

4. **Construct the prompt** with the reordered document list and generate the answer.

5. **(Optional) Context permutation + majority voting**: randomly shuffle all documents `k` times, call the API once per shuffle (all with `temperature=0`), and pick the most common answer. This is a deterministic alternative to high-temperature sampling — instead of stochastic variation within one context, you vary the context order itself. Use `scripts/permute_and_vote.py` to automate this.

## Using the Script

Install the `openai` package if not already available:

```bash
pip install openai
```

Run with document files (one text file per retrieved document):

```bash
python scripts/permute_and_vote.py \
  --question "What country is the birthplace of the director of Inception?" \
  --docs doc1.txt doc2.txt doc3.txt \
  --k 5 \
  --output result.json
```

- `--k`: number of random shuffles to run (default: 5). Each shuffle is one API call.
- `--seed`: optional integer for reproducibility.
- `--model`: defaults to `MHQA_MODEL` env var or `gpt-4o-mini`.
- `--base-url`: optional, for OpenAI-compatible endpoints.

The script returns `majority_answer` (most common answer across all `k` runs) along with per-shuffle details.

## Prompt Template

```
You are given the following documents in the order relevant to answering the question step by step.

[Document 1 — answers sub-question 1]
{doc_1}

[Document 2 — answers sub-question 2]
{doc_2}

...

[Noise documents]
{noise_docs}

Question: {question}
Answer step by step, citing which document supports each reasoning step.
```

## Output

- The final answer generated from the reordered context.
- A brief explanation of which document supported each reasoning hop.

## Boundaries

- This skill is a **prompting and context-ordering layer only**; it does not require model fine-tuning or access to model internals.
- Not designed for single-hop retrieval or open-ended generation without a retrievable source.
- Does not replace a retrieval system; assumes documents have already been fetched.

See `references/acl2025-masking-mhqa.md` for detailed paper findings and experimental results.

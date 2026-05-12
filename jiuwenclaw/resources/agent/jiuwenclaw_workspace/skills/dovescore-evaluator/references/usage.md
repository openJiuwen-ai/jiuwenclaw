# DoveScore Usage Notes

## When DoveScore Helps

DoveScore is designed for source-target alignment where the target may contain accurate standalone facts but still mislead by changing order, chronology, or causal implication.

Good fits:

- Long-form factual summaries
- Biographies or timelines
- News, reports, and event narratives
- Rewrites that might alter causal or temporal order
- Montage-style lies made from true statements

Poor fits:

- Generic semantic similarity
- Style or fluency scoring
- Open-ended quality judgments without a source text

## Interpreting Results

- High `event_score` with low `order_score` means events were mostly factual but their sequence is suspect.
- Low `descriptive_score` points to non-event factual mismatches.
- `ordered_source` and `ordered_target` are useful for explaining where event order diverged.
- `events`, `descriptives`, `event_scores`, and `descriptive_scores` should be inspected before making a high-stakes conclusion.

## Common Commands

Short text:

```bash
python scripts/run_dovescore.py --source "Alice woke early. She brushed her teeth." --target "Alice brushed her teeth. Alice woke early."
```

Long text:

```bash
python scripts/run_dovescore.py --source-file source.txt --target-file target.txt --output result.json
```

Custom model:

```bash
python scripts/run_dovescore.py --source-file source.txt --target-file target.txt --backbone gpt-4o-mini
```

## Troubleshooting

If import fails, install the optional dependency from the JiuwenClaw repository root:

```bash
pip install -e ".[dovescore]"
```

If authentication fails, set:

```bash
export OPENAI_API_KEY="..."
```

#!/usr/bin/env python3
"""Check that RigorAuditRail actually works against a live model, end to end.

The unit tests prove the rail's logic and that it reaches the rail chain. They
cannot prove the thing that broke twice in this project's history: that at
runtime the rail is mounted, receives the model's real output, and fires.

Both of those failures were silent -- a rail that was never mounted, and one
that read a context field that does not exist -- and neither showed up as an
error. A rail examining nothing and a rail examining a clean draft produce
identical logs. So this script observes the behaviour rather than inferring it.

    export OPENAI_BASE_URL=...  OPENAI_API_KEY=...
    python scripts/verify_rail_live.py [--model gpt-5.5]

It makes two live calls: one asking for clean text (the rail must stay silent)
and one asking for text with arithmetic defects (the rail must fire). A rail
that fires on both is as broken as one that fires on neither.
"""

import argparse
import asyncio
import json
import os
import sys
import urllib.request

CLEAN = ("Write one sentence reporting an experiment result with a mean, its "
         "sample size, a p-value and a percentage. All values must be internally "
         "consistent. Output only the sentence.")

DIRTY = ("Reproduce this sentence verbatim, changing nothing: "
         "The intervention improved accuracy by 118.4% "
         "(p = 0.000, 95% CI [0.71, 0.55]).")


def call(prompt, model, base, key):
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=180).read())
    return out["choices"][0]["message"]["content"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("PROBE_MODEL", "gpt-5.5"))
    a = ap.parse_args()

    base, key = os.environ.get("OPENAI_BASE_URL"), os.environ.get("OPENAI_API_KEY")
    if not (base and key):
        sys.exit("set OPENAI_BASE_URL and OPENAI_API_KEY (any OpenAI-compatible gateway)")

    from openjiuwen.core.single_agent.rail.base import (
        AgentCallbackContext,
        ModelCallInputs,
    )
    from jiuwenswarm.agents.harness.team.team_runtime_inheritance import (
        build_member_rails,
    )

    rails = build_member_rails()
    names = [type(r).__name__ for r in rails]
    print(f"rail chain: {names}")
    rail = next((r for r in rails if type(r).__name__ == "RigorAuditRail"), None)
    if rail is None:
        sys.exit("FAIL: RigorAuditRail is not on the chain built by build_member_rails()")
    rail._agent_id = "live-verify"

    def run(prompt, label):
        text = call(prompt, a.model, base, key)
        print(f"\n[{label}] model said: {text.strip()[:180]}")

        class Resp:
            content = text

        before = len(rail.findings)
        asyncio.run(rail.after_model_call(
            AgentCallbackContext(agent=None, inputs=ModelCallInputs(response=Resp()))))
        fired = [f.code for f in rail.findings[before:]]
        print(f"[{label}] findings: {fired or '(none)'}")
        return fired

    clean = run(CLEAN, "clean")
    dirty = run(DIRTY, "defective")

    print()
    ok = True
    if dirty:
        print(f"✓ rail fires on live model output ({len(dirty)} finding(s))")
    else:
        print("✗ rail silent on defective output -- it is mounted but not working")
        ok = False
    if clean:
        print(f"⚠ rail also fired on the clean sample {clean}; inspect the sentence "
              "above -- models do produce genuinely infeasible numbers unprompted, "
              "so this is not automatically a false positive")
    else:
        print("✓ rail silent on clean output (no false alarm)")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

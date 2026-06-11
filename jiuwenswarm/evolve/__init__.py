# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""JiuwenSwarm Self-Evolution Framework.

Provides a pluggable, dual-track evolution pipeline:

* **Component evolution track**: ``Trace -> Proposal -> Decision -> Apply``
  for Skills, Memory, and Tools.
* **Model evolution track**: Training Candidate data pool for future model
  fine-tuning (separate from the main pipeline).

Both tracks share OTEL trace data as the single source of truth.
"""

from jiuwenswarm.evolve.registry import (
    apply_writers,
    decision_policies,
    proposal_generators,
    trace_samplers,
    Registry,
)

__all__ = [
    "Registry",
    "apply_writers",
    "decision_policies",
    "proposal_generators",
    "trace_samplers",
]

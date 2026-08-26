# workswarm-skills-ascend

Optional skill pack for JiuwenSwarm targeting the Huawei Ascend NPU ecosystem.

It bundles four skills for Ascend operator development and profiling:

- `akg-agents` — drives AKG (MindSpore auto kernel generator) operator tasks
- `ascend-moe-optimizer-auto-trace` — TRACE_POINT instrumentation for Ascend operators
- `ascend-moe-optimizer-trace-analyzer` — Chrome/Perfetto trace analysis for Ascend MoE operators
- `deepep-to-cam-converter` — DeepEP to CAM operator migration (CUDA/NCCL to NPU/HCCL)

These skills assume an Ascend/CANN toolchain and their content is written in
Chinese. They are not part of the default JiuwenSwarm install; to enable them:

```bash
pip install "workswarm[ascend-skills]"
# or
pip install workswarm-skills-ascend
```

Once installed, the skills appear in the JiuwenSwarm builtin skill catalog and
can be installed into a workspace from there.

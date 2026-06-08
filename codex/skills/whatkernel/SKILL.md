---
name: whatkernel
description: Use when the user asks which DLC custom kernels a pass targets, asks for `whatkernel <pass>` or `/whatkernel <pass>`, or wants pass-hit kernels ranked by a stats counter for follow-up IR/MIR/asm or performance analysis.
---

# whatkernel

Trigger this skill when the user says `whatkernel <pass-source-file>`, `/whatkernel <pass-source-file>`, or asks for the ranked DLC kernels hit by a compiler pass. Use pass source filenames/stems such as `DLCMachineCSE.cpp` or `DLCMachineCSE`; do not use `DEBUG_TYPE` as the input key.

## Required action

Run the local helper instead of explaining the workflow from memory:

```bash
/root/bin/whatkernel <pass-source-file> [flags]
```

## Workflow

1. Resolve the pass source filename/stem from the registry cache or by scanning DLC pass source files.
2. Execute `/root/bin/whatkernel <pass-source-file> [flags]`.
3. Summarize:
   - candidate kernel count
   - top kernels by counter descending
   - output directory for the full report
   - any compile failure, missing stats, or unknown pass issue
4. If the pass is not registered, let `whatkernel` auto-discover and auto-register it; only explain manual follow-up if discovery fails.

## Good defaults

- Fresh run: `/root/bin/whatkernel DLCMachineCSE.cpp --top 20`
- Keep raw stats: `/root/bin/whatkernel DLCMachineCSE.cpp --artifacts stats --top 20`
- Single-kernel smoke run: `/root/bin/whatkernel DLCMachineCSE.cpp --source gptq_gemm --jobs 1`
- Re-summarize existing output: `/root/bin/whatkernel DLCMachineCSE.cpp --out-dir <run-dir> --summarize-existing`

## Notes

- `candidate=yes` means at least one configured candidate counter met the pass threshold.
- `primary counter` is the counter used to rank kernels when a pass exposes multiple stats.
- The default job count is `8`, and the default artifact level is `summary`; use `--artifacts stats` to keep raw stats JSON or `--artifacts debug` to keep all per-kernel files.
- The tool shows source paths as `/dlc_kernels/...`.
- For implementation or onboarding details, read `/root/docs/pass/whatkernel.md`.

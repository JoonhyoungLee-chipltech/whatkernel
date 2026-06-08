# whatkernel

`whatkernel` is a Codex-friendly helper for answering:

> Which DLC custom kernels does this compiler pass hit?

It replays DLC custom-kernel compile commands with LLVM stats enabled, then ranks kernels by the pass' `STATISTIC(...)` counters.

Primary example pass:

```text
/root/LLVM/llvm/lib/Target/DLC/DLCMachineCSE.cpp
```

Use it through Codex first; direct CLI usage is also available.

## Install

Default team layout:

```text
/root/LLVM
/root/LLVM/build/bin/clang
/root/DLC_Custom_Kernel
/root/DLC_Custom_Kernel/build/build.ninja
/root/DLC_Custom_Kernel/dlc_kernels
```

Install:

```bash
git clone https://github.com/JoonhyoungLee-chipltech/whatkernel.git
cd whatkernel
./install.sh --prefix /root
```

Install and run a one-kernel smoke test:

```bash
./install.sh --prefix /root --smoke
```

`install.sh` installs both the runtime helper and the Codex skill:

```text
/root/bin/whatkernel
/root/tools/whatkernel
/root/.codex/skills/whatkernel/SKILL.md
```

If Codex does not pick up the skill immediately, start a new Codex session after installation.

## Use From Codex

Ask Codex directly:

```text
whatkernel dlc-machine-cse
```

or:

```text
/whatkernel dlc-machine-cse
```

Codex will run:

```bash
/root/bin/whatkernel dlc-machine-cse
```

and summarize:

- candidate kernel count
- top kernels ranked by the primary counter
- output directory for full artifacts
- compile failures, missing stats, or unknown-pass issues

## Common Codex Requests

Quick smoke test:

```text
whatkernel dlc-machine-cse --limit 1 --jobs 1 --top 5
```

Focus on a kernel family:

```text
whatkernel dlc-machine-cse --source gptq_gemm --jobs 1 --top 20
```

Keep raw stats JSON for follow-up analysis:

```text
whatkernel dlc-machine-cse --artifacts stats --top 20
```

Keep all compile commands, stdout/stderr, asm, and stats:

```text
whatkernel dlc-machine-cse --artifacts debug --limit 5 --jobs 1
```

Rebuild a report from an existing run without recompiling:

```text
whatkernel dlc-machine-cse --out-dir /tmp/whatkernel/dlc-machine-cse/<run-id> --summarize-existing
```

Ask Codex to inspect the generated artifacts:

```text
Open the report from the last whatkernel run and summarize the top candidate kernels.
```

## Useful Options

- `--source <selector>`: limit to source paths, stems, or basenames containing the selector. Can be repeated.
- `--limit <N>`: process only the first `N` selected kernel sources; useful for smoke tests.
- `--jobs <N>`: parallel compile jobs. Default is `8`; use `1` for easier debugging.
- `--top <N>`: number of top candidate kernels to print. Default is `20`.
- `--artifacts summary`: keep only reports and structured summaries. This is the default.
- `--artifacts stats`: also keep raw stats JSON.
- `--artifacts debug`: keep commands, stdout, stderr, assembly, and stats for every processed kernel.
- `--out-dir <dir>`: write or re-read a specific run directory.
- `--summarize-existing`: regenerate summary/report from an existing output directory without recompiling.
- `--refresh-auto`: refresh auto-discovered pass metadata before running.
- `--llvm <dir>`, `--repo <dir>`, `--build-dir <dir>`: override default checkout/build paths.

## Non-`/root` Layouts

If your workspace is not under `/root`, install with a different prefix:

```bash
./install.sh --prefix /home/alice
```

This installs:

```text
/home/alice/bin/whatkernel
/home/alice/tools/whatkernel
/home/alice/.codex/skills/whatkernel/SKILL.md
```

`install.sh` rewrites the installed Codex skill paths for that prefix, so Codex calls `/home/alice/bin/whatkernel` instead of `/root/bin/whatkernel`.

If LLVM or DLC custom kernels are also elsewhere, pass explicit paths in the Codex request:

```text
whatkernel dlc-machine-cse --llvm /home/alice/LLVM --repo /home/alice/DLC_Custom_Kernel --build-dir /home/alice/DLC_Custom_Kernel/build
```

Direct CLI equivalent:

```bash
/home/alice/bin/whatkernel dlc-machine-cse \
  --llvm /home/alice/LLVM \
  --repo /home/alice/DLC_Custom_Kernel \
  --build-dir /home/alice/DLC_Custom_Kernel/build
```

For repeated use, prefer a shared team layout or a small wrapper script that includes the path overrides.

## Pass Requirements

`whatkernel` is stats-only. The target pass must expose at least one LLVM `STATISTIC(...)` counter.

For example, `/root/LLVM/llvm/lib/Target/DLC/DLCMachineCSE.cpp` has counters such as:

```cpp
STATISTIC(NumCSEsDLC, "Number of common subexpression eliminated");
```

Passes without `STATISTIC(...)` cannot be ranked by `whatkernel`. Add a meaningful counter to the pass first, rebuild LLVM, then rerun `whatkernel`.

Typical workflow for a no-counter pass:

1. Add `#include "llvm/ADT/Statistic.h"` if needed.
2. Add one or more `STATISTIC(...)` counters near the pass' other file-level definitions.
3. Increment the counter at the transformation point you care about.
4. Rebuild clang, for example `ninja -C /root/LLVM/build clang`.
5. Run `whatkernel <pass>` again.

If a pass has no counters, `whatkernel` fails clearly instead of falling back to assembly diffing:

```text
pass 'dlc-peephole' has no STATISTIC counters; stats-only whatkernel requires at least one pass counter
```

## Output

Each run writes under:

```text
/tmp/whatkernel/<pass>/<timestamp>/
```

Always-kept files:

```text
summary.csv
summary.json
run_metadata.json
report.md
compile_command_manifest.txt
discovered_pass_config.json
```

Useful fields:

- `candidate=yes`: at least one configured candidate counter met the threshold.
- `primary counter`: the counter used to rank kernels.
- `inconclusive`: compile failed, stats were missing, or the pass could not be resolved.

## Direct CLI

Codex normally runs the helper for you, but direct CLI usage is supported:

```bash
/root/bin/whatkernel --list-passes
/root/bin/whatkernel dlc-machine-cse --limit 1 --jobs 1 --top 5
/root/bin/whatkernel dlc-machine-cse --jobs 8 --top 20
```

## Development Checks

From this repository:

```bash
python3 -m py_compile tools/whatkernel/collect_pass_stats.py tools/whatkernel/whatkernel.py
bin/whatkernel --list-passes
bin/whatkernel dlc-machine-cse --limit 1 --jobs 1 --top 5 --artifacts summary
```

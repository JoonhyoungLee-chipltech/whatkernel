# whatkernel

`whatkernel` ranks DLC custom kernels hit by a DLC/LLVM compiler pass using LLVM `STATISTIC(...)` counters.

It is intended for quick pass bring-up, rollout planning, and follow-up kernel analysis.

## Requirements

The default installation and command paths assume this layout:

```text
/root/LLVM
/root/LLVM/build/bin/clang
/root/DLC_Custom_Kernel
/root/DLC_Custom_Kernel/build/build.ninja
/root/DLC_Custom_Kernel/dlc_kernels
```

Required tools:

```text
python3
ninja
cmake  # only needed if the DLC custom kernel build tree must be configured
```

## Install

```bash
git clone https://github.com/JoonhyoungLee-chipltech/whatkernel.git
cd whatkernel
./install.sh --prefix /root
```

Install without Codex skill files:

```bash
./install.sh --prefix /root --no-codex-skill
```

Install and run a one-kernel smoke test:

```bash
./install.sh --prefix /root --smoke
```

## Codex Quick Start

This repository is primarily intended for Codex users. After installation, ask Codex to run the skill directly:

```text
whatkernel dlcasmprinter
```

or:

```text
/whatkernel dlcasmprinter
```

Codex will use the installed skill at:

```text
/root/.codex/skills/whatkernel/SKILL.md
```

and run the local helper:

```bash
/root/bin/whatkernel dlcasmprinter
```

Codex should summarize:

- candidate kernel count
- top kernels by counter descending
- output directory for full artifacts
- compile failures, missing stats, or unknown-pass issues

### Common Codex Requests

Run a full pass scan:

```text
whatkernel dlcasmprinter
```

Run a quick smoke test:

```text
whatkernel dlcasmprinter --limit 1 --jobs 1 --top 5
```

Focus on kernels whose path/name contains a selector:

```text
whatkernel dlcmachinepipeliner --source gptq_gemm --jobs 1 --top 20
```

Keep raw stats JSON for deeper follow-up:

```text
whatkernel dlcasmprinter --artifacts stats --top 20
```

Re-summarize an existing run without recompiling:

```text
whatkernel dlcasmprinter --out-dir /tmp/whatkernel/dlcasmprinter/<run-id> --summarize-existing
```

Ask Codex to inspect the generated report:

```text
Open the report from the last whatkernel run and summarize the top candidate kernels.
```

### Useful Options

- `--source <selector>`: limit to source paths, stems, or basenames containing the selector. Can be repeated.
- `--limit <N>`: process only the first `N` selected kernel sources; useful for smoke tests.
- `--jobs <N>`: parallel compile jobs. Default is `8`; use `1` for easier debugging.
- `--top <N>`: number of top candidate kernels to print. Default is `20`.
- `--artifacts summary`: keep only report and structured summary files. This is the default.
- `--artifacts stats`: also keep raw stats JSON files.
- `--artifacts debug`: keep commands, stdout, stderr, assembly, and stats for every processed kernel.
- `--out-dir <dir>`: write or re-read a specific run directory.
- `--summarize-existing`: regenerate summary/report from an existing output directory without recompiling.
- `--refresh-auto`: refresh auto-discovered pass metadata before running.
- `--llvm <dir>`, `--repo <dir>`, `--build-dir <dir>`: override default checkout/build paths.

## Direct CLI Usage

Codex normally runs these commands for you, but direct shell usage is also supported.

List registered passes:

```bash
/root/bin/whatkernel --list-passes
```

Run a small smoke test:

```bash
/root/bin/whatkernel dlcasmprinter --limit 1 --jobs 1 --top 5
```

Run a full pass scan:

```bash
/root/bin/whatkernel dlcasmprinter --jobs 8 --top 20
```

Keep raw stats JSON:

```bash
/root/bin/whatkernel dlcasmprinter --artifacts stats --top 20
```

Re-summarize an existing run without recompiling:

```bash
/root/bin/whatkernel dlcasmprinter \
  --out-dir /tmp/whatkernel/dlcasmprinter/<run-id> \
  --summarize-existing
```

If your LLVM or DLC custom kernel checkout is not under `/root`, pass explicit paths:

```bash
/root/bin/whatkernel <pass> \
  --llvm /path/to/LLVM \
  --repo /path/to/DLC_Custom_Kernel \
  --build-dir /path/to/DLC_Custom_Kernel/build
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

Artifact levels:

- `summary`: keep only reports and structured summaries.
- `stats`: also keep raw stats JSON.
- `debug`: keep all per-kernel command/stdout/stderr/assembly files.

## Stats-Only Behavior

`whatkernel` requires the pass source to expose at least one LLVM `STATISTIC(...)` counter. Passes without counters are unsupported and fail with a clear message, for example:

```text
pass 'dlc-peephole' has no STATISTIC counters; stats-only whatkernel requires at least one pass counter
```

There is no assembly diff fallback mode.

## Installed Codex Skill Files

`install.sh` installs the Codex skill by default:

```text
/root/.codex/skills/whatkernel/SKILL.md
/root/.codex/skills/whatkernel/agents/openai.yaml
```

If Codex does not pick up the skill immediately, start a new Codex session after installation.

## Development Checks

From this repository:

```bash
python3 -m py_compile tools/whatkernel/collect_pass_stats.py tools/whatkernel/whatkernel.py
bin/whatkernel --list-passes
bin/whatkernel dlcasmprinter --limit 1 --jobs 1 --top 5 --artifacts summary
```

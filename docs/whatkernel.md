# whatkernel

`whatkernel` answers a practical compiler question:

> For a DLC compiler pass, which DLC custom kernels does it hit, and how strongly?

It is intended for quick pass bring-up, rollout planning, and follow-up kernel analysis.

## Quick Start

Run it with a DLC pass source filename, case-insensitively. The `.cpp` suffix is optional.

```bash
/root/bin/whatkernel dlcasmprinter
/root/bin/whatkernel DLCAsmPrinter.cpp
```

Useful options:

```bash
# Limit to kernels whose source path/name contains a selector
/root/bin/whatkernel dlcasmprinter --source flash_attention

# Fast smoke test
/root/bin/whatkernel dlcasmprinter --limit 20

# Keep raw stats JSON for deeper debugging
/root/bin/whatkernel dlcasmprinter --artifacts stats

# Keep all raw per-kernel files, including asm/cmd/stdout/stderr
/root/bin/whatkernel dlcasmprinter --artifacts debug

# Rebuild a report from an existing output directory without recompiling
/root/bin/whatkernel dlcasmprinter \
  --out-dir /tmp/whatkernel/dlcasmprinter/<run-id> \
  --summarize-existing
```

Defaults:

- `--jobs 8`
- `--artifacts summary`
- `--top 20`
- output directory: `/tmp/whatkernel/<pass>/<run-id>/`

## Input Naming Rule

`whatkernel` discovers passes only under:

```text
/root/LLVM/llvm/lib/Target/DLC
```

The input name is matched against the pass source filename, ignoring case and allowing the `.cpp` suffix to be omitted.

Examples:

| Input | Matches |
| --- | --- |
| `dlcasmprinter` | `DLCAsmPrinter.cpp` |
| `DLCAsmPrinter` | `DLCAsmPrinter.cpp` |
| `dlcasmprinter.cpp` | `DLCAsmPrinter.cpp` |
| `DLCASMPRINTER.CPP` | `DLCAsmPrinter.cpp` |

`DEBUG_TYPE` is not used as the user-facing input key. For example, `asm-printer` is ambiguous across printer files, so use `dlcasmprinter` instead.

Stats-only `whatkernel` requires the pass source to expose at least one `STATISTIC(...)` counter. Passes without counters are reported as unsupported instead of falling back to assembly diffing.

## How It Works

A run has four main stages.

1. Resolve the pass
   - Look up the pass in the auto registry cache.
   - If it is not present, scan `/root/LLVM/llvm/lib/Target/DLC/**/*.cpp` and match by filename.
   - Extract pass metadata such as `STATISTIC(...)` counters, flags, and source fingerprint.

2. Configure stats collection
   - Use discovered `STATISTIC` counters to choose the primary and candidate counters.

3. Replay DLC kernel compiles
   - Read compile commands from the DLC custom kernel build tree.
   - Re-run the selected kernel compiles with stats flags.
   - Process all selected kernels, using `--jobs 8` by default.

4. Write summaries and artifacts
   - Rank kernels by the selected primary counter.
   - Write reports under `/tmp/whatkernel/<pass>/<run-id>/`.

## Auto Registry

`whatkernel` uses one persistent metadata cache:

```text
/root/tools/whatkernel/pass_registry.auto.yaml
```

This is not a kernel-result database. It stores pass metadata, not per-kernel results.

It records information such as:

- canonical pass name
- aliases derived from the source filename
- pass source file
- source fingerprint
- selected mode: `stats`
- available counters
- primary/candidate counters
- enable/disable flags if discovered

On later runs, the cached metadata is reused so pass resolution is stable and fast. If the pass source file changes, `whatkernel` detects the source fingerprint mismatch and refreshes the metadata automatically. Existing valid counter preferences are preserved when possible.

Per-kernel results are written to each run directory and are recomputed on a new run.

## Output Files

Each run writes a directory like:

```text
/tmp/whatkernel/dlcasmprinter/20260605T094905Z/
```

Always-kept files:

- `summary.csv`
- `summary.json`
- `run_metadata.json`
- `report.md`
- `compile_command_manifest.txt`
- `discovered_pass_config.json`

Additional files by artifact level:

- `summary` keeps only report and summary files.
- `stats` also keeps raw stats JSON.
- `debug` keeps all raw per-kernel files, including assembly, commands, stdout, and stderr.

## Reading Results

Important fields:

- `candidate=yes`
  - A stats counter crossed the configured threshold for that kernel.
- `primary counter`
  - The counter used to rank kernels.
- `inconclusive`
  - The compile failed, stats were missing, or the pass could not be isolated cleanly.

Typical next steps after a run:

- inspect the top kernels in `report.md`
- run focused IR/MIR/asm analysis on top candidates
- compare syntest cycles for the highest-impact kernels
- use the ranked list to plan pass rollout or opt-in testing

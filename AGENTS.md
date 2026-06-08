# AGENTS.md

Guidelines for maintaining this repository.

- `whatkernel` is stats-only. Do not reintroduce assembly diff fallback mode.
- Runtime entry is `bin/whatkernel` -> `tools/whatkernel/whatkernel.py` -> `tools/whatkernel/collect_pass_stats.py`.
- Keep default runtime paths compatible with `/root/LLVM` and `/root/DLC_Custom_Kernel`.
- If a pass has no `STATISTIC(...)` counters, report it as unsupported instead of producing candidate results.
- After edits, run:
  - `python3 -m py_compile tools/whatkernel/collect_pass_stats.py tools/whatkernel/whatkernel.py`
  - `bin/whatkernel --list-passes`
  - `bin/whatkernel dlcasmprinter --limit 1 --jobs 1 --top 5 --artifacts summary`
- Use focused changes; avoid broad refactors unless requested.

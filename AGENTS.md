# AGENTS.md

Guidelines for maintaining this repository.

- `whatkernel` is stats-only. Do not reintroduce assembly diff fallback mode.
- `whatkernel` input naming is source filename/stem based, e.g. `DLCMachineCSE.cpp` or `DLCMachineCSE`; do not use `DEBUG_TYPE` or compact debug-type-style aliases.
- Runtime entry is `bin/whatkernel` -> `tools/whatkernel/whatkernel.py` -> `tools/whatkernel/collect_pass_stats.py`.
- Keep default runtime paths compatible with `/root/LLVM` and `/root/DLC_Custom_Kernel`.
- If a pass has no `STATISTIC(...)` counters, report it as unsupported instead of producing candidate results.
- After edits, run:
  - `python3 -m py_compile tools/whatkernel/collect_pass_stats.py tools/whatkernel/whatkernel.py`
  - `bin/whatkernel --list-passes`
  - `bin/whatkernel DLCMachineCSE.cpp --limit 1 --jobs 1 --top 5 --artifacts summary`
- Use focused changes; avoid broad refactors unless requested.

#!/usr/bin/env bash
set -euo pipefail

prefix="${WHATKERNEL_PREFIX:-/root}"
pass_name="${1:-dlcasmprinter}"

python3 -m py_compile \
  "$prefix/tools/whatkernel/collect_pass_stats.py" \
  "$prefix/tools/whatkernel/whatkernel.py"

"$prefix/bin/whatkernel" --list-passes
"$prefix/bin/whatkernel" "$pass_name" --limit 1 --jobs 1 --top 5 --artifacts summary

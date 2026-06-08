#!/usr/bin/env bash
set -euo pipefail

prefix="/root"
install_codex_skill=1
run_smoke=0

usage() {
  cat <<'USAGE'
Usage: ./install.sh [--prefix DIR] [--no-codex-skill] [--smoke]

Installs whatkernel runtime files under DIR. Defaults to /root.

Options:
  --prefix DIR       Install under DIR instead of /root.
  --no-codex-skill  Do not install the Codex skill files.
  --smoke           Run a one-kernel smoke test after installation.
  -h, --help        Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      [[ $# -ge 2 ]] || { echo "--prefix requires a value" >&2; exit 2; }
      prefix="$2"
      shift 2
      ;;
    --no-codex-skill)
      install_codex_skill=0
      shift
      ;;
    --smoke)
      run_smoke=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install_file() {
  local src="$1"
  local dst="$2"
  install -D -m 0644 "$src" "$dst"
}

install_exec() {
  local src="$1"
  local dst="$2"
  install -D -m 0755 "$src" "$dst"
}

install_exec "$repo_dir/bin/whatkernel" "$prefix/bin/whatkernel"
install_file "$repo_dir/tools/whatkernel/whatkernel.py" "$prefix/tools/whatkernel/whatkernel.py"
install_file "$repo_dir/tools/whatkernel/collect_pass_stats.py" "$prefix/tools/whatkernel/collect_pass_stats.py"
install_file "$repo_dir/tools/whatkernel/pass_registry.auto.yaml" "$prefix/tools/whatkernel/pass_registry.auto.yaml"
install_file "$repo_dir/docs/whatkernel.md" "$prefix/docs/pass/whatkernel.md"

if [[ "$install_codex_skill" -eq 1 ]]; then
  skill_dst="$prefix/.codex/skills/whatkernel/SKILL.md"
  install_file "$repo_dir/codex/skills/whatkernel/SKILL.md" "$skill_dst"
  install_file "$repo_dir/codex/skills/whatkernel/agents/openai.yaml" "$prefix/.codex/skills/whatkernel/agents/openai.yaml"
  if [[ "$prefix" != "/root" ]]; then
    python3 - "$skill_dst" "$prefix" <<'PYEDIT'
from pathlib import Path
import sys
path = Path(sys.argv[1])
prefix = sys.argv[2].rstrip("/") or "/"
text = path.read_text(encoding="utf-8")
text = text.replace("/root/bin/whatkernel", f"{prefix}/bin/whatkernel")
text = text.replace("/root/tools/whatkernel", f"{prefix}/tools/whatkernel")
text = text.replace("/root/docs/pass/whatkernel.md", f"{prefix}/docs/pass/whatkernel.md")
path.write_text(text, encoding="utf-8")
PYEDIT
  fi
fi

python3 -m py_compile \
  "$prefix/tools/whatkernel/collect_pass_stats.py" \
  "$prefix/tools/whatkernel/whatkernel.py"

"$prefix/bin/whatkernel" --list-passes >/dev/null

echo "Installed whatkernel under $prefix"
echo "Try: $prefix/bin/whatkernel dlcasmprinter --limit 1 --jobs 1 --top 5"

if [[ "$run_smoke" -eq 1 ]]; then
  "$prefix/bin/whatkernel" dlcasmprinter --limit 1 --jobs 1 --top 5 --artifacts summary
fi

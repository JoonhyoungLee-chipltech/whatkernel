#!/usr/bin/env python3
"""Collect per-kernel pass targeting evidence for DLC custom kernels."""

from __future__ import annotations

import argparse
from collections import Counter
import concurrent.futures
import csv
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import MISSING, asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SOURCE_SUFFIXES = {".c", ".cpp"}
COMMENT_PREFIXES = ("#", "//", ";")
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("pass_registry.yaml")
DEFAULT_AUTO_REGISTRY_PATH = Path(__file__).with_name("pass_registry.auto.yaml")
DEFAULT_REPO = Path("/root/DLC_Custom_Kernel")
DEFAULT_BUILD_DIR = DEFAULT_REPO / "build"
DEFAULT_LLVM = Path("/root/LLVM")
DEFAULT_OUT_ROOT = Path("/tmp/whatkernel")
DEFAULT_PASS_DOCS_ROOT = Path("/root/docs/pass")

COUNTER_PREFERENCE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"allowed",
        r"changed",
        r"applied",
        r"transform",
        r"elim",
        r"delete",
        r"combine",
        r"fold",
        r"promot",
        r"pipeline",
        r"sink",
        r"rewrite",
        r"cse",
        r"pre",
        r"coalesc",
        r"commut",
    ]
]

PASS_BASE_PATTERNS = [
    r"PassInfoMixin<[^>]+>",
    r"MachineFunctionPass",
    r"MachineModulePass",
    r"FunctionPass",
    r"ModulePass",
    r"LoopPass",
    r"Pass",
]


@dataclass(frozen=True)
class CompileCommand:
    kernel: str
    source: Path
    rel_source: str
    cwd: Path
    argv: list[str]
    original_output: str


@dataclass
class PassConfig:
    canonical_name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    mode: str = "stats"
    source_file: str = ""
    class_name: str = ""
    registration_name: str = ""
    debug_type: str = ""
    enable_args: list[str] = field(default_factory=list)
    isolation_kind: str = ""
    counters: list[str] = field(default_factory=list)
    selected_counter: str = ""
    candidate_counters: list[str] = field(default_factory=list)
    counter_groups: dict[str, list[str]] = field(default_factory=dict)
    counter_match_mode: str = "suffix"
    positive_threshold: int = 1
    auto_generated: bool = False
    source_fingerprint: str = ""
    last_refreshed_utc: str = ""
    registry_kind: str = ""

    @property
    def name(self) -> str:
        return self.canonical_name

    def all_names(self) -> list[str]:
        names = [self.canonical_name, *self.aliases]
        return [name for index, name in enumerate(names) if name and name not in names[:index]]

    def available_counters(self) -> list[str]:
        grouped = [counter for counters in self.counter_groups.values() for counter in counters]
        return unique_strings([*self.counters, self.selected_counter, *self.candidate_counters, *grouped])

    def resolved_selected_counter(self) -> str:
        if self.selected_counter:
            return self.selected_counter
        counters = self.available_counters()
        return counters[0] if counters else ""

    def resolved_candidate_counters(self) -> list[str]:
        if self.candidate_counters:
            return unique_strings(self.candidate_counters)
        selected_counter = self.resolved_selected_counter()
        if selected_counter:
            return [selected_counter]
        return self.available_counters()

    def candidate_rule_text(self) -> str:
        counters = self.resolved_candidate_counters()
        if not counters:
            return "no candidate counters configured"
        return f"any of [{', '.join(counters)}] >= {self.positive_threshold}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "aliases": self.aliases,
            "mode": self.mode,
            "source_file": self.source_file,
            "class_name": self.class_name,
            "registration_name": self.registration_name,
            "enable_args": self.enable_args,
            "isolation_kind": self.isolation_kind,
            "counters": self.available_counters(),
            "selected_counter": self.resolved_selected_counter(),
            "candidate_counters": self.resolved_candidate_counters(),
            "counter_groups": self.counter_groups,
            "counter_match_mode": self.counter_match_mode,
            "positive_threshold": self.positive_threshold,
            "auto_generated": self.auto_generated,
            "source_fingerprint": self.source_fingerprint,
            "last_refreshed_utc": self.last_refreshed_utc,
        }


@dataclass
class Result:
    kernel: str
    source: str
    pass_name: str
    mode: str
    counter: int | str = ""
    counter_breakdown: dict[str, int] = field(default_factory=dict)
    candidate: str = ""
    status: str = ""
    stats_path: str = ""
    command_path: str = ""
    stderr_path: str = ""
    stdout_path: str = ""
    asm_path: str = ""
    reason: str = ""
    message: str = ""


@dataclass(frozen=True)
class RunPaths:
    out_dir: Path
    report: Path


@dataclass
class RunMetadata:
    pass_name: str
    description: str
    mode: str
    counter_name: str
    available_counters: list[str]
    candidate_counters: list[str]
    counter_groups: dict[str, list[str]]
    candidate_rule: str
    registry_kind: str
    auto_registered: bool
    refreshed_on_run: bool
    repo: str
    build_dir: str
    llvm: str
    kernels_dir: str
    out_dir: str
    report: str
    selected_source_count: int
    total_kernel_source_count: int
    compile_command_count: int
    run_timestamp_utc: str
    enable_args: list[str]
    isolation_kind: str
    source_selectors: list[str]
    limit: int
    jobs: int
    top: int
    summarize_existing: bool
    artifact_level: str
    registry_path: str
    auto_registry_path: str
    source_file: str = ""
    source_fingerprint: str = ""
    selected_counter: str = ""
    inactive_counters: list[str] = field(default_factory=list)
    temporary_instrumentation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcePassMetadata:
    source_file: Path
    file_stem: str
    registration_names: list[str]
    debug_types: list[str]
    class_names: list[str]
    create_names: list[str]
    counters: list[str]
    flags: list[str]


@dataclass(frozen=True)
class PipelineEntry:
    pipeline_name: str
    class_token: str
    source_file: Path | None


@dataclass
class TemporaryInstrumentation:
    source_file: str
    counter_name: str
    snapshot_path: str
    patch_path: str
    original_sha256: str
    instrumented_sha256: str = ""
    applied: bool = False
    restored: bool = False
    restore_reason: str = ""
    build_command: list[str] = field(default_factory=list)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect pass targeting evidence for DLC kernels.")
    parser.add_argument("pass_name", nargs="?", help="Pass canonical name or alias.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--auto-registry", type=Path, default=DEFAULT_AUTO_REGISTRY_PATH)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--llvm", type=Path, default=DEFAULT_LLVM)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Limit to a source path, stem, or basename. Can be repeated.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of kernels processed.")
    parser.add_argument("--jobs", type=int, default=8, help="Parallel compile jobs.")
    parser.add_argument("--top", type=int, default=20, help="Top rows to print.")
    parser.add_argument(
        "--summarize-existing",
        action="store_true",
        help="Regenerate summary/report from existing output without recompiling.",
    )
    parser.add_argument(
        "--list-passes",
        action="store_true",
        help="List curated and auto-registered passes.",
    )
    parser.add_argument(
        "--refresh-auto",
        action="store_true",
        help="Force refresh of auto-registered metadata before running.",
    )
    parser.add_argument(
        "--artifacts",
        choices=["summary", "stats", "debug"],
        default="summary",
        help="Artifact retention level: summary keeps only reports, stats keeps raw stats JSON, debug keeps all per-kernel files.",
    )
    return parser.parse_args(argv)


def run_cmd(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def normalize_name(text: str) -> str:
    lowered = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-+", "-", lowered)


def normalize_compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_name(text))


def strip_cpp_suffix(text: str) -> str:
    return text[:-4] if text.lower().endswith(".cpp") else text


def unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def kernel_source_label(rel_source: str) -> str:
    return f"/dlc_kernels/{rel_source.replace(os.sep, '/')}"


def load_registry_file(path: Path, registry_kind: str) -> dict[str, PassConfig]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                f"registry {path} must be JSON-compatible YAML when PyYAML is unavailable"
            ) from exc
        raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise RuntimeError(f"registry {path} must contain a top-level mapping")

    registry: dict[str, PassConfig] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f"registry entry {key!r} must be a mapping")
        canonical_name = str(entry.get("canonical_name", key))
        counters = [str(counter) for counter in entry.get("counters", []) if str(counter)]
        selected_counter = str(entry.get("selected_counter", counters[0] if counters else ""))
        candidate_counters = [str(counter) for counter in entry.get("candidate_counters", []) if str(counter)]
        raw_counter_groups = entry.get("counter_groups", {})
        if not isinstance(raw_counter_groups, dict):
            raise RuntimeError(f"registry entry {canonical_name!r} has non-mapping counter_groups")
        counter_groups = {
            str(group): [str(counter) for counter in counters_in_group if str(counter)]
            for group, counters_in_group in raw_counter_groups.items()
            if isinstance(counters_in_group, list)
        }
        counters = unique_strings([
            *counters,
            selected_counter,
            *candidate_counters,
            *(counter for counters_in_group in counter_groups.values() for counter in counters_in_group),
        ])
        mode = "stats"
        config = PassConfig(
            canonical_name=canonical_name,
            description=str(entry.get("description", "")),
            aliases=[str(alias) for alias in entry.get("aliases", []) if str(alias)],
            mode=mode,
            source_file=str(entry.get("source_file", "")),
            class_name=str(entry.get("class_name", "")),
            registration_name=str(entry.get("registration_name", "")),
            debug_type=str(entry.get("debug_type", "")),
            enable_args=[str(arg) for arg in entry.get("enable_args", [])],
            isolation_kind=str(entry.get("isolation_kind", "")),
            counters=counters,
            selected_counter=selected_counter,
            candidate_counters=unique_strings(candidate_counters),
            counter_groups={group: unique_strings(counters_in_group) for group, counters_in_group in counter_groups.items()},
            counter_match_mode=str(entry.get("counter_match_mode", "suffix")),
            positive_threshold=int(entry.get("positive_threshold", 1)),
            auto_generated=bool(entry.get("auto_generated", registry_kind == "auto")),
            source_fingerprint=str(entry.get("source_fingerprint", "")),
            last_refreshed_utc=str(entry.get("last_refreshed_utc", "")),
            registry_kind=registry_kind,
        )
        config.aliases = unique_strings(config.aliases)
        if config.counter_match_mode not in {"exact", "suffix"}:
            raise RuntimeError(f"registry entry {canonical_name!r} has unsupported counter_match_mode")
        registry[canonical_name] = config
    return registry


def save_registry_file(path: Path, registry: dict[str, PassConfig]) -> None:
    payload = {name: config.to_dict() for name, config in sorted(registry.items())}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def merge_registries(curated: dict[str, PassConfig], auto: dict[str, PassConfig]) -> dict[str, PassConfig]:
    merged = {name: config for name, config in auto.items()}
    merged.update({name: config for name, config in curated.items()})
    return merged


def build_alias_map(curated: dict[str, PassConfig], auto: dict[str, PassConfig]) -> dict[str, PassConfig]:
    mapping: dict[str, PassConfig] = {}
    # Match merge_registries(): curated entries override auto-discovered entries.
    for registry in (curated, auto):
        for config in registry.values():
            for alias in config.all_names():
                mapping.setdefault(alias, config)
                mapping.setdefault(normalize_name(alias), config)
    return mapping


def compute_source_fingerprint(source_file: str) -> str:
    if not source_file:
        return ""
    path = Path(source_file)
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_alias_variants(values: Iterable[str]) -> list[str]:
    aliases: list[str] = []
    for value in values:
        if not value:
            continue
        aliases.append(value)
        aliases.append(normalize_name(value))
        if value.endswith("Pass"):
            aliases.append(value[: -4])
            aliases.append(normalize_name(value[: -4]))
    return unique_strings(aliases)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_source_pass_metadata(source_file: Path) -> SourcePassMetadata:
    text = read_text(source_file)
    registration_names = re.findall(
        r'INITIALIZE_PASS(?:_BEGIN)?\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*"([^"]+)"',
        text,
        flags=re.MULTILINE,
    )
    debug_types = re.findall(r'#define\s+DEBUG_TYPE\s+"([^"]+)"', text)
    class_names = re.findall(
        rf'class\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*public\s+(?:{"|".join(PASS_BASE_PATTERNS)})',
        text,
        flags=re.MULTILINE,
    )
    create_names = re.findall(
        r'llvm::create([A-Za-z0-9_]+)\s*\(',
        text,
        flags=re.MULTILINE,
    )
    counters = re.findall(r'STATISTIC\(\s*([A-Za-z0-9_]+)\s*,', text)
    flags = re.findall(r'cl::opt<[^>]+>\s+[A-Za-z0-9_]+\s*\(\s*"([^"]+)"', text)
    return SourcePassMetadata(
        source_file=source_file,
        file_stem=source_file.stem,
        registration_names=unique_strings(registration_names),
        debug_types=unique_strings(debug_types),
        class_names=unique_strings(class_names),
        create_names=unique_strings(create_names),
        counters=unique_strings(counters),
        flags=unique_strings(flags),
    )


def collect_source_metadata(llvm: Path) -> list[SourcePassMetadata]:
    lib_root = llvm / "llvm" / "lib" / "Target" / "DLC"
    metas: list[SourcePassMetadata] = []
    for source_file in sorted(lib_root.rglob("*.cpp")):
        try:
            metas.append(extract_source_pass_metadata(source_file))
        except Exception:
            continue
    return metas


def find_source_for_token(token: str, metas: list[SourcePassMetadata]) -> Path | None:
    for meta in metas:
        if token in meta.class_names or token in meta.create_names or token == meta.file_stem:
            return meta.source_file
    for meta in metas:
        text = read_text(meta.source_file)
        if token in text:
            return meta.source_file
    return None


def collect_pipeline_entries(llvm: Path, metas: list[SourcePassMetadata]) -> list[PipelineEntry]:
    entries: list[PipelineEntry] = []
    for registry_file in [llvm / "llvm" / "lib" / "Passes" / "PassRegistry.def", llvm / "llvm" / "include" / "llvm" / "Passes" / "MachinePassRegistry.def"]:
        if not registry_file.exists():
            continue
        text = read_text(registry_file)
        for pipeline_name, expr in re.findall(
            r'(?:FUNCTION|LOOP|MODULE|CGSCC|MACHINE_FUNCTION|MACHINE_MODULE)_PASS\("([^"]+)"\s*,\s*([^\n]+)',
            text,
        ):
            class_match = re.search(r'([A-Za-z_][A-Za-z0-9_]*Pass)', expr)
            if not class_match:
                continue
            class_token = class_match.group(1)
            source_file = find_source_for_token(class_token, metas)
            entries.append(PipelineEntry(pipeline_name=pipeline_name, class_token=class_token, source_file=source_file))
    return entries


def score_pipeline_entry(input_name: str, normalized_input: str, entry: PipelineEntry) -> int:
    if input_name == entry.pipeline_name:
        return 130
    if normalized_input == normalize_name(entry.pipeline_name):
        return 125
    if input_name == entry.class_token:
        return 118
    if normalized_input == normalize_name(entry.class_token):
        return 116
    if entry.class_token.endswith("Pass") and normalize_name(entry.class_token[: -4]) == normalized_input:
        return 112
    return -1


def score_source_metadata(input_name: str, normalized_input: str, meta: SourcePassMetadata) -> int:
    raw_input = input_name.lower()
    stem_input = strip_cpp_suffix(input_name)
    normalized_stem_input = normalize_name(stem_input)

    if raw_input == meta.source_file.name.lower():
        return 130
    if stem_input.lower() == meta.file_stem.lower():
        return 128
    if normalized_input == normalize_name(meta.source_file.name):
        return 126
    if normalized_stem_input == normalize_name(meta.file_stem):
        return 124
    return -1


def pick_canonical_from_meta(meta: SourcePassMetadata) -> str:
    return meta.file_stem


def select_enable_args(meta: SourcePassMetadata, canonical_name: str) -> list[str]:
    enable_args: list[str] = []
    for flag in meta.flags:
        normalized_flag = normalize_name(flag)
        if not normalized_flag.endswith(canonical_name):
            continue
        if normalized_flag.startswith("enable-") or normalized_flag.startswith("dlc-enable-"):
            enable_args = ["-mllvm", f"-{flag}"]
    return enable_args


def select_counter(counters: list[str]) -> tuple[str, str]:
    if not counters:
        return "", "no-counters"
    if len(counters) == 1:
        return counters[0], "single-counter"
    scored: list[tuple[int, str]] = []
    for counter in counters:
        score = 0
        for index, pattern in enumerate(COUNTER_PREFERENCE_PATTERNS):
            if pattern.search(counter):
                score = 100 - index
                break
        if score:
            scored.append((score, counter))
    if not scored:
        return counters[0], "fallback-counter"
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return scored[0][1], "heuristic-counter"


def select_candidate_counters(counters: list[str], selected_counter: str) -> list[str]:
    candidates: list[str] = []
    for counter in counters:
        if any(pattern.search(counter) for pattern in COUNTER_PREFERENCE_PATTERNS):
            candidates.append(counter)
    if candidates:
        return unique_strings(candidates)
    return [selected_counter] if selected_counter else []


def finalize_discovered_config(
    canonical_name: str,
    description: str,
    meta: SourcePassMetadata,
    registry_kind: str,
    auto_generated: bool,
    aliases: Iterable[str],
) -> PassConfig:
    if not meta.counters:
        raise RuntimeError(
            f"pass {canonical_name!r} has no STATISTIC counters; "
            "stats-only whatkernel requires at least one pass counter"
        )
    enable_args = select_enable_args(meta, canonical_name)
    selected_counter, _ = select_counter(meta.counters)
    mode = "stats"
    isolation_kind = "default_pipeline_stats"

    config = PassConfig(
        canonical_name=canonical_name,
        description=description,
        aliases=unique_strings(add_alias_variants(aliases)),
        mode=mode,
        source_file=str(meta.source_file),
        class_name=meta.class_names[0] if meta.class_names else "",
        registration_name=meta.registration_names[0] if meta.registration_names else "",
        debug_type=meta.debug_types[0] if meta.debug_types else "",
        enable_args=enable_args,
        isolation_kind=isolation_kind,
        counters=meta.counters,
        selected_counter=selected_counter,
        candidate_counters=select_candidate_counters(meta.counters, selected_counter),
        counter_groups={},
        counter_match_mode="suffix",
        positive_threshold=1,
        auto_generated=auto_generated,
        source_fingerprint=compute_source_fingerprint(str(meta.source_file)),
        last_refreshed_utc=utc_timestamp(),
        registry_kind=registry_kind,
    )
    return config


def discover_pass(pass_name: str, llvm: Path, preferred_source: str = "") -> PassConfig:
    metas = collect_source_metadata(llvm)
    pipeline_entries = collect_pipeline_entries(llvm, metas)
    normalized_input = normalize_name(pass_name)

    if preferred_source:
        preferred_path = Path(preferred_source)
        meta = next((candidate for candidate in metas if candidate.source_file == preferred_path), None)
        if meta is None:
            raise RuntimeError(f"preferred source file not found during refresh: {preferred_source}")
        canonical_name = pick_canonical_from_meta(meta)
        return finalize_discovered_config(
            canonical_name=canonical_name,
            description=f"Auto-discovered pass from {meta.source_file}",
            meta=meta,
            registry_kind="auto",
            auto_generated=True,
            aliases=[meta.source_file.name, meta.file_stem],
        )

    source_candidates: list[tuple[int, SourcePassMetadata]] = []
    for meta in metas:
        score = score_source_metadata(pass_name, normalized_input, meta)
        if score >= 0:
            source_candidates.append((score, meta))
    if not source_candidates:
        raise RuntimeError(f"could not discover pass metadata for {pass_name!r}; use the pass source filename/stem, e.g. DLCMachineCSE.cpp, not DEBUG_TYPE")

    source_candidates.sort(key=lambda item: (-item[0], str(item[1].source_file)))
    top_score = source_candidates[0][0]
    top_metas = [meta for score, meta in source_candidates if score == top_score]
    if len(top_metas) != 1:
        candidates = ", ".join(str(meta.source_file) for meta in top_metas[:5])
        raise RuntimeError(f"ambiguous pass {pass_name!r}; candidate sources: {candidates}")

    meta = top_metas[0]
    canonical_name = pick_canonical_from_meta(meta)
    return finalize_discovered_config(
        canonical_name=canonical_name,
        description=f"Auto-discovered pass from {meta.source_file}",
        meta=meta,
        registry_kind="auto",
        auto_generated=True,
        aliases=[meta.source_file.name, meta.file_stem],
    )


def repair_stats_config(config: PassConfig) -> bool:
    if not config.available_counters():
        return False
    changed = False
    counters = config.available_counters()
    selected_counter, _ = select_counter(counters)
    candidate_counters = select_candidate_counters(counters, selected_counter)
    if config.mode != "stats":
        config.mode = "stats"
        changed = True
    if selected_counter and (not config.selected_counter or config.selected_counter not in counters or changed):
        config.selected_counter = selected_counter
        changed = True
    if candidate_counters and (not config.candidate_counters or changed):
        config.candidate_counters = candidate_counters
        changed = True
    if not config.enable_args and config.isolation_kind == "unsupported":
        config.isolation_kind = "default_pipeline_stats"
        changed = True
    return changed


def preserve_auto_registry_preferences(existing: PassConfig, refreshed: PassConfig) -> PassConfig:
    available = set(refreshed.available_counters())
    if existing.selected_counter and existing.selected_counter in available:
        refreshed.selected_counter = existing.selected_counter
    candidate_counters = [counter for counter in existing.candidate_counters if counter in available]
    if candidate_counters:
        refreshed.candidate_counters = candidate_counters
    counter_groups = {
        group: [counter for counter in counters if counter in available]
        for group, counters in existing.counter_groups.items()
    }
    refreshed.counter_groups = {group: counters for group, counters in counter_groups.items() if counters}
    return refreshed


def resolve_pass_config(
    pass_name: str,
    curated_path: Path,
    auto_path: Path,
    llvm: Path,
    refresh_auto: bool,
) -> tuple[PassConfig, dict[str, PassConfig], dict[str, PassConfig], bool, bool]:
    curated = load_registry_file(curated_path, "curated")
    auto = load_registry_file(auto_path, "auto")
    alias_map = build_alias_map(curated, auto)
    config = alias_map.get(pass_name) or alias_map.get(normalize_name(pass_name))
    auto_registered = False
    refreshed_on_run = False

    if config is None:
        config = discover_pass(pass_name, llvm)
        config.registry_kind = "auto"
        auto[config.canonical_name] = config
        save_registry_file(auto_path, auto)
        auto_registered = True
        refreshed_on_run = True
        return config, curated, auto, auto_registered, refreshed_on_run

    if config.registry_kind == "auto":
        if repair_stats_config(config):
            auto[config.canonical_name] = config
            save_registry_file(auto_path, auto)
            refreshed_on_run = True
        current_fingerprint = compute_source_fingerprint(config.source_file)
        if refresh_auto or (config.source_file and current_fingerprint != config.source_fingerprint):
            refreshed = discover_pass(pass_name, llvm, preferred_source=config.source_file)
            refreshed = preserve_auto_registry_preferences(config, refreshed)
            refreshed.aliases = unique_strings(config.aliases + refreshed.aliases + [config.canonical_name])
            if config.canonical_name != refreshed.canonical_name:
                refreshed.aliases = unique_strings(refreshed.aliases + [config.canonical_name])
                auto.pop(config.canonical_name, None)
            refreshed.registry_kind = "auto"
            auto[refreshed.canonical_name] = refreshed
            save_registry_file(auto_path, auto)
            config = refreshed
            refreshed_on_run = True
        return config, curated, auto, auto_registered, refreshed_on_run

    return config, curated, auto, auto_registered, refreshed_on_run


def default_out_dir(pass_name: str) -> Path:
    return DEFAULT_OUT_ROOT / pass_name / utc_timestamp()


def resolve_run_paths(pass_name: str, out_dir: Path | None, report: Path | None) -> RunPaths:
    resolved_out_dir = out_dir.resolve() if out_dir else default_out_dir(pass_name)
    resolved_report = report.resolve() if report else resolved_out_dir / "report.md"
    return RunPaths(out_dir=resolved_out_dir, report=resolved_report)


def ensure_build_tree(build_dir: Path, llvm: Path) -> None:
    if (build_dir / "build.ninja").exists():
        return
    build_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("LLVM_PATH", str(llvm))
    proc = subprocess.run(
        ["cmake", "..", "-G", "Ninja", "-DBUILD_TARGET=LIB"],
        cwd=str(build_dir),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "failed to configure DLC_Custom_Kernel build tree\n" + proc.stdout + proc.stderr
        )


def split_shell_segments(command: str) -> list[str]:
    return [part.strip() for part in command.split("&&") if part.strip()]


def parse_cd_segment(segment: str, fallback: Path) -> Path:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return fallback
    if len(tokens) >= 2 and tokens[0] == "cd":
        return Path(tokens[1]).resolve()
    return fallback


def is_dlc_source(path: Path, kernels_dir: Path) -> bool:
    try:
        path.resolve().relative_to(kernels_dir.resolve())
    except ValueError:
        return False
    return path.suffix in SOURCE_SUFFIXES


def extract_clang_segment(line: str, build_dir: Path, kernels_dir: Path) -> CompileCommand | None:
    segments = split_shell_segments(line)
    cwd = build_dir
    if segments:
        cwd = parse_cd_segment(segments[0], build_dir)

    for segment in segments:
        try:
            argv = shlex.split(segment)
        except ValueError:
            continue
        if not argv:
            continue
        exe = Path(argv[0]).name
        if not (exe.startswith("clang") or argv[0].endswith("/clang")):
            continue
        if "-S" not in argv or "--target=dlc" not in argv or "-o" not in argv:
            continue

        sources: list[Path] = []
        for token in argv[1:]:
            if token.startswith("-"):
                continue
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = (cwd / candidate).resolve()
            if is_dlc_source(candidate, kernels_dir):
                sources.append(candidate)
        if len(sources) != 1:
            continue

        source = sources[0]
        out_index = argv.index("-o")
        if out_index + 1 >= len(argv):
            continue
        rel_source = source.relative_to(kernels_dir).as_posix()
        return CompileCommand(
            kernel=source.stem,
            source=source,
            rel_source=rel_source,
            cwd=cwd,
            argv=argv,
            original_output=argv[out_index + 1],
        )
    return None


def load_compile_commands(build_dir: Path, kernels_dir: Path) -> dict[Path, CompileCommand]:
    proc = run_cmd(["ninja", "-t", "commands"], cwd=build_dir)
    if proc.returncode != 0:
        raise RuntimeError("ninja -t commands failed\n" + proc.stdout + proc.stderr)
    commands: dict[Path, CompileCommand] = {}
    for line in proc.stdout.splitlines():
        cmd = extract_clang_segment(line, build_dir, kernels_dir)
        if cmd is None:
            continue
        commands.setdefault(cmd.source.resolve(), cmd)
    return commands


def all_kernel_sources(kernels_dir: Path) -> list[Path]:
    sources: list[Path] = []
    for suffix in SOURCE_SUFFIXES:
        sources.extend(kernels_dir.rglob(f"*{suffix}"))
    return sorted(path.resolve() for path in sources)


def sanitize_relpath(rel_source: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", rel_source)
    return stem.replace("/", "__")


def source_matches(source: Path, selectors: list[str], kernels_dir: Path) -> bool:
    if not selectors:
        return True
    rel_source = source.relative_to(kernels_dir).as_posix()
    label = kernel_source_label(rel_source)
    haystacks = {str(source), source.name, source.stem, rel_source, label}
    return any(selector in haystack or selector == source.name or selector == source.stem for selector in selectors for haystack in haystacks)


def json_candidates(text: str) -> Iterable[Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        yield value


def matches_counter(key: str, config: PassConfig) -> bool:
    for counter_name in config.available_counters():
        if config.counter_match_mode == "exact":
            if key == counter_name:
                return True
            continue
        if key == counter_name or key.endswith("." + counter_name):
            return True
    return False


def contains_counter(value: Any, config: PassConfig) -> bool:
    if isinstance(value, dict):
        return any(matches_counter(key, config) or contains_counter(child, config) for key, child in value.items())
    if isinstance(value, list):
        return any(contains_counter(child, config) for child in value)
    return False


def extract_stats_json(stderr_text: str, config: PassConfig) -> Any | None:
    fallback = None
    for value in json_candidates(stderr_text):
        fallback = value
        if contains_counter(value, config):
            return value
    return fallback


def sum_counters(value: Any, config: PassConfig) -> int:
    if isinstance(value, dict):
        total = 0
        for key, child in value.items():
            if matches_counter(key, config) and isinstance(child, int):
                total += child
            total += sum_counters(child, config)
        return total
    if isinstance(value, list):
        return sum(sum_counters(child, config) for child in value)
    return 0


def counter_totals(value: Any, config: PassConfig) -> dict[str, int]:
    totals = {counter: 0 for counter in config.available_counters()}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, int):
                    for counter_name in totals:
                        if config.counter_match_mode == "exact":
                            if key == counter_name:
                                totals[counter_name] += child
                            continue
                        if key == counter_name or key.endswith("." + counter_name):
                            totals[counter_name] += child
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return totals


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=False) + "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def counter_incremented(source_text: str, counter_name: str) -> bool:
    escaped = re.escape(counter_name)
    patterns = [
        rf"\+\+\s*{escaped}\b",
        rf"\b{escaped}\s*\+\+",
        rf"\b{escaped}\s*\+=",
        rf"\b{escaped}\s*=\s*{escaped}\s*\+",
    ]
    return any(re.search(pattern, source_text) for pattern in patterns)


def inactive_stat_counters(source_file: str, counters: Iterable[str]) -> list[str]:
    if not source_file:
        return []
    path = Path(source_file)
    if not path.exists():
        return []
    text = read_text(path)
    inactive: list[str] = []
    for counter in counters:
        if not counter:
            continue
        declared = re.search(rf"STATISTIC\(\s*{re.escape(counter)}\b", text)
        if declared and not counter_incremented(text, counter):
            inactive.append(counter)
    return inactive


def temporary_counter_name(source_file: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]", "", source_file.stem)
    return f"WhatKernel{stem}EmittedBundles"


def build_instrumented_text(source_file: Path, original_text: str, counter_name: str) -> str:
    if not source_file.name.endswith("AsmPrinter.cpp"):
        raise RuntimeError("temporary instrumentation currently supports *AsmPrinter.cpp sources only")
    if counter_name in original_text:
        raise RuntimeError(f"temporary counter already exists in source: {counter_name}")

    statistic = (
        f'STATISTIC({counter_name},\n'
        f'          "whatkernel temporary counter: non-empty DLC bundles emitted");\n'
    )
    stat_matches = list(re.finditer(r"STATISTIC\([^;]+;\n", original_text, flags=re.MULTILINE | re.DOTALL))
    if not stat_matches:
        raise RuntimeError("could not find an existing STATISTIC declaration to place the temporary counter")
    insert_at = stat_matches[-1].end()
    text = original_text[:insert_at] + "\n" + statistic + original_text[insert_at:]

    needle = "  if (DLCMCInstrInfo::bundleSize(MCB) == 0)\n    return;\n"
    if needle not in text:
        raise RuntimeError("could not find the non-empty bundle guard in emitInstruction")
    replacement = needle + f"\n  ++{counter_name};\n"
    return text.replace(needle, replacement, 1)


def write_instrumentation_patch(original_text: str, instrumented_text: str, source_file: Path, patch_path: Path) -> None:
    diff = difflib.unified_diff(
        original_text.splitlines(),
        instrumented_text.splitlines(),
        fromfile=str(source_file),
        tofile=str(source_file),
        lineterm="",
    )
    write_text(patch_path, "\n".join(diff) + "\n")


def create_temporary_instrumentation(config: PassConfig, out_dir: Path, apply_patch: bool) -> TemporaryInstrumentation:
    if not config.source_file:
        raise RuntimeError("cannot instrument a pass without a source_file")
    source_file = Path(config.source_file)
    if not source_file.exists():
        raise RuntimeError(f"pass source file not found: {source_file}")
    original_text = read_text(source_file)
    counter_name = temporary_counter_name(source_file)
    instrumented_text = build_instrumented_text(source_file, original_text, counter_name)

    inst_dir = out_dir / "temporary_instrumentation"
    snapshot_path = inst_dir / f"{source_file.name}.before"
    patch_path = inst_dir / f"{source_file.stem}.whatkernel.patch"
    write_text(snapshot_path, original_text)
    write_instrumentation_patch(original_text, instrumented_text, source_file, patch_path)

    plan = TemporaryInstrumentation(
        source_file=str(source_file),
        counter_name=counter_name,
        snapshot_path=str(snapshot_path),
        patch_path=str(patch_path),
        original_sha256=sha256_text(original_text),
        instrumented_sha256=sha256_text(instrumented_text),
        applied=False,
    )
    if apply_patch:
        source_file.write_text(instrumented_text, encoding="utf-8")
        plan.applied = True
    return plan


def apply_temporary_counter_config(config: PassConfig, plan: TemporaryInstrumentation) -> None:
    config.mode = "stats"
    config.counters = unique_strings([plan.counter_name])
    config.selected_counter = plan.counter_name
    config.candidate_counters = [plan.counter_name]
    config.counter_groups = {}
    config.counter_match_mode = "suffix"
    config.positive_threshold = 1
    config.isolation_kind = "temporary_instrumentation_stats"


def instrument_build_command(args: argparse.Namespace, llvm: Path) -> list[str]:
    if args.instrument_build_cmd:
        return shlex.split(args.instrument_build_cmd)
    return ["ninja", "-C", str(llvm / "build"), "clang"]


def run_instrument_build(command: list[str], label: str) -> None:
    print(f"{label}: {shlex.join(command)}", flush=True)
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="")
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}")


def restore_temporary_instrumentation(plan: TemporaryInstrumentation, rebuild_command: list[str] | None) -> None:
    source_file = Path(plan.source_file)
    if not plan.applied:
        return
    if not source_file.exists():
        plan.restore_reason = f"source file disappeared: {source_file}"
        return
    current_text = read_text(source_file)
    current_sha = sha256_text(current_text)
    if current_sha != plan.instrumented_sha256:
        plan.restore_reason = "source changed after instrumentation; refusing to overwrite it"
        return
    original_text = Path(plan.snapshot_path).read_text(encoding="utf-8")
    source_file.write_text(original_text, encoding="utf-8")
    plan.restored = True
    plan.restore_reason = "restored original source snapshot"
    if rebuild_command:
        run_instrument_build(rebuild_command, "Rebuilding LLVM after restoring temporary instrumentation")


def rewrite_metadata_temporary_instrumentation(run_paths: RunPaths, plan: TemporaryInstrumentation) -> None:
    metadata_path = run_paths.out_dir / "run_metadata.json"
    if not metadata_path.exists():
        return
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data["temporary_instrumentation"] = asdict(plan)
    write_json(metadata_path, data)


def rewrite_command(cmd: CompileCommand, extra_args: list[str], asm_path: Path) -> list[str]:
    argv = list(cmd.argv)
    out_index = argv.index("-o")
    asm_path.parent.mkdir(parents=True, exist_ok=True)
    argv[out_index + 1] = str(asm_path)
    if extra_args:
        argv[out_index:out_index] = extra_args
    return argv


def run_compile_variant(
    cmd: CompileCommand,
    extra_args: list[str],
    asm_path: Path,
    command_path: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> subprocess.CompletedProcess[str]:
    argv = rewrite_command(cmd, extra_args, asm_path)
    command_text = "cd " + shlex.quote(str(cmd.cwd)) + " && " + shlex.join(argv) + "\n"
    write_text(command_path, command_text)
    proc = subprocess.run(
        argv,
        cwd=str(cmd.cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    write_text(stdout_path, proc.stdout)
    write_text(stderr_path, proc.stderr)
    return proc


def normalize_asm_text(text: str) -> str:
    normalized_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        if any(stripped.lstrip().startswith(prefix) for prefix in COMMENT_PREFIXES):
            continue
        normalized_lines.append(stripped)
    return "\n".join(normalized_lines) + ("\n" if normalized_lines else "")


def candidate_label(counter_breakdown: dict[str, int], status: str, config: PassConfig) -> str:
    if status != "ok":
        return "inconclusive"
    for counter_name in config.resolved_candidate_counters():
        if counter_breakdown.get(counter_name, 0) >= config.positive_threshold:
            return "yes"
    return "no"


def process_source_stats(source: Path, cmd: CompileCommand | None, kernels_dir: Path, out_dir: Path, config: PassConfig) -> Result:
    rel_source = source.relative_to(kernels_dir).as_posix()
    source_label = kernel_source_label(rel_source)
    kernel = source.stem
    safe_name = sanitize_relpath(rel_source)
    paths = {
        "stats": out_dir / "json" / f"{safe_name}.json",
        "stderr": out_dir / "stderr" / f"{safe_name}.stderr.txt",
        "stdout": out_dir / "stdout" / f"{safe_name}.stdout.txt",
        "command": out_dir / "cmd" / f"{safe_name}.cmd.txt",
        "asm": out_dir / "asm" / f"{safe_name}.s",
    }

    if cmd is None:
        write_text(paths["command"], "")
        return Result(
            kernel=kernel,
            source=source_label,
            pass_name=config.name,
            mode="stats",
            candidate="inconclusive",
            status="no-command",
            command_path=str(paths["command"]),
            message="No DLC clang -S command found in ninja -t commands.",
        )

    compile_args = [*config.enable_args, "-mllvm", "-stats", "-mllvm", "-stats-json"]
    proc = run_compile_variant(cmd, compile_args, paths["asm"], paths["command"], paths["stdout"], paths["stderr"])

    if proc.returncode != 0:
        return Result(
            kernel=kernel,
            source=source_label,
            pass_name=config.name,
            mode="stats",
            candidate="inconclusive",
            status=f"compile-failed:{proc.returncode}",
            command_path=str(paths["command"]),
            stderr_path=str(paths["stderr"]),
            stdout_path=str(paths["stdout"]),
            asm_path=str(paths["asm"]),
            message="Compile command failed; see stderr path.",
        )

    stats = extract_stats_json(proc.stderr, config)
    if stats is None:
        return Result(
            kernel=kernel,
            source=source_label,
            pass_name=config.name,
            mode="stats",
            candidate="inconclusive",
            status="stats-missing",
            command_path=str(paths["command"]),
            stderr_path=str(paths["stderr"]),
            stdout_path=str(paths["stdout"]),
            asm_path=str(paths["asm"]),
            message="No JSON stats object found on stderr.",
        )

    write_json(paths["stats"], stats)
    breakdown = counter_totals(stats, config)
    selected_counter = config.resolved_selected_counter()
    counter = breakdown.get(selected_counter, 0) if selected_counter else sum_counters(stats, config)
    candidate = candidate_label(breakdown, "ok", config)
    return Result(
        kernel=kernel,
        source=source_label,
        pass_name=config.name,
        mode="stats",
        counter=counter,
        counter_breakdown=breakdown,
        candidate=candidate,
        status="ok",
        stats_path=str(paths["stats"]),
        command_path=str(paths["command"]),
        stderr_path=str(paths["stderr"]),
        stdout_path=str(paths["stdout"]),
        asm_path=str(paths["asm"]),
    )



def result_sort_key(result: Result) -> tuple[int, str]:
    counter = result.counter if isinstance(result.counter, int) else -1
    return (-counter, result.source)


def write_csv(path: Path, results: list[Result]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Result.__dataclass_fields__.keys()))
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))


def result_rows(results: list[Result]) -> list[dict[str, Any]]:
    return [asdict(result) for result in results]


def md_link_path(path: str) -> str:
    return f"`{path}`" if path else ""


def metadata_from_dict(data: dict[str, Any], fallback: RunMetadata) -> RunMetadata:
    values = asdict(fallback)
    values.update(data)
    allowed = RunMetadata.__dataclass_fields__
    return RunMetadata(**{key: value for key, value in values.items() if key in allowed})


def apply_artifact_policy(results: list[Result], artifact_level: str) -> None:
    if artifact_level == "debug":
        return
    for result in results:
        result.command_path = ""
        result.stderr_path = ""
        result.stdout_path = ""
        result.asm_path = ""
        if artifact_level == "summary":
            result.stats_path = ""


def prune_run_artifacts(out_dir: Path, mode: str, artifact_level: str) -> None:
    if artifact_level == "debug":
        return
    dirs_to_remove = {"asm", "cmd", "stderr", "stdout"}
    dirs_to_remove.add("diff")
    if artifact_level == "summary":
        dirs_to_remove.add("json")
    for name in dirs_to_remove:
        shutil.rmtree(out_dir / name, ignore_errors=True)


def default_dataclass_value(dataclass_type: Any, field_name: str) -> Any:
    data_field = dataclass_type.__dataclass_fields__[field_name]
    if data_field.default is not MISSING:
        return data_field.default
    if data_field.default_factory is not MISSING:
        return data_field.default_factory()
    return None


def result_from_row(row: dict[str, Any]) -> Result:
    values = {
        field_name: row.get(field_name, default_dataclass_value(Result, field_name))
        for field_name in Result.__dataclass_fields__
    }
    if not isinstance(values.get("counter_breakdown"), dict):
        values["counter_breakdown"] = {}
    return Result(**values)


def format_counter_names(counters: list[str]) -> str:
    return ", ".join(counters) if counters else "(none)"


def format_counter_breakdown(counter_breakdown: dict[str, int], counters: list[str]) -> str:
    details = [f"{counter}={counter_breakdown[counter]}" for counter in counters if counter_breakdown.get(counter, 0)]
    return ", ".join(details) if details else ""


def hydrate_result_from_stats(result: Result, config: PassConfig) -> Result:
    if result.mode != "stats" or result.counter_breakdown or not result.stats_path:
        return result
    stats_path = Path(result.stats_path)
    if not stats_path.exists():
        return result
    try:
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    result.counter_breakdown = counter_totals(stats, config)
    selected_counter = config.resolved_selected_counter()
    if selected_counter:
        result.counter = result.counter_breakdown.get(selected_counter, 0)
    if result.status == "ok":
        result.candidate = candidate_label(result.counter_breakdown, "ok", config)
    return result


def format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(100.0 * numerator / denominator):.1f}%"


def summarize_status_counts(results: list[Result]) -> str:
    counts = Counter(result.status or "unknown" for result in results)
    if not counts:
        return "no results"
    if len(counts) == 1 and "ok" in counts:
        return f"all {counts['ok']} entries have status=ok"
    return ", ".join(f"{status}={counts[status]}" for status in sorted(counts))


def compact_reason(result: Result) -> str:
    text = result.reason or result.message or ""
    text = " ".join(text.split())
    if len(text) <= 120:
        return text
    return text[:117] + "..."


def write_report(report: Path, results: list[Result], metadata: RunMetadata, config: PassConfig) -> None:
    processed = len(results)
    status_summary = summarize_status_counts(results)
    lines = [
        f"# whatkernel report: {config.name}",
        "",
        "## At a Glance",
        "",
        "| field | value |",
        "|---|---|",
        f"| pass | `{config.name}` |",
        f"| mode | `{metadata.mode}` |",
        f"| description | {config.description} |",
        f"| processed sources | {processed} / {metadata.total_kernel_source_count} |",
        f"| output directory | `{metadata.out_dir}` |",
        f"| report path | `{metadata.report}` |",
        f"| artifact level | `{metadata.artifact_level}` |",
        f"| structured outputs | `summary.csv`, `summary.json`, `run_metadata.json` |",
        "",
    ]

    yes = [result for result in results if result.candidate == "yes"]
    no = [result for result in results if result.candidate == "no"]
    inconclusive = [result for result in results if result.candidate == "inconclusive"]
    top_results = sorted(yes, key=result_sort_key)[: metadata.top]
    issue_results = [result for result in results if result.status != "ok" or result.candidate == "inconclusive"]

    lines.extend([
        "## Summary",
        "",
        "| metric | value |",
        "|---|---|",
        f"| primary counter | `{metadata.counter_name}` |",
        f"| candidate rule | `{metadata.candidate_rule}` |",
        f"| available counters | `{format_counter_names(metadata.available_counters)}` |",
        f"| candidate kernels | {len(yes)} / {processed} ({format_percent(len(yes), processed)}) |",
        f"| candidate no | {len(no)} |",
        f"| inconclusive | {len(inconclusive)} |",
        f"| result quality | {status_summary} |",
        "",
        f"## Top {len(top_results)} Candidate Kernels by `{metadata.counter_name}`",
        "",
    ])
    if top_results:
        lines.extend([
            "| rank | kernel | source | primary counter | counter breakdown | stats | cmd |",
            "|---:|---|---|---:|---|---|---|",
        ])
        for index, result in enumerate(top_results, start=1):
            lines.append(
                f"| {index} | {result.kernel} | `{result.source}` | {result.counter} | {format_counter_breakdown(result.counter_breakdown, metadata.available_counters)} | {md_link_path(result.stats_path)} | {md_link_path(result.command_path)} |"
            )
    else:
        lines.append("No candidate kernels found in this run.")

    if issue_results:
        lines.extend([
            "",
            "## Inconclusive or Failed Kernels",
            "",
            "| kernel | source | candidate | status | note |",
            "|---|---|---|---|---|",
        ])
        for result in sorted(issue_results, key=result_sort_key):
            lines.append(
                f"| {result.kernel} | `{result.source}` | {result.candidate} | {result.status} | {compact_reason(result)} |"
            )

    lines.extend([
        "",
        f"<details>",
        f"<summary>Full kernel results ({processed} rows)</summary>",
        "",
        "| kernel | source | primary counter | counter breakdown | candidate | status | stats path | command path | stderr path |",
        "|---|---|---:|---|---|---|---|---|---|",
    ])
    for result in sorted(results, key=lambda item: item.source):
        counter = result.counter if result.counter != "" else ""
        lines.append(
            f"| {result.kernel} | `{result.source}` | {counter} | {format_counter_breakdown(result.counter_breakdown, metadata.available_counters)} | {result.candidate} | {result.status} | {md_link_path(result.stats_path)} | {md_link_path(result.command_path)} | {md_link_path(result.stderr_path)} |"
        )
    lines.extend(["", "</details>"])
    lines.extend([
        "",
        "<details>",
        "<summary>Run metadata</summary>",
        "",
        "| field | value |",
        "|---|---|",
        f"| registry kind | `{metadata.registry_kind}` |",
        f"| auto-registered on run | `{metadata.auto_registered}` |",
        f"| refreshed on run | `{metadata.refreshed_on_run}` |",
        f"| repo | `{metadata.repo}` |",
        f"| build dir | `{metadata.build_dir}` |",
        f"| llvm | `{metadata.llvm}` |",
        f"| source file | `{metadata.source_file}` |",
        f"| source fingerprint | `{metadata.source_fingerprint}` |",
        f"| compile commands found | {metadata.compile_command_count} |",
        f"| source selectors | `{metadata.source_selectors}` |",
        f"| limit | {metadata.limit} |",
        f"| jobs | {metadata.jobs} |",
        f"| top | {metadata.top} |",
        f"| enable args | `{metadata.enable_args}` |",
        f"| primary counter | `{metadata.counter_name}` |",
        f"| candidate counters | `{format_counter_names(metadata.candidate_counters)}` |",
        f"| available counters | `{format_counter_names(metadata.available_counters)}` |",
        f"| counter groups | `{json.dumps(metadata.counter_groups, sort_keys=True)}` |",
        f"| candidate rule | `{metadata.candidate_rule}` |",
        f"| registry path | `{metadata.registry_path}` |",
        f"| auto registry path | `{metadata.auto_registry_path}` |",
        f"| run timestamp (UTC) | `{metadata.run_timestamp_utc}` |",
        f"| summarize-existing | `{metadata.summarize_existing}` |",
        f"| artifact level | `{metadata.artifact_level}` |",
        "",
        "</details>",
    ])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def source_label_from_command(command_path: Path, kernels_dir: Path) -> str:
    if not command_path.exists():
        return command_path.stem
    command_text = command_path.read_text(encoding="utf-8")
    try:
        tokens = shlex.split(command_text)
    except ValueError:
        return command_path.stem
    for token in tokens:
        candidate = Path(token)
        if candidate.suffix not in SOURCE_SUFFIXES:
            continue
        try:
            rel_source = candidate.resolve().relative_to(kernels_dir.resolve()).as_posix()
            return kernel_source_label(rel_source)
        except ValueError:
            continue
    return command_path.stem


def summarize_existing(run_paths: RunPaths, kernels_dir: Path, config: PassConfig, metadata: RunMetadata) -> tuple[list[Result], RunMetadata]:
    summary_path = run_paths.out_dir / "summary.json"
    metadata_path = run_paths.out_dir / "run_metadata.json"
    requested_artifact_level = metadata.artifact_level
    if summary_path.exists():
        rows = json.loads(summary_path.read_text(encoding="utf-8"))
        results = [hydrate_result_from_stats(result_from_row(row), config) for row in rows]
        if metadata_path.exists():
            metadata = metadata_from_dict(json.loads(metadata_path.read_text(encoding="utf-8")), metadata)
        metadata.out_dir = str(run_paths.out_dir)
        metadata.report = str(run_paths.report)
        metadata.summarize_existing = True
        metadata.artifact_level = requested_artifact_level
        apply_artifact_policy(results, metadata.artifact_level)
        write_csv(run_paths.out_dir / "summary.csv", results)
        write_json(summary_path, result_rows(results))
        write_json(metadata_path, asdict(metadata))
        write_report(run_paths.report, results, metadata, config)
        prune_run_artifacts(run_paths.out_dir, metadata.mode, metadata.artifact_level)
        return results, metadata
    raise RuntimeError(f"no existing summary.json found under {run_paths.out_dir}")


def build_metadata(
    config: PassConfig,
    args: argparse.Namespace,
    repo: Path,
    build_dir: Path,
    llvm: Path,
    kernels_dir: Path,
    run_paths: RunPaths,
    total_sources: int,
    selected_sources: int,
    compile_command_count: int,
    auto_registered: bool,
    refreshed_on_run: bool,
) -> RunMetadata:
    counter_name = config.resolved_selected_counter()
    return RunMetadata(
        pass_name=config.name,
        description=config.description,
        mode=config.mode,
        counter_name=counter_name,
        available_counters=config.available_counters(),
        candidate_counters=config.resolved_candidate_counters(),
        counter_groups=config.counter_groups,
        candidate_rule=config.candidate_rule_text(),
        registry_kind=config.registry_kind or ("auto" if config.auto_generated else "curated"),
        auto_registered=auto_registered,
        refreshed_on_run=refreshed_on_run,
        repo=str(repo),
        build_dir=str(build_dir),
        llvm=str(llvm),
        kernels_dir=str(kernels_dir),
        out_dir=str(run_paths.out_dir),
        report=str(run_paths.report),
        selected_source_count=selected_sources,
        total_kernel_source_count=total_sources,
        compile_command_count=compile_command_count,
        run_timestamp_utc=utc_timestamp(),
        enable_args=config.enable_args,
        isolation_kind=config.isolation_kind,
        source_selectors=list(args.source),
        limit=args.limit,
        jobs=args.jobs,
        top=args.top,
        summarize_existing=args.summarize_existing,
        artifact_level=args.artifacts,
        registry_path=str(args.registry.resolve()),
        auto_registry_path=str(args.auto_registry.resolve()),
        source_file=config.source_file,
        source_fingerprint=config.source_fingerprint,
        selected_counter=counter_name,
    )


def print_pass_list(curated: dict[str, PassConfig], auto: dict[str, PassConfig]) -> None:
    merged = merge_registries(curated, auto)
    for name in sorted(merged):
        config = merged[name]
        aliases = f" aliases={','.join(config.aliases)}" if config.aliases else ""
        print(f"{name} [{config.mode}, {config.registry_kind or ('auto' if config.auto_generated else 'curated')}] {config.description}{aliases}")


def print_summary(results: list[Result], metadata: RunMetadata, config: PassConfig, top: int) -> None:
    print(f"pass: {config.name}")
    print(f"mode: {metadata.mode}")
    yes = [result for result in results if result.candidate == "yes"]
    print(f"primary counter: {metadata.counter_name}")
    print(f"candidate rule: {metadata.candidate_rule}")
    print(f"available counters: {format_counter_names(metadata.available_counters)}")
    print(f"artifacts: {metadata.artifact_level}")
    print(f"candidate kernels: {len(yes)} / {len(results)}")
    print(f"output: {metadata.out_dir}")
    print(f"source format: /dlc_kernels/...\n")

    top_results = sorted([result for result in results if result.candidate == "yes"], key=result_sort_key)[:top]
    if not top_results:
        print("no candidate kernels found")
        return
    width = max(len(result.kernel) for result in top_results)
    for index, result in enumerate(top_results, start=1):
        print(f"{index}. {result.kernel.ljust(width)}  {result.counter:>4}  {result.source}")
        breakdown = format_counter_breakdown(result.counter_breakdown, metadata.available_counters)
        if breakdown and len(metadata.available_counters) > 1:
            print(f"    counters: {breakdown}")

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.jobs < 1:
        raise SystemExit("--jobs must be >= 1")
    if args.top < 1:
        raise SystemExit("--top must be >= 1")

    curated = load_registry_file(args.registry.resolve(), "curated")
    auto = load_registry_file(args.auto_registry.resolve(), "auto")
    if args.list_passes:
        print_pass_list(curated, auto)
        return 0
    if not args.pass_name:
        raise SystemExit("pass_name is required unless --list-passes is used")

    try:
        config, curated, auto, auto_registered, refreshed_on_run = resolve_pass_config(
            args.pass_name,
            args.registry.resolve(),
            args.auto_registry.resolve(),
            args.llvm.resolve(),
            args.refresh_auto,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    config.aliases = unique_strings(config.aliases + ([args.pass_name] if args.pass_name != config.name else []))
    if not config.available_counters():
        raise SystemExit(
            f"pass {config.name!r} has no STATISTIC counters; "
            "stats-only whatkernel cannot rank candidate kernels"
        )
    repo = args.repo.resolve()
    build_dir = args.build_dir.resolve()
    llvm = args.llvm.resolve()
    kernels_dir = repo / "dlc_kernels"
    if not kernels_dir.exists():
        raise SystemExit(f"kernel directory not found: {kernels_dir}")

    run_paths = resolve_run_paths(config.name, args.out_dir, args.report)
    total_sources = len(all_kernel_sources(kernels_dir))
    run_paths.out_dir.mkdir(parents=True, exist_ok=True)

    metadata = build_metadata(
        config,
        args,
        repo,
        build_dir,
        llvm,
        kernels_dir,
        run_paths,
        total_sources,
        selected_sources=0,
        compile_command_count=0,
        auto_registered=auto_registered,
        refreshed_on_run=refreshed_on_run,
    )

    if args.summarize_existing:
        results, metadata = summarize_existing(run_paths, kernels_dir, config, metadata)
        print_summary(results, metadata, config, args.top)
        return 0

    ensure_build_tree(build_dir, llvm)
    commands = load_compile_commands(build_dir, kernels_dir)
    sources = [source for source in all_kernel_sources(kernels_dir) if source_matches(source, args.source, kernels_dir)]
    if args.limit:
        sources = sources[: args.limit]

    metadata = build_metadata(
        config,
        args,
        repo,
        build_dir,
        llvm,
        kernels_dir,
        run_paths,
        total_sources,
        selected_sources=len(sources),
        compile_command_count=len(commands),
        auto_registered=auto_registered,
        refreshed_on_run=refreshed_on_run,
    )
    write_text(
        run_paths.out_dir / "compile_command_manifest.txt",
        "\n".join(f"{cmd.rel_source}\t{cmd.cwd}\t{shlex.join(cmd.argv)}" for cmd in commands.values()) + "\n",
    )
    write_json(run_paths.out_dir / "discovered_pass_config.json", config.to_dict())

    print(f"Kernel sources total: {total_sources}")
    print(f"Sources selected: {len(sources)}")
    print(f"Compile commands found: {len(commands)}")
    print(f"Artifact level: {args.artifacts}")
    print(f"Output directory: {run_paths.out_dir}")
    print(f"Resolved pass: {config.name} [{config.mode}, {metadata.registry_kind}]")
    if auto_registered:
        print("Auto-registered this pass during the run.")
    elif refreshed_on_run:
        print("Refreshed auto-registered pass metadata during the run.")

    results: list[Result] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        future_to_source = {
            executor.submit(process_source_stats, source, commands.get(source), kernels_dir, run_paths.out_dir, config): source
            for source in sources
        }
        for index, future in enumerate(concurrent.futures.as_completed(future_to_source), start=1):
            source = future_to_source[future]
            try:
                result = future.result()
            except Exception as exc:
                rel_source = source.relative_to(kernels_dir).as_posix()
                result = Result(
                    kernel=source.stem,
                    source=kernel_source_label(rel_source),
                    pass_name=config.name,
                    mode=config.mode,
                    candidate="inconclusive",
                    status="script-error",
                    reason=str(exc),
                    message=str(exc),
                )
            results.append(result)
            marker = result.counter
            print(
                f"[{index}/{len(sources)}] {str(marker):12s} {result.status:24s} {result.source}",
                flush=True,
            )

    results.sort(key=lambda result: result.source)
    apply_artifact_policy(results, args.artifacts)
    write_csv(run_paths.out_dir / "summary.csv", results)
    write_json(run_paths.out_dir / "summary.json", result_rows(results))
    write_json(run_paths.out_dir / "run_metadata.json", asdict(metadata))
    write_report(run_paths.report, results, metadata, config)
    prune_run_artifacts(run_paths.out_dir, metadata.mode, metadata.artifact_level)
    print(f"Wrote CSV: {run_paths.out_dir / 'summary.csv'}")
    print(f"Wrote JSON: {run_paths.out_dir / 'summary.json'}")
    print(f"Wrote metadata: {run_paths.out_dir / 'run_metadata.json'}")
    print(f"Wrote report: {run_paths.report}\n")
    print_summary(results, metadata, config, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

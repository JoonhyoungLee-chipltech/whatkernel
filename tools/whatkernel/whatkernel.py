#!/usr/bin/env python3
"""Convenience wrapper for the whatkernel collector."""

from __future__ import annotations

import sys

import collect_pass_stats


def main(argv: list[str] | None = None) -> int:
    return collect_pass_stats.main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())

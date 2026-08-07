#!/usr/bin/env python3
"""Regression guard: HTTPS must coexist with Vibbe's internal-RAM audio tasks."""

from pathlib import Path


root = Path(__file__).resolve().parents[1]
defaults = (root / "sdkconfig.defaults").read_text(encoding="utf-8")

assert "CONFIG_MBEDTLS_EXTERNAL_MEM_ALLOC=y" in defaults, (
    "mbedTLS must allocate from PSRAM so Solelkollen HTTPS can coexist "
    "with Vibbe's USB/audio task stacks"
)
assert "CONFIG_MBEDTLS_INTERNAL_MEM_ALLOC=y" not in defaults, (
    "sdkconfig.defaults must not force TLS back into scarce internal RAM"
)

print("OK: TLS-minnet ligger i PSRAM bredvid Vibbes ljudnav")

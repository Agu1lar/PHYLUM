# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Local golden tasks and domain benchmarks for automation evaluation."""

from evaluation.benchmark_domains import DOMAIN_BENCHMARKS, list_domains
from evaluation.golden_models import GoldenTask, GoldenTaskResult
from evaluation.golden_runner import GoldenRunner, load_golden_tasks

__all__ = [
    "DOMAIN_BENCHMARKS",
    "GoldenRunner",
    "GoldenTask",
    "GoldenTaskResult",
    "list_domains",
    "load_golden_tasks",
]

#!/usr/bin/env python3
"""Generate the one-second-shifted wall-time performance profile."""
import os

from generate_bb_time_performance_profile import generate_profile


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT = os.path.join(
    REPO_ROOT, "analysis", "figures",
    "bb_jointly_optimal_shifted_time_performance_profile.svg"
)


if __name__ == "__main__":
    generate_profile(time_shift=1.0, output=OUTPUT)

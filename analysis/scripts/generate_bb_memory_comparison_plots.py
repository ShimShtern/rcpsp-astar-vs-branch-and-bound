#!/usr/bin/env python3
"""Generate the A*/best-first B&B memory-limit counts figure and summary table."""
import csv
import math
import os
from bisect import bisect_right
from xml.sax.saxutils import escape


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIGURES_DIR = os.path.join(REPO_ROOT, "analysis", "figures")
TABLES_DIR = os.path.join(REPO_ROOT, "analysis", "tables")
MEMORY_LIMIT_OUTPUT = os.path.join(
    FIGURES_DIR, "bb_dominance_memory_limit_counts.svg"
)
SUMMARY_OUTPUT = os.path.join(
    TABLES_DIR, "bb_dominance_memory_summary.csv"
)

TOTAL_INSTANCES = 480
SEARCH_COLORS = {
    "A*": "#D55E00",
    "Best-first B&B": "#0072B2",
}
PRUNING_LINE_STYLES = {
    "Neither": "1 9",
    "Lexicographic": "12 12",
    "Lexicographic + active": "",
}
MARKER_SHAPES = {
    "Neither": "circle",
    "Lexicographic": "square",
    "Lexicographic + active": "triangle",
}
PRUNING_LABELS = {
    "Neither": "CP",
    "Lexicographic": "CP + Lexicographic",
    "Lexicographic + active": "CP + Lexicographic + Active schedules",
}

CONFIGURATIONS = [
    {
        "strategy": "A*",
        "pruning": "Neither",
        "memory_limit_enforced": False,
        "path": os.path.join(
            REPO_ROOT, "Results", "astar", "non_dominance",
            "no_explicit_memory_limit", "j30_astar_120min.csv"
        ),
    },
    {
        "strategy": "A*",
        "pruning": "Lexicographic",
        "memory_limit_enforced": True,
        "path": os.path.join(
            REPO_ROOT, "Results", "astar", "same_start_dominance",
            "memory_limit_64GiB", "j30_astar_120min.csv"
        ),
    },
    {
        "strategy": "A*",
        "pruning": "Lexicographic + active",
        "memory_limit_enforced": True,
        "path": os.path.join(
            REPO_ROOT, "Results", "astar", "same_start_dominance",
            "exact_active_schedule", "memory_limit_64GiB",
            "j30_astar_120min.csv"
        ),
    },
    {
        "strategy": "Best-first B&B",
        "pruning": "Neither",
        "memory_limit_enforced": True,
        "path": os.path.join(
            REPO_ROOT, "Results", "bb", "parent_linked", "non_dominance",
            "duplicate_tracking", "memory_limit_64GiB", "j30_bb_120min.csv"
        ),
    },
    {
        "strategy": "Best-first B&B",
        "pruning": "Lexicographic",
        "memory_limit_enforced": True,
        "path": os.path.join(
            REPO_ROOT, "Results", "bb", "parent_linked",
            "same_start_dominance", "no_duplicate_tracking",
            "memory_limit_64GiB", "j30_bb_120min.csv"
        ),
    },
    {
        "strategy": "Best-first B&B",
        "pruning": "Lexicographic + active",
        "memory_limit_enforced": True,
        "path": os.path.join(
            REPO_ROOT, "Results", "bb", "parent_linked",
            "same_start_dominance", "active_schedule", "no_duplicate_tracking",
            "memory_limit_64GiB",
            "j30_bb_best_first_dominance_120min_all.csv"
        ),
    },
]


def load_rows(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return (ordered[lower] +
            (ordered[upper] - ordered[lower]) * (position - lower))


def status_counts(rows):
    counts = {}
    for row in rows:
        status = row.get("status", "")
        counts[status] = counts.get(status, 0) + 1
    return counts


def marker_svg(shape, x, y, color, size=5.0):
    common = 'fill="#ffffff" stroke="%s" stroke-width="2.2"' % color
    if shape == "circle":
        return '<circle class="profile-marker" cx="%.2f" cy="%.2f" r="%.1f" %s/>' % (
            x, y, size, common
        )
    if shape == "square":
        return '<rect class="profile-marker" x="%.2f" y="%.2f" width="%.1f" height="%.1f" %s/>' % (
            x - size, y - size, 2.0 * size, 2.0 * size, common
        )
    return '<path class="profile-marker" d="M %.2f %.2f L %.2f %.2f L %.2f %.2f Z" %s/>' % (
        x, y - size - 0.8,
        x + size + 0.8, y + size,
        x - size - 0.8, y + size,
        common,
    )


def write_summary(data):
    thresholds = (1000, 10000, 100000, 1000000, 10000000, 100000000)
    fields = [
        "search", "configuration", "instances", "optimal", "time_limit",
        "time_limit_feasible", "memory_limit", "max_open_min",
        "max_open_q1", "max_open_median", "max_open_q3", "max_open_max",
    ] + ["within_max_open_%d" % value for value in thresholds] + ["result_file"]
    os.makedirs(TABLES_DIR, exist_ok=True)
    with open(SUMMARY_OUTPUT, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in data:
            values = item["max_open"]
            counts = item["statuses"]
            record = {
                "search": item["strategy"],
                "configuration": PRUNING_LABELS[item["pruning"]],
                "instances": len(item["rows"]),
                "optimal": counts.get("optimal", 0),
                "time_limit": counts.get("time_limit", 0),
                "time_limit_feasible": counts.get("time_limit_feasible", 0),
                "memory_limit": (counts.get("memory_limit", 0)
                                 if item["memory_limit_enforced"] else ""),
                "max_open_min": min(values),
                "max_open_q1": "%.3f" % percentile(values, 0.25),
                "max_open_median": "%.3f" % percentile(values, 0.50),
                "max_open_q3": "%.3f" % percentile(values, 0.75),
                "max_open_max": max(values),
                "result_file": os.path.relpath(item["path"], REPO_ROOT),
            }
            for threshold in thresholds:
                record["within_max_open_%d" % threshold] = bisect_right(
                    values, threshold
                )
            writer.writerow(record)


def generate_memory_limit_bars(data):
    width = 1340
    height = 620
    left = 105.0
    right = 1260.0
    top = 25.0
    bottom = 430.0
    maximum = 250.0
    group_centers = (290.0, 680.0, 1070.0)
    bar_width = 105.0
    offset = 62.0

    def sy(value):
        return bottom - value / maximum * (bottom - top)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-labelledby="title desc">' %
        (width, height, width, height),
        '<title id="title">Memory-limit counts by search and pruning configuration</title>',
        '<desc id="desc">Memory-limit exits for the same A-star and best-first branch-and-bound configurations used in the dominance cactus plot. The A-star critical-path-only run had no explicit memory limit and is marked not available.</desc>',
        '<rect width="%d" height="%d" fill="#ffffff"/>' % (width, height),
    ]
    for value in (0, 50, 100, 150, 200, 250):
        y = sy(value)
        parts.append(
            '<line x1="%.1f" y1="%.2f" x2="%.1f" y2="%.2f" stroke="#dfe3e8" stroke-width="1"/>' %
            (left, y, right, y)
        )
        parts.append(
            '<text x="92" y="%.2f" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="16" fill="#5f6368">%d</text>' %
            (y + 6.0, value)
        )
    parts.append(
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#7b8187" stroke-width="1.5"/>' %
        (left, top, left, bottom)
    )
    parts.append(
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#7b8187" stroke-width="1.5"/>' %
        (left, bottom, right, bottom)
    )
    by_key = {(item["strategy"], item["pruning"]): item for item in data}
    for group_center, pruning in zip(group_centers, PRUNING_LABELS):
        for strategy, direction in (("A*", -1.0), ("Best-first B&B", 1.0)):
            item = by_key[(strategy, pruning)]
            center = group_center + direction * offset
            color = SEARCH_COLORS[strategy]
            if item["memory_limit_enforced"]:
                value = item["statuses"].get("memory_limit", 0)
                y = sy(value)
                parts.append(
                    '<rect x="%.1f" y="%.2f" width="%.1f" height="%.2f" fill="%s" stroke="#303134" stroke-width="1.2"/>' %
                    (center - bar_width / 2.0, y, bar_width,
                     bottom - y, color)
                )
                parts.append(
                    '<text x="%.1f" y="%.2f" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="18" font-weight="700" fill="#303134">%d</text>' %
                    (center, y - 10.0, value)
                )
            else:
                parts.append(
                    '<rect x="%.1f" y="%.1f" width="%.1f" height="34" fill="#ffffff" stroke="%s" stroke-width="2" stroke-dasharray="5 4"/>' %
                    (center - bar_width / 2.0, bottom - 34.0,
                     bar_width, color)
                )
                parts.append(
                    '<text x="%.1f" y="%.1f" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="16" font-weight="700" fill="#303134">N/A</text>' %
                    (center, bottom - 11.0)
                )
    labels = [
        (group_centers[0], ("CP",)),
        (group_centers[1], ("CP + Lexicographic",)),
        (group_centers[2], ("CP + Lexicographic +", "Active schedules")),
    ]
    for center, lines in labels:
        parts.append(
            '<text x="%.1f" y="468" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="16" fill="#303134">%s</text>' %
            (center, escape(lines[0]))
        )
        if len(lines) > 1:
            parts.append(
                '<text x="%.1f" y="490" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="16" fill="#303134">%s</text>' %
                (center, escape(lines[1]))
            )
    parts.append('<text x="485" y="555" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="16" font-weight="700" fill="#303134">Search:</text>')
    for x, strategy in ((510.0, "A*"), (690.0, "Best-first B&B")):
        parts.append(
            '<line x1="%.1f" y1="550" x2="%.1f" y2="550" stroke="%s" stroke-width="10"/>' %
            (x, x + 38.0, SEARCH_COLORS[strategy])
        )
        parts.append(
            '<text x="%.1f" y="555" font-family="Times New Roman, Times, serif" font-size="15" fill="#303134">%s</text>' %
            (x + 47.0, escape(strategy))
        )
    parts.extend([
        '<text x="26" y="228" transform="rotate(-90 26 228)" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="18" fill="#303134">Runs reaching the 64 GiB memory limit</text>',
        '</svg>',
    ])
    os.makedirs(FIGURES_DIR, exist_ok=True)
    with open(MEMORY_LIMIT_OUTPUT, "w") as handle:
        handle.write("\n".join(parts) + "\n")


def main():
    data = []
    for configuration in CONFIGURATIONS:
        rows = load_rows(configuration["path"])
        item = dict(configuration)
        item["rows"] = rows
        item["statuses"] = status_counts(rows)
        item["max_open"] = sorted(
            int(float(row["max_open"])) for row in rows
        )
        data.append(item)

    write_summary(data)
    generate_memory_limit_bars(data)
    print(MEMORY_LIMIT_OUTPUT)
    print(SUMMARY_OUTPUT)


if __name__ == "__main__":
    main()

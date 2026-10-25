#!/usr/bin/env python3
"""Generate a two-panel runtime/frontier figure with shared legends."""
import csv
import math
import os
from bisect import bisect_right
from xml.sax.saxutils import escape


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT = os.path.join(
    REPO_ROOT, "analysis", "figures",
    "astar_bb_dominance_runtime_frontier_combined.svg",
)
FIGURE_TITLE = "Runtime and maximum-frontier profiles"
FIGURE_DESC = (
    "Side-by-side cumulative runtime and maximum-frontier profiles for A-star "
    "and best-first branch-and-bound under three pruning configurations, with "
    "shared search and pruning legends."
)
RUNTIME_AXIS_LABEL = "Reported runtime (seconds)"

TOTAL_INSTANCES = 480
WIDTH = 1450
HEIGHT = 715
PLOT_TOP = 30.0
PLOT_BOTTOM = 500.0
X_TICK_LABEL_Y = 530.0
X_AXIS_LABEL_Y = 570.0
PLOT_WIDTH = 580.0
LEFT_X = 105.0
RIGHT_X = 815.0

TIME_MIN = 0.001
TIME_MAX = 7200.0
FRONTIER_MIN = 10.0
FRONTIER_MAX = 1000000000.0

SEARCH_COLORS = {
    "A*": "#D55E00",
    "Best-first B&B": "#0072B2",
}
PRUNING_STYLES = {
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


def result_path(*parts):
    return os.path.join(REPO_ROOT, "Results", *parts)


SERIES = [
    ("A*", "Neither", result_path(
        "astar", "non_dominance", "no_explicit_memory_limit",
        "j30_astar_120min.csv")),
    ("A*", "Lexicographic", result_path(
        "astar", "same_start_dominance", "memory_limit_64GiB",
        "j30_astar_120min.csv")),
    # Lexicographic + active: pinned 1-CPU runs (see EXPERIMENTS.md); the
    # other series are the earlier unpinned runs with identical settings.
    ("A*", "Lexicographic + active", result_path(
        "astar", "same_start_dominance", "exact_active_schedule",
        "memory_limit_64GiB", "j30_astar_120min.csv")),
    ("Best-first B&B", "Neither", result_path(
        "bb", "parent_linked", "non_dominance", "duplicate_tracking",
        "memory_limit_64GiB", "j30_bb_120min.csv")),
    ("Best-first B&B", "Lexicographic", result_path(
        "bb", "parent_linked", "same_start_dominance",
        "no_duplicate_tracking", "memory_limit_64GiB", "j30_bb_120min.csv")),
    ("Best-first B&B", "Lexicographic + active", result_path(
        "bb", "parent_linked", "same_start_dominance", "active_schedule",
        "no_duplicate_tracking", "memory_limit_64GiB",
        "j30_bb_best_first_dominance_120min_all.csv")),
]


def load_data():
    data = []
    for strategy, pruning, path in SERIES:
        with open(path, newline="") as handle:
            rows = list(csv.DictReader(handle))
        data.append({
            "strategy": strategy,
            "pruning": pruning,
            "optimal_times": sorted(
                float(row["runtime"])
                for row in rows if row.get("status") == "optimal"
            ),
            "max_open": sorted(int(float(row["max_open"])) for row in rows),
        })
    return data


def log_position(value, minimum, maximum, left):
    value = max(minimum, min(maximum, value))
    fraction = ((math.log10(value) - math.log10(minimum)) /
                (math.log10(maximum) - math.log10(minimum)))
    return left + fraction * PLOT_WIDTH


def y_position(count):
    return PLOT_BOTTOM - count / float(TOTAL_INSTANCES) * (
        PLOT_BOTTOM - PLOT_TOP
    )


def step_path(values, minimum, maximum, left):
    commands = [
        "M %.2f %.2f" % (log_position(minimum, minimum, maximum, left),
                          y_position(0))
    ]
    for index, value in enumerate(values, 1):
        commands.append("H %.2f" % log_position(value, minimum, maximum, left))
        commands.append("V %.2f" % y_position(index))
    commands.append("H %.2f" % log_position(maximum, minimum, maximum, left))
    return " ".join(commands)


def marker_svg(shape, x, y, color, size=5.4):
    common = 'fill="#ffffff" stroke="%s" stroke-width="2.4"' % color
    if shape == "circle":
        return '<circle cx="%.2f" cy="%.2f" r="%.1f" %s/>' % (
            x, y, size, common
        )
    if shape == "square":
        return '<rect x="%.2f" y="%.2f" width="%.1f" height="%.1f" %s/>' % (
            x - size, y - size, 2.0 * size, 2.0 * size, common
        )
    return '<path d="M %.2f %.2f L %.2f %.2f L %.2f %.2f Z" %s/>' % (
        x, y - size - 0.8,
        x + size + 0.8, y + size,
        x - size - 0.8, y + size,
        common,
    )


def add_panel_axes(parts, left, x_ticks, x_label, y_label):
    for value, label, minimum, maximum in x_ticks:
        x = log_position(value, minimum, maximum, left)
        parts.append(
            '<line x1="%.2f" y1="%.1f" x2="%.2f" y2="%.1f" '
            'stroke="#edf0f2" stroke-width="1"/>' %
            (x, PLOT_TOP, x, PLOT_BOTTOM)
        )
        parts.append(
            '<text x="%.2f" y="%.1f" text-anchor="middle" '
            'font-family="Times New Roman, Times, serif" font-size="20" fill="#5f6368">%s</text>' %
            (x, X_TICK_LABEL_Y, escape(label))
        )
    for count in (0, 96, 192, 288, 384, 480):
        y = y_position(count)
        parts.append(
            '<line x1="%.1f" y1="%.2f" x2="%.1f" y2="%.2f" '
            'stroke="#dfe3e8" stroke-width="1"/>' %
            (left, y, left + PLOT_WIDTH, y)
        )
        parts.append(
            '<text x="%.1f" y="%.2f" text-anchor="end" '
            'font-family="Times New Roman, Times, serif" font-size="20" fill="#5f6368">%d%%</text>' %
            (left - 12.0, y + 7.0,
             int(round(100.0 * count / TOTAL_INSTANCES)))
        )
    parts.append(
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
        'stroke="#7b8187" stroke-width="1.7"/>' %
        (left, PLOT_TOP, left, PLOT_BOTTOM)
    )
    parts.append(
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
        'stroke="#7b8187" stroke-width="1.7"/>' %
        (left, PLOT_BOTTOM, left + PLOT_WIDTH, PLOT_BOTTOM)
    )
    parts.append(
        '<text x="%.1f" y="%.1f" text-anchor="middle" '
        'font-family="Times New Roman, Times, serif" font-size="24" fill="#303134">%s</text>' %
        (left + PLOT_WIDTH / 2.0, X_AXIS_LABEL_Y, escape(x_label))
    )
    parts.append(
        '<text x="%.1f" y="%.1f" transform="rotate(-90 %.1f %.1f)" '
        'text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="23" '
        'fill="#303134">%s</text>' %
        (left - 76.0, (PLOT_TOP + PLOT_BOTTOM) / 2.0,
         left - 76.0, (PLOT_TOP + PLOT_BOTTOM) / 2.0, escape(y_label))
    )


def add_curves(parts, data, key, minimum, maximum, left, marker_values,
               clip_id=None):
    if clip_id is None:
        clip_id = "left" if left == LEFT_X else "right"
    parts.append(
        '<g clip-path="url(#clip-%s)">' % clip_id
    )
    for item in data:
        values = item[key]
        color = SEARCH_COLORS[item["strategy"]]
        dash = PRUNING_STYLES[item["pruning"]]
        dash_attr = (' stroke-dasharray="%s"' % dash) if dash else ""
        parts.append(
            '<path d="%s" fill="none" stroke="%s" stroke-width="4"%s '
            'stroke-linejoin="round" stroke-linecap="round"/>' %
            (step_path(values, minimum, maximum, left), color, dash_attr)
        )
        shape = MARKER_SHAPES[item["pruning"]]
        for value in marker_values:
            count = bisect_right(values, value)
            if count:
                parts.append(marker_svg(
                    shape,
                    log_position(value, minimum, maximum, left),
                    y_position(count), color,
                ))
    parts.append('</g>')


def add_shared_legends(parts):
    parts.append(
        '<text x="245" y="625" text-anchor="end" font-family="Times New Roman, Times, serif" '
        'font-size="21" font-weight="700" fill="#303134">Search:</text>'
    )
    for x, strategy in ((275.0, "A*"), (455.0, "Best-first B&B")):
        parts.append(
            '<line x1="%.1f" y1="619" x2="%.1f" y2="619" stroke="%s" '
            'stroke-width="6"/>' % (x, x + 45.0, SEARCH_COLORS[strategy])
        )
        parts.append(
            '<text x="%.1f" y="626" font-family="Times New Roman, Times, serif" font-size="20" '
            'fill="#303134">%s</text>' % (x + 56.0, escape(strategy))
        )

    parts.append(
        '<text x="245" y="665" text-anchor="end" font-family="Times New Roman, Times, serif" '
        'font-size="21" font-weight="700" fill="#303134">Pruning:</text>'
    )
    for x, pruning in zip((275.0, 480.0, 890.0), PRUNING_LABELS):
        dash = PRUNING_STYLES[pruning]
        dash_attr = (' stroke-dasharray="%s"' % dash) if dash else ""
        parts.append(
            '<line x1="%.1f" y1="659" x2="%.1f" y2="659" stroke="#303134" '
            'stroke-width="6" stroke-linecap="round"%s/>' %
            (x, x + 55.0, dash_attr)
        )
        parts.append(marker_svg(
            MARKER_SHAPES[pruning], x + 27.5, 659.0, "#303134", size=5.2
        ))
        parts.append(
            '<text x="%.1f" y="666" font-family="Times New Roman, Times, serif" font-size="20" '
            'fill="#303134">%s</text>' %
            (x + 67.0, escape(PRUNING_LABELS[pruning]))
        )


def generate_svg(data):
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img" aria-labelledby="title desc">' %
        (WIDTH, HEIGHT, WIDTH, HEIGHT),
        '<title id="title">%s</title>' % escape(FIGURE_TITLE),
        '<desc id="desc">%s</desc>' % escape(FIGURE_DESC),
        '<rect width="%d" height="%d" fill="#ffffff"/>' % (WIDTH, HEIGHT),
        '<defs>',
        '<clipPath id="clip-left"><rect x="%.1f" y="%.1f" width="%.1f" '
        'height="%.1f"/></clipPath>' %
        (LEFT_X, PLOT_TOP, PLOT_WIDTH, PLOT_BOTTOM - PLOT_TOP),
        '<clipPath id="clip-right"><rect x="%.1f" y="%.1f" width="%.1f" '
        'height="%.1f"/></clipPath>' %
        (RIGHT_X, PLOT_TOP, PLOT_WIDTH, PLOT_BOTTOM - PLOT_TOP),
        '</defs>',
    ]

    time_ticks = tuple(
        (value, label, TIME_MIN, TIME_MAX)
        for value, label in (
            (0.001, "0.001"), (0.01, "0.01"), (0.1, "0.1"),
            (1.0, "1"), (10.0, "10"), (100.0, "100"),
            (1000.0, "1k"), (7200.0, "7.2k"),
        )
    )
    frontier_ticks = tuple(
        (value, label, FRONTIER_MIN, FRONTIER_MAX)
        for value, label in (
            (10, "10"), (100, "100"), (1000, "1,000"),
            (10000, "10k"), (100000, "100k"), (1000000, "1m"),
            (10000000, "10m"), (100000000, "100m"),
            (1000000000, "1b"),
        )
    )
    add_panel_axes(
        parts, LEFT_X, time_ticks, RUNTIME_AXIS_LABEL,
        "Instances proven optimal",
    )
    add_panel_axes(
        parts, RIGHT_X, frontier_ticks, "Maximum frontier size",
        "Instances within frontier size",
    )
    add_curves(
        parts, data, "optimal_times", TIME_MIN, TIME_MAX, LEFT_X,
        (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0),
    )
    add_curves(
        parts, data, "max_open", FRONTIER_MIN, FRONTIER_MAX, RIGHT_X,
        (100, 1000, 10000, 100000, 1000000, 10000000, 100000000),
    )
    add_shared_legends(parts)
    parts.append('</svg>')

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w") as handle:
        handle.write("\n".join(parts) + "\n")


def main():
    generate_svg(load_data())
    print(OUTPUT)


if __name__ == "__main__":
    main()

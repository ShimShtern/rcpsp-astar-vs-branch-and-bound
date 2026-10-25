#!/usr/bin/env python3
"""Generate runtime, frontier, and RSS profiles for A* and B&B LP settings."""
import csv
import math
import os
from xml.sax.saxutils import escape

import generate_astar_bb_runtime_frontier_combined as base


OUTPUT = os.path.join(
    base.REPO_ROOT, "analysis", "figures",
    "astar_bb_lp_runtime_frontier_combined.svg",
)

SEARCH_COLORS = {
    "A*": "#D55E00",
    "Best-first B&B": "#0072B2",
    "Depth-first B&B": "#009E73",
}
LP_STYLES = {
    "No LP": "",
    "Weak LP": "12 8",
    "Strong LP": "2 7",
}
MARKER_SHAPES = {
    "No LP": "circle",
    "Weak LP": "square",
    "Strong LP": "triangle",
}

WIDTH = 1450
HEIGHT = 715
PLOT_WIDTH = 360.0
PANEL_X = (90.0, 565.0, 1040.0)
PLOT_BOTTOM = 500.0
X_TICK_LABEL_Y = 530.0
X_AXIS_LABEL_Y = 570.0
RSS_MIN = 10.0
RSS_MAX = 65536.0


def result_path(*parts):
    return os.path.join(base.REPO_ROOT, "Results", *parts)


SERIES = [
    ("A*", "No LP", result_path(
        "astar", "same_start_dominance", "exact_active_schedule",
        "memory_limit_64GiB", "j30_astar_120min.csv")),
    ("Best-first B&B", "No LP", result_path(
        "bb", "parent_linked", "same_start_dominance", "active_schedule",
        "no_duplicate_tracking", "memory_limit_64GiB",
        "j30_bb_best_first_dominance_120min_all.csv")),
    ("Best-first B&B", "Weak LP", result_path(
        "bb", "parent_linked", "same_start_dominance", "active_schedule",
        "no_duplicate_tracking", "memory_limit_64GiB", "best_first",
        "lp_relaxation", "weak_precedence", "j30_bb_120min.csv")),
    ("Best-first B&B", "Strong LP", result_path(
        "bb", "parent_linked", "same_start_dominance", "active_schedule",
        "no_duplicate_tracking", "memory_limit_64GiB", "best_first",
        "lp_relaxation", "strong_precedence", "j30_bb_120min.csv")),
    ("Depth-first B&B", "No LP", result_path(
        "bb", "parent_linked", "same_start_dominance", "active_schedule",
        "no_duplicate_tracking", "memory_limit_64GiB", "depth_first_best_lb",
        "j30_bb_depth_first_best_lb_active_schedule_dominance_120min_all.csv")),
    ("Depth-first B&B", "Weak LP", result_path(
        "bb", "parent_linked", "same_start_dominance", "active_schedule",
        "no_duplicate_tracking", "memory_limit_64GiB", "depth_first_best_lb",
        "lp_relaxation", "weak_precedence", "j30_bb_120min.csv")),
    ("Depth-first B&B", "Strong LP", result_path(
        "bb", "parent_linked", "same_start_dominance", "active_schedule",
        "no_duplicate_tracking", "memory_limit_64GiB", "depth_first_best_lb",
        "lp_relaxation", "strong_precedence", "j30_bb_120min.csv")),
]


def load_data():
    data = []
    for strategy, lp_setting, path in SERIES:
        with open(path, newline="") as handle:
            rows = list(csv.DictReader(handle))
        data.append({
            "strategy": strategy,
            "pruning": lp_setting,
            "optimal_times": sorted(
                float(row.get("wall_runtime") or row["runtime"])
                for row in rows if row.get("status") == "optimal"
            ),
            "max_open": sorted(int(float(row["max_open"])) for row in rows),
            "max_rss": sorted(
                float(row.get("max_rss_mib") or row["max_rss_mb"])
                for row in rows
            ),
        })
    return data


def add_shared_legends(parts):
    parts.append(
        '<text x="245" y="625" text-anchor="end" font-family="Times New Roman, Times, serif" '
        'font-size="21" font-weight="700" fill="#303134">Method:</text>'
    )
    for x, strategy in (
        (275.0, "A*"),
        (455.0, "Best-first B&B"),
        (790.0, "Depth-first B&B"),
    ):
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
        'font-size="21" font-weight="700" fill="#303134">LP:</text>'
    )
    for x, lp_setting in zip((275.0, 480.0, 720.0), LP_STYLES):
        dash = LP_STYLES[lp_setting]
        dash_attr = (' stroke-dasharray="%s"' % dash) if dash else ""
        parts.append(
            '<line x1="%.1f" y1="659" x2="%.1f" y2="659" stroke="#303134" '
            'stroke-width="6" stroke-linecap="round"%s/>' %
            (x, x + 55.0, dash_attr)
        )
        parts.append(base.marker_svg(
            MARKER_SHAPES[lp_setting], x + 27.5, 659.0,
            "#303134", size=5.2
        ))
        parts.append(
            '<text x="%.1f" y="666" font-family="Times New Roman, Times, serif" font-size="20" '
            'fill="#303134">%s</text>' %
            (x + 67.0, escape(lp_setting))
        )


def configure_base():
    base.OUTPUT = OUTPUT
    base.FIGURE_TITLE = "Runtime, maximum-frontier, and peak-RSS profiles by LP setting"
    base.FIGURE_DESC = (
        "Cumulative wall-time, maximum-frontier, and peak-RSS profiles for "
        "A-star, best-first branch-and-bound, and depth-first branch-and-bound "
        "under no-LP, weak-LP, and strong-LP configurations."
    )
    base.RUNTIME_AXIS_LABEL = "Wall time (seconds)"
    base.SEARCH_COLORS = SEARCH_COLORS
    base.PRUNING_STYLES = LP_STYLES
    base.MARKER_SHAPES = MARKER_SHAPES
    base.add_shared_legends = add_shared_legends


# Markers at fixed x values miss steep stretches of a curve, and markers at
# fixed fractions miss flat ones.  The frontier and RSS panels therefore place
# a marker every MARKER_SPACING pixels along the drawn curve.  Each series
# starts at its own phase so markers of overlapping curves do not coincide.
MARKER_SPACING = 60.0


def curve_points(values, minimum, maximum, left):
    """Screen-space vertices of a step curve, from its first rise to its end.

    The flat run along 0% before the first instance is left out, so no marker
    sits on the x-axis.
    """
    xs = [base.log_position(value, minimum, maximum, left) for value in values]
    points = [(xs[0], base.y_position(0))]
    for count, x in enumerate(xs, 1):
        points.append((x, base.y_position(count)))
        if count < len(xs):
            points.append((xs[count], base.y_position(count)))
    return points


def spaced_marker_points(points, phase):
    """Points every MARKER_SPACING pixels along a polyline, starting at phase."""
    markers = []
    next_at = phase
    travelled = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        while length > 0.0 and next_at <= travelled + length:
            t = (next_at - travelled) / length
            markers.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
            next_at += MARKER_SPACING
        travelled += length
    return markers


def add_curves_spaced_markers(parts, data, key, minimum, maximum, left,
                              clip_id):
    parts.append('<g clip-path="url(#clip-%s)">' % clip_id)
    for index, item in enumerate(data):
        values = sorted(item[key])
        color = base.SEARCH_COLORS[item["strategy"]]
        dash = base.PRUNING_STYLES[item["pruning"]]
        dash_attr = (' stroke-dasharray="%s"' % dash) if dash else ""
        parts.append(
            '<path d="%s" fill="none" stroke="%s" stroke-width="4"%s '
            'stroke-linejoin="round" stroke-linecap="round"/>' %
            (base.step_path(values, minimum, maximum, left), color, dash_attr)
        )
        if not values:
            continue
        shape = base.MARKER_SHAPES[item["pruning"]]
        phase = MARKER_SPACING * (0.5 + index / float(len(data)))
        for x, y in spaced_marker_points(
                curve_points(values, minimum, maximum, left), phase):
            parts.append(base.marker_svg(shape, x, y, color))
    parts.append('</g>')


def generate_svg(data):
    base.WIDTH = WIDTH
    base.HEIGHT = HEIGHT
    base.PLOT_WIDTH = PLOT_WIDTH
    base.LEFT_X = PANEL_X[0]
    base.RIGHT_X = PANEL_X[1]
    base.PLOT_BOTTOM = PLOT_BOTTOM
    base.X_TICK_LABEL_Y = X_TICK_LABEL_Y
    base.X_AXIS_LABEL_Y = X_AXIS_LABEL_Y

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img" aria-labelledby="title desc">' %
        (WIDTH, HEIGHT, WIDTH, HEIGHT),
        '<title id="title">%s</title>' % escape(base.FIGURE_TITLE),
        '<desc id="desc">%s Runtime includes only optimally solved runs; '
        'frontier and RSS profiles include all 480 runs.</desc>' %
        escape(base.FIGURE_DESC),
        '<rect width="%d" height="%d" fill="#ffffff"/>' % (WIDTH, HEIGHT),
        '<defs>',
    ]
    for clip_id, left in zip(("runtime", "frontier", "rss"), PANEL_X):
        parts.append(
            '<clipPath id="clip-%s"><rect x="%.1f" y="%.1f" width="%.1f" '
            'height="%.1f"/></clipPath>' %
            (clip_id, left, base.PLOT_TOP, PLOT_WIDTH,
             base.PLOT_BOTTOM - base.PLOT_TOP)
        )
    parts.append('</defs>')

    time_ticks = tuple(
        (value, label, base.TIME_MIN, base.TIME_MAX)
        for value, label in (
            (0.001, "0.001"), (0.01, "0.01"), (0.1, "0.1"),
            (1.0, "1"), (10.0, "10"), (100.0, "100"),
            (1000.0, "1k"), (7200.0, "7.2k"),
        )
    )
    frontier_ticks = tuple(
        (value, label, base.FRONTIER_MIN, base.FRONTIER_MAX)
        for value, label in (
            (10, "10"), (1000, "1k"), (100000, "100k"),
            (10000000, "10m"), (1000000000, "1b"),
        )
    )
    rss_ticks = tuple(
        (value, label, RSS_MIN, RSS_MAX)
        for value, label in (
            (10, "10"), (100, "100"), (1000, "1k"),
            (10000, "10k"), (65536, "64 GiB"),
        )
    )

    base.add_panel_axes(
        parts, PANEL_X[0], time_ticks, "Wall time (seconds)",
        "Instances proven optimal",
    )
    base.add_panel_axes(
        parts, PANEL_X[1], frontier_ticks, "Maximum frontier size",
        "Instances within frontier size",
    )
    base.add_panel_axes(
        parts, PANEL_X[2], rss_ticks, "Peak RSS (MiB)",
        "Instances within peak RSS",
    )
    base.add_curves(
        parts, data, "optimal_times", base.TIME_MIN, base.TIME_MAX,
        PANEL_X[0], (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0),
        clip_id="runtime",
    )
    add_curves_spaced_markers(
        parts, data, "max_open", base.FRONTIER_MIN, base.FRONTIER_MAX,
        PANEL_X[1], clip_id="frontier",
    )
    add_curves_spaced_markers(
        parts, data, "max_rss", RSS_MIN, RSS_MAX, PANEL_X[2], clip_id="rss",
    )
    add_shared_legends(parts)
    parts.append('</svg>')

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w") as handle:
        handle.write("\n".join(parts) + "\n")


def main():
    configure_base()
    generate_svg(load_data())
    print(OUTPUT)


if __name__ == "__main__":
    main()

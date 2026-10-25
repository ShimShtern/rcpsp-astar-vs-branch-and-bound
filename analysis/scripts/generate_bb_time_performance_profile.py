#!/usr/bin/env python3
"""Generate the jointly-optimal B&B wall-time performance profile as SVG."""
import csv
import math
import os


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = os.path.join(
    REPO_ROOT, "Results", "bb", "parent_linked", "same_start_dominance",
    "active_schedule", "no_duplicate_tracking", "memory_limit_64GiB"
)
METHODS = [
    ("Best-first / No LP", "#0072B2", "solid",
     os.path.join(BASE, "j30_bb_best_first_dominance_120min_all.csv")),
    ("Best-first / Weak LP", "#0072B2", "dashed",
     os.path.join(BASE, "best_first", "lp_relaxation", "weak_precedence",
                  "j30_bb_120min.csv")),
    ("Best-first / Strong LP", "#0072B2", "dotted",
     os.path.join(BASE, "best_first", "lp_relaxation", "strong_precedence",
                  "j30_bb_120min.csv")),
    ("Depth-first / No LP", "#009E73", "solid",
     os.path.join(BASE, "depth_first_best_lb",
                  "j30_bb_depth_first_best_lb_active_schedule_dominance_120min_all.csv")),
    ("Depth-first / Weak LP", "#009E73", "dashed",
     os.path.join(BASE, "depth_first_best_lb", "lp_relaxation",
                  "weak_precedence", "j30_bb_120min.csv")),
    ("Depth-first / Strong LP", "#009E73", "dotted",
     os.path.join(BASE, "depth_first_best_lb", "lp_relaxation",
                  "strong_precedence", "j30_bb_120min.csv")),
    ("A*", "#D55E00", "solid",
     os.path.join(REPO_ROOT, "Results", "astar", "same_start_dominance",
                  "exact_active_schedule", "memory_limit_64GiB",
                  "j30_astar_120min.csv")),
]
OUTPUT = os.path.join(
    REPO_ROOT, "analysis", "figures",
    "bb_jointly_optimal_time_performance_profile.svg"
)


def load(path):
    with open(path, newline="") as handle:
        return {row["instance"]: row for row in csv.DictReader(handle)}


def sx(value):
    return 90.0 + math.log10(value) / 4.0 * 740.0


def sy(value):
    return 590.0 - value * 460.0


def step_path(values, maximum=10000.0):
    values = sorted(values)
    n = float(len(values))
    at_one = sum(value <= 1.0 + 1e-12 for value in values) / n
    commands = ["M %.2f %.2f" % (sx(1.0), sy(at_one))]
    count = int(round(at_one * n))
    for value in values[count:]:
        if value > maximum:
            break
        commands.append("H %.2f" % sx(value))
        count += 1
        commands.append("V %.2f" % sy(count / n))
    commands.append("H %.2f" % sx(maximum))
    return " ".join(commands)


def generate_profile(time_shift=0.0, output=OUTPUT):
    data = [(name, color, style, load(path))
            for name, color, style, path in METHODS]
    common = set.intersection(*[
        {instance for instance, row in rows.items()
         if row["status"] == "optimal"}
        for _, _, _, rows in data
    ])
    fastest = {
        instance: min(float(rows[instance].get("wall_runtime") or
                            rows[instance]["runtime"])
                      for _, _, _, rows in data)
        for instance in common
    }

    curves = []
    for name, color, style, rows in data:
        ratios = [(float(rows[instance].get("wall_runtime") or
                         rows[instance]["runtime"]) + time_shift) /
                  (fastest[instance] + time_shift)
                  for instance in common]
        curves.append((name, color, style, step_path(ratios)))

    if time_shift:
        title = "Shifted time to proven optimality"
        subtitle = ("Performance profile on %d jointly optimal J30 instances "
                    "with a %g-second shift" % (len(common), time_shift))
        xlabel = ("Shifted wall-time factor relative to fastest method "
                  "(%g-second shift; log scale)" % time_shift)
        description = ("Performance profile over %d instances solved "
                       "optimally by all seven methods. Each ratio is "
                       "(method wall time plus %g-second shift) divided by "
                       "(fastest wall time plus the same shift)." %
                       (len(common), time_shift))
    else:
        title = "Time to proven optimality"
        subtitle = ("Performance profile on %d jointly optimal J30 instances"
                    % len(common))
        xlabel = ("Wall-time factor relative to fastest method "
                  "(log scale)")
        description = ("Performance profile over %d instances solved "
                       "optimally by all seven methods. The horizontal axis "
                       "is wall time divided by the fastest method on each "
                       "instance, on a logarithmic scale." % len(common))

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="800" viewBox="0 0 900 800" role="img" aria-labelledby="title desc">',
        '<title id="title">Wall-time performance profile for jointly optimal B&amp;B instances</title>',
        '<desc id="desc">%s The vertical axis is the fraction of instances completed within that factor. Color identifies the search method, while line style identifies the LP configuration.</desc>' % description,
        '<rect width="900" height="800" fill="#ffffff"/>',
        '<text x="450" y="39" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="24" font-weight="700" fill="#202124">%s</text>' % title,
        '<text x="450" y="68" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="15" fill="#5f6368">%s</text>' % subtitle,
    ]

    for fraction in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        y = sy(fraction)
        parts.append('<line x1="90" y1="%.2f" x2="830" y2="%.2f" stroke="#dfe3e8" stroke-width="1"/>' % (y, y))
        parts.append('<text x="78" y="%.2f" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="16" fill="#5f6368">%d%%</text>' % (y + 6, int(fraction * 100)))
    for value in (1, 10, 100, 1000, 10000):
        x = sx(value)
        parts.append('<line x1="%.2f" y1="130" x2="%.2f" y2="590" stroke="#edf0f2" stroke-width="1"/>' % (x, x))
        label = "1" if value == 1 else "10%s" % ({10: "", 100: "²", 1000: "³", 10000: "⁴"}[value])
        parts.append('<text x="%.2f" y="617" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="16" fill="#5f6368">%s×</text>' % (x, label))

    parts.extend([
        '<line x1="90" y1="130" x2="90" y2="590" stroke="#7b8187" stroke-width="1.5"/>',
        '<line x1="90" y1="590" x2="830" y2="590" stroke="#7b8187" stroke-width="1.5"/>',
        '<text x="460" y="653" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="18" fill="#303134">%s</text>' % xlabel,
        '<text x="22" y="360" transform="rotate(-90 22 360)" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="18" fill="#303134">Instances completed within factor</text>',
    ])

    for _, color, style, path in curves:
        dash = (' stroke-dasharray="12 8"' if style == "dashed" else
                ' stroke-dasharray="2 7"' if style == "dotted" else "")
        parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="4"%s stroke-linejoin="round"/>' % (path, color, dash))

    parts.append('<text x="120" y="713" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="16" font-weight="700" fill="#303134">Method:</text>')
    for x, color, label in (
        (145, "#D55E00", "A*"),
        (280, "#0072B2", "Best-first B&amp;B"),
        (555, "#009E73", "Depth-first B&amp;B"),
    ):
        parts.append('<line x1="%d" y1="708" x2="%d" y2="708" stroke="%s" stroke-width="5"/>' % (x, x + 42, color))
        parts.append('<text x="%d" y="713" font-family="Times New Roman, Times, serif" font-size="15" fill="#303134">%s</text>' % (x + 50, label))

    parts.append('<text x="120" y="758" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="16" font-weight="700" fill="#303134">LP:</text>')
    for x, style, label in (
        (145, "solid", "No LP"),
        (305, "dashed", "Weak LP"),
        (495, "dotted", "Strong LP"),
    ):
        dash = (' stroke-dasharray="12 8"' if style == "dashed" else
                ' stroke-dasharray="2 7"' if style == "dotted" else "")
        parts.append('<line x1="%d" y1="753" x2="%d" y2="753" stroke="#303134" stroke-width="5" stroke-linecap="round"%s/>' % (x, x + 46, dash))
        parts.append('<text x="%d" y="758" font-family="Times New Roman, Times, serif" font-size="15" fill="#303134">%s</text>' % (x + 55, label))

    parts.append('</svg>')
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w") as handle:
        handle.write("\n".join(parts) + "\n")
    print("%s: %d jointly optimal instances" % (output, len(common)))


def main():
    generate_profile()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate upper- and lower-bound gap profiles (percent from the optimum) as SVG."""
import math
import os
from bisect import bisect_right
from xml.sax.saxutils import escape

from generate_bb_time_performance_profile import METHODS, load


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT = os.path.join(
    REPO_ROOT, "analysis", "figures", "bb_final_bound_performance_profile.svg"
)
UPPER_MAX_GAP = 300.0
LOWER_MAX_GAP = 60.0
PANEL_WIDTH = 580.0
PANEL_TOP = 30.0
PANEL_BOTTOM = 500.0
Y_MIN = 0.75
INSET_LEFT = 470.0
INSET_RIGHT = 660.0
INSET_TOP = 340.0
INSET_BOTTOM = 440.0
INSET_X_MAX = 300.0
INSET_Y_MIN = 0.99
LOWER_INSET_LEFT = 1180.0
LOWER_INSET_RIGHT = 1370.0
LOWER_INSET_TOP = 340.0
LOWER_INSET_BOTTOM = 440.0
LOWER_INSET_X_MAX = 60.0
LOWER_INSET_Y_MIN = 0.99

METHOD_COLORS = {
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


def upper_bound(row):
    """Return the best recorded feasible bound, with the horizon as fallback."""
    value = row.get("makespan") or row.get("upper_bound") or row["horizon"]
    return float(value)


def lower_bound(row):
    """Return the final global bound available in the result schema."""
    if row["status"] == "optimal":
        return float(row.get("known_optimum") or row["makespan"])
    value = row.get("best_active_lb")
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        pass
    # A* does not record a final frontier bound. Its CP/resource bound is the
    # conservative recorded lower bound for an unfinished search.
    return max(float(row.get("cp_lb") or 0.0),
               float(row.get("res_lb") or 0.0))


def sx(value, left, maximum):
    return left + value / maximum * PANEL_WIDTH


def sy(value):
    return PANEL_BOTTOM - ((value - Y_MIN) / (1.0 - Y_MIN) *
                           (PANEL_BOTTOM - PANEL_TOP))


def step_path(values, left, maximum):
    values = sorted(values)
    total = float(len(values))
    count = sum(value <= 1e-9 for value in values)
    commands = ["M %.2f %.2f" %
                (sx(0.0, left, maximum), sy(count / total))]
    for value in values[count:]:
        if value > maximum:
            break
        commands.append("H %.2f" % sx(value, left, maximum))
        count += 1
        commands.append("V %.2f" % sy(count / total))
    commands.append("H %.2f" % sx(maximum, left, maximum))
    return " ".join(commands)


def marker_svg(shape, x, y, color, size=4.6):
    common = 'fill="#ffffff" stroke="%s" stroke-width="2"' % color
    if shape == "circle":
        return '<circle cx="%.2f" cy="%.2f" r="%.1f" %s/>' % (
            x, y, size, common
        )
    if shape == "square":
        return '<rect x="%.2f" y="%.2f" width="%.1f" height="%.1f" %s/>' % (
            x - size, y - size, 2.0 * size, 2.0 * size, common
        )
    return '<path d="M %.2f %.2f L %.2f %.2f L %.2f %.2f Z" %s/>' % (
        x, y - size - 0.6,
        x + size + 0.6, y + size,
        x - size - 0.6, y + size,
        common,
    )


def inset_sx(value):
    return (INSET_LEFT + value / INSET_X_MAX *
            (INSET_RIGHT - INSET_LEFT))


def inset_sy(value):
    return (INSET_BOTTOM - (value - INSET_Y_MIN) / (1.0 - INSET_Y_MIN) *
            (INSET_BOTTOM - INSET_TOP))


def inset_step_path(values):
    values = sorted(values)
    total = float(len(values))
    count = sum(value <= 1e-9 for value in values)
    commands = ["M %.2f %.2f" %
                (inset_sx(0.0), inset_sy(count / total))]
    for value in values[count:]:
        if value > INSET_X_MAX:
            break
        commands.append("H %.2f" % inset_sx(value))
        count += 1
        commands.append("V %.2f" % inset_sy(count / total))
    commands.append("H %.2f" % inset_sx(INSET_X_MAX))
    return " ".join(commands)


def lower_inset_sx(value):
    return (LOWER_INSET_LEFT +
            value / LOWER_INSET_X_MAX *
            (LOWER_INSET_RIGHT - LOWER_INSET_LEFT))


def lower_inset_sy(value):
    return (LOWER_INSET_BOTTOM -
            (value - LOWER_INSET_Y_MIN) / (1.0 - LOWER_INSET_Y_MIN) *
            (LOWER_INSET_BOTTOM - LOWER_INSET_TOP))


def lower_inset_step_path(values):
    values = sorted(values)
    total = float(len(values))
    count = sum(value <= 1e-9 for value in values)
    commands = ["M %.2f %.2f" %
                (lower_inset_sx(0.0), lower_inset_sy(count / total))]
    for value in values[count:]:
        if value > LOWER_INSET_X_MAX:
            break
        commands.append("H %.2f" % lower_inset_sx(value))
        count += 1
        commands.append("V %.2f" % lower_inset_sy(count / total))
    commands.append("H %.2f" % lower_inset_sx(LOWER_INSET_X_MAX))
    return " ".join(commands)


def method_label(name):
    if name.startswith("A*"):
        return "A*"
    if name.startswith("Best-first"):
        return "Best-first B&B"
    return "Depth-first B&B"


def lp_label(name):
    return "No LP" if name.startswith("A*") else name.split(" / ")[-1]


def dash_attribute(lp_setting):
    dash = LP_STYLES[lp_setting]
    return (' stroke-dasharray="%s"' % dash) if dash else ""


def main():
    data = [
        (name, METHOD_COLORS[method_label(name)], lp_label(name), load(path))
        for name, _, _, path in METHODS
    ]
    common = set.intersection(*[set(rows) for _, _, _, rows in data])

    upper_ratios = {name: [] for name, _, _, _ in data}
    lower_ratios = {name: [] for name, _, _, _ in data}
    for instance in common:
        optimum = float(next(
            rows[instance]["known_optimum"]
            for _, _, _, rows in data
            if rows[instance].get("known_optimum")
        ))
        upper_values = {
            name: upper_bound(rows[instance])
            for name, _, _, rows in data
        }
        lower_values = {
            name: lower_bound(rows[instance])
            for name, _, _, rows in data
        }
        for name, _, _, _ in data:
            upper_ratios[name].append(
                100.0 * (upper_values[name] - optimum) / optimum)
            lower_ratios[name].append(
                100.0 * (optimum - lower_values[name]) / optimum)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1450" height="715" viewBox="0 0 1450 715" role="img" aria-labelledby="title desc">',
        '<title id="title">Final upper- and lower-bound performance profiles</title>',
        '<desc id="desc">Two performance profiles over all %d PSPLIB J30 instances. The horizontal axes give the relative gap of each method to the known optimum: (UB minus optimum) divided by the optimum, and (optimum minus LB) divided by the optimum, in percent. A gap of zero means that the bound equals the optimum. The insets enlarge the 99 through 100 percent tails. Color identifies the search method, while line style identifies the LP configuration.</desc>' % len(common),
        '<rect width="1450" height="715" fill="#ffffff"/>',
        '<defs>',
        '<clipPath id="upper-inset-clip"><rect x="%.2f" y="%.2f" width="%.2f" height="%.2f"/></clipPath>' % (
            INSET_LEFT, INSET_TOP, INSET_RIGHT - INSET_LEFT,
            INSET_BOTTOM - INSET_TOP),
        '<clipPath id="lower-inset-clip"><rect x="%.2f" y="%.2f" width="%.2f" height="%.2f"/></clipPath>' % (
            LOWER_INSET_LEFT, LOWER_INSET_TOP,
            LOWER_INSET_RIGHT - LOWER_INSET_LEFT,
            LOWER_INSET_BOTTOM - LOWER_INSET_TOP),
        '</defs>',
    ]

    panels = [
        (105.0, "Upper bound", upper_ratios,
         "(UB \u2212 optimum) / optimum", UPPER_MAX_GAP,
         (0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0)),
        (815.0, "Lower bound", lower_ratios,
         "(optimum \u2212 LB) / optimum", LOWER_MAX_GAP,
         (0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0)),
    ]
    for left, title, ratios, xlabel, maximum, ticks in panels:
        for fraction in (0.75, 0.80, 0.85, 0.90, 0.95, 1.0):
            y = sy(fraction)
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#dfe3e8" stroke-width="1"/>' % (left, y, left + PANEL_WIDTH, y))
            parts.append('<text x="%.2f" y="%.2f" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="20" fill="#5f6368">%d%%</text>' % (left - 12, y + 7, int(100 * fraction)))
        for tick in ticks:
            x = sx(tick, left, maximum)
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#edf0f2" stroke-width="1"/>' % (x, PANEL_TOP, x, PANEL_BOTTOM))
            parts.append('<text x="%.2f" y="530" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="20" fill="#5f6368">%g%%</text>' % (x, tick))
        parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#7b8187" stroke-width="1.7"/>' % (left, PANEL_TOP, left, PANEL_BOTTOM))
        parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#7b8187" stroke-width="1.7"/>' % (left, PANEL_BOTTOM, left + PANEL_WIDTH, PANEL_BOTTOM))
        parts.append('<text x="%.2f" y="570" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="24" fill="#303134">%s</text>' % (left + PANEL_WIDTH / 2.0, xlabel))
        for name, color, style, _ in data:
            parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="4"%s stroke-linejoin="round"/>' % (
                step_path(ratios[name], left, maximum), color,
                dash_attribute(style)))
            ordered = sorted(ratios[name])
            for marker_value in ticks:
                count = bisect_right(ordered, marker_value)
                if count:
                    parts.append(marker_svg(
                        MARKER_SHAPES[style],
                        sx(marker_value, left, maximum),
                        sy(count / float(len(ordered))), color, size=5.4,
                    ))

        if title == "Upper bound":
            parts.append('<rect x="420" y="300" width="255" height="170" rx="4" fill="#ffffff" fill-opacity="0.96" stroke="#9aa0a6" stroke-width="1"/>')
            parts.append('<text x="547.5" y="324" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="13" font-weight="700" fill="#303134">Upper-bound tail detail</text>')
            for fraction in (0.99, 0.995, 1.0):
                y = inset_sy(fraction)
                parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#e3e6e8" stroke-width="1"/>' % (INSET_LEFT, y, INSET_RIGHT, y))
                parts.append('<text x="%.2f" y="%.2f" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="10" fill="#5f6368">%g%%</text>' % (INSET_LEFT - 5.0, y + 3.5, 100.0 * fraction))
            for tick in (0.0, 100.0, 200.0, 300.0):
                x = inset_sx(tick)
                parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#eef0f2" stroke-width="1"/>' % (x, INSET_TOP, x, INSET_BOTTOM))
                parts.append('<text x="%.2f" y="455" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="10" fill="#5f6368">%g%%</text>' % (x, tick))
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#7b8187" stroke-width="1"/>' % (INSET_LEFT, INSET_TOP, INSET_LEFT, INSET_BOTTOM))
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#7b8187" stroke-width="1"/>' % (INSET_LEFT, INSET_BOTTOM, INSET_RIGHT, INSET_BOTTOM))
            parts.append('<g clip-path="url(#upper-inset-clip)">')
            for name, color, style, _ in data:
                parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"%s stroke-linejoin="round"/>' % (
                    inset_step_path(ratios[name]), color,
                    dash_attribute(style)))
                ordered = sorted(ratios[name])
                for marker_value in (100.0, 200.0, 300.0):
                    count = bisect_right(ordered, marker_value)
                    if count:
                        parts.append(marker_svg(
                            MARKER_SHAPES[style], inset_sx(marker_value),
                            inset_sy(count / float(len(ordered))), color,
                            size=2.8,
                        ))
            parts.append('</g>')

        if title == "Lower bound":
            parts.append('<rect x="1130" y="300" width="255" height="170" rx="4" fill="#ffffff" fill-opacity="0.96" stroke="#9aa0a6" stroke-width="1"/>')
            parts.append('<text x="1257.5" y="324" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="13" font-weight="700" fill="#303134">Lower-bound tail detail</text>')
            for fraction in (0.99, 0.995, 1.0):
                y = lower_inset_sy(fraction)
                parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#e3e6e8" stroke-width="1"/>' % (LOWER_INSET_LEFT, y, LOWER_INSET_RIGHT, y))
                parts.append('<text x="%.2f" y="%.2f" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="10" fill="#5f6368">%g%%</text>' % (LOWER_INSET_LEFT - 5.0, y + 3.5, 100.0 * fraction))
            for tick in (0.0, 20.0, 40.0, 60.0):
                x = lower_inset_sx(tick)
                parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#eef0f2" stroke-width="1"/>' % (x, LOWER_INSET_TOP, x, LOWER_INSET_BOTTOM))
                parts.append('<text x="%.2f" y="455" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="10" fill="#5f6368">%g%%</text>' % (x, tick))
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#7b8187" stroke-width="1"/>' % (LOWER_INSET_LEFT, LOWER_INSET_TOP, LOWER_INSET_LEFT, LOWER_INSET_BOTTOM))
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#7b8187" stroke-width="1"/>' % (LOWER_INSET_LEFT, LOWER_INSET_BOTTOM, LOWER_INSET_RIGHT, LOWER_INSET_BOTTOM))
            parts.append('<g clip-path="url(#lower-inset-clip)">')
            for name, color, style, _ in data:
                parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"%s stroke-linejoin="round"/>' % (
                    lower_inset_step_path(ratios[name]), color,
                    dash_attribute(style)))
                ordered = sorted(ratios[name])
                for marker_value in (10.0, 20.0, 30.0, 40.0, 50.0):
                    count = bisect_right(ordered, marker_value)
                    if count:
                        parts.append(marker_svg(
                            MARKER_SHAPES[style],
                            lower_inset_sx(marker_value),
                            lower_inset_sy(count / float(len(ordered))),
                            color, size=2.8,
                        ))
            parts.append('</g>')

    for x in (29, 739):
        parts.append('<text x="%d" y="265" transform="rotate(-90 %d 265)" text-anchor="middle" font-family="Times New Roman, Times, serif" font-size="23" fill="#303134">Fraction of instances within gap</text>' % (x, x))

    parts.append('<text x="245" y="625" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="21" font-weight="700" fill="#303134">Method:</text>')
    for x, method in ((275, "A*"), (455, "Best-first B&B"),
                      (790, "Depth-first B&B")):
        parts.append('<line x1="%d" y1="619" x2="%d" y2="619" stroke="%s" stroke-width="6"/>' % (
            x, x + 42, METHOD_COLORS[method]))
        parts.append('<text x="%d" y="626" font-family="Times New Roman, Times, serif" font-size="20" fill="#303134">%s</text>' % (
            x + 56, escape(method)))

    parts.append('<text x="245" y="665" text-anchor="end" font-family="Times New Roman, Times, serif" font-size="21" font-weight="700" fill="#303134">LP:</text>')
    for x, lp_setting in ((275, "No LP"), (480, "Weak LP"),
                          (720, "Strong LP")):
        parts.append('<line x1="%d" y1="659" x2="%d" y2="659" stroke="#303134" stroke-width="6" stroke-linecap="round"%s/>' % (
            x, x + 55, dash_attribute(lp_setting)))
        parts.append(marker_svg(
            MARKER_SHAPES[lp_setting], x + 27.5, 659.0,
            "#303134", size=5.2,
        ))
        parts.append('<text x="%d" y="666" font-family="Times New Roman, Times, serif" font-size="20" fill="#303134">%s</text>' % (
            x + 67, lp_setting))
    parts.append('</svg>')

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w") as handle:
        handle.write("\n".join(parts) + "\n")
    print("%s: %d instances" % (OUTPUT, len(common)))


if __name__ == "__main__":
    main()

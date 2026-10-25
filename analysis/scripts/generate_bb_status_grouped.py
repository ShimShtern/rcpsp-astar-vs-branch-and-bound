#!/usr/bin/env python3
"""Generate the grouped A*/B&B outcome-count bar chart as SVG.

Four outcome groups (x-axis), seven bars each: A*, then best-first and
depth-first B&B with no LP, weak LP, and strong LP.  The series are the same
result files as the performance profiles (``METHODS``).
"""
import os

from generate_bb_time_performance_profile import METHODS, load


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT = os.path.join(
    REPO_ROOT, "analysis", "figures", "bb_status_all_methods_grouped.svg"
)

STATUSES = [
    ("optimal", ["Proven optimal"]),
    ("known_found", ["Known optimum found,", "not proven"]),
    ("suboptimal", ["Suboptimal incumbent", "at time limit"]),
    ("no_incumbent", ["Initial horizon bound", "only at time limit"]),
]
# (method name in METHODS, fill, x offset within a status group)
BARS = [
    ("A*", "#D55E00", 0),
    ("Best-first / No LP", "#0072B2", 45),
    ("Best-first / Weak LP", "url(#bf-weak)", 77),
    ("Best-first / Strong LP", "url(#bf-strong)", 109),
    ("Depth-first / No LP", "#009E73", 154),
    ("Depth-first / Weak LP", "url(#df-weak)", 186),
    ("Depth-first / Strong LP", "url(#df-strong)", 218),
]
GROUP_X = 105.0
GROUP_STEP = 290.0
BAR_WIDTH = 27.0
Y_ZERO = 550.0
Y_TOP = 120.0
Y_MAX = 480.0
FONT = 'font-family="Times New Roman, Times, serif"'


def outcome(row):
    """Classify one result row into one of the four outcome groups."""
    if row["status"] == "optimal":
        return "optimal"
    makespan = row.get("makespan")
    if makespan in (None, "", "None"):
        return "no_incumbent"
    known = row.get("known_optimum")
    if known not in (None, "") and int(float(makespan)) == int(float(known)):
        return "known_found"
    return "suboptimal"


def y_of(count):
    return Y_ZERO - count / Y_MAX * (Y_ZERO - Y_TOP)


def pattern(pattern_id, color, kind):
    if kind == "weak":
        return (
            '    <pattern id="%s" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">\n'
            '      <rect width="9" height="9" fill="%s"/><line x1="0" y1="0" x2="0" y2="9" stroke="#ffffff" stroke-width="3" stroke-opacity="0.78"/>\n'
            '    </pattern>' % (pattern_id, color))
    return (
        '    <pattern id="%s" width="9" height="9" patternUnits="userSpaceOnUse">\n'
        '      <rect width="9" height="9" fill="%s"/><circle cx="4.5" cy="4.5" r="1.8" fill="#ffffff" fill-opacity="0.9"/>\n'
        '    </pattern>' % (pattern_id, color))


def main():
    paths = {name: path for name, _color, _style, path in METHODS}
    counts = {}
    for name, _fill, _offset in BARS:
        rows = load(paths[name])
        counts[name] = {key: 0 for key, _label in STATUSES}
        for row in rows.values():
            counts[name][outcome(row)] += 1

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1300" height="680" viewBox="0 70 1300 680" role="img" aria-labelledby="title desc">',
        '  <title id="title">A* and B&amp;B outcome counts</title>',
        '  <desc id="desc">One grouped bar chart with status on the x-axis and seven bars per status. Color identifies A-star, best-first branch-and-bound, and depth-first best-lower-bound branch-and-bound. Solid, diagonally hatched, and dotted fills identify no LP, weak LP, and strong LP.</desc>',
        '  <rect width="1300" height="750" fill="#ffffff"/>',
        '  <defs>',
        pattern("bf-weak", "#0072B2", "weak"),
        pattern("bf-strong", "#0072B2", "strong"),
        pattern("df-weak", "#009E73", "weak"),
        pattern("df-strong", "#009E73", "strong"),
        pattern("legend-weak", "#666666", "weak"),
        pattern("legend-strong", "#666666", "strong"),
        '  </defs>',
        '',
        '  <g stroke="#dfe3e8" stroke-width="1">',
    ]
    for value in (0, 100, 200, 300, 400, 480):
        parts.append('    <line x1="95" y1="%.1f" x2="1225" y2="%.1f"/>' % (y_of(value), y_of(value)))
    parts.append('  </g>')
    parts.append('  <g %s font-size="17" fill="#5f6368" text-anchor="end">' % FONT)
    for value in (0, 100, 200, 300, 400, 480):
        parts.append('    <text x="86" y="%.1f">%d</text>' % (y_of(value) + 5, value))
    parts.append('  </g>')
    parts.append('  <line x1="95" y1="120" x2="95" y2="550" stroke="#7b8187" stroke-width="1.2"/>')
    parts.append('  <line x1="95" y1="550" x2="1225" y2="550" stroke="#7b8187" stroke-width="1.2"/>')
    parts.append('  <text x="25" y="335" transform="rotate(-90 25 335)" text-anchor="middle" %s font-size="20" fill="#303134">Number of instances</text>' % FONT)

    values = []
    for group, (key, _label) in enumerate(STATUSES):
        left = GROUP_X + group * GROUP_STEP
        parts.append('')
        parts.append('  <!-- %s: %s -->' % (key, ", ".join(
            "%s = %d" % (name, counts[name][key]) for name, _f, _o in BARS)))
        for name, fill, offset in BARS:
            count = counts[name][key]
            top = y_of(count)
            parts.append('  <rect x="%.0f" y="%.1f" width="%.0f" height="%.1f" fill="%s"/>' % (
                left + offset, top, BAR_WIDTH, Y_ZERO - top, fill))
            label_y = 538.0 if count == 0 else top - 10.0
            values.append('    <text x="%.1f" y="%.0f">%d</text>' % (
                left + offset + BAR_WIDTH / 2.0, label_y, count))

    parts.append('')
    parts.append('  <g text-anchor="middle" %s font-size="15" font-weight="700" fill="#303134">' % FONT)
    parts.extend(values)
    parts.append('  </g>')

    parts.append('  <g text-anchor="middle" %s font-size="12" fill="#6a7076">' % FONT)
    for group in range(len(STATUSES)):
        left = GROUP_X + group * GROUP_STEP
        parts.append('    <text x="%.1f" y="571">A*</text><text x="%.1f" y="571">Best-first</text><text x="%.1f" y="571">Depth-first</text>' % (
            left + 13.5, left + 90.5, left + 199.5))
    parts.append('  </g>')
    parts.append('  <g text-anchor="middle" %s font-size="17" font-weight="600" fill="#303134">' % FONT)
    for group, (_key, label) in enumerate(STATUSES):
        center = GROUP_X + group * GROUP_STEP + 122.5
        if len(label) == 1:
            parts.append('    <text x="%.1f" y="600">%s</text>' % (center, label[0]))
        else:
            parts.append('    <text x="%.1f" y="596">%s</text><text x="%.1f" y="616">%s</text>' % (
                center, label[0], center, label[1]))
    parts.append('  </g>')

    parts.extend([
        '',
        '  <text x="155" y="666" text-anchor="end" %s font-size="17" font-weight="700" fill="#303134">Method:</text>' % FONT,
        '  <g %s font-size="17" fill="#303134">' % FONT,
        '    <rect x="170" y="651" width="22" height="22" rx="2" fill="#D55E00"/><text x="201" y="668">A*</text>',
        '    <rect x="380" y="651" width="22" height="22" rx="2" fill="#0072B2"/><text x="411" y="668">BF B&amp;B</text>',
        '    <rect x="580" y="651" width="22" height="22" rx="2" fill="#009E73"/><text x="611" y="668">DF B&amp;B</text>',
        '  </g>',
        '  <text x="155" y="706" text-anchor="end" %s font-size="17" font-weight="700" fill="#303134">LP:</text>' % FONT,
        '  <g %s font-size="17" fill="#303134">' % FONT,
        '    <rect x="170" y="689" width="22" height="22" rx="2" fill="#666666"/><text x="201" y="707">No LP</text>',
        '    <rect x="350" y="689" width="22" height="22" rx="2" fill="url(#legend-weak)"/><text x="381" y="707">Weak LP</text>',
        '    <rect x="560" y="689" width="22" height="22" rx="2" fill="url(#legend-strong)"/><text x="591" y="707">Strong LP</text>',
        '  </g>',
        '</svg>',
    ])

    with open(OUTPUT, "w") as handle:
        handle.write("\n".join(parts) + "\n")
    print(OUTPUT)
    for name, _fill, _offset in BARS:
        print("  %-24s %s" % (name, counts[name]))


if __name__ == "__main__":
    main()

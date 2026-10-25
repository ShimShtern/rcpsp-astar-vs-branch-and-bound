#!/usr/bin/env python3
"""Generate pairwise bound-win matrices for the six B&B methods.

A* is omitted: it proves every instance optimal, so its column would be all
zeros and its row would only repeat each method's unsolved count.
"""
import csv
import math
import os

from generate_bb_time_performance_profile import METHODS, load
from generate_bb_bound_performance_profile import lower_bound, upper_bound


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT_SVG = os.path.join(
    REPO_ROOT, "analysis", "figures",
    "astar_bb_pairwise_win_tie_loss_matrices.svg"
)
OUTPUT_CSV = os.path.join(
    REPO_ROOT, "analysis", "tables",
    "astar_bb_pairwise_win_tie_loss_counts.csv"
)

SHORT_LABELS = {
    "A*": "A*",
    "Best-first / No LP": "BF No LP",
    "Best-first / Weak LP": "BF Weak LP",
    "Best-first / Strong LP": "BF Strong LP",
    "Depth-first / No LP": "DF No LP",
    "Depth-first / Weak LP": "DF Weak LP",
    "Depth-first / Strong LP": "DF Strong LP",
}


def blend(base, amount):
    """Blend an RGB color from white by ``amount`` in [0, 1]."""
    amount = max(0.0, min(1.0, amount))
    return "#%02x%02x%02x" % tuple(
        int(round(255 + (component - 255) * amount))
        for component in base
    )


def cell_color(wins, total):
    if total <= 0 or wins <= 0:
        return "#ffffff"
    # Darker blue means the row method wins on more instances.
    return blend((0, 114, 178), 0.15 + 0.70 * wins / float(total))


def compare(methods, instances, value, lower_is_better):
    result = {}
    for row_name, row_data in methods:
        for column_name, column_data in methods:
            wins = ties = losses = 0
            for instance in instances:
                left = value(row_data[instance])
                right = value(column_data[instance])
                if math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12):
                    ties += 1
                elif ((left < right) if lower_is_better else (left > right)):
                    wins += 1
                else:
                    losses += 1
            result[(row_name, column_name)] = (wins, ties, losses)
    return result


def add_matrix(parts, panel_x, panel_y, title, methods, counts, sample_size):
    cell_width = 70.0
    cell_height = 43.0
    grid_left = panel_x + 104.0
    grid_top = panel_y + 60.0

    parts.append(
        '<text x="%.1f" y="%.1f" font-family="Times New Roman, Times, serif" font-size="20" '
        'font-weight="700" fill="#202124">%s</text>' %
        (panel_x + 10.0, panel_y + 22, title)
    )
    for column, (name, _) in enumerate(methods):
        x = grid_left + (column + 0.5) * cell_width
        parts.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" '
            'font-family="Times New Roman, Times, serif" font-size="11" fill="#303134">%s</text>' %
            (x, grid_top - 16.0, SHORT_LABELS[name])
        )

    for row, (row_name, _) in enumerate(methods):
        y = grid_top + row * cell_height
        parts.append(
            '<text x="%.1f" y="%.1f" text-anchor="end" '
            'font-family="Times New Roman, Times, serif" font-size="12" fill="#303134">%s</text>' %
            (grid_left - 10.0, y + cell_height / 2.0 + 4.5,
             SHORT_LABELS[row_name])
        )
        for column, (column_name, _) in enumerate(methods):
            x = grid_left + column * cell_width
            wins, ties, losses = counts[(row_name, column_name)]
            fill = "#f1f3f4" if row == column else cell_color(
                wins, sample_size
            )
            parts.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                'fill="%s" stroke="#c8ccd0" stroke-width="1"/>' %
                (x, y, cell_width, cell_height, fill)
            )
            label = "—" if row == column else "%d" % wins
            parts.append(
                '<text x="%.1f" y="%.1f" text-anchor="middle" '
                'font-family="Times New Roman, Times, serif" font-size="12" '
                'font-weight="%s" fill="#202124">%s</text>' %
                (x + cell_width / 2.0, y + cell_height / 2.0 + 4.5,
                 "700" if row != column else "400", label)
            )


def main():
    loaded = [(name, load(path)) for name, _, _, path in METHODS
              if not name.startswith("A*")]
    methods = sorted(loaded, key=lambda item: (
        0 if item[0].startswith("Best-first") else 1,
        ("No LP", "Weak LP", "Strong LP").index(item[0].split(" / ")[-1])
    ))
    all_instances = sorted(set.intersection(*[set(rows) for _, rows in methods]))
    matrices = [
        ("Upper bound",
         "upper_bound", all_instances, upper_bound, True),
        ("Lower bound",
         "lower_bound", all_instances, lower_bound, False),
    ]
    calculated = []
    for title, key, instances, value, lower_is_better in matrices:
        calculated.append((title, key, instances,
                           compare(methods, instances, value,
                                   lower_is_better)))

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "metric", "row_method", "column_method", "row_wins",
            "instances"
        ])
        for _, key, instances, counts in calculated:
            for row_name, _ in methods:
                for column_name, _ in methods:
                    wins, ties, losses = counts[(row_name, column_name)]
                    writer.writerow([
                        key, row_name, column_name, wins, len(instances)
                    ])

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1130" height="335" viewBox="0 0 1130 335" role="img" aria-labelledby="title desc">',
        '<title id="title">Pairwise B&amp;B bound comparisons</title>',
        '<desc id="desc">Pairwise upper- and lower-bound matrices. Each cell gives the number of instances on which the row method has a better bound than the column method.</desc>',
        '<rect width="1130" height="335" fill="#ffffff"/>',
    ]
    panel_positions = ((10.0, 0.0), (575.0, 0.0))
    for (panel_x, panel_y), (title, _, instances, counts) in zip(
            panel_positions, calculated):
        add_matrix(parts, panel_x, panel_y, title, methods, counts,
                   len(instances))

    parts.append('</svg>')

    os.makedirs(os.path.dirname(OUTPUT_SVG), exist_ok=True)
    with open(OUTPUT_SVG, "w") as handle:
        handle.write("\n".join(parts) + "\n")
    print("%s" % OUTPUT_SVG)
    print("%s" % OUTPUT_CSV)


if __name__ == "__main__":
    main()

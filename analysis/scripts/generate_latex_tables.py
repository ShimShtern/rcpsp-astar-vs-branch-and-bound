#!/usr/bin/env python3
"""Generate the LaTeX computational-summary and unresolved-subclass tables.

Both tables use the same seven result files as the figures (``METHODS``).
"""
import os
import sys

from generate_bb_time_performance_profile import METHODS, load


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TABLES_DIR = os.path.join(REPO_ROOT, "analysis", "tables")
SUMMARY_OUTPUT = os.path.join(TABLES_DIR, "astar_bb_computational_summary.tex")
UNRESOLVED_OUTPUT = os.path.join(TABLES_DIR, "unresolved_by_psplib_subclass.tex")

# (method name in METHODS, Method column, Search column, LP column)
ROWS = [
    ("A*", "A$^*$", "--", "--"),
    ("Best-first / No LP", "B\\&B", "BF", "No"),
    ("Best-first / Weak LP", "", "BF", "Weak"),
    ("Best-first / Strong LP", "", "BF", "Strong"),
    ("Depth-first / No LP", "", "DF", "No"),
    ("Depth-first / Weak LP", "", "DF", "Weak"),
    ("Depth-first / Strong LP", "", "DF", "Strong"),
]
UNRESOLVED_COLUMNS = [
    "A*",
    "Best-first / No LP", "Best-first / Weak LP", "Best-first / Strong LP",
    "Depth-first / No LP", "Depth-first / Weak LP", "Depth-first / Strong LP",
]


def quantile(values, q):
    """Linear-interpolation quantile (NumPy's default method)."""
    values = sorted(values)
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def wall_time(row):
    return float(row.get("wall_runtime") or row["runtime"])


def rss(row):
    return float(row.get("max_rss_mib") or row["max_rss_mb"])


def summary_cell(values, fmt):
    median = fmt.format(quantile(values, 0.5))
    low = fmt.format(quantile(values, 0.25))
    high = fmt.format(quantile(values, 0.75))
    return "\\shortstack[r]{%s\\\\{[%s--%s]}}" % (median, low, high)


def generate_summary(data):
    common = sorted(set.intersection(*[
        {k for k, row in rows.items() if row["status"] == "optimal"}
        for rows in data.values()
    ]))
    n = len(common)
    lines = [
        "% Requires \\usepackage{booktabs} in the document preamble.",
        "\\begin{table*}[t]",
        "  \\centering",
        "  \\caption{Computational summary on the %d PSPLIB J30 instances for which" % n,
        "  every method proved optimality. Values in brackets are interquartile ranges.}",
        "  \\label{tab:astar-bb-computational-summary}",
        "  \\small",
        "  \\setlength{\\tabcolsep}{3pt}",
        "  \\begin{tabular}{@{}lllrrrrr@{}}",
        "    \\toprule",
        "    Method &",
        "    Search &",
        "    \\shortstack{LP\\\\relaxation} &",
        "    \\shortstack{Wall time (s)\\\\median\\\\{[IQR]}} &",
        "    \\shortstack{Expanded\\\\median\\\\{[IQR]}} &",
        "    \\shortstack{Generated\\\\median\\\\{[IQR]}} &",
        "    \\shortstack{Max. frontier\\\\median\\\\{[IQR]}} &",
        "    \\shortstack{Peak RSS (MiB)\\\\median\\\\{[IQR]}} \\\\",
        "    \\midrule",
    ]
    for name, method, search, lp in ROWS:
        rows = [data[name][k] for k in common]
        cells = [
            summary_cell([wall_time(r) for r in rows], "{:.3f}"),
            summary_cell([float(r["expanded"]) for r in rows], "{:,.0f}"),
            summary_cell([float(r["generated"]) for r in rows], "{:,.0f}"),
            summary_cell([float(r["max_open"]) for r in rows], "{:,.0f}"),
            summary_cell([rss(r) for r in rows], "{:.1f}"),
        ]
        lines.append("    %s" % method if method else "")
        lines.append("      & %s" % search)
        lines.append("      & %s" % lp)
        for i, cell in enumerate(cells):
            lines.append("      & %s%s" % (cell, " \\\\" if i == len(cells) - 1 else ""))
    lines += [
        "    \\bottomrule",
        "  \\end{tabular}",
        "",
        "  \\vspace{2pt}",
        "  \\parbox{0.98\\textwidth}{\\footnotesize",
        "    Every column uses the same common subset of %d instances. Wall time" % n,
        "    includes LP overhead and measures time until optimality was proven.",
        "    Max. frontier is the largest candidate-node list observed during a run.",
        "    BF denotes global best-first search; DF denotes depth-first search with",
        "    local best-lower-bound child ordering.}",
        "\\end{table*}",
    ]
    # Continuation rows have an empty Method cell: keep the old layout, where
    # the "& BF" line follows the previous row directly.
    text = "\n".join(line for line in lines if line != "") + "\n"
    text = text.replace("\\end{tabular}\n  \\vspace", "\\end{tabular}\n\n  \\vspace")
    return n, text


def generate_unresolved(data):
    groups = {}
    for name in UNRESOLVED_COLUMNS:
        for row in data[name].values():
            key = (float(row["rs_class"]), float(row["rf_class"]), float(row["nc"]))
            groups.setdefault(key, {c: 0 for c in UNRESOLVED_COLUMNS})
            if row["status"] != "optimal":
                groups[key][name] += 1
    lines = [
        "% Requires \\usepackage{booktabs} and \\usepackage{graphicx}.",
        "\\begin{table*}[t]",
        "  \\centering",
        "  \\caption{Instances for which optimality was not proven, partitioned by",
        "  PSPLIB parameter subclass. Each $(NC,RF,RS)$ subclass contains ten",
        "  instances, so every entry is a count out of ten. Subclasses omitted from",
        "  the table have zero unresolved instances for every method.}",
        "  \\label{tab:unresolved-by-psplib-subclass}",
        "  \\scriptsize",
        "  \\setlength{\\tabcolsep}{3.5pt}",
        "  \\renewcommand{\\arraystretch}{0.92}",
        "  \\resizebox{0.88\\textwidth}{!}{%",
        "  \\begin{tabular}{@{}rrr r rrr rrr@{}}",
        "    \\toprule",
        "    & & & & \\multicolumn{3}{c}{Best-first B\\&B}",
        "          & \\multicolumn{3}{c}{Depth-first B\\&B} \\\\",
        "    \\cmidrule(lr){5-7}\\cmidrule(l){8-10}",
        "    $RS$ & $RF$ & $NC$ & A$^*$",
        "      & No LP & Weak LP & Strong LP",
        "      & No LP & Weak LP & Strong LP \\\\",
        "    \\midrule",
    ]
    previous_rs = None
    for key in sorted(groups):
        counts = groups[key]
        if not any(counts.values()):
            continue
        rs, rf, nc = key
        if previous_rs is not None and rs != previous_rs:
            lines.append("    \\addlinespace")
        previous_rs = rs
        cells = ["%d" % counts["A*"]] + [
            "%2d" % counts[c] for c in UNRESOLVED_COLUMNS[1:]]
        lines.append("    %.2f & %.2f & %.1f & %s \\\\" % (rs, rf, nc, " & ".join(cells)))
    lines += [
        "    \\bottomrule",
        "  \\end{tabular}%",
        "  }",
        "",
        "  %\\vspace{2pt}",
        "  %\\parbox{0.98\\textwidth}{\\footnotesize",
        "  %  BF denotes global best-first search. DF denotes depth-first search with",
        "  %  local best-lower-bound child ordering. Weak and Strong identify the LP",
        "  %  precedence formulations. ``Unresolved'' means that the method did not",
        "  %  prove optimality within the two-hour limit; it does not imply that no",
        "  %  feasible incumbent was found.}",
        "\\end{table*}",
    ]
    return "\n".join(lines) + "\n"


def main(methods=METHODS, summary_output=SUMMARY_OUTPUT,
         unresolved_output=UNRESOLVED_OUTPUT):
    data = {name: load(path) for name, _color, _style, path in methods}
    n, summary = generate_summary(data)
    with open(summary_output, "w") as handle:
        handle.write(summary)
    with open(unresolved_output, "w") as handle:
        handle.write(generate_unresolved(data))
    print("%s (%d jointly optimal instances)" % (summary_output, n))
    print(unresolved_output)


if __name__ == "__main__":
    main()

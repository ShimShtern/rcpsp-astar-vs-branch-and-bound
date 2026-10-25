#!/usr/bin/env python3
"""Command-line runner for the RCPSP branch-and-bound core module.

This file handles instance collection, PSPLIB metadata/classification,
known-optimum loading, terminal printing, and CSV writing.  The actual B&B
partial-schedule tree logic is in ``rcpsp_bb_core_py36.py``.

The runner reuses helper functions from ``run_rcpsp_astar_py36.py`` rather
than duplicating the A* runner utilities.
"""
import argparse
import csv
import gc

from rcpsp_astar_core_py36 import (
    parse_psplib_sm,
    validate_schedule,
)
from rcpsp_bb_core_parent_py36 import branch_and_bound_rcpsp
from run_rcpsp_astar_py36 import (
    FIELDNAMES as ASTAR_FIELDNAMES,
    collect_sm_files,
    instance_attributes,
    load_optima,
    load_par_table,
    normalize_name,
    parse_instance_name,
    peak_rss_mib,
)

SCRIPT_VERSION = "2026-10-01-bb-conditional-bound-frontier"

BB_EXTRA_FIELDNAMES = [
    "duplicates_tracked",
    "wall_runtime",
    "cpu_time",
    "search_strategy",
    "incumbent_updates",
    "pruned_bound",
    "pruned_infeasible",
    "best_bound",
    "best_active_lb",
    "upper_bound",
    "relaxation_bound",
    "tree_nodes_popped",
]

LP_DIAGNOSTIC_FIELDNAMES = [
    "lp_precedence",
    "lp_ordering",
    "lp_incumbent_updates",
    "lp_model_build_time",
    "lp_bound_update_time",
    "lp_optimize_time",
    "lp_solve_count",
    "lp_lb_changes",
    "lp_ub_changes",
    "lp_simplex_iterations",
]

# State-progress dominance is currently A*-specific.  Excluding its result
# field keeps the B&B schema stable, including for experiments already running.
FIELDNAMES = [
    name for name in ASTAR_FIELDNAMES
    if name != "pruned_state_dominance"
] + BB_EXTRA_FIELDNAMES


def make_bb_row(inst, res, attrs, optima, fieldnames=None):
    if fieldnames is None:
        fieldnames = FIELDNAMES
    known = optima.get(normalize_name(inst.name), "")
    makespan = res.makespan if res.makespan is not None else ""
    gap = ""
    match = ""
    if known != "" and makespan != "":
        gap = int(makespan) - int(known)
        match = 1 if gap == 0 else 0

    row = dict(attrs)
    row.update({
        "known_optimum": known,
        "known_gap": gap,
        "matches_known_opt": match,
        "status": res.status,
        "makespan": makespan,
        "expanded": res.expanded,
        "generated": res.generated,
        "duplicates": res.duplicates if res.duplicates_tracked else "",
        "duplicates_tracked": 1 if res.duplicates_tracked else 0,
        "pruned_dominance": res.pruned_dominance,
        "max_open": res.max_open,
        "runtime": "%.6f" % res.runtime,
        "max_rss_mib": "%.3f" % peak_rss_mib(),
        "wall_runtime": "%.6f" % res.wall_runtime,
        "cpu_time": "" if res.cpu_time is None else "%.6f" % res.cpu_time,
        "search_strategy": res.search_strategy,
        "incumbent_updates": res.incumbent_updates,
        "pruned_bound": res.pruned_bound,
        "pruned_infeasible": res.pruned_infeasible,
        "best_bound": res.best_bound,
        "best_active_lb": "" if res.best_active_lb is None else res.best_active_lb,
        "upper_bound": "" if res.upper_bound is None else res.upper_bound,
        "relaxation_bound": "" if res.relaxation_bound is None else res.relaxation_bound,
        "tree_nodes_popped": res.tree_nodes_popped,
        "lp_model_build_time": ("" if res.lp_model_build_time is None else
                                "%.6f" % res.lp_model_build_time),
        "lp_bound_update_time": ("" if res.lp_bound_update_time is None else
                                 "%.6f" % res.lp_bound_update_time),
        "lp_optimize_time": ("" if res.lp_optimize_time is None else
                             "%.6f" % res.lp_optimize_time),
        "lp_solve_count": "" if res.lp_solve_count is None else res.lp_solve_count,
        "lp_lb_changes": "" if res.lp_lb_changes is None else res.lp_lb_changes,
        "lp_ub_changes": "" if res.lp_ub_changes is None else res.lp_ub_changes,
        "lp_simplex_iterations": ("" if res.lp_simplex_iterations is None else
                                  res.lp_simplex_iterations),
        "lp_precedence": "" if res.lp_precedence is None else res.lp_precedence,
        "lp_ordering": "" if res.lp_ordering is None else res.lp_ordering,
        "lp_incumbent_updates": ("" if res.lp_incumbent_updates is None else
                                  res.lp_incumbent_updates),
    })
    return {k: row.get(k, "") for k in fieldnames}


def print_pretty(row):
    print("\n[%s]" % row["instance"])
    print("  classification: " + ", ".join("%s=%s" % (k, row[k]) for k in [
        "psplib_set", "param_cell", "replicate", "nc", "rf_class", "rs_class", "known_optimum"
    ]))
    print("  bounds: " + ", ".join("%s=%s" % (k, row[k]) for k in [
        "cp_lb", "res_lb", "best_bound", "best_active_lb", "upper_bound"
    ]))
    print("  search: " + ", ".join("%s=%s" % (k, row[k]) for k in [
        "search_strategy", "status", "makespan", "known_gap", "matches_known_opt",
        "expanded", "generated", "duplicates", "duplicates_tracked",
        "pruned_bound", "incumbent_updates",
        "pruned_dominance", "max_open", "runtime", "wall_runtime",
        "max_rss_mib"
    ]))


def main():
    ap = argparse.ArgumentParser(description="Run tree-based B&B for PSPLIB RCPSP .sm files")
    ap.add_argument("--version", action="version", version=SCRIPT_VERSION)
    ap.add_argument("inputs", nargs="+", help=".sm files, directories, .zip files, or URL to PSPLIB data")
    ap.add_argument("--prefix", default="j30", help="only run files whose basename starts with this prefix; use '' for all")
    ap.add_argument("--limit", type=int, default=None, help="maximum number of instances to run")
    ap.add_argument("--time-limit", type=float, default=None, help="seconds per instance")
    ap.add_argument("--max-nodes", type=int, default=None, help="maximum expanded B&B tree nodes per instance")
    ap.add_argument("--max-memory-mb", type=float, default=None,
                    help="soft per-process RSS limit in MiB; stops cleanly when reached (Linux)")
    ap.add_argument("--same-start-lexicographic", action="store_true",
                    help="prune non-lexicographic permutations of independent jobs with equal start times")
    ap.add_argument("--active-schedule", action="store_true",
                    help="prune a child when its new job could start earlier in the partial schedule")
    ap.add_argument("--no-duplicate-tracking", action="store_true",
                    help="do not retain state keys or report duplicate counts; search is unchanged")
    ap.add_argument("--gc-before-solve", action="store_true",
                    help="run a full Python garbage collection after instance setup and before each solve")
    ap.add_argument("--strategy", choices=["best_first", "depth_first",
                                            "depth_first_best_lb", "breadth_first"],
                    default="best_first", help="B&B active-node selection rule; default is best_first")
    ap.add_argument("--initial-ub", type=int, default=None, help="optional initial incumbent upper bound")
    ap.add_argument("--relaxation-bound", action="store_true",
                    help="solve a node-specific time-indexed LP relaxation for stronger lower bounds (requires Gurobi)")
    ap.add_argument("--lp-precedence", choices=["weak", "strong"],
                    default="strong",
                    help="LP precedence formulation; default is strong cumulative constraints")
    ap.add_argument("--lp-ordering", choices=["fractional", "integer"],
                    default="fractional",
                    help="LP value used for node ordering only; pruning remains integer-aware (default: fractional)")
    ap.add_argument("--lp-diagnostics", action="store_true",
                    help="append LP timing, bound-change, solve-count, and simplex-iteration fields to CSV output")
    ap.add_argument("--csv", default=None, help="optional output CSV path")
    ap.add_argument("--pretty", action="store_true", help="also print a readable multi-line summary per instance")
    ap.add_argument("--opt-file", default=None, help="known optimum file, e.g., j30opt.sm; auto-detected beside this script")
    ap.add_argument("--par-file", default=None, help="PSPLIB parameter-class file par.sm; auto-detected beside this script")
    ap.add_argument("--no-opt-download", action="store_true", help="do not try to download missing optima")
    ap.add_argument("--no-validate", action="store_true", help="skip final schedule validation")
    args = ap.parse_args()
    fieldnames = list(FIELDNAMES)
    if args.lp_diagnostics:
        fieldnames.extend(LP_DIAGNOSTIC_FIELDNAMES)

    files = collect_sm_files(args.inputs, prefix=args.prefix)
    if args.limit is not None:
        files = files[: args.limit]
    if not files:
        raise SystemExit("No .sm files found. Check the path/URL and --prefix.")

    first_set, _, _ = parse_instance_name(files[0].stem)
    par_table = load_par_table(args.par_file)
    optima = load_optima(args.opt_file, first_set or args.prefix.lower(), not args.no_opt_download)

    rows = []
    print(",".join(fieldnames))
    for path in files:
        inst = parse_psplib_sm(path)
        attrs = instance_attributes(inst, par_table)
        if args.gc_before_solve:
            gc.collect()
        res = branch_and_bound_rcpsp(
            inst,
            search_strategy=args.strategy,
            time_limit=args.time_limit,
            max_nodes=args.max_nodes,
            initial_ub=args.initial_ub,
            max_memory_mb=args.max_memory_mb,
            same_start_lexicographic=args.same_start_lexicographic,
            track_duplicates=not args.no_duplicate_tracking,
            active_schedule=args.active_schedule,
            relaxation_bound=args.relaxation_bound,
            lp_precedence=args.lp_precedence,
            lp_ordering=args.lp_ordering,
        )
        if res.schedule is not None and not args.no_validate:
            validate_schedule(inst, res.schedule)
        row = make_bb_row(inst, res, attrs, optima, fieldnames)
        rows.append(row)
        print(",".join(str(row[k]) for k in fieldnames), flush=True)
        if args.pretty:
            print_pretty(row)

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()

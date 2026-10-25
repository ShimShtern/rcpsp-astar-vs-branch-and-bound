# RCPSP solver and results guide

This document describes the active solver files, their command-line options,
and the configuration represented by each canonical result file.

## Dataset

- `j30.sm/` contains the 480 PSPLIB J30 instances.
- `j30opt.sm` contains the known optimal makespans.

## Common execution semantics

- Limits and reported times are measured separately for each instance.
- `--time-limit 7200` requests a two-hour wall-clock limit. Both active
  solvers enforce this limit using elapsed wall time.
- A* reports this elapsed time as `runtime`.
- B&B reports both `wall_runtime` and `runtime`. With duplicate tracking
  enabled, `runtime` subtracts explicitly measured duplicate-checking time;
  with duplicate tracking disabled, `runtime` and `wall_runtime` are equal.
  Therefore, a B&B `time_limit` row can report `runtime` below the requested
  wall-time limit.
- `--max-memory-mb 65536` requests a soft 64 GiB current-RSS limit.
- Memory is checked periodically, so a result can exceed the requested limit
  slightly before the solver exits cleanly.
- `--same-start-lexicographic` enables the same dominance rule in both
  solvers. It removes non-lexicographic permutations of independent jobs
  assigned the same start time; jobs related by direct precedence are exempt.
- `pruned_dominance` reports the number of child branches rejected by this
  rule.
- A* eliminates duplicate states as part of its search. B&B duplicate
  tracking is reporting-only: duplicate B&B nodes are counted but not
  discarded.
- Runs marked "one dedicated CPU per solver" pin each solver process to its
  own physical core with `taskset` and set `OMP_NUM_THREADS` and related
  thread variables to 1; up to 8 (one run 16) solvers run concurrently. Their
  `cpu_time` is within about 0.3% of wall time. Each run directory records its
  settings and core hash in `configuration.txt`.

## Common result fields

The active runners share these CSV fields:

| Fields | Meaning |
|---|---|
| `instance`, `psplib_set`, `param_cell`, `replicate` | Instance identity and PSPLIB classification |
| `nc`, `rf_class`, `rs_class` | PSPLIB network-complexity, resource-factor, and resource-strength classes |
| `jobs`, `real_jobs`, `num_resources`, `horizon` | Instance dimensions |
| `sum_duration`, `avg_duration` | Total and average activity duration |
| `precedence_arcs`, `precedence_density` | Precedence-network statistics |
| `capacity_sum`, `actual_rf` | Resource-capacity total and measured resource factor |
| `cp_lb`, `res_lb` | Critical-path and resource lower bounds |
| `known_optimum`, `known_gap`, `matches_known_opt` | Comparison with the known PSPLIB optimum |
| `status`, `makespan` | Termination reason and returned objective value |
| `expanded`, `generated`, `duplicates` | Search-state counts |
| `pruned_dominance`, `max_open` | Dominance-pruned children and maximum frontier size |
| `runtime` | A* wall time; B&B wall time minus measured duplicate-checking time when tracking is enabled |
| `max_rss_mib` | Peak resident memory in MiB |
| `cpu_time` | Process CPU time in seconds (all threads); recorded by the current runners |

Historical CSV schemas predate some fields, so a blank or absent column does
not necessarily mean that the corresponding mechanism was disabled. The
configuration descriptions below state what was enabled for each result set.

The B&B runner appends B&B-specific fields; they are documented in the active
B&B section.

## Result organization

- The canonical result filename records the dataset and time limit. Directory
  names record the algorithm and other configuration choices.
- A* results are stored below `Results/astar/`; B&B results are stored below
  `Results/bb/`.
- Subdirectories identify choices such as dominance, duplicate tracking,
  node representation, and explicit memory limiting.
- Derived publication material is stored separately below `analysis/`:
  generator programs are in `analysis/scripts/`, plots are in
  `analysis/figures/`, and tabular outputs are in `analysis/tables/`.
- The analysis generators resolve both input and output paths relative to the
  repository root, so they can be launched from any working directory.

## Python compatibility

The current implementations are compatible with Python 3.6.

## Common runner options

Both `run_rcpsp_astar_py36.py` and `run_rcpsp_bb_py36.py` accept:

| Option | Meaning |
|---|---|
| `-h`, `--help` | Print command-line help |
| `--version` | Print the runner version |
| `inputs` | One or more `.sm` files, directories, ZIP files, or supported URLs |
| `--prefix TEXT` | Keep only input basenames beginning with `TEXT`; use `""` for all |
| `--limit N` | Run at most `N` collected instances |
| `--time-limit SECONDS` | Per-instance wall-clock limit |
| `--max-memory-mb MIB` | Soft current-RSS limit |
| `--same-start-lexicographic` | Enable same-start lexicographic dominance |
| `--csv PATH` | Write the aggregate CSV to `PATH` |
| `--pretty` | Print an additional readable summary |
| `--opt-file PATH` | Explicit known-optimum file |
| `--par-file PATH` | Explicit PSPLIB parameter-class file |
| `--no-opt-download` | Disable automatic optimum-data download |
| `--no-validate` | Skip validation of returned schedules |

## Active A* files

### `rcpsp_astar_core_py36.py`

This is the active A* implementation. It contains:

- PSPLIB parsing and schedule validation;
- the timed A* state representation;
- state transitions and lower bounds;
- duplicate-state elimination using Open/Closed state keys;
- depth-first tie-breaking among equal `f` values.

This is a library module and normally should not be executed directly.

### `run_rcpsp_astar_py36.py`

This is the command-line runner for the active A* core.

A*-specific option:

| Option | Meaning |
|---|---|
| `--max-expansions N` | Maximum expanded A* states |
| `--state-progress-dominance` | Prune progress-dominated states with identical started-job sets |
| `--exact-active-schedule` | Reconstruct the retained partial schedule and prune a child when its newly inserted job can be moved to an earlier precedence- and resource-feasible start while all existing jobs stay fixed |

A*-specific result field:

| Field | Meaning |
|---|---|
| `pruned_state_dominance` | Successors rejected by started-set progress dominance |
| `pruned_active_schedule` | Successors rejected because the newly inserted job could be left-shifted in the reconstructed partial schedule |

Example for one instance:

```bash
python3 run_rcpsp_astar_py36.py j30.sm/j301_1.sm \
    --prefix "" \
    --time-limit 7200 \
    --max-memory-mb 65536 \
    --same-start-lexicographic \
    --csv astar_j301_1.csv
```

Example for all J30 instances, processed sequentially:

```bash
python3 run_rcpsp_astar_py36.py j30.sm \
    --prefix j30 \
    --time-limit 7200 \
    --max-memory-mb 65536 \
    --same-start-lexicographic \
    --csv j30_astar_120min.csv
```

For long experiments, per-instance subprocesses are preferable because they
preserve partial results and isolate peak-RSS measurements.

## Active B&B files

### `rcpsp_bb_core_parent_py36.py`

This is the active parent-linked branch-and-bound implementation. Each search
node stores a link to its parent plus the job and start time introduced at that
node. Full start vectors are reconstructed only when required.

The implementation supports:

- best-first, depth-first, locally best-bound depth-first, and breadth-first
  frontier strategies;
- incumbent-bound pruning;
- optional reporting-only duplicate tracking;
- an optional reusable time-indexed LP relaxation, with weak or strong
  precedence formulations;
- an optional node limit.

B&B is deliberately tree-based. Duplicate states are counted for reporting
when tracking is enabled, but they are not discarded and do not change the
search.

### `run_rcpsp_bb_py36.py`

This is the command-line runner for the active parent-linked B&B core.

B&B-specific options:

| Option | Meaning |
|---|---|
| `--max-nodes N` | Maximum expanded B&B tree nodes |
| `--no-duplicate-tracking` | Do not construct or retain reporting-only state keys |
| `--active-schedule` | Prune a child if its newly added job could be left-shifted to an earlier precedence- and resource-feasible start in the partial schedule |
| `--strategy STRATEGY` | `best_first`, `depth_first`, `depth_first_best_lb`, or `breadth_first` |
| `--initial-ub VALUE` | Supply an initial incumbent upper bound |
| `--relaxation-bound` | Solve a node-specific time-indexed LP relaxation, fixing scheduled tasks, for an additional lower bound; requires Gurobi |
| `--lp-precedence FORMULATION` | Select `weak` expected-start precedence constraints or `strong` cumulative precedence constraints; default is `strong` |
| `--lp-ordering ORDERING` | Select the LP value used to order nodes: tolerance-quantized `fractional` (default) or the integer proof bound (`integer`); pruning is integer-aware in both modes |
| `--lp-diagnostics` | Append LP formulation, timing, solve-count, bound-change, incumbent, and simplex-iteration diagnostics to the CSV |
| `--gc-before-solve` | Run a full Python garbage collection after instance setup and before solving |

The LP model is built once per instance and reoptimized at each eligible B&B
child by changing variable bounds. Scheduled activities are fixed at their
chosen starts, and starts earlier than the current node time are excluded for
unscheduled activities. The inexpensive critical-path bound is evaluated
first, so Gurobi is not called when that bound already prunes the child.

Because RCPSP makespans are integral, the LP bound used for proof and pruning
is `ceil(lp_bound - Gurobi OptimalityTol)`, combined with the critical-path
bound. The raw LP objective is retained for diagnostics and, after tolerance
quantization, for best-first ordering. An integral LP solution is validated
as an RCPSP schedule before it may update the incumbent.

Further LP details of the current core:

- Incumbent upper-bound cutoff: once an incumbent of makespan `UB` exists,
  every start whose activity would finish after `UB` gets variable upper
  bound 0 (schedules tying the incumbent remain feasible). A node whose fixed
  activities already finish after `UB` is pruned without calling Gurobi, and
  an LP made infeasible by the cutoff prunes the node.
- After each solve, the integrality check reads only the admissible start
  window of each unscheduled activity, with one batched
  `getAttr("X", ...)` call per activity. Scheduled activities are integral by
  construction and their starts are taken from the node. This removed most of
  the non-LP time per node (about 8 ms with strong LP and 4 ms with weak LP)
  without changing the search.
- Gurobi status 12 (`NUMERIC`) is handled by discarding the warm-start basis
  (`model.reset()`) and re-solving the same node; if that also fails, the node
  keeps its critical-path bound. Each occurrence is written to the instance
  log as `LP NUMERIC (status 12) ...`. In the October runs every retry
  returned an infeasible LP, so the node was pruned.
- The root LP uses Gurobi's default concurrent method, so which of several
  optimal root solutions is returned can vary between runs. This can change
  the search path, and therefore node counts and outcomes, on some instances.

Active-schedule cuts are currently included in the existing
`pruned_dominance` count. This preserves the CSV layout used by ongoing B&B
batches; use a separate result directory when benchmarking this option.

B&B-specific result fields:

| Fields | Meaning |
|---|---|
| `duplicates_tracked` | `1` when reporting-only duplicate tracking was enabled, otherwise `0` |
| `wall_runtime` | Total elapsed wall time used to enforce the time limit |
| `search_strategy` | Active-node selection strategy |
| `incumbent_updates` | Number of improvements to the incumbent solution |
| `pruned_bound`, `pruned_infeasible` | Children rejected by bound or infeasibility checks |
| `best_bound`, `best_active_lb`, `upper_bound` | Reported lower- and upper-bound information at termination; when a run stops in the middle of expanding a node (LP time limit or node limit), the current core includes that node's bound in `best_active_lb` |
| `relaxation_bound` | Relaxation objective reported for the root, when enabled; this is a lower bound for the minimization problem |
| `tree_nodes_popped` | Number of tree nodes removed from the active frontier |
| `lp_precedence` | LP precedence formulation used: `weak` or `strong` |
| `lp_ordering` | LP node-ordering value used: `fractional` or `integer` |
| `lp_incumbent_updates` | Validated integral LP schedules that improved the B&B incumbent |
| `lp_model_build_time` | Seconds spent building the reusable LP model |
| `lp_bound_update_time` | Seconds spent updating variable bounds between node solves |
| `lp_optimize_time` | Cumulative seconds spent inside Gurobi optimization calls |
| `lp_solve_count` | Number of LP optimization calls, including diagnostic retries |
| `lp_lb_changes`, `lp_ub_changes` | Variable lower- and upper-bound changes applied to the reusable model |
| `lp_simplex_iterations` | Cumulative Gurobi simplex iterations |

Example with dominance and duplicate tracking:

```bash
python3 run_rcpsp_bb_py36.py j30.sm/j301_1.sm \
    --prefix "" \
    --strategy best_first \
    --time-limit 7200 \
    --max-memory-mb 65536 \
    --same-start-lexicographic \
    --csv bb_j301_1.csv
```

Example with dominance and duplicate tracking disabled:

```bash
python3 run_rcpsp_bb_py36.py j30.sm/j301_1.sm \
    --prefix "" \
    --strategy best_first \
    --time-limit 7200 \
    --max-memory-mb 65536 \
    --same-start-lexicographic \
    --no-duplicate-tracking \
    --csv bb_j301_1.csv
```

When duplicate tracking is disabled, `duplicates` is blank and
`duplicates_tracked` is `0`.

## Running a batch: `run_j30_batch.sh`

`run_j30_batch.sh` runs one solver configuration on the J30 instances, one
process per instance, with each concurrent process pinned to its own CPU:

```bash
[MAX_JOBS=8] [CPU_LIST=0,1,...] [INSTANCES_FILE=list.txt] \
./run_j30_batch.sh <output_dir> <runner> [runner options...]
```

- Pass the per-instance running options listed for a result below, without
  `--prefix` and `--csv`; the script adds them. Relative output directories
  are relative to the repository root.
- Results go to `<output_dir>/results/<instance>.csv` and logs to
  `<output_dir>/logs/<instance>.log`. When every instance has a result, the
  per-instance CSVs are combined into `<output_dir>/j30_all.csv`.
- Rerunning the same command skips instances that already have a complete
  result, so an interrupted batch can be resumed.
- `<output_dir>/configuration.txt` records the runner and core checksums,
  runner options, `MAX_JOBS`, `CPU_LIST`, and instance count.
- `MAX_JOBS` (default 8) solvers run at once on the CPUs in `CPU_LIST`
  (default `0..MAX_JOBS-1`). `INSTANCES_FILE` optionally lists the instances
  to run, one name per line (for example `j301_1`); by default all 480 are run.

CPU pinning is provided by `cpu_worker_pool.sh`, which the script sources. It
starts each solver with `taskset` on one CPU from `CPU_LIST`, sets
`OMP_NUM_THREADS` and related thread variables to 1, checks that the
requested CPUs exist and are distinct, and refuses to resume an output
directory with a different `MAX_JOBS` or `CPU_LIST`.

Examples:

```bash
# A* with same-start dominance and exact active-schedule pruning
./run_j30_batch.sh astar_runs/exact_active run_rcpsp_astar_py36.py \
    --same-start-lexicographic --exact-active-schedule \
    --time-limit 7200 --max-memory-mb 65536

# Best-first B&B with the weak LP relaxation bound
./run_j30_batch.sh bb_runs/weak_lp_best_first run_rcpsp_bb_py36.py \
    --strategy best_first --same-start-lexicographic --active-schedule \
    --no-duplicate-tracking --relaxation-bound --lp-precedence weak \
    --lp-ordering integer --lp-diagnostics \
    --time-limit 7200 --max-memory-mb 65536
```

## Canonical A* results

### Baseline without dominance

Path:

```text
Results/astar/non_dominance/no_explicit_memory_limit/j30_astar_120min.csv
```

Implementation files:

- runner: historical revision of `run_rcpsp_astar_py36.py`;
- core: historical revision of `rcpsp_astar_core_py36.py`.

Per-instance running options:

```text
--prefix "" --time-limit 7200 --csv <per-instance CSV>
```

`--same-start-lexicographic` and `--max-memory-mb` were omitted.

Configuration:

- A* with depth-first tie-breaking;
- duplicate-state elimination enabled;
- same-start dominance disabled;
- two-hour time limit;
- no solver-enforced memory limit;
- peak RSS not recorded.

Outcomes:

- 480 instances;
- 476 optimal;
- 4 time limits.

### Dominance without an explicit memory limit

Path:

```text
Results/astar/same_start_dominance/no_explicit_memory_limit/j30_astar_120min.csv
```

Implementation files:

- runner: historical revision of `run_rcpsp_astar_py36.py`;
- core: historical revision of `rcpsp_astar_core_py36.py`.

Per-instance running options:

```text
--prefix "" --same-start-lexicographic --time-limit 7200
--csv <per-instance CSV>
```

`--max-memory-mb` was omitted.

Configuration:

- A* with depth-first tie-breaking;
- duplicate-state elimination enabled;
- same-start dominance enabled;
- two-hour time limit;
- no solver-enforced memory limit;
- dominance pruning recorded;
- peak RSS not recorded.

Outcomes:

- 480 instances;
- 479 optimal;
- 1 time limit.

### Dominance with a 64 GiB limit

Path:

```text
Results/astar/same_start_dominance/memory_limit_64GiB/j30_astar_120min.csv
```

Implementation files:

- runner: `run_rcpsp_astar_py36.py`;
- core: `rcpsp_astar_core_py36.py`.

Per-instance running options:

```text
--prefix "" --same-start-lexicographic --time-limit 7200
--max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- A* with depth-first tie-breaking;
- duplicate-state elimination enabled;
- same-start dominance enabled;
- two-hour time limit;
- soft 64 GiB current-RSS limit;
- dominance pruning and peak RSS recorded.

Outcomes:

- 480 instances;
- 479 optimal;
- 1 time limit;
- no instance reached the 64 GiB limit.

### Same-start and state-progress dominance with a 64 GiB limit

Path:

```text
Results/astar/same_start_dominance/state_progress_dominance/memory_limit_64GiB/j30_astar_120min.csv
```

Implementation files:

- runner: `run_rcpsp_astar_py36.py`;
- core: `rcpsp_astar_core_py36.py`.

Per-instance running options:

```text
--prefix "" --same-start-lexicographic --state-progress-dominance
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- A* with depth-first tie-breaking;
- duplicate-state elimination enabled;
- same-start lexicographic dominance enabled;
- state-progress skyline dominance enabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- same-start cuts, state-progress cuts, and peak RSS recorded separately.

Outcomes:

- 480 instances;
- 480 optimal;
- no time or memory limits;
- maximum recorded peak RSS of 1,279.750 MiB.

### Same-start dominance and exact active-schedule pruning with a 64 GiB limit

Path:

```text
Results/astar/same_start_dominance/exact_active_schedule/memory_limit_64GiB/j30_astar_120min.csv
```

Implementation files:

- runner: `run_rcpsp_astar_py36.py`;
- core: `rcpsp_astar_core_py36.py`.

Run with 8 solvers in parallel, each pinned to its own CPU.

Per-instance running options:

```text
--prefix "" --same-start-lexicographic --exact-active-schedule
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- A* with depth-first tie-breaking;
- duplicate-state elimination enabled;
- same-start lexicographic dominance enabled;
- exact active-schedule pruning enabled by reconstructing the retained partial
  schedule and rejecting a newly inserted job that can be left-shifted;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- one dedicated CPU per solver;
- same-start and active-schedule cuts recorded separately;
- `cpu_time` recorded.

Outcomes:

- 480 instances;
- 480 optimal;
- no time or memory limits;
- maximum recorded peak RSS of 15,445.242 MiB.

## Canonical B&B results

### Original full-vector B&B

Path:

```text
Results/bb/original_full_vector/non_dominance/duplicate_tracking/memory_limit_64GiB/j30_bb_120min.csv
```

Implementation files:

- runner: `archive/bb_legacy/run_rcpsp_bb_original_py36.py`;
- core: `archive/bb_legacy/rcpsp_bb_core_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy best_first --time-limit 7200
--max-memory-mb 65536 --csv <per-instance CSV>
```

`--same-start-lexicographic` and `--no-duplicate-tracking` were omitted.

Configuration:

- original node representation containing a full partial-start vector;
- best-first frontier;
- same-start dominance disabled;
- reporting-only duplicate tracking enabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- peak RSS not recorded in this historical CSV.

Outcomes:

- 480 instances;
- 243 optimal;
- 236 memory limits;
- 1 time limit.

The original full-vector core and runner are kept in the repository under
`archive/bb_legacy/` to reproduce this result; they are not the active B&B
solver.

### Parent-linked B&B with dominance and duplicate tracking

Path:

```text
Results/bb/parent_linked/same_start_dominance/duplicate_tracking/memory_limit_64GiB/j30_bb_120min.csv
```

Implementation files:

- runner: historical revision of `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy best_first --same-start-lexicographic
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

`--no-duplicate-tracking` was omitted.

Configuration:

- parent-linked node representation;
- best-first frontier;
- same-start dominance enabled;
- reporting-only duplicate tracking enabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- peak RSS not recorded in this historical CSV.

Outcomes:

- 480 instances;
- 385 optimal;
- 81 memory limits;
- 14 time limits.

### Parent-linked depth-first B&B with dominance and duplicate tracking

Path:

```text
Results/bb/parent_linked/same_start_dominance/duplicate_tracking/memory_limit_64GiB/depth_first/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy depth_first --same-start-lexicographic
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

`--active-schedule` and `--no-duplicate-tracking` were omitted.

Configuration:

- parent-linked node representation;
- depth-first frontier with smallest-job-first child ordering;
- same-start dominance enabled;
- reporting-only duplicate tracking enabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- peak RSS recorded.

Outcomes:

- 480 instances;
- 373 optimal;
- 107 time-limit results with feasible incumbents;
- no memory limits.

### Parent-linked depth-first B&B with dominance and no duplicate tracking

Path:

```text
Results/bb/parent_linked/same_start_dominance/no_duplicate_tracking/memory_limit_64GiB/depth_first/j30_bb_depth_first_dominance_120min_all.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy depth_first --same-start-lexicographic
--no-duplicate-tracking --time-limit 7200 --max-memory-mb 65536
--csv <per-instance CSV>
```

`--active-schedule` was omitted.

Configuration:

- parent-linked node representation;
- depth-first frontier with smallest-job-first child ordering;
- same-start dominance enabled;
- reporting-only duplicate tracking disabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- peak RSS recorded.

This is the counterpart of the duplicate-tracking depth-first result above.
Because duplicate tracking does not change the search, both runs expand and
generate exactly the same nodes on the 373 instances both solve; without the
tracking overhead the median runtime is about 0.4 times as long.

Outcomes:

- 480 instances;
- 390 optimal;
- 90 time-limit results with feasible incumbents;
- no memory limits;
- maximum recorded peak RSS of 23.375 MiB.

### Parent-linked locally best-bound depth-first B&B

Path:

```text
Results/bb/parent_linked/same_start_dominance/duplicate_tracking/memory_limit_64GiB/depth_first_best_lb/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy depth_first_best_lb --same-start-lexicographic
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

`--active-schedule` and `--no-duplicate-tracking` were omitted.

Configuration:

- parent-linked node representation;
- depth-first frontier, ordering each node's children by lower bound;
- same-start dominance enabled;
- reporting-only duplicate tracking enabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- peak RSS recorded.

Outcomes:

- 480 instances;
- 375 optimal;
- 105 time-limit results with feasible incumbents;
- no memory limits.

### Parent-linked locally best-bound depth-first B&B without duplicate tracking

Path:

```text
Results/bb/parent_linked/same_start_dominance/no_duplicate_tracking/memory_limit_64GiB/depth_first_best_lb/j30_bb_depth_first_best_lb_dominance_120min_all.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy depth_first_best_lb --same-start-lexicographic
--no-duplicate-tracking --time-limit 7200 --max-memory-mb 65536
--csv <per-instance CSV>
```

`--active-schedule` was omitted.

Configuration:

- parent-linked node representation;
- depth-first frontier, ordering each node's children by lower bound;
- same-start dominance enabled;
- reporting-only duplicate tracking disabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- peak RSS recorded.

This is the counterpart of the duplicate-tracking result above. Both runs
expand and generate exactly the same nodes on the 375 instances both solve;
without the tracking overhead the median runtime is about half as long.

Outcomes:

- 480 instances;
- 389 optimal;
- 91 time-limit results with feasible incumbents;
- no memory limits;
- maximum recorded peak RSS of 23.406 MiB.

### Parent-linked locally best-bound depth-first B&B with active schedules

Path:

```text
Results/bb/parent_linked/same_start_dominance/active_schedule/duplicate_tracking/memory_limit_64GiB/depth_first_best_lb/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy depth_first_best_lb --same-start-lexicographic
--active-schedule --time-limit 7200 --max-memory-mb 65536
--csv <per-instance CSV>
```

`--no-duplicate-tracking` was omitted.

Configuration:

- parent-linked node representation;
- depth-first frontier, ordering each node's children by lower bound;
- same-start dominance enabled;
- exact active-schedule pruning enabled;
- reporting-only duplicate tracking enabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- active-schedule and same-start cuts combined in `pruned_dominance` to retain
  the existing B&B CSV schema;
- peak RSS recorded.

Outcomes:

- 480 instances;
- 440 optimal;
- 34 time-limit results with feasible incumbents;
- 6 time limits without an incumbent;
- no memory limits;
- maximum recorded peak RSS of 3,117.441 MiB.

### Parent-linked B&B with dominance and no duplicate tracking

Path:

```text
Results/bb/parent_linked/same_start_dominance/no_duplicate_tracking/memory_limit_64GiB/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy best_first --same-start-lexicographic
--no-duplicate-tracking --time-limit 7200 --max-memory-mb 65536
--csv <per-instance CSV>
```

Configuration:

- parent-linked node representation;
- best-first frontier;
- same-start dominance enabled;
- reporting-only duplicate tracking disabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- peak RSS recorded.

Outcomes:

- 480 instances;
- 387 optimal;
- 82 memory limits;
- 11 time limits.


### Parent-linked B&B without dominance and with duplicate tracking

Path:

```text
Results/bb/parent_linked/non_dominance/duplicate_tracking/memory_limit_64GiB/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py`.

Per-instance running options:

```text
--prefix "" --strategy best_first --time-limit 7200
--max-memory-mb 65536 --csv <per-instance CSV>
```

`--same-start-lexicographic` and `--no-duplicate-tracking` are omitted.

Configuration:

- parent-linked node representation;
- best-first frontier;
- same-start dominance disabled;
- reporting-only duplicate tracking enabled;
- two-hour wall-time limit;
- soft 64 GiB current-RSS limit;
- aggregate normalized to the current 41-column schema from five historical
  per-instance schemas, with unavailable historical fields left blank.

Outcomes:

- 480 instances;
- 249 optimal;
- 230 memory limits;
- 1 time limit.

### Parent-linked best-first B&B with active schedules, no LP

Path:

```text
Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/j30_bb_best_first_dominance_120min_all.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py` (sha256 `a3e7b651…`).

Run with 8 solvers in parallel, each pinned to its own CPU.

Per-instance running options:

```text
--prefix "" --strategy best_first --same-start-lexicographic
--active-schedule --no-duplicate-tracking --time-limit 7200
--max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- parent-linked node representation and global best-first frontier;
- same-start dominance and exact active-schedule pruning enabled;
- reporting-only duplicate tracking disabled;
- no LP relaxation;
- two-hour wall-time limit and soft 64 GiB current-RSS limit per instance;
- one dedicated CPU per solver; peak RSS and `cpu_time` recorded.

Outcomes:

- 480 instances;
- 445 optimal;
- 35 time limits without an incumbent;
- no memory limits;
- maximum recorded peak RSS of 56,965.766 MiB.

### Parent-linked depth-first best-LB B&B with active schedules, no LP

Path:

```text
Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/depth_first_best_lb/j30_bb_depth_first_best_lb_active_schedule_dominance_120min_all.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py` (sha256 `a3e7b651…`).

Run with 8 solvers in parallel, each pinned to its own CPU.

Per-instance running options:

```text
--prefix "" --strategy depth_first_best_lb --same-start-lexicographic
--active-schedule --no-duplicate-tracking --time-limit 7200
--max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- parent-linked node representation and depth-first frontier, ordering each
  node's children by lower bound;
- same-start dominance and exact active-schedule pruning enabled;
- reporting-only duplicate tracking disabled;
- no LP relaxation;
- two-hour wall-time limit and soft 64 GiB current-RSS limit per instance;
- one dedicated CPU per solver; peak RSS and `cpu_time` recorded.

Outcomes:

- 480 instances;
- 440 optimal;
- 34 time-limit results with feasible incumbents;
- 6 time limits without an incumbent;
- no memory limits;
- maximum recorded peak RSS of 23.492 MiB.

### Parent-linked depth-first best-LB B&B with strong LP precedence

Path:

```text
Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/depth_first_best_lb/lp_relaxation/strong_precedence/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py` (sha256 `8dd91b4e…` for 475 instances; `060b1086…` (adds status-12 retry) for the 5 instances rerun after a status-12 crash (`j305_5`, `j3013_9`, `j3029_6`, `j3029_8`, `j3030_1`)).

Run with 8 solvers in parallel, each pinned to its own CPU.

Per-instance running options:

```text
--prefix "" --strategy depth_first_best_lb --same-start-lexicographic
--active-schedule --no-duplicate-tracking --relaxation-bound
--lp-precedence strong --lp-ordering integer --lp-diagnostics
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- parent-linked node representation and depth-first frontier, with surviving
  children locally ordered by their LP-enhanced lower bound;
- same-start dominance and exact active-schedule pruning enabled;
- reporting-only duplicate tracking disabled;
- one reusable time-indexed continuous LP model per instance;
- strong cumulative precedence constraints for every precedence arc and time
  point;
- integer-aware LP proof bounds, also used for node ordering (`--lp-ordering integer`);
- incumbent upper-bound cutoff on the LP start variables;
- validated integral LP schedules allowed to update the incumbent;
- LP diagnostics recorded;
- two-hour wall-time limit and soft 64 GiB current-RSS limit per instance;
- one dedicated CPU per solver; peak RSS and `cpu_time` recorded.

Outcomes:

- 480 instances;
- 371 optimal;
- 93 time-limit results with feasible incumbents;
- 16 time limits without an incumbent;
- no memory limits;
- maximum recorded peak RSS of 1,166.867 MiB.

### Parent-linked depth-first best-LB B&B with weak LP precedence

Path:

```text
Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/depth_first_best_lb/lp_relaxation/weak_precedence/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py` (sha256 `5ea78fab…`).

Run with 8 solvers in parallel, each pinned to its own CPU.

Per-instance running options:

```text
--prefix "" --strategy depth_first_best_lb --same-start-lexicographic
--active-schedule --no-duplicate-tracking --relaxation-bound
--lp-precedence weak --lp-ordering integer --lp-diagnostics
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- parent-linked node representation and depth-first frontier, with surviving
  children locally ordered by their LP-enhanced lower bound;
- same-start dominance and exact active-schedule pruning enabled;
- reporting-only duplicate tracking disabled;
- one reusable time-indexed continuous LP model per instance;
- one expected-start precedence constraint per precedence arc;
- integer-aware LP proof bounds, also used for node ordering (`--lp-ordering integer`);
- incumbent upper-bound cutoff on the LP start variables;
- validated integral LP schedules allowed to update the incumbent;
- LP diagnostics recorded;
- two-hour wall-time limit and soft 64 GiB current-RSS limit per instance;
- one dedicated CPU per solver; peak RSS and `cpu_time` recorded.

Outcomes:

- 480 instances;
- 388 optimal;
- 73 time-limit results with feasible incumbents;
- 19 time limits without an incumbent;
- no memory limits;
- maximum recorded peak RSS of 107.930 MiB.

### Parent-linked best-first B&B with strong LP precedence

Path:

```text
Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/best_first/lp_relaxation/strong_precedence/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py` (sha256 `060b1086…` for most instances; `5ea78fab…` (adds the lower-bound reporting fix) for instances started after Oct 7 23:37, including `j3013_3` and `j309_2`, which were rerun with it).

Run with 8 solvers in parallel, each pinned to its own CPU.

Per-instance running options:

```text
--prefix "" --strategy best_first --same-start-lexicographic
--active-schedule --no-duplicate-tracking --relaxation-bound
--lp-precedence strong --lp-ordering integer --lp-diagnostics
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- parent-linked node representation and global best-first frontier;
- same-start dominance and exact active-schedule pruning enabled;
- reporting-only duplicate tracking disabled;
- one reusable time-indexed continuous LP model per instance;
- strong cumulative precedence constraints for every precedence arc and time
  point;
- integer-aware LP proof bounds, also used for node ordering (`--lp-ordering integer`);
- incumbent upper-bound cutoff on the LP start variables;
- validated integral LP schedules allowed to update the incumbent;
- LP diagnostics recorded;
- two-hour wall-time limit and soft 64 GiB current-RSS limit per instance;
- one dedicated CPU per solver; peak RSS and `cpu_time` recorded.

Outcomes:

- 480 instances;
- 371 optimal;
- 14 time-limit results with feasible incumbents;
- 95 time limits without an incumbent;
- no memory limits;
- maximum recorded peak RSS of 1,167.742 MiB.

### Parent-linked best-first B&B with weak LP precedence

Path:

```text
Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/best_first/lp_relaxation/weak_precedence/j30_bb_120min.csv
```

Implementation files:

- runner: `run_rcpsp_bb_py36.py`;
- core: `rcpsp_bb_core_parent_py36.py` (sha256 `5ea78fab…`).

The first 237 instances were run with 8 solvers in parallel and the rest with
16, each pinned to its own CPU.

Per-instance running options:

```text
--prefix "" --strategy best_first --same-start-lexicographic
--active-schedule --no-duplicate-tracking --relaxation-bound
--lp-precedence weak --lp-ordering integer --lp-diagnostics
--time-limit 7200 --max-memory-mb 65536 --csv <per-instance CSV>
```

Configuration:

- parent-linked node representation and global best-first frontier;
- same-start dominance and exact active-schedule pruning enabled;
- reporting-only duplicate tracking disabled;
- one reusable time-indexed continuous LP model per instance;
- one expected-start precedence constraint per precedence arc;
- integer-aware LP proof bounds, also used for node ordering (`--lp-ordering integer`);
- incumbent upper-bound cutoff on the LP start variables;
- validated integral LP schedules allowed to update the incumbent;
- LP diagnostics recorded;
- two-hour wall-time limit and soft 64 GiB current-RSS limit per instance;
- one dedicated CPU per solver; peak RSS and `cpu_time` recorded.

Outcomes:

- 480 instances;
- 380 optimal;
- 100 time limits without an incumbent;
- no time-limit results with feasible incumbents;
- no memory limits;
- maximum recorded peak RSS of 238.500 MiB.

## Integer LP-ordering comparison results

These are earlier integer-ordering runs (Sep 15–19, 2026, core sha256
`a318583d…`, unpinned, with `--gc-before-solve`). They pass
`--lp-ordering integer` like the current primary LP files above, but predate
the incumbent upper-bound cutoff and the batched post-solve reads. All other
solver options, including search strategy, precedence formulation, dominance,
active-schedule pruning, disabled duplicate tracking, two-hour wall-time
limit, and soft 64 GiB RSS limit, match the primary LP files.

| Search | LP precedence | Result file | Optimal | Time limit with incumbent | Time limit without incumbent | Maximum RSS (MiB) |
|---|---|---|---:|---:|---:|---:|
| Best-first | Weak | `Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/best_first/lp_relaxation/weak_precedence/integer_ordering/j30_bb_120min.csv` | 377 | 0 | 103 | 428.164 |
| Best-first | Strong | `Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/best_first/lp_relaxation/strong_precedence/integer_ordering/j30_bb_120min.csv` | 366 | 19 | 95 | 1,155.363 |
| Depth-first best-LB | Weak | `Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/depth_first_best_lb/lp_relaxation/weak_precedence/integer_ordering/j30_bb_120min.csv` | 373 | 83 | 24 | 368.496 |
| Depth-first best-LB | Strong | `Results/bb/parent_linked/same_start_dominance/active_schedule/no_duplicate_tracking/memory_limit_64GiB/depth_first_best_lb/lp_relaxation/strong_precedence/integer_ordering/j30_bb_120min.csv` | 356 | 108 | 16 | 1,171.266 |

Each file contains all 480 J30 instances and none reports a memory-limit
termination. These files are comparison data; the primary LP files documented
in the preceding sections are the results used by the analysis artifacts
below.

## Analysis artifacts

The `analysis/` tree contains derived figures and tables rather than solver
result CSVs. The current analysis compares A* with best-first and depth-first
best-LB B&B using no LP, weak LP, and strong LP. All seven series read the
primary result files above: A* with exact active-schedule pruning, the two
no-LP active-schedule B&B files, and the four primary LP files (integer
ordering with the upper-bound cutoff). All seven are pinned runs with one
dedicated CPU per solver. Time axes use wall time.

Reproducible generators:

| Script | Outputs | Meaning |
|---|---|---|
| `analysis/scripts/generate_bb_time_performance_profile.py` | `analysis/figures/bb_jointly_optimal_time_performance_profile.svg` | Wall-time performance profile on the 365 instances proven optimal by all seven methods; time is relative to the fastest method on each instance. Its method list is shared by the bound profile, pairwise matrices, status chart, and LaTeX tables. |
| `analysis/scripts/generate_bb_shifted_time_performance_profile.py` | `analysis/figures/bb_jointly_optimal_shifted_time_performance_profile.svg` | The same profile after adding one second to every wall time, reducing distortion from extremely short runs. |
| `analysis/scripts/generate_bb_bound_performance_profile.py` | `analysis/figures/bb_final_bound_performance_profile.svg` | Final upper- and lower-bound profiles over all 480 instances, as percentage gaps to the known optimum: (UB − optimum)/optimum and (optimum − LB)/optimum. A run without an incumbent uses the instance horizon as its upper bound. |
| `analysis/scripts/generate_astar_bb_pairwise_matrices.py` | `analysis/figures/astar_bb_pairwise_win_tie_loss_matrices.svg`; `analysis/tables/astar_bb_pairwise_win_tie_loss_counts.csv` | Pairwise counts, over the six B&B methods, of instances on which the row method has the better final upper or lower bound. A* is omitted because it proves every instance optimal. |
| `analysis/scripts/generate_bb_status_grouped.py` | `analysis/figures/bb_status_all_methods_grouped.svg` | Outcome counts for all seven methods: proven optimal, known optimum found but not proven, suboptimal incumbent at the time limit, and horizon bound only at the time limit. |
| `analysis/scripts/generate_astar_bb_lp_runtime_frontier_combined.py` | `analysis/figures/astar_bb_lp_runtime_frontier_combined.svg` | Cumulative wall time to optimality, maximum frontier size, and peak RSS for all seven methods. |
| `analysis/scripts/generate_astar_bb_runtime_frontier_combined.py` | `analysis/figures/astar_bb_dominance_runtime_frontier_combined.svg` | Runtime and frontier profiles of A* and best-first B&B under three pruning levels (CP; CP and lexicographic; CP, lexicographic, and active schedules). The last level reads the pinned primary files; the other two levels are earlier unpinned runs with otherwise identical settings. |
| `analysis/scripts/generate_latex_tables.py` | `analysis/tables/astar_bb_computational_summary.tex`; `analysis/tables/unresolved_by_psplib_subclass.tex` | Median and interquartile range of wall time, expanded and generated nodes, maximum frontier, and peak RSS on the 365 jointly optimal instances; and the number of instances not proven optimal per PSPLIB $(NC,RF,RS)$ subclass. Both require `booktabs`; the second also requires `graphicx`. |
| `analysis/scripts/generate_bb_memory_comparison_plots.py` | `analysis/figures/bb_dominance_memory_limit_counts.svg`; `analysis/tables/bb_dominance_memory_summary.csv` | Number of runs reaching the 64 GiB memory limit for A* and best-first B&B under the three pruning levels, with a summary table of outcomes and maximum-frontier statistics. The last pruning level reads the pinned primary files; the other two are earlier unpinned runs with otherwise identical settings. A* with CP only is shown as N/A because that run had no memory limit. |

Run any generator directly with Python, for example:

```bash
python3 analysis/scripts/generate_bb_bound_performance_profile.py
```

The generators resolve paths relative to the repository root, create their
output directory if necessary, and overwrite their named outputs; they do not
modify any CSV below `Results/`.

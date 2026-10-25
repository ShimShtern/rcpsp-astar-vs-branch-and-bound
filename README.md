# RCPSP: A* versus Branch-and-Bound

This repository contains exact A* and branch-and-bound (B&B) solvers for the
resource-constrained project scheduling problem (RCPSP), together with the
PSPLIB J30 instances and results from several search, dominance, and memory
management configurations.

The experiments compare solution status, runtime, memory use, search-tree
size, duplicate states, and dominance pruning across the two algorithms.

## Implementations

The active Python 3.6-compatible solver files are:

| Algorithm | Core | Command-line runner |
|---|---|---|
| A* | `rcpsp_astar_core_py36.py` | `run_rcpsp_astar_py36.py` |
| B&B | `rcpsp_bb_core_parent_py36.py` | `run_rcpsp_bb_py36.py` |

A* uses duplicate-state elimination and depth-first tie-breaking between
states with equal `f` values. Optional pruning rules include same-start
lexicographic dominance, state-progress dominance, and an exact
active-schedule check.

B&B uses a memory-efficient parent-linked node representation. It supports
best-first, depth-first, locally best-bound depth-first, and breadth-first
search. Duplicate tracking is optional and reporting-only; it does not alter
the B&B search tree. Same-start and active-schedule pruning are optional, as
is a time-indexed LP relaxation bound (weak or strong precedence formulation,
solved with Gurobi).

## Requirements

- Python 3 (tested with 3.10.13; the code is written to also run on 3.6).
- Gurobi Optimizer 13.0.3 via the `gurobipy` package, needed only for the LP
  relaxation bound (`--relaxation-bound`). Installing `gurobipy` with pip
  includes the solver itself, so nothing else has to be installed, but a
  Gurobi license is required: the size-limited license bundled with pip is
  too small for these models. Academic licenses are free from Gurobi.
- No other Python packages: the solvers and the analysis scripts use only
  the Python standard library. `requirements.txt` lists `gurobipy`.
- Linux for `run_j30_batch.sh` (it pins solvers to CPUs with `taskset`);
  Linux or macOS for the runners (the A* runner uses the `resource` module).

The experiments were run with Python 3.10.13, Gurobi 13.0.3, Bash 4.4, and
util-linux `taskset` 2.32 on Rocky Linux 8.10.

## Quick start

Run A* on one instance:

```bash
python3 run_rcpsp_astar_py36.py j30.sm/j301_1.sm \
    --prefix "" \
    --time-limit 7200 \
    --max-memory-mb 65536 \
    --same-start-lexicographic \
    --csv astar_j301_1.csv
```

Run best-first B&B on the same instance:

```bash
python3 run_rcpsp_bb_py36.py j30.sm/j301_1.sm \
    --prefix "" \
    --strategy best_first \
    --time-limit 7200 \
    --max-memory-mb 65536 \
    --same-start-lexicographic \
    --csv bb_j301_1.csv
```

Use `python3 <runner> --help` for the available command-line options.

To run one configuration on all J30 instances, with each solver pinned to its
own CPU, use `run_j30_batch.sh`; see
[EXPERIMENTS.md](EXPERIMENTS.md#running-a-batch-run_j30_batchsh).

## Repository layout

| Path | Contents |
|---|---|
| `j30.sm/` | The 480 PSPLIB J30 instances |
| `j30opt.sm` | Known optimal J30 makespans |
| `Results/astar/` | Canonical A* result tables organized by configuration |
| `Results/bb/` | Canonical B&B result tables organized by configuration |
| `analysis/` | Scripts that generate the figures and tables, and their outputs (`scripts/`, `figures/`, `tables/`) |
| `archive/bb_legacy/` | Original full-vector B&B, needed to reproduce `Results/bb/original_full_vector/` |
| `EXPERIMENTS.md` | Detailed solver options, result fields, configurations, and outcomes |
| `requirements.txt` | Python package requirement (`gurobipy`, for the LP bound only) |
| `LICENSE` | MIT License for the source code |

## Data

The 480 J30 instances in `j30.sm/` and the known optimal makespans in
`j30opt.sm` are taken unchanged from PSPLIB, the project scheduling problem
library (https://www.om-db.wi.tum.de/psplib/):

R. Kolisch and A. Sprecher (1997). PSPLIB – A project scheduling problem
library. *European Journal of Operational Research* 96(1), 205–216.

## Experimental documentation

See [EXPERIMENTS.md](EXPERIMENTS.md) for:

- execution and resource-limit semantics;
- all common and algorithm-specific command-line options;
- definitions of CSV result fields;
- the configuration represented by each canonical result file;
- aggregate outcomes for the recorded experiments;
- the analysis scripts and the figures and tables they produce.

The detailed information lives there rather than being duplicated in this
README, so each experiment has a single authoritative description.

## Resource-limit notes

`--time-limit` is enforced using elapsed wall time for each instance.
`--max-memory-mb` is a soft current-RSS limit checked periodically, so the
reported peak can be slightly above the requested threshold. The documented
64 GiB experiments use `--max-memory-mb 65536`.

## License

The source code is released under the MIT License (see `LICENSE`). The
result files, figures, and tables (`Results/`, `analysis/figures/`,
`analysis/tables/`) are released under the Creative Commons Attribution 4.0
International License (CC BY 4.0, https://creativecommons.org/licenses/by/4.0/).
Neither license covers the PSPLIB data in `j30.sm/` and `j30opt.sm`, which are
redistributed with credit to PSPLIB as described above.

#!/usr/bin/env python3
# Archived original full-start-vector B&B core.
"""Core branch-and-bound logic for PSPLIB RCPSP instances.

This module intentionally contains no command-line parsing, CSV output,
PSPLIB design-class lookup, or known-optimum lookup.  It reuses the parser,
state object, critical-path lower bound, and schedule validator from the A*
core module.

The B&B implementation is deliberately tree-based:

* a node is a partial schedule, represented by fixed start times for a
  predecessor-closed set of scheduled jobs;
* the lower bound is the same state-consistent critical-path relaxation used
  by the A* code, evaluated on the timed state induced by the partial schedule;
* an incumbent upper bound is maintained and nodes with LB >= UB are pruned;
* no Open/Closed duplicate-state pruning is applied.  A generated state key is
  counted as a duplicate only for reporting and is not used to discard nodes.

Compatible with Python 3.6.
"""
import heapq
import math
import os
import time
from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

from rcpsp_astar_core_py36 import (
    RCPSPInstance,
    State,
    cp_state_consistent,
    selectable_jobs as astar_selectable_jobs,
    transition,
)


class BBNode(object):
    """A branch-and-bound tree node for a partial RCPSP schedule."""

    __slots__ = ("node_id", "parent_id", "job", "job_start", "scheduled",
                 "starts", "g", "lb", "h", "depth")

    def __init__(self, node_id, parent_id, job, job_start, scheduled,
                 starts, g, lb, h, depth):
        self.node_id = node_id
        self.parent_id = parent_id
        self.job = job
        self.job_start = job_start
        self.scheduled = scheduled
        self.starts = starts
        self.g = g
        self.lb = lb
        self.h = h
        self.depth = depth


class BBSearchResult(object):
    """Result object with the A* SearchResult fields plus B&B statistics."""

    def __init__(self, instance, status, makespan, schedule, expanded,
                 generated, duplicates, max_open, runtime, wall_runtime,
                 incumbent_updates,
                 pruned_bound, pruned_infeasible, pruned_dominance,
                 search_strategy,
                 best_bound, best_active_lb, upper_bound, tree_nodes_popped,
                 duplicates_tracked=True):
        self.instance = instance
        self.status = status
        self.makespan = makespan
        self.schedule = schedule
        self.expanded = expanded
        self.generated = generated
        self.duplicates = duplicates
        self.max_open = max_open
        self.runtime = runtime
        self.wall_runtime = wall_runtime
        self.incumbent_updates = incumbent_updates
        self.pruned_bound = pruned_bound
        self.pruned_infeasible = pruned_infeasible
        self.pruned_dominance = pruned_dominance
        self.search_strategy = search_strategy
        self.best_bound = best_bound
        self.best_active_lb = best_active_lb
        self.upper_bound = upper_bound
        self.tree_nodes_popped = tree_nodes_popped
        self.duplicates_tracked = duplicates_tracked


# --------------------------- partial schedule helpers --------------------


def _popcount(x):
    return bin(x).count("1")


def partial_schedule_to_state(inst, scheduled_mask, starts, g):
    """Return the A* State induced by a partial schedule at current time g."""
    completed = 0
    ongoing = []
    for j in range(inst.n):
        if scheduled_mask & (1 << j):
            st = starts[j]
            if st < 0:
                continue
            finish = st + inst.durations[j]
            if finish <= g:
                completed |= 1 << j
            else:
                ongoing.append((j, finish - g))
    ongoing.sort()
    return State(g, completed, tuple(ongoing))


def lower_bound_for_partial(inst, scheduled_mask, starts, g):
    """Compute (f_hat, h) for a partial schedule using the shared CP bound."""
    state = partial_schedule_to_state(inst, scheduled_mask, starts, g)
    h = cp_state_consistent(inst, state)
    return g + h, h


def lower_bound_for_existing_state(inst, state):
    """Compute (f_hat, h) when the corresponding A* State is already known."""
    h = cp_state_consistent(inst, state)
    return state.g + h, h


def selectable_jobs_from_state(inst, state):
    """Selectable B&B branching jobs from an already constructed A* state."""
    return astar_selectable_jobs(inst, state)


def selectable_jobs_bb(inst, scheduled_mask, starts, g):
    """Selectable B&B branching jobs, delegated to the shared A* predicate."""
    state = partial_schedule_to_state(inst, scheduled_mask, starts, g)
    return selectable_jobs_from_state(inst, state)


def transition_from_state(inst, state, job):
    """Use the shared A* transition from an already constructed state."""
    return transition(inst, state, job)


def transition_from_partial_schedule(inst, scheduled_mask, starts, g, job):
    """Use the shared A* transition to start job from a partial schedule."""
    state = partial_schedule_to_state(inst, scheduled_mask, starts, g)
    return transition_from_state(inst, state, job)


def earliest_start_partial(inst, scheduled_mask, starts, g, job):
    """Earliest feasible start time, delegated to the shared A* transition."""
    # Defensive check: B&B should only call this after selectable_jobs_bb.
    if inst.pred_mask[job] & ~scheduled_mask:
        return None
    _state, start_time = transition_from_partial_schedule(
        inst, scheduled_mask, starts, g, job
    )
    return start_time


def make_root_node(inst):
    starts = tuple([-1] * inst.n)
    lb, h = lower_bound_for_partial(inst, 0, starts, 0)
    return BBNode(0, None, None, None, 0, starts, 0, lb, h, 0)


def make_child_node_from_state(inst, parent, parent_state, node_id, job):
    """Create a B&B child using the already constructed parent A* state."""
    if inst.pred_mask[job] & ~parent.scheduled:
        return None

    succ_state, st = transition_from_state(inst, parent_state, job)
    starts = list(parent.starts)
    starts[job] = st
    starts = tuple(starts)
    scheduled = parent.scheduled | (1 << job)

    # The lower bound can be evaluated directly on succ_state, avoiding another
    # partial_schedule_to_state conversion.
    g = succ_state.g
    lb, h = lower_bound_for_existing_state(inst, succ_state)
    return BBNode(node_id, parent.node_id, job, st, scheduled, starts, g, lb, h,
                  parent.depth + 1)


def make_child_node(inst, parent, node_id, job):
    """Backward-compatible wrapper; prefer make_child_node_from_state in loops."""
    parent_state = partial_schedule_to_state(
        inst, parent.scheduled, parent.starts, parent.g
    )
    return make_child_node_from_state(inst, parent, parent_state, node_id, job)


def node_state_key(inst, node):
    """State key induced by the partial schedule, for reporting duplicates only."""
    return partial_schedule_to_state(inst, node.scheduled, node.starts, node.g).key()


def node_makespan(inst, node):
    if node.scheduled != inst.all_mask:
        return None
    return max(node.starts[j] + inst.durations[j] for j in range(inst.n))


# ------------------------------- frontiers -------------------------------


class BestFirstFrontier(object):
    def __init__(self):
        self.heap = []
        self.counter = 0

    def push(self, node):
        self.counter += 1
        heapq.heappush(self.heap, (node.lb, -node.g, -node.depth, self.counter, node))

    def pop(self):
        return heapq.heappop(self.heap)[4]

    def __len__(self):
        return len(self.heap)

    def min_lb(self):
        return self.heap[0][0] if self.heap else math.inf


class DepthFirstFrontier(object):
    def __init__(self):
        self.stack = []

    def push(self, node):
        self.stack.append(node)

    def pop(self):
        return self.stack.pop()

    def __len__(self):
        return len(self.stack)

    def min_lb(self):
        if not self.stack:
            return math.inf
        return min(n.lb for n in self.stack)


class BreadthFirstFrontier(object):
    def __init__(self):
        self.queue = deque()

    def push(self, node):
        self.queue.append(node)

    def pop(self):
        return self.queue.popleft()

    def __len__(self):
        return len(self.queue)

    def min_lb(self):
        if not self.queue:
            return math.inf
        return min(n.lb for n in self.queue)


def make_frontier(search_strategy):
    if search_strategy == "best_first":
        return BestFirstFrontier()
    if search_strategy == "depth_first":
        return DepthFirstFrontier()
    if search_strategy == "breadth_first":
        return BreadthFirstFrontier()
    raise ValueError("unknown search strategy: %s" % search_strategy)


def current_rss_bytes():
    """Return this process's current resident-set size on Linux.

    Reading ``/proc`` avoids optional dependencies and reports current RSS,
    rather than the high-water mark returned by ``resource.getrusage``.
    ``None`` is returned on platforms where the value is unavailable.
    """
    try:
        with open("/proc/self/statm", "r") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (IOError, OSError, ValueError, IndexError):
        return None


# ----------------------------- main B&B solver ---------------------------


def branch_and_bound_rcpsp(inst, search_strategy="best_first", time_limit=None,
                           max_nodes=None, initial_ub=None, max_memory_mb=None,
                           same_start_lexicographic=False,
                           track_duplicates=True):
    """Run tree-based B&B for a single RCPSP instance.

    Parameters
    ----------
    inst : RCPSPInstance
        Parsed RCPSP instance from rcpsp_astar_core_py36.parse_psplib_sm.
    search_strategy : str
        One of "best_first", "depth_first", or "breadth_first".  The default
        is best-first, selecting the active node with smallest lower bound.
    time_limit : float or None
        Per-instance wall-clock time limit in seconds.
    max_nodes : int or None
        Maximum number of expanded tree nodes.
    initial_ub : int or None
        Optional incumbent upper bound.  If provided, nodes with LB >= UB are
        pruned before any complete schedule is found.
    max_memory_mb : float or None
        Soft limit on this process's resident memory in MiB.  The solver checks
        the limit between node expansions and returns a normal result when it
        is reached, allowing callers to preserve CSV output.  Supported on
        Linux via ``/proc/self/statm``.
    same_start_lexicographic : bool
        If true, prune non-lexicographic permutations of independent jobs
        assigned the same start time.
    track_duplicates : bool
        If true, retain state keys and count duplicate generated states for
        reporting.  This does not affect pruning or the search order.
    """
    t0 = time.perf_counter()
    memory_limit_bytes = None
    if max_memory_mb is not None:
        if max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be greater than zero")
        memory_limit_bytes = int(max_memory_mb * 1024 * 1024)

    frontier = make_frontier(search_strategy)
    root = make_root_node(inst)

    incumbent_value = float(initial_ub) if initial_ub is not None else math.inf
    incumbent_schedule = None
    incumbent_updates = 0

    generated = 1
    expanded = 0
    tree_nodes_popped = 0
    pruned_bound = 0
    pruned_infeasible = 0
    pruned_dominance = 0
    duplicates = 0
    max_open = 1
    next_node_id = 1

    dup_check_time = 0.0
    runtime = 0.0

    seen_state_keys = (
        set([node_state_key(inst, root)]) if track_duplicates else None
    )
    frontier.push(root)

    while len(frontier) > 0:
        wall_time = time.perf_counter() - t0
        runtime = wall_time - dup_check_time

        if time_limit is not None and wall_time >= time_limit:
            status = "time_limit_feasible" if incumbent_schedule is not None else "time_limit"
            return BBSearchResult(inst.name, status,
                                  None if incumbent_schedule is None else int(incumbent_value),
                                  incumbent_schedule, expanded, generated, duplicates,
                                  max_open, runtime, wall_time, incumbent_updates,
                                  pruned_bound, pruned_infeasible,
                                  pruned_dominance, search_strategy,
                                  root.lb, frontier.min_lb(),
                                  None if math.isinf(incumbent_value) else int(incumbent_value),
                                  tree_nodes_popped, track_duplicates)

        # Reading /proc for every node is unnecessarily expensive on large
        # searches.  A 1024-pop interval keeps the overhead negligible while
        # bounding the usual overshoot to roughly one small batch of nodes.
        if memory_limit_bytes is not None and tree_nodes_popped % 1024 == 0:
            rss_bytes = current_rss_bytes()
            if rss_bytes is not None and rss_bytes >= memory_limit_bytes:
                status = ("memory_limit_feasible" if incumbent_schedule is not None
                          else "memory_limit")
                return BBSearchResult(
                    inst.name, status,
                    None if incumbent_schedule is None else int(incumbent_value),
                    incumbent_schedule, expanded, generated, duplicates,
                    max_open, runtime, wall_time, incumbent_updates,
                    pruned_bound, pruned_infeasible, pruned_dominance,
                    search_strategy,
                    root.lb, frontier.min_lb(),
                    None if math.isinf(incumbent_value) else int(incumbent_value),
                    tree_nodes_popped, track_duplicates
                )

        if incumbent_schedule is not None and frontier.min_lb() >= incumbent_value:
            break

        node = frontier.pop()
        tree_nodes_popped += 1

        if node.lb >= incumbent_value:
            pruned_bound += 1
            continue

        if node.scheduled == inst.all_mask:
            ms = node_makespan(inst, node)
            if ms is not None and ms < incumbent_value:
                incumbent_value = ms
                incumbent_schedule = [int(x) for x in node.starts]
                incumbent_updates += 1
            continue

        if max_nodes is not None and expanded >= max_nodes:
            status = "node_limit_feasible" if incumbent_schedule is not None else "node_limit"
            return BBSearchResult(inst.name, status,
                                  None if incumbent_schedule is None else int(incumbent_value),
                                  incumbent_schedule, expanded, generated, duplicates,
                                  max_open, runtime, wall_time, incumbent_updates,
                                  pruned_bound, pruned_infeasible,
                                  pruned_dominance, search_strategy,
                                  root.lb, frontier.min_lb(),
                                  None if math.isinf(incumbent_value) else int(incumbent_value),
                                  tree_nodes_popped, track_duplicates)

        expanded += 1

        # Convert the partial schedule to the A* state once for this expansion.
        # The same state is then reused both to obtain selectable jobs and to
        # compute each child transition.
        node_state = partial_schedule_to_state(
            inst, node.scheduled, node.starts, node.g
        )
        jobs = selectable_jobs_from_state(inst, node_state)

        # Deterministic order.  For DFS, pushing in reverse makes the smallest
        # job index be explored first.
        if search_strategy == "depth_first":
            jobs_to_push = list(reversed(jobs))
        else:
            jobs_to_push = jobs

        for j in jobs_to_push:
            child = make_child_node_from_state(inst, node, node_state, next_node_id, j)
            next_node_id += 1
            if child is None:
                pruned_infeasible += 1
                continue

            if same_start_lexicographic and node.job is not None:
                parent_precedes_child = (
                    inst.pred_mask[j] & (1 << node.job)
                ) != 0
                if (child.job_start == node.job_start and j < node.job and
                        not parent_precedes_child):
                    pruned_dominance += 1
                    continue

            generated += 1
            if track_duplicates:
                t_dup0 = time.perf_counter()
                skey = node_state_key(inst, child)
                if skey in seen_state_keys:
                    duplicates += 1
                else:
                    seen_state_keys.add(skey)
                dup_check_time += time.perf_counter() - t_dup0



            if child.lb >= incumbent_value:
                pruned_bound += 1
                continue
            frontier.push(child)

        max_open = max(max_open, len(frontier))

    wall_time = time.perf_counter() - t0
    runtime = wall_time - dup_check_time
    if incumbent_schedule is None:
        return BBSearchResult(inst.name, "infeasible", None, None, expanded,
                              generated, duplicates, max_open, runtime, wall_time,
                              incumbent_updates, pruned_bound, pruned_infeasible,
                              pruned_dominance, search_strategy, root.lb,
                              math.inf, None,
                              tree_nodes_popped, track_duplicates)

    return BBSearchResult(inst.name, "optimal", int(incumbent_value),
                          incumbent_schedule, expanded, generated, duplicates,
                          max_open, runtime, wall_time, incumbent_updates, pruned_bound,
                          pruned_infeasible, pruned_dominance, search_strategy,
                          root.lb,
                          frontier.min_lb(), int(incumbent_value),
                          tree_nodes_popped, track_duplicates)

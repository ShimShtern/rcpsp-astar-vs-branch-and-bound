#!/usr/bin/env python3
"""Core branch-and-bound logic for PSPLIB RCPSP instances.

This module intentionally contains no command-line parsing, CSV output,
PSPLIB design-class lookup, or known-optimum lookup.  It reuses the parser,
state object, critical-path lower bound, and schedule validator from the A*
core module.

The B&B implementation is deliberately tree-based:

* a node is a partial schedule represented by a parent link plus the job and
  start time added at that node; full start vectors are reconstructed only
  when needed;
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
import sys
import time
from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

from rcpsp_astar_core_py36 import (
    RCPSPInstance,
    State,
    cp_state_consistent,
    selectable_jobs as astar_selectable_jobs,
    transition,
    validate_schedule,
)


class BBNode(object):
    """A B&B node storing only its incremental scheduling decision."""

    __slots__ = ("node_id", "parent", "job", "job_start", "scheduled",
                 "g", "lb", "order_lb", "h", "depth")

    def __init__(self, node_id, parent, job, job_start, scheduled,
                 g, lb, h, depth):
        self.node_id = node_id
        self.parent = parent
        self.job = job
        self.job_start = job_start
        self.scheduled = scheduled
        self.g = g
        self.lb = lb
        # Search ordering may quantize a Gurobi-derived bound.  The raw lb is
        # retained separately for pruning and optimality proofs.
        self.order_lb = lb
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
                 duplicates_tracked=True, cpu_time=None):
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
        self.cpu_time = cpu_time
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
        self.relaxation_bound = None
        self.lp_model_build_time = None
        self.lp_bound_update_time = None
        self.lp_optimize_time = None
        self.lp_solve_count = None
        self.lp_lb_changes = None
        self.lp_ub_changes = None
        self.lp_simplex_iterations = None
        self.lp_precedence = None
        self.lp_ordering = None
        self.lp_incumbent_updates = None


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


def reconstruct_starts(inst, node):
    """Reconstruct a full start-time tuple from a node's parent chain."""
    starts = [-1] * inst.n
    current = node
    while current.parent is not None:
        starts[current.job] = current.job_start
        current = current.parent
    return tuple(starts)


def node_to_state(inst, node):
    """Build the timed A* state directly from a node's decision chain."""
    completed = 0
    ongoing = []
    current = node
    while current.parent is not None:
        finish = current.job_start + inst.durations[current.job]
        if finish <= node.g:
            completed |= 1 << current.job
        else:
            ongoing.append((current.job, finish - node.g))
        current = current.parent
    ongoing.sort()
    return State(node.g, completed, tuple(ongoing))


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


def _resources_allow_interval(inst, starts, scheduled_mask, job, start):
    """Return whether ``job`` can occupy its whole interval at ``start``.

    Resource use only changes at starts and finishes of already scheduled
    jobs, so checking the candidate start and scheduled-job start events in
    the candidate interval is sufficient.
    """
    duration = inst.durations[job]
    if duration == 0:
        return True

    finish = start + duration
    events = set([start])
    intervals = []
    for other in range(inst.n):
        if not (scheduled_mask & (1 << other)):
            continue
        other_start = starts[other]
        if other_start < 0 or inst.durations[other] == 0:
            continue
        other_finish = other_start + inst.durations[other]
        intervals.append((other, other_start, other_finish))
        if start < other_start < finish:
            events.add(other_start)

    for event_time in events:
        used = [0] * len(inst.capacities)
        for other, other_start, other_finish in intervals:
            if other_start <= event_time < other_finish:
                demand = inst.demands[other]
                for resource in range(len(used)):
                    used[resource] += demand[resource]
        if any(used[resource] + inst.demands[job][resource] >
               inst.capacities[resource]
               for resource in range(len(used))):
            return False
    return True


def can_left_shift_job(inst, scheduled_mask, starts, job, proposed_start):
    """Return whether ``job`` has a feasible start before ``proposed_start``.

    This is the active-schedule test for a newly generated child.  Precedence
    fixes the first possible time; after that, a new feasible resource window
    can begin only when an already scheduled job finishes.
    """
    precedence_start = 0
    for predecessor in inst.predecessors[job]:
        predecessor_start = starts[predecessor]
        if predecessor_start < 0:
            return False
        precedence_start = max(
            precedence_start,
            predecessor_start + inst.durations[predecessor]
        )

    if precedence_start >= proposed_start:
        return False

    candidates = set([precedence_start])
    for other in range(inst.n):
        if not (scheduled_mask & (1 << other)):
            continue
        other_start = starts[other]
        if other_start < 0:
            continue
        other_finish = other_start + inst.durations[other]
        if precedence_start <= other_finish < proposed_start:
            candidates.add(other_finish)

    for candidate_start in sorted(candidates):
        if _resources_allow_interval(
                inst, starts, scheduled_mask, job, candidate_start):
            return True
    return False


def make_root_node(inst):
    root_state = State(0, 0, tuple())
    lb, h = lower_bound_for_existing_state(inst, root_state)
    return BBNode(0, None, None, None, 0, 0, lb, h, 0)


def make_child_node_and_state_from_state(inst, parent, parent_state, node_id,
                                         job):
    """Create a delta node and return the already-computed successor state."""
    if inst.pred_mask[job] & ~parent.scheduled:
        return None, None

    succ_state, st = transition_from_state(inst, parent_state, job)
    scheduled = parent.scheduled | (1 << job)
    g = succ_state.g
    lb, h = lower_bound_for_existing_state(inst, succ_state)
    child = BBNode(node_id, parent, job, st, scheduled, g, lb, h,
                   parent.depth + 1)
    return child, succ_state


def make_child_node_from_state(inst, parent, parent_state, node_id, job):
    """Create a B&B child using the already constructed parent A* state."""
    child, _succ_state = make_child_node_and_state_from_state(
        inst, parent, parent_state, node_id, job
    )
    return child


def make_child_node(inst, parent, node_id, job):
    """Backward-compatible wrapper; prefer make_child_node_from_state in loops."""
    parent_state = node_to_state(inst, parent)
    return make_child_node_from_state(inst, parent, parent_state, node_id, job)


def node_state_key(inst, node):
    """State key induced by the partial schedule, for reporting duplicates only."""
    return node_to_state(inst, node).key()


def node_makespan(inst, node):
    if node.scheduled != inst.all_mask:
        return None
    starts = reconstruct_starts(inst, node)
    return max(starts[j] + inst.durations[j] for j in range(inst.n))


# ------------------------------- frontiers -------------------------------


class BestFirstFrontier(object):
    def __init__(self, separate_order_bound=False):
        self.heap = []
        # A second heap is necessary only when the node-ordering bound can
        # differ from the proof bound (fractional LP ordering).  In ordinary
        # B&B and integer LP ordering, order_lb == lb, so the main heap already
        # exposes the minimum valid lower bound.  Avoiding these structures in
        # that case saves one heap tuple and one set entry per frontier node.
        self.separate_order_bound = separate_order_bound
        self.raw_lb_heap = [] if separate_order_bound else None
        self.active_node_ids = set() if separate_order_bound else None
        self.counter = 0

    def push(self, node):
        self.counter += 1
        heapq.heappush(
            self.heap,
            (node.order_lb, -node.g, -node.depth, self.counter, node)
        )
        if self.separate_order_bound:
            heapq.heappush(
                self.raw_lb_heap,
                (node.lb, self.counter, node.node_id)
            )
            self.active_node_ids.add(node.node_id)

    def pop(self):
        node = heapq.heappop(self.heap)[4]
        if self.separate_order_bound:
            self.active_node_ids.remove(node.node_id)
        return node

    def __len__(self):
        return len(self.heap)

    def min_lb(self):
        if not self.separate_order_bound:
            return self.heap[0][0] if self.heap else math.inf
        while (self.raw_lb_heap and
               self.raw_lb_heap[0][2] not in self.active_node_ids):
            heapq.heappop(self.raw_lb_heap)
        return self.raw_lb_heap[0][0] if self.raw_lb_heap else math.inf


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


def make_frontier(search_strategy, separate_order_bound=False):
    if search_strategy == "best_first":
        return BestFirstFrontier(separate_order_bound)
    if search_strategy in ("depth_first", "depth_first_best_lb"):
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


# -------------------------- optional LP relaxation ----------------------


class TimeIndexedLPRelaxation(object):
    """Reusable time-indexed LP relaxation for one RCPSP instance."""

    def __init__(self, inst, precedence_formulation="strong",
                 ordering_mode="fractional"):
        if precedence_formulation not in ("weak", "strong"):
            raise ValueError(
                "precedence_formulation must be 'weak' or 'strong'"
            )
        if ordering_mode not in ("fractional", "integer"):
            raise ValueError("ordering_mode must be 'fractional' or 'integer'")
        self.inst = inst
        self.precedence_formulation = precedence_formulation
        self.ordering_mode = ordering_mode
        self.fixed_starts = {}
        # For an unscheduled job j, starts before this cutoff are excluded.
        # The values are updated incrementally as the reusable model moves
        # between B&B nodes (which need not be parent/child nodes).
        self.unscheduled_start_cutoffs = [0] * inst.n
        # Inclusive latest starts imposed by the current incumbent.  These
        # are kept separately from the node-time cutoffs because the reusable
        # model moves between unrelated nodes while the incumbent may tighten.
        self.incumbent_latest_starts = [None] * inst.n
        self.applied_incumbent_ub = None
        self.bound_update_time = 0.0
        self.optimize_time = 0.0
        self.solve_count = 0
        self.lb_changes = 0
        self.ub_changes = 0
        self.simplex_iterations = 0.0
        # Number of validated, integral LP schedules that improved the B&B
        # incumbent.  This is a subset of the solver's incumbent_updates.
        self.incumbent_updates = 0
        # Status-12 (NUMERIC) recoveries; reported on stderr only, so the
        # result CSV schema is unchanged.
        self.numeric_retries = 0
        self.numeric_fallbacks = 0
        self.infeasibility_diagnostic_written = False
        build_started = time.perf_counter()
        self._build_model()
        self.model_build_time = time.perf_counter() - build_started

    def _build_model(self):
        """Build the variables and constraints once for this instance."""
        inst = self.inst
        try:
            import gurobipy as gp
        except ImportError:
            raise RuntimeError("--relaxation-bound requires gurobipy")

        self.gp = gp
        horizon = inst.horizon
        if horizon is None or horizon < 0:
            horizon = sum(inst.durations)
        self.horizon = horizon
        self.starts_by_job = [
            list(range(horizon - inst.durations[j] + 1))
            for j in range(inst.n)
        ]
        model = gp.Model("rcpsp_time_indexed_lp")
        model.Params.OutputFlag = 0
        # This parameter is constant for the lifetime of the reusable model.
        # It is also used to quantize only Gurobi-derived search-order keys.
        self.optimality_tol = float(model.Params.OptimalityTol)

        variable_index = {}
        for j in range(inst.n):
            for start in self.starts_by_job[j]:
                variable_index[(j, start)] = model.addVar(
                    lb=0.0, ub=1.0,
                    vtype=gp.GRB.CONTINUOUS,
                    name="x_%d_%d" % (j, start)
                )
        if not variable_index:
            raise RuntimeError("time-indexed relaxation has no start-time variables")
        model.update()

        sink = inst.topo[-1]
        for j in range(inst.n):
            model.addConstr(
                gp.quicksum(variable_index[(j, start)]
                            for start in self.starts_by_job[j]) == 1.0,
                name="assign_%d" % j
            )

        for predecessor in range(inst.n):
            duration = inst.durations[predecessor]
            for successor in inst.successors[predecessor]:
                if self.precedence_formulation == "weak":
                    model.addConstr(
                        gp.quicksum(
                            (start + duration) *
                            variable_index[(predecessor, start)]
                            for start in self.starts_by_job[predecessor]
                        ) <= gp.quicksum(
                            start * variable_index[(successor, start)]
                            for start in self.starts_by_job[successor]
                        ),
                        name="prec_%d_%d" % (predecessor, successor)
                    )
                else:
                    for s in range(horizon + 1):
                        model.addConstr(
                            gp.quicksum(
                                variable_index[(predecessor, start)]
                                for start in self.starts_by_job[predecessor]
                                if start <= s - duration
                            ) >= gp.quicksum(
                                variable_index[(successor, start)]
                                for start in self.starts_by_job[successor]
                                if start <= s
                            ),
                            name="prec_%d_%d_%d" %
                                 (predecessor, successor, s)
                        )

        for resource, capacity in enumerate(inst.capacities):
            for time_point in range(horizon):
                model.addConstr(
                    gp.quicksum(
                        inst.demands[j][resource] * variable_index[(j, start)]
                        for j in range(inst.n)
                        for start in self.starts_by_job[j]
                        if start <= time_point < start + inst.durations[j]
                    ) <= capacity,
                    name="res_%d_%d" % (resource, time_point)
                )

        model.setObjective(
            gp.quicksum(
                (start + inst.durations[sink]) * variable_index[(sink, start)]
                for start in self.starts_by_job[sink]
            ),
            gp.GRB.MINIMIZE
        )
        self.model = model
        self.variable_index = variable_index
        # job_variables[j][start] is x_{j,start}; starts_by_job[j] begins at 0.
        self.job_variables = [
            [variable_index[(j, start)] for start in self.starts_by_job[j]]
            for j in range(inst.n)
        ]

    def ordering_bound(self, bound):
        """Return the Gurobi-derived bound used only for node ordering."""
        if math.isinf(bound):
            return bound
        if self.ordering_mode == "integer":
            return int(math.ceil(bound - self.optimality_tol))
        tolerance = self.optimality_tol
        if tolerance <= 0.0:
            return bound
        return round(bound / tolerance) * tolerance

    def proof_bound(self, cp_bound, lp_bound):
        """Return the integer node bound used for pruning and proof."""
        if math.isinf(lp_bound):
            return lp_bound
        # RCPSP start times, durations, and makespans are integral.  Therefore
        # the ceiling of a valid LP bound is also a valid lower bound.  Back
        # off by Gurobi's configured tolerance before taking the ceiling so a
        # value just above an integer because of numerical noise is not raised
        # to the following integer.
        integer_lp_bound = int(math.ceil(lp_bound - self.optimality_tol))
        return max(cp_bound, integer_lp_bound)

    @staticmethod
    def _interval_difference(low, high, other_low, other_high):
        """Yield integer intervals in [low, high] but not the other interval."""
        if low > high:
            return
        left_high = min(high, other_low - 1)
        if low <= left_high:
            yield low, left_high
        right_low = max(low, other_high + 1)
        if right_low <= high:
            yield right_low, high

    def _apply_node_fixings(self, node, incumbent_ub=None):
        """Change only the variable bounds needed to represent ``node``.

        In addition to the fixed prefix and the node-time lower cutoff, rule
        out every start whose activity would finish after ``incumbent_ub``.
        The upper bound is inclusive: schedules tying the incumbent remain in
        the relaxation, while starts with ``start + duration > incumbent_ub``
        are disabled.

        Return false when a fixed activity already finishes after the bound;
        such a node cannot improve or tie the incumbent and need not be sent
        to Gurobi.
        """
        starts = reconstruct_starts(self.inst, node)
        new_fixed_starts = {
            j: starts[j]
            for j in range(self.inst.n)
            if node is not None and node.scheduled & (1 << j)
        }

        # A descendant of this node can only schedule another job at or after
        # node.g.  Exclude earlier starts for every job that is not scheduled
        # yet.  Restore bounds first when moving to another part of the tree;
        # the persistent model may be reoptimized for unrelated nodes in
        # succession rather than strictly along one root-to-leaf path.
        node_time = 0 if node is None else int(node.g)
        new_cutoffs = [
            0 if j in new_fixed_starts else node_time
            for j in range(self.inst.n)
        ]

        normalized_ub = None
        if incumbent_ub is not None and not math.isinf(incumbent_ub):
            normalized_ub = int(math.floor(
                float(incumbent_ub) + self.optimality_tol
            ))

        # Computing the latest starts belongs to an incumbent update, not a
        # branch transition.  On ordinary node changes reuse the cached
        # limits; only the node-specific lower cutoffs/fixings then change.
        incumbent_changed = (
            normalized_ub != self.applied_incumbent_ub or
            any(value is None for value in self.incumbent_latest_starts)
        )
        if incumbent_changed:
            if normalized_ub is None:
                new_latest_starts = [
                    self.starts_by_job[j][-1] for j in range(self.inst.n)
                ]
            else:
                new_latest_starts = [
                    min(self.starts_by_job[j][-1],
                        normalized_ub - self.inst.durations[j])
                    for j in range(self.inst.n)
                ]
        else:
            new_latest_starts = self.incumbent_latest_starts

        for job, fixed_start in new_fixed_starts.items():
            if fixed_start > new_latest_starts[job]:
                return False

        for job, old_cutoff in enumerate(self.unscheduled_start_cutoffs):
            old_latest = self.incumbent_latest_starts[job]
            if old_latest is None:
                old_latest = self.starts_by_job[job][-1]
            new_cutoff = new_cutoffs[job]
            new_latest = new_latest_starts[job]

            # Restore starts newly admitted by the intersection of the node
            # lower cutoff and incumbent upper cutoff, then disable starts
            # that have just left that interval.
            for first, last in self._interval_difference(
                    new_cutoff, new_latest, old_cutoff, old_latest):
                for start in range(first, last + 1):
                    self.variable_index[(job, start)].UB = 1.0
                    self.ub_changes += 1
            for first, last in self._interval_difference(
                    old_cutoff, old_latest, new_cutoff, new_latest):
                for start in range(first, last + 1):
                    self.variable_index[(job, start)].UB = 0.0
                    self.ub_changes += 1

        for job, old_start in self.fixed_starts.items():
            if new_fixed_starts.get(job) != old_start:
                self.variable_index[(job, old_start)].LB = 0.0
                self.lb_changes += 1

        for job, new_start in new_fixed_starts.items():
            if self.fixed_starts.get(job) != new_start:
                # assign_job == 1 and non-negativity force every other start
                # variable for this job to zero when this variable has LB 1.
                self.variable_index[(job, new_start)].LB = 1.0
                self.lb_changes += 1

        self.fixed_starts = new_fixed_starts
        self.unscheduled_start_cutoffs = new_cutoffs
        self.incumbent_latest_starts = new_latest_starts
        self.applied_incumbent_ub = normalized_ub
        return True

    def _reset_and_reoptimize(self, retry_deadline=None):
        """Discard retained LP state and solve again with unchanged bounds."""
        retry_started = time.perf_counter()
        try:
            self.model.reset()
            if retry_deadline is None:
                self.model.Params.TimeLimit = self.gp.GRB.INFINITY
            else:
                self.model.Params.TimeLimit = max(
                    0.0, retry_deadline - time.perf_counter()
                )
            self.solve_count += 1
            optimize_started = time.perf_counter()
            self.model.optimize()
            self.optimize_time += time.perf_counter() - optimize_started
            self.simplex_iterations += float(self.model.IterCount)
            status = self.model.Status
            objective = (float(self.model.ObjVal)
                         if self.model.SolCount > 0 else None)
            bound = (float(self.model.ObjBound)
                     if status in (self.gp.GRB.OPTIMAL,
                                   self.gp.GRB.TIME_LIMIT) else None)
            return (status, objective, bound,
                    time.perf_counter() - retry_started, None)
        except self.gp.GurobiError as exc:
            return (None, None, None,
                    time.perf_counter() - retry_started, str(exc))

    def _log_infeasibility_diagnostic(self, node, retry_deadline=None):
        """Diagnose status 3, reset retained LP state, and reoptimize.

        The full diagnostic is written only for the first occurrence in an
        instance, but every occurrence receives a fresh reset/reoptimization.
        The returned value is the retry's Gurobi status (or ``None`` if the
        retry itself raised a Gurobi error).
        """
        if self.infeasibility_diagnostic_written:
            return self._reset_and_reoptimize(retry_deadline)[0]
        self.infeasibility_diagnostic_written = True

        inst = self.inst
        original_status = self.model.Status
        starts = reconstruct_starts(inst, node)
        scheduled = []
        for job in range(inst.n):
            if node.scheduled & (1 << job):
                start = starts[job]
                scheduled.append((job, start, inst.durations[job],
                                  start + inst.durations[job]))

        max_fixed_finish = max(
            [finish for _job, _start, _duration, finish in scheduled] or [0]
        )
        busy = [False] * max_fixed_finish
        for _job, start, _duration, finish in scheduled:
            for time_point in range(max(0, start), min(finish,
                                                        max_fixed_finish)):
                busy[time_point] = True
        idle_intervals = []
        interval_start = None
        for time_point in range(max_fixed_finish + 1):
            is_idle = time_point < max_fixed_finish and not busy[time_point]
            if is_idle and interval_start is None:
                interval_start = time_point
            elif not is_idle and interval_start is not None:
                idle_intervals.append((interval_start, time_point))
                interval_start = None

        serial = list(starts)
        cursor = max(int(node.g), max_fixed_finish)
        for job in inst.topo:
            if serial[job] >= 0:
                continue
            predecessor_finish = max(
                [serial[p] + inst.durations[p]
                 for p in inst.predecessors[job] if serial[p] >= 0] or [0]
            )
            serial[job] = max(cursor, predecessor_finish)
            cursor = serial[job] + inst.durations[job]
        serial_makespan = max(
            serial[j] + inst.durations[j] for j in range(inst.n)
        )
        try:
            validate_schedule(inst, serial)
            serial_validation = "valid"
        except ValueError as exc:
            serial_validation = "invalid: %s" % exc

        bound_problems = []
        for job in range(inst.n):
            fixed_start = self.fixed_starts.get(job)
            cutoff = self.unscheduled_start_cutoffs[job]
            latest = self.incumbent_latest_starts[job]
            if latest is None:
                latest = self.starts_by_job[job][-1]
            for start in self.starts_by_job[job]:
                variable = self.variable_index[(job, start)]
                expected_lb = 1.0 if fixed_start == start else 0.0
                expected_ub = (0.0 if fixed_start is None and
                               start < cutoff else 1.0)
                if start > latest:
                    expected_ub = 0.0
                if (abs(variable.LB - expected_lb) > 1e-12 or
                        abs(variable.UB - expected_ub) > 1e-12):
                    bound_problems.append(
                        "%s(LB=%g expected=%g UB=%g expected=%g)" %
                        (variable.VarName, variable.LB, expected_lb,
                         variable.UB, expected_ub)
                    )

        iis_constraints = []
        iis_lower_bounds = []
        iis_upper_bounds = []
        iis_error = None
        try:
            self.model.computeIIS()
            iis_constraints = [
                constraint.ConstrName for constraint in self.model.getConstrs()
                if constraint.IISConstr
            ]
            for variable in self.model.getVars():
                if variable.IISLB:
                    iis_lower_bounds.append(variable.VarName)
                if variable.IISUB:
                    iis_upper_bounds.append(variable.VarName)
        except self.gp.GurobiError as exc:
            iis_error = str(exc)

        # Model.reset() preserves the current mathematical model, including
        # all LB/UB changes, but discards the retained solution and LP basis.
        # Re-solving after it distinguishes a bad warm reoptimization path
        # from genuinely infeasible current bounds.  A successful retry is
        # returned to the caller and used to continue the B&B search.
        reset_retry_status = None
        reset_retry_objective = None
        reset_retry_bound = None
        reset_retry_time = None
        reset_retry_error = None
        (reset_retry_status, reset_retry_objective, reset_retry_bound,
         reset_retry_time, reset_retry_error) = \
            self._reset_and_reoptimize(retry_deadline)

        lines = [
            "",
            "=== LP INFEASIBILITY DIAGNOSTIC BEGIN ===",
            "instance=%s formulation=%s gurobi_status=%s" %
            (inst.name, self.precedence_formulation, original_status),
            "node_id=%s depth=%s g=%s cp_lb=%s" %
            (node.node_id, node.depth, node.g, node.lb),
            "horizon=%s sum_durations=%s scheduled_count=%s" %
            (self.horizon, sum(inst.durations), len(scheduled)),
            "fixed_schedule(job,start,duration,finish)=%s" % scheduled,
            "fixed_prefix_max_finish=%s idle_intervals=%s" %
            (max_fixed_finish, idle_intervals),
            "serial_completion=%s" % serial,
            "serial_makespan=%s within_horizon=%s validation=%s" %
            (serial_makespan, serial_makespan <= self.horizon,
             serial_validation),
            "lp_fixed_starts=%s" % sorted(self.fixed_starts.items()),
            "lp_unscheduled_cutoffs=%s" %
            self.unscheduled_start_cutoffs,
            "lp_incumbent_latest_starts=%s" %
            self.incumbent_latest_starts,
            "unexpected_variable_bounds_count=%s" % len(bound_problems),
            "unexpected_variable_bounds=%s" % bound_problems,
            "iis_constraints_count=%s" % len(iis_constraints),
            "iis_constraints=%s" % iis_constraints,
            "iis_lower_bounds_count=%s" % len(iis_lower_bounds),
            "iis_lower_bounds=%s" % iis_lower_bounds,
            "iis_upper_bounds_count=%s" % len(iis_upper_bounds),
            "iis_upper_bounds=%s" % iis_upper_bounds,
            "reset_retry_status=%s" % reset_retry_status,
            "reset_retry_objective=%s" % reset_retry_objective,
            "reset_retry_bound=%s" % reset_retry_bound,
            "reset_retry_time=%s" % reset_retry_time,
        ]
        if iis_error is not None:
            lines.append("iis_error=%s" % iis_error)
        if reset_retry_error is not None:
            lines.append("reset_retry_error=%s" % reset_retry_error)
        lines.append("=== LP INFEASIBILITY DIAGNOSTIC END ===")
        print("\n".join(lines), file=sys.stderr, flush=True)
        return reset_retry_status

    def solve(self, node=None, solver_time_limit=None, incumbent_ub=None):
        """Reoptimize the persistent LP after applying a node's fixings."""
        retry_deadline = None
        if solver_time_limit is not None:
            retry_deadline = (time.perf_counter() +
                              max(0.0, solver_time_limit))
        update_started = time.perf_counter()
        if not self._apply_node_fixings(node, incumbent_ub):
            self.bound_update_time += time.perf_counter() - update_started
            return math.inf, None
        # Gurobi optimize() also applies lazy attribute updates, but making the
        # update explicit lets diagnostics inspect the exact committed bounds
        # and removes ambiguity about queued changes between B&B nodes.
        self.model.update()
        self.bound_update_time += time.perf_counter() - update_started
        if solver_time_limit is None:
            self.model.Params.TimeLimit = self.gp.GRB.INFINITY
        else:
            self.model.Params.TimeLimit = max(
                0.0, retry_deadline - time.perf_counter()
            )
        self.solve_count += 1
        optimize_started = time.perf_counter()
        self.model.optimize()
        self.optimize_time += time.perf_counter() - optimize_started
        self.simplex_iterations += float(self.model.IterCount)

        status = self.model.Status
        if status == self.gp.GRB.NUMERIC:
            # Unrecoverable numerical trouble is a property of the retained
            # warm-start basis, not of the node.  Discard it and solve the
            # same bounds from scratch.  If that also fails, fall back to the
            # node's own (critical-path) bound: valid, only weaker.
            self.numeric_retries += 1
            retry = self._reset_and_reoptimize(retry_deadline)
            status = retry[0]
            print("LP NUMERIC (status 12) at B&B node %s: reset retry "
                  "returned status %s%s" %
                  (None if node is None else node.node_id, status,
                   "" if retry[4] is None else " (%s)" % retry[4]),
                  file=sys.stderr, flush=True)
            if status is None or status == self.gp.GRB.NUMERIC:
                self.numeric_fallbacks += 1
                return (0.0 if node is None else float(node.lb)), None
        if (status == self.gp.GRB.INFEASIBLE and
                incumbent_ub is not None and
                not math.isinf(incumbent_ub)):
            # Infeasibility is expected when the incumbent latest-start
            # cutoffs prove that this node cannot complete within the current
            # upper bound.  Infinity causes the caller to prune the node.
            return math.inf, None
        if status == self.gp.GRB.INFEASIBLE:
            status = self._log_infeasibility_diagnostic(
                node, retry_deadline
            )
            if status is None:
                raise RuntimeError(
                    "time-indexed relaxation reset retry failed at B&B "
                    "node %s; see LP INFEASIBILITY DIAGNOSTIC above" %
                    node.node_id
                )
            if status == self.gp.GRB.INFEASIBLE:
                raise RuntimeError(
                    "time-indexed relaxation remains infeasible after "
                    "reset at B&B node %s; see LP INFEASIBILITY "
                    "DIAGNOSTIC above" % node.node_id
                )

        if status == self.gp.GRB.OPTIMAL:
            bound = float(self.model.ObjVal)
        elif (status == self.gp.GRB.TIME_LIMIT and
              self.model.SolCount > 0):
            bound = float(self.model.ObjBound)
        elif status == self.gp.GRB.TIME_LIMIT:
            # The caller's global time limit can expire during reoptimization
            # before Gurobi obtains a usable solution or bound for this node.
            return None, None
        else:
            raise RuntimeError(
                "time-indexed relaxation failed with Gurobi status %d" %
                status
            )

        # Only unscheduled jobs can be fractional.  A scheduled job has LB 1
        # on its start, which with assign_job == 1 forces its other starts to
        # zero, and starts outside [cutoff, latest] have UB 0.  Read just the
        # admissible window of each unscheduled job, one batched call per job.
        schedule = [-1] * self.inst.n
        for j in range(self.inst.n):
            fixed_start = self.fixed_starts.get(j)
            if fixed_start is not None:
                schedule[j] = fixed_start
                continue
            first = self.unscheduled_start_cutoffs[j]
            last = self.incumbent_latest_starts[j]
            values = self.model.getAttr(
                "X", self.job_variables[j][first:last + 1]
            )
            selected_start = None
            for offset, value in enumerate(values):
                rounded = round(value)
                if abs(value - rounded) > 1e-6:
                    return bound, None
                if rounded == 1:
                    if selected_start is not None:
                        return bound, None
                    selected_start = first + offset
            if selected_start is None:
                return bound, None
            schedule[j] = selected_start
        return bound, schedule


def time_indexed_lp_bound(inst, node=None, solver_time_limit=None,
                          precedence_formulation="strong",
                          incumbent_ub=None):
    """Build and solve one standalone time-indexed LP relaxation.

    The main B&B solver uses ``TimeIndexedLPRelaxation`` directly so that its
    model is built only once.  This wrapper remains available for callers that
    need a single independent relaxation solve.
    """
    return TimeIndexedLPRelaxation(
        inst, precedence_formulation
    ).solve(node, solver_time_limit, incumbent_ub)


def _with_relaxation_bound(result, relaxation_value, relaxation=None):
    result.relaxation_bound = relaxation_value
    if relaxation is not None:
        result.lp_model_build_time = relaxation.model_build_time
        result.lp_bound_update_time = relaxation.bound_update_time
        result.lp_optimize_time = relaxation.optimize_time
        result.lp_solve_count = relaxation.solve_count
        result.lp_lb_changes = relaxation.lb_changes
        result.lp_ub_changes = relaxation.ub_changes
        result.lp_simplex_iterations = relaxation.simplex_iterations
        result.lp_precedence = relaxation.precedence_formulation
        result.lp_ordering = relaxation.ordering_mode
        result.lp_incumbent_updates = relaxation.incumbent_updates
    return result


# ----------------------------- main B&B solver ---------------------------


def branch_and_bound_rcpsp(inst, search_strategy="best_first", time_limit=None,
                           max_nodes=None, initial_ub=None, max_memory_mb=None,
                           same_start_lexicographic=False,
                           track_duplicates=True, active_schedule=False,
                           relaxation_bound=False, lp_precedence="strong",
                           lp_ordering="fractional"):
    """Run tree-based B&B for a single RCPSP instance.

    Parameters
    ----------
    inst : RCPSPInstance
        Parsed RCPSP instance from rcpsp_astar_core_py36.parse_psplib_sm.
    search_strategy : str
        One of "best_first", "depth_first", "depth_first_best_lb", or
        "breadth_first".  The default is best-first, selecting the active node
        with smallest lower bound.
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
    active_schedule : bool
        If true, prune a child when its newly added job could be left-shifted
        to an earlier precedence- and resource-feasible start in the current
        partial schedule.  This restricts the search to active schedules.
    relaxation_bound : bool
        If true, solve a time-indexed LP relaxation at the root and at every
        generated node, fixing the start times already assigned on that node.
        Each LP objective is combined with the node's critical-path bound.
    lp_precedence : str
        "strong" uses cumulative precedence constraints at every time point;
        "weak" uses one expected-start precedence constraint per arc.
    lp_ordering : str
        "fractional" orders nodes by the tolerance-quantized LP value;
        "integer" orders them by the same integer ceiling used for proof.
        This changes branching order only, not the pruning bound.
    """
    t0 = time.perf_counter()
    cpu_t0 = time.process_time()
    search_horizon = inst.horizon
    if search_horizon is None or search_horizon < 0:
        search_horizon = sum(inst.durations)
    memory_limit_bytes = None
    if max_memory_mb is not None:
        if max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be greater than zero")
        memory_limit_bytes = int(max_memory_mb * 1024 * 1024)

    frontier = make_frontier(
        search_strategy,
        separate_order_bound=(relaxation_bound and
                              lp_ordering == "fractional")
    )
    root = make_root_node(inst)
    relaxation_value = None
    if relaxation_bound:
        relaxation = TimeIndexedLPRelaxation(
            inst, lp_precedence, ordering_mode=lp_ordering
        )
        remaining_time = None
        if time_limit is not None:
            remaining_time = max(0.0, time_limit - (time.perf_counter() - t0))
        relaxation_value, root_relaxation_schedule = relaxation.solve(
            root, remaining_time, initial_ub)
        if relaxation_value is not None:
            root_cp_lb = root.lb
            root.lb = relaxation.proof_bound(root_cp_lb, relaxation_value)
            root.order_lb = max(
                root_cp_lb,
                relaxation.ordering_bound(relaxation_value)
            )
    else:
        relaxation = None
        root_relaxation_schedule = None

    incumbent_value = float(initial_ub) if initial_ub is not None else math.inf
    incumbent_schedule = None
    incumbent_updates = 0
    if root_relaxation_schedule is not None:
        validate_schedule(inst, root_relaxation_schedule)
        root_makespan = max(
            root_relaxation_schedule[j] + inst.durations[j]
            for j in range(inst.n)
        )
        if root_makespan < incumbent_value:
            incumbent_value = root_makespan
            incumbent_schedule = root_relaxation_schedule
            incumbent_updates += 1
            relaxation.incumbent_updates += 1

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
            return _with_relaxation_bound(BBSearchResult(inst.name, status,
                                  None if incumbent_schedule is None else int(incumbent_value),
                                  incumbent_schedule, expanded, generated, duplicates,
                                  max_open, runtime, wall_time, incumbent_updates,
                                  pruned_bound, pruned_infeasible,
                                  pruned_dominance, search_strategy,
                                  root.lb, frontier.min_lb(),
                                  None if math.isinf(incumbent_value) else int(incumbent_value),
                                      tree_nodes_popped, track_duplicates,
                                      time.process_time() - cpu_t0),
                                          relaxation_value, relaxation)

        # Reading /proc for every node is unnecessarily expensive on large
        # searches.  A 1024-pop interval keeps the overhead negligible while
        # bounding the usual overshoot to roughly one small batch of nodes.
        if memory_limit_bytes is not None and tree_nodes_popped % 1024 == 0:
            rss_bytes = current_rss_bytes()
            if rss_bytes is not None and rss_bytes >= memory_limit_bytes:
                status = ("memory_limit_feasible" if incumbent_schedule is not None
                          else "memory_limit")
                return _with_relaxation_bound(BBSearchResult(
                    inst.name, status,
                    None if incumbent_schedule is None else int(incumbent_value),
                    incumbent_schedule, expanded, generated, duplicates,
                    max_open, runtime, wall_time, incumbent_updates,
                    pruned_bound, pruned_infeasible, pruned_dominance,
                    search_strategy,
                    root.lb, frontier.min_lb(),
                    None if math.isinf(incumbent_value) else int(incumbent_value),
                    tree_nodes_popped, track_duplicates,
                    time.process_time() - cpu_t0
                ), relaxation_value, relaxation)

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
                incumbent_schedule = [int(x) for x in reconstruct_starts(inst, node)]
                incumbent_updates += 1
            continue

        if max_nodes is not None and expanded >= max_nodes:
            status = "node_limit_feasible" if incumbent_schedule is not None else "node_limit"
            return _with_relaxation_bound(BBSearchResult(inst.name, status,
                                  None if incumbent_schedule is None else int(incumbent_value),
                                  incumbent_schedule, expanded, generated, duplicates,
                                  max_open, runtime, wall_time, incumbent_updates,
                                  pruned_bound, pruned_infeasible,
                                  pruned_dominance, search_strategy,
                                  # The popped node is no longer in the frontier; include it so
                                  # the reported bound remains a proven lower bound.
                                  root.lb, min(frontier.min_lb(), node.lb),
                                  None if math.isinf(incumbent_value) else int(incumbent_value),
                                      tree_nodes_popped, track_duplicates,
                                      time.process_time() - cpu_t0),
                                          relaxation_value, relaxation)

        expanded += 1

        # Convert the partial schedule to the A* state once for this expansion.
        # The same state is then reused both to obtain selectable jobs and to
        # compute each child transition.
        node_state = node_to_state(inst, node)
        jobs = selectable_jobs_from_state(inst, node_state)
        node_starts = reconstruct_starts(inst, node) if active_schedule else None

        # Deterministic order.  For plain DFS, pushing in reverse makes the
        # smallest job index be explored first.  The locally best-bound DFS
        # variant orders the surviving children below, after their bounds are
        # available.
        if search_strategy == "depth_first":
            jobs_to_push = list(reversed(jobs))
        else:
            jobs_to_push = jobs

        locally_ordered_children = []
        for j in jobs_to_push:
            child, child_state = make_child_node_and_state_from_state(
                inst, node, node_state, next_node_id, j
            )
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

            if (active_schedule and
                    can_left_shift_job(inst, node.scheduled, node_starts, j,
                                       child.job_start)):
                # Count active-schedule cuts with the other dominance cuts so
                # the CSV schema stays compatible with in-progress batches.
                pruned_dominance += 1
                continue

            generated += 1
            if track_duplicates:
                t_dup0 = time.perf_counter()
                skey = child_state.key()
                if skey in seen_state_keys:
                    duplicates += 1
                else:
                    seen_state_keys.add(skey)
                dup_check_time += time.perf_counter() - t_dup0

            # The inexpensive state-consistent CP bound is already stored in
            # child.lb.  Do not invoke Gurobi for a node that this bound can
            # prune by itself.
            if child.lb >= incumbent_value:
                pruned_bound += 1
                continue

            # The declared horizon bounds the time-indexed search domain.
            # Without a stored incumbent at exactly the horizon, equality
            # cannot be discarded; a strictly larger integer lower bound can.
            if child.lb > search_horizon:
                pruned_bound += 1
                continue

            if relaxation is not None:
                remaining_time = None
                if time_limit is not None:
                    remaining_time = max(0.0, time_limit -
                                         (time.perf_counter() - t0))
                child_relaxation, child_relaxation_schedule = relaxation.solve(
                    child, remaining_time, incumbent_value)
                if child_relaxation is None:
                    wall_time = time.perf_counter() - t0
                    runtime = wall_time - dup_check_time
                    status = ("time_limit_feasible"
                              if incumbent_schedule is not None else
                              "time_limit")
                    return _with_relaxation_bound(BBSearchResult(
                        inst.name, status,
                        None if incumbent_schedule is None else
                        int(incumbent_value),
                        incumbent_schedule, expanded, generated, duplicates,
                        max_open, runtime, wall_time, incumbent_updates,
                        pruned_bound, pruned_infeasible, pruned_dominance,
                        # Timed out mid-expansion: this node's remaining
                        # children are unevaluated, so its own bound still
                        # limits the proven lower bound.
                        search_strategy, root.lb,
                        min(frontier.min_lb(), node.lb),
                        None if math.isinf(incumbent_value) else
                        int(incumbent_value),
                        tree_nodes_popped, track_duplicates,
                        time.process_time() - cpu_t0
                    ), relaxation_value, relaxation)
                child_cp_lb = child.lb
                child.lb = relaxation.proof_bound(
                    child_cp_lb, child_relaxation)
                child.order_lb = max(
                    child_cp_lb,
                    relaxation.ordering_bound(child_relaxation)
                )
                if child_relaxation_schedule is not None:
                    validate_schedule(inst, child_relaxation_schedule)
                    child_makespan = max(
                        child_relaxation_schedule[k] + inst.durations[k]
                        for k in range(inst.n)
                    )
                    if child_makespan < incumbent_value:
                        incumbent_value = child_makespan
                        incumbent_schedule = child_relaxation_schedule
                        incumbent_updates += 1
                        relaxation.incumbent_updates += 1

                if child.lb >= incumbent_value:
                    pruned_bound += 1
                    continue

            if search_strategy == "depth_first_best_lb":
                locally_ordered_children.append(child)
            else:
                frontier.push(child)

        if search_strategy == "depth_first_best_lb":
            # DepthFirstFrontier is LIFO, so push in reverse preference order.
            # Match best-first's main preferences locally: lower bound first,
            # then larger g; use the job index as a deterministic final tie.
            locally_ordered_children.sort(
                key=lambda candidate: (
                    candidate.order_lb, -candidate.g, candidate.job
                )
            )
            for child in reversed(locally_ordered_children):
                frontier.push(child)

        max_open = max(max_open, len(frontier))

    wall_time = time.perf_counter() - t0
    runtime = wall_time - dup_check_time
    if incumbent_schedule is None:
        return _with_relaxation_bound(BBSearchResult(inst.name, "infeasible", None, None, expanded,
                              generated, duplicates, max_open, runtime, wall_time,
                              incumbent_updates, pruned_bound, pruned_infeasible,
                              pruned_dominance, search_strategy, root.lb,
                              math.inf, None,
                              tree_nodes_popped, track_duplicates,
                              time.process_time() - cpu_t0),
                                  relaxation_value, relaxation)

    return _with_relaxation_bound(BBSearchResult(inst.name, "optimal", int(incumbent_value),
                          incumbent_schedule, expanded, generated, duplicates,
                          max_open, runtime, wall_time, incumbent_updates, pruned_bound,
                          pruned_infeasible, pruned_dominance, search_strategy,
                          root.lb,
                          frontier.min_lb(), int(incumbent_value),
                             tree_nodes_popped, track_duplicates,
                             time.process_time() - cpu_t0),
                                 relaxation_value, relaxation)

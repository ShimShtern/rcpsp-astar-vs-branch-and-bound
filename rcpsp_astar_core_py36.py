#!/usr/bin/env python3
"""Core RCPSP A* solver logic.

This module intentionally contains no command-line printing, CSV output,
PSPLIB design-class lookup, or known-optimum lookup.  It only contains:

* RCPSP data objects
* PSPLIB .sm parsing
* state representation
* state transition logic
* the state-consistent critical-path heuristic
* A* with depth-first tie-breaking
* schedule validation

Use ``run_rcpsp_astar.py`` as the command-line driver.
"""
import heapq
import math
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


# ----------------------------- data objects -----------------------------

class RCPSPInstance(object):
    """Single-mode RCPSP instance.

    Jobs use Python 0-based indices. PSPLIB j30 instances usually contain
    32 jobs: dummy source, 30 real jobs, and dummy sink.
    """

    def __init__(self, name, durations, demands, capacities, successors,
                 predecessors, pred_mask, topo, horizon=None):
        self.name = name
        self.durations = durations
        self.demands = demands
        self.capacities = capacities
        self.successors = successors
        self.predecessors = predecessors
        self.pred_mask = pred_mask
        self.topo = topo
        self.horizon = horizon

    @property
    def n(self):
        return len(self.durations)

    @property
    def all_mask(self):
        return (1 << self.n) - 1


class State(object):
    """Timed state used by the A* search.

    ``g`` is the current system time.
    ``completed`` is a bit mask of jobs completed no later than ``g``.
    ``ongoing`` is a sorted tuple ``(job, residual_duration)``.
    """

    __slots__ = ("g", "completed", "ongoing")

    def __init__(self, g, completed, ongoing):
        self.g = g
        self.completed = completed
        self.ongoing = ongoing

    def key(self):
        return (self.g, self.completed, self.ongoing)

    def ongoing_mask(self):
        mask = 0
        for j, _ in self.ongoing:
            mask |= 1 << j
        return mask

    def depth(self):
        """Number of activities that have already been started."""
        return bin(self.completed).count("1") + len(self.ongoing)


class SearchResult(object):
    def __init__(self, instance, status, makespan, schedule,
                 expanded, generated, duplicates, max_open, runtime,
                 pruned_dominance=0, pruned_state_dominance=0,
                 pruned_active_schedule=0, final_lower_bound=None,
                 cpu_time=None):
        self.instance = instance
        self.status = status
        self.makespan = makespan
        self.schedule = schedule
        self.expanded = expanded
        self.generated = generated
        self.duplicates = duplicates
        self.max_open = max_open
        self.runtime = runtime
        self.pruned_dominance = pruned_dominance
        self.pruned_state_dominance = pruned_state_dominance
        self.pruned_active_schedule = pruned_active_schedule
        self.final_lower_bound = final_lower_bound
        self.cpu_time = cpu_time


def current_rss_bytes():
    """Return current resident memory on Linux, or ``None`` if unavailable."""
    try:
        with open("/proc/self/statm", "r") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (IOError, OSError, ValueError, IndexError):
        return None


# ----------------------------- PSPLIB parser -----------------------------

_INT_RE = re.compile(r"-?\d+")


def ints(line: str) -> List[int]:
    return [int(x) for x in _INT_RE.findall(line)]


def find_section(lines: Sequence[str], title: str) -> int:
    title = title.upper()
    for i, line in enumerate(lines):
        if line.strip().upper().startswith(title):
            return i
    raise ValueError(f"section not found: {title}")


def parse_horizon(lines: Sequence[str]) -> Optional[int]:
    for line in lines:
        if "horizon" in line.lower():
            vals = ints(line)
            if vals:
                return vals[0]
    return None


def parse_psplib_sm(path):
    """Read a single-mode PSPLIB ``.sm`` file."""
    path = Path(path)
    lines = path.read_text(errors="replace").splitlines()

    prec_i = find_section(lines, "PRECEDENCE RELATIONS")
    req_i = find_section(lines, "REQUESTS/DURATIONS")
    avail_i = find_section(lines, "RESOURCEAVAILABILITIES")
    horizon = parse_horizon(lines)

    capacities: Optional[Tuple[int, ...]] = None
    for line in lines[avail_i + 1 :]:
        s = line.strip()
        if s and re.fullmatch(r"[-+\d\s]+", s):
            capacities = tuple(ints(s))
            break
    if not capacities:
        raise ValueError(f"{path}: could not parse resource capacities")
    num_resources = len(capacities)

    n = None
    for line in lines:
        if "jobs" in line.lower() and "incl" in line.lower():
            vals = ints(line)
            if vals:
                n = vals[0]
                break

    successors_tmp: Dict[int, List[int]] = {}
    max_job = 0
    for line in lines[prec_i + 1 : req_i]:
        vals = ints(line)
        if len(vals) >= 3:
            job = vals[0] - 1
            if job < 0:
                continue
            nsucc = vals[2]
            succs = [x - 1 for x in vals[3 : 3 + nsucc]]
            successors_tmp[job] = succs
            max_job = max(max_job, job + 1, *(s + 1 for s in succs))
    if n is None:
        n = max_job

    successors = [[] for _ in range(n)]
    for j, succs in successors_tmp.items():
        if 0 <= j < n:
            successors[j] = [s for s in succs if 0 <= s < n]

    durations = [0] * n
    demands = [[0] * num_resources for _ in range(n)]
    seen_req = set()
    for line in lines[req_i + 1 : avail_i]:
        vals = ints(line)
        # Single-mode rows: job mode duration r1 ... rm
        if len(vals) >= 3 + num_resources and 1 <= vals[0] <= n:
            job = vals[0] - 1
            durations[job] = vals[2]
            demands[job] = vals[3 : 3 + num_resources]
            seen_req.add(job)
    if len(seen_req) != n:
        missing = sorted(set(range(n)) - seen_req)
        raise ValueError(f"{path}: missing request/duration rows for jobs {missing}")

    predecessors = [[] for _ in range(n)]
    for j, succs in enumerate(successors):
        for s in succs:
            predecessors[s].append(j)

    for j, req in enumerate(demands):
        if any(req[k] > capacities[k] for k in range(num_resources)):
            raise ValueError(f"{path}: job {j + 1} demand exceeds capacity")

    topo = topological_order(successors, predecessors)
    pred_mask = tuple(sum(1 << p for p in predecessors[j]) for j in range(n))

    return RCPSPInstance(
        name=path.stem,
        durations=tuple(durations),
        demands=tuple(tuple(r) for r in demands),
        capacities=capacities,
        successors=tuple(tuple(s) for s in successors),
        predecessors=tuple(tuple(p) for p in predecessors),
        pred_mask=pred_mask,
        topo=tuple(topo),
        horizon=horizon,
    )


def topological_order(
    successors: Sequence[Sequence[int]],
    predecessors: Sequence[Sequence[int]],
) -> List[int]:
    indeg = [len(p) for p in predecessors]
    q = [j for j, d in enumerate(indeg) if d == 0]
    order = []
    for j in q:
        order.append(j)
        for s in successors[j]:
            indeg[s] -= 1
            if indeg[s] == 0:
                q.append(s)
    if len(order) != len(successors):
        raise ValueError("precedence graph has a cycle")
    return order


# --------------------------- lower bounds/helpers ------------------------


def critical_path_lb(inst: RCPSPInstance) -> int:
    """Critical-path lower bound from the initial state, ignoring resources."""
    comp = [0] * inst.n
    for j in inst.topo:
        est = 0
        for p in inst.predecessors[j]:
            est = max(est, comp[p])
        comp[j] = est + inst.durations[j]
    return comp[inst.n - 1]


def resource_work_lb(inst: RCPSPInstance) -> int:
    """Single-resource workload lower bound."""
    lbs = []
    for k, cap in enumerate(inst.capacities):
        work = sum(inst.durations[j] * inst.demands[j][k] for j in range(inst.n))
        lbs.append(math.ceil(work / cap) if cap else 0)
    return max(lbs) if lbs else 0


# ----------------------------- A* components -----------------------------


def cp_state_consistent(inst: RCPSPInstance, state: State) -> int:
    """State-consistent critical-path heuristic ``h(s)``.

    Completed jobs are available at ``g``.  Ongoing jobs finish at
    ``g + residual``.  Unstarted jobs are scheduled as early as precedence
    allows while future resource constraints are ignored.  The return value is
    the remaining-time lower bound ``C_CP(s) - g``.
    """
    g = state.g
    comp_time: List[Optional[int]] = [None] * inst.n

    for j in range(inst.n):
        if state.completed & (1 << j):
            comp_time[j] = g
    for j, rem in state.ongoing:
        comp_time[j] = g + rem

    for j in inst.topo:
        if comp_time[j] is not None:
            continue
        est = g
        for p in inst.predecessors[j]:
            est = max(est, comp_time[p] if comp_time[p] is not None else g)
        comp_time[j] = est + inst.durations[j]

    return max(0, int(comp_time[inst.n - 1]) - g)


def selectable_jobs(inst: RCPSPInstance, state: State) -> List[int]:
    """Jobs whose predecessors are completed or currently ongoing."""
    started = state.completed | state.ongoing_mask()
    unstarted = inst.all_mask & ~started
    jobs = []
    x = unstarted
    while x:
        lsb = x & -x
        j = lsb.bit_length() - 1
        if inst.pred_mask[j] & ~started == 0:
            jobs.append(j)
        x ^= lsb
    return jobs


def transition(inst: RCPSPInstance, state: State, job: int) -> Tuple[State, int]:
    """Start ``job`` at its earliest feasible time.

    Returns ``(successor_state, start_time_of_job)``.
    """
    g = state.g
    ongoing = state.ongoing
    finish_time = {i: g + rem for i, rem in ongoing}

    earliest = g
    for p in inst.predecessors[job]:
        if p in finish_time:
            earliest = max(earliest, finish_time[p])

    # Resource availability changes only when an ongoing job finishes.
    candidates = [earliest] + sorted(t for t in set(finish_time.values()) if t >= earliest)
    start = candidates[-1] if candidates else earliest
    for t in candidates:
        used = [0] * len(inst.capacities)
        for i, _ in ongoing:
            if finish_time[i] > t:
                req = inst.demands[i]
                for k in range(len(used)):
                    used[k] += req[k]
        if all(inst.demands[job][k] <= inst.capacities[k] - used[k]
               for k in range(len(used))):
            start = t
            break

    delta = start - g
    completed = state.completed
    new_ongoing: List[Tuple[int, int]] = []
    for i, rem in ongoing:
        if rem <= delta:
            completed |= 1 << i
        else:
            new_ongoing.append((i, rem - delta))

    if inst.durations[job] == 0:
        completed |= 1 << job
    else:
        new_ongoing.append((job, inst.durations[job]))

    new_ongoing.sort()
    return State(start, completed, tuple(new_ongoing)), start


def reconstruct_partial_starts(
    inst: RCPSPInstance,
    parent: Dict[Tuple, Tuple[Tuple, int, int]],
    state_key: Tuple,
) -> Tuple[int, ...]:
    """Reconstruct start times for the retained path to ``state_key``."""
    starts = [-1] * inst.n
    current = state_key
    while current in parent:
        previous, job, start = parent[current]
        starts[job] = start
        current = previous
    return tuple(starts)


def _resources_allow_interval_in_partial_schedule(
    inst: RCPSPInstance,
    starts: Tuple[int, ...],
    started_mask: int,
    job: int,
    start: int,
) -> bool:
    """Return whether ``job`` fits at ``start`` beside fixed activities."""
    duration = inst.durations[job]
    if duration == 0:
        return True

    finish = start + duration
    events = {start}
    intervals = []
    for other in range(inst.n):
        if not (started_mask & (1 << other)):
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
        if any(
            used[resource] + inst.demands[job][resource]
            > inst.capacities[resource]
            for resource in range(len(used))
        ):
            return False
    return True


def can_left_shift_inserted_job(
    inst: RCPSPInstance,
    starts: Tuple[int, ...],
    started_mask: int,
    job: int,
    proposed_start: int,
) -> bool:
    """Return whether a newly inserted job can start before its proposal.

    All activities in ``started_mask`` retain their reconstructed start times.
    A true result is therefore a direct left-shift certificate for the new
    child, rather than a comparison with another generated A* state.
    """
    precedence_start = 0
    for predecessor in inst.predecessors[job]:
        predecessor_start = starts[predecessor]
        if predecessor_start < 0:
            return False
        precedence_start = max(
            precedence_start,
            predecessor_start + inst.durations[predecessor],
        )

    if precedence_start >= proposed_start:
        return False

    candidates = {precedence_start}
    for other in range(inst.n):
        if not (started_mask & (1 << other)):
            continue
        other_start = starts[other]
        if other_start < 0:
            continue
        other_finish = other_start + inst.durations[other]
        if precedence_start <= other_finish < proposed_start:
            candidates.add(other_finish)

    for candidate_start in sorted(candidates):
        if _resources_allow_interval_in_partial_schedule(
            inst,
            starts,
            started_mask,
            job,
            candidate_start,
        ):
            return True
    return False


def started_mask_from_key(state_key):
    """Return the completed-or-ongoing job mask of a state key."""
    _g, completed, ongoing = state_key
    started = completed
    for job, _remaining in ongoing:
        started |= 1 << job
    return started


def state_progress_dominates(dominating_key, candidate_key):
    """Return whether ``dominating_key`` progress-dominates ``candidate_key``.

    The states must have identical started-job sets.  The dominating state
    must be no later, must have completed a superset of the candidate's
    completed jobs, and must have no greater remaining duration for every job
    that is ongoing in both states.
    """
    dominating_g, dominating_completed, dominating_ongoing = dominating_key
    candidate_g, candidate_completed, candidate_ongoing = candidate_key

    if dominating_g > candidate_g:
        return False
    if started_mask_from_key(dominating_key) != started_mask_from_key(candidate_key):
        return False
    if candidate_completed & ~dominating_completed:
        return False

    candidate_remaining = dict(candidate_ongoing)
    for job, remaining in dominating_ongoing:
        other_remaining = candidate_remaining.get(job)
        if other_remaining is None or remaining > other_remaining:
            return False
    return True


def update_progress_dominance_bucket(bucket, candidate_key):
    """Check a candidate against, then update, one started-mask skyline.

    Returns true when an existing record dominates the candidate.  Otherwise,
    records dominated by the candidate are removed and the candidate is added.
    """
    for existing_key in bucket:
        if state_progress_dominates(existing_key, candidate_key):
            return True

    survivors = [
        existing_key for existing_key in bucket
        if not state_progress_dominates(candidate_key, existing_key)
    ]
    survivors.append(candidate_key)
    bucket[:] = survivors
    return False


def astar_depth_first_tie(
    inst: RCPSPInstance,
    max_expansions: Optional[int] = None,
    time_limit: Optional[float] = None,
    same_start_lexicographic: bool = False,
    max_memory_mb: Optional[float] = None,
    state_progress_dominance: bool = False,
    exact_active_schedule: bool = False,
) -> SearchResult:
    """Run exact A* with DFS-style tie-breaking among equal ``f`` values.

    Priority is ``(f, -g, -depth, -generation)``.  This preserves A*'s
    minimum-``f`` rule, while ties prefer deeper/newer states.  When
    ``same_start_lexicographic`` is enabled, consecutive independent jobs
    assigned the same start time must be started in increasing job-index
    order.  ``max_memory_mb`` is a soft current-RSS limit checked every 1,024
    expansions on Linux.  When ``state_progress_dominance`` is enabled, a
    successor is pruned if a previously generated state with the same set of
    started jobs is no later and is at least as advanced in completion and
    ongoing-job residual durations.  When ``exact_active_schedule`` is
    enabled, the retained path to each expanded state is reconstructed and a
    successor is pruned if its newly inserted job can be left-shifted while
    every previously scheduled job remains fixed.
    """
    t0 = time.perf_counter()
    cpu_t0 = time.process_time()
    memory_limit_bytes = None
    if max_memory_mb is not None:
        if max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be greater than zero")
        memory_limit_bytes = int(max_memory_mb * 1024 * 1024)

    start = State(g=0, completed=0, ongoing=())
    h0 = cp_state_consistent(inst, start)

    open_heap = []
    counter = 0
    heapq.heappush(open_heap, (h0, 0, 0, counter, start))

    open_seen = {start.key()}
    closed = set()
    parent: Dict[Tuple, Tuple[Tuple, int, int]] = {}
    progress_dominance_buckets = None
    if state_progress_dominance:
        start_key = start.key()
        progress_dominance_buckets = {
            started_mask_from_key(start_key): [start_key]
        }

    expanded = 0
    generated = 1
    duplicates = 0
    pruned_dominance = 0
    pruned_state_dominance = 0
    pruned_active_schedule = 0
    max_open = 1

    while open_heap:
        if time_limit is not None and time.perf_counter() - t0 >= time_limit:
            return SearchResult(inst.name, "time_limit", None, None,
                                expanded, generated, duplicates, max_open,
                                time.perf_counter() - t0,
                                pruned_dominance, pruned_state_dominance,
                                pruned_active_schedule,
                                final_lower_bound=open_heap[0][0],
                                cpu_time=time.process_time() - cpu_t0)

        if memory_limit_bytes is not None and expanded % 1024 == 0:
            rss_bytes = current_rss_bytes()
            if rss_bytes is not None and rss_bytes >= memory_limit_bytes:
                return SearchResult(inst.name, "memory_limit", None, None,
                                    expanded, generated, duplicates, max_open,
                                    time.perf_counter() - t0,
                                    pruned_dominance,
                                    pruned_state_dominance,
                                    pruned_active_schedule,
                                    final_lower_bound=open_heap[0][0],
                                    cpu_time=time.process_time() - cpu_t0)

        current_f, _, _, _, s = heapq.heappop(open_heap)
        skey = s.key()
        if skey in closed:
            continue
        open_seen.discard(skey)

        if s.completed == inst.all_mask and not s.ongoing:
            sched = reconstruct_schedule(inst, parent, skey)
            return SearchResult(inst.name, "optimal", s.g, sched,
                                expanded, generated, duplicates, max_open,
                                time.perf_counter() - t0,
                                pruned_dominance, pruned_state_dominance,
                                pruned_active_schedule,
                                final_lower_bound=s.g,
                                cpu_time=time.process_time() - cpu_t0)

        if max_expansions is not None and expanded >= max_expansions:
            return SearchResult(inst.name, "expansion_limit", None, None,
                                expanded, generated, duplicates, max_open,
                                time.perf_counter() - t0,
                                pruned_dominance, pruned_state_dominance,
                                pruned_active_schedule,
                                final_lower_bound=min(
                                    current_f,
                                    open_heap[0][0] if open_heap else current_f,
                                ),
                                cpu_time=time.process_time() - cpu_t0)

        closed.add(skey)
        expanded += 1

        parent_entry = parent.get(skey)
        retained_starts = None
        retained_started_mask = None
        if exact_active_schedule:
            retained_starts = reconstruct_partial_starts(inst, parent, skey)
            retained_started_mask = s.completed | s.ongoing_mask()

        for j in reversed(selectable_jobs(inst, s)):
            ns, st = transition(inst, s, j)
            if same_start_lexicographic and parent_entry is not None:
                _previous_key, last_job, last_start = parent_entry
                parent_precedes_child = (
                    inst.pred_mask[j] & (1 << last_job)
                ) != 0
                if (st == last_start and j < last_job and
                        not parent_precedes_child):
                    pruned_dominance += 1
                    continue

            if exact_active_schedule and can_left_shift_inserted_job(
                inst,
                retained_starts,
                retained_started_mask,
                j,
                st,
            ):
                pruned_active_schedule += 1
                continue

            nk = ns.key()
            if nk in closed or nk in open_seen:
                duplicates += 1
                continue

            if state_progress_dominance:
                started_mask = started_mask_from_key(nk)
                bucket = progress_dominance_buckets.setdefault(started_mask, [])
                if update_progress_dominance_bucket(bucket, nk):
                    pruned_state_dominance += 1
                    continue

            h = cp_state_consistent(inst, ns)
            f = ns.g + h
            counter += 1
            heapq.heappush(open_heap, (f, -ns.g, -ns.depth(), -counter, ns))
            open_seen.add(nk)
            parent[nk] = (skey, j, st)
            generated += 1

        max_open = max(max_open, len(open_heap))

    return SearchResult(inst.name, "infeasible", None, None,
                        expanded, generated, duplicates, max_open,
                        time.perf_counter() - t0, pruned_dominance,
                        pruned_state_dominance, pruned_active_schedule,
                        cpu_time=time.process_time() - cpu_t0)


def reconstruct_schedule(
    inst: RCPSPInstance,
    parent: Dict[Tuple, Tuple[Tuple, int, int]],
    goal_key: Tuple,
) -> List[int]:
    starts: List[Optional[int]] = [None] * inst.n
    cur = goal_key
    while cur in parent:
        prev, job, st = parent[cur]
        starts[job] = st
        cur = prev
    if any(x is None for x in starts):
        missing = [i for i, x in enumerate(starts) if x is None]
        raise RuntimeError(f"missing start times for jobs {missing}")
    return [int(x) for x in starts]


def validate_schedule(inst: RCPSPInstance, starts: Sequence[int]) -> None:
    """Raise ``ValueError`` if the schedule violates precedence/resources."""
    n, m = inst.n, len(inst.capacities)
    if len(starts) != n or any(s is None for s in starts):
        raise ValueError("incomplete schedule")

    for i, succs in enumerate(inst.successors):
        ci = starts[i] + inst.durations[i]
        for j in succs:
            if ci > starts[j]:
                raise ValueError(f"precedence violation: {i + 1}->{j + 1}")

    makespan = max(starts[j] + inst.durations[j] for j in range(n))
    for t in range(makespan + 1):
        used = [0] * m
        for j in range(n):
            if starts[j] <= t < starts[j] + inst.durations[j]:
                for k in range(m):
                    used[k] += inst.demands[j][k]
        for k in range(m):
            if used[k] > inst.capacities[k]:
                raise ValueError(f"resource {k + 1} violation at t={t}: {used[k]}>{inst.capacities[k]}")

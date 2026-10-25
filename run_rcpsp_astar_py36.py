#!/usr/bin/env python3
"""Command-line runner for the RCPSP A* core module.

This file handles instance collection, PSPLIB metadata/classification,
known-optimum loading, terminal printing, and CSV writing.  The actual A*
state-space logic is in ``rcpsp_astar_core.py``.
"""
import argparse
import csv
import os
import re
import resource
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

from rcpsp_astar_core_py36 import (
    RCPSPInstance,
    SearchResult,
    astar_depth_first_tie,
    critical_path_lb,
    parse_psplib_sm,
    resource_work_lb,
    validate_schedule,
)

SCRIPT_VERSION = "2026-07-08-py36-streaming-csv"

_INT_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
# PSPLIB instance names: j301_1, j3010_1, j3048_10, etc.
_NAME_RE = re.compile(r"^(j(?:30|60|90|120))(\d{1,3})[_\-]?(\d+)$", re.IGNORECASE)


def floats(line: str) -> List[float]:
    return [float(x) for x in _FLOAT_RE.findall(line)]


# -------------------------- instance descriptors -------------------------

def default_par_table() -> Dict[int, Tuple[float, float, float]]:
    """Fallback PSPLIB/ProGen design classes for the 48 parameter cells."""
    table: Dict[int, Tuple[float, float, float]] = {}
    cell = 1
    for nc in (1.5, 1.8, 2.1):
        for rf in (0.25, 0.50, 0.75, 1.00):
            for rs in (0.25, 0.50, 0.75, 1.00):
                table[cell] = (nc, rf, rs)
                cell += 1
    return table


def parse_instance_name(name: str) -> Tuple[str, Optional[int], Optional[int]]:
    """Return ``(set_name, parameter_cell, replicate)``.

    Examples:
    ``j301_1`` -> ``(j30, 1, 1)``;
    ``j3010_1`` -> ``(j30, 10, 1)``;
    ``j3048_10`` -> ``(j30, 48, 10)``.
    """
    m = _NAME_RE.match(Path(name).stem.lower())
    if not m:
        return "", None, None
    return m.group(1), int(m.group(2)), int(m.group(3))


def load_par_table(path: Optional[str]) -> Dict[int, Tuple[float, float, float]]:
    """Load ``par.sm`` if available; otherwise use the standard fallback table."""
    fallback = default_par_table()
    candidates: List[Path] = []
    if path:
        candidates.append(Path(path))
    candidates.append(Path(__file__).resolve().with_name("par.sm"))

    for p in candidates:
        if not p.exists():
            continue
        table: Dict[int, Tuple[float, float, float]] = {}
        for line in p.read_text(errors="replace").splitlines():
            vals = floats(line)
            if len(vals) >= 4:
                cell = int(vals[0])
                if 1 <= cell <= 200:
                    table[cell] = (float(vals[1]), float(vals[2]), float(vals[3]))
        if table:
            fallback.update(table)
            return fallback
    return fallback


def instance_attributes(inst: RCPSPInstance, par_table: Dict[int, Tuple[float, float, float]]) -> Dict[str, object]:
    psplib_set, cell, rep = parse_instance_name(inst.name)
    nc = rf_class = rs_class = ""
    if cell is not None and cell in par_table:
        nc, rf_class, rs_class = par_table[cell]

    real_jobs = max(0, inst.n - 2)
    arcs = sum(len(s) for s in inst.successors)
    possible_arcs = inst.n * (inst.n - 1) / 2
    prec_density = arcs / possible_arcs if possible_arcs else 0.0
    num_resources = len(inst.capacities)
    real_pairs = max(1, real_jobs * num_resources)
    positive_pairs = sum(
        1
        for j in range(1, inst.n - 1)
        for k in range(num_resources)
        if inst.demands[j][k] > 0
    )
    actual_rf = positive_pairs / real_pairs
    sum_duration = sum(inst.durations)
    avg_duration = sum_duration / real_jobs if real_jobs else 0.0

    return {
        "instance": inst.name,
        "psplib_set": psplib_set,
        "param_cell": cell if cell is not None else "",
        "replicate": rep if rep is not None else "",
        "nc": nc,
        "rf_class": rf_class,
        "rs_class": rs_class,
        "jobs": inst.n,
        "real_jobs": real_jobs,
        "num_resources": num_resources,
        "horizon": inst.horizon if inst.horizon is not None else "",
        "sum_duration": sum_duration,
        "avg_duration": f"{avg_duration:.3f}",
        "precedence_arcs": arcs,
        "precedence_density": f"{prec_density:.6f}",
        "capacity_sum": sum(inst.capacities),
        "actual_rf": f"{actual_rf:.6f}",
        "cp_lb": critical_path_lb(inst),
        "res_lb": resource_work_lb(inst),
    }


# --------------------------- known optima loader -------------------------

def normalize_name(name: str) -> str:
    return Path(name.strip()).stem.lower()


def infer_set_from_opt_path(path, psplib_set=""):
    if psplib_set:
        return psplib_set.lower()
    stem = Path(path).stem.lower()
    m = re.search(r"(j(?:30|60|90|120))", stem)
    return m.group(1) if m else ""


def parse_opt_file(path, psplib_set=""):
    """Parse known optimum files.

    Handles both explicit rows such as ``j301_1 43`` and official PSPLIB rows
    such as ``1 1 43 0.30`` in ``j30opt.sm``.
    """
    opt: Dict[str, int] = {}
    set_name = infer_set_from_opt_path(path, psplib_set)
    inst_pat = re.compile(r"j(?:30|60|90|120)\d{1,3}[_\-]?\d+", re.IGNORECASE)

    for raw in Path(path).read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("=") or line.startswith("-"):
            continue

        m = inst_pat.search(line)
        if m:
            vals = floats(line[m.end():])
            if vals:
                opt[normalize_name(m.group(0))] = int(round(vals[0]))
            continue

        vals = floats(line)
        if set_name and len(vals) >= 3:
            cell_f, rep_f, ms_f = vals[0], vals[1], vals[2]
            cell, rep, ms = int(round(cell_f)), int(round(rep_f)), int(round(ms_f))
            if (abs(cell - cell_f) < 1e-9 and abs(rep - rep_f) < 1e-9
                    and 1 <= cell <= 200 and 1 <= rep <= 100 and ms >= 0):
                opt[f"{set_name}{cell}_{rep}".lower()] = ms

    return opt


def load_optima(path: Optional[str], psplib_set: str, allow_download: bool) -> Dict[str, int]:
    """Load known optima from an explicit file or files beside this script."""
    candidates: List[Path] = []
    if path:
        candidates.append(Path(path))
    script_dir = Path(__file__).resolve().parent
    if psplib_set:
        candidates.extend([
            script_dir / f"{psplib_set}opt.sm",
            script_dir / f"{psplib_set}.opt",
            script_dir / f"opt_{psplib_set}.sm",
        ])
    candidates.extend([script_dir / "j30opt.sm", script_dir / "j30.opt"])

    for p in candidates:
        if p.exists():
            opt = parse_opt_file(p, psplib_set)
            if opt:
                return opt

    if allow_download and psplib_set:
        tmp = Path(tempfile.mkdtemp(prefix="psplib_opt_")) / f"{psplib_set}opt.sm"
        urls = [
            f"https://www.om-db.wi.tum.de/psplib/files/{psplib_set}opt.sm",
            f"https://www.om-db.wi.tum.de/psplib/files/{psplib_set}.opt",
        ]
        for url in urls:
            try:
                urllib.request.urlretrieve(url, str(tmp))
                opt = parse_opt_file(tmp, psplib_set)
                if opt:
                    return opt
            except Exception:
                pass
    return {}


# ----------------------------- input helpers -----------------------------

def collect_sm_files(inputs: Sequence[str], prefix: str = "j30") -> List[Path]:
    """Collect ``.sm`` files from files, directories, zip files, or URLs."""
    out: List[Path] = []
    tmp_root = Path(tempfile.mkdtemp(prefix="psplib_"))

    def add_path(p: Path) -> None:
        if p.is_dir():
            out.extend(sorted(x for x in p.rglob("*.sm")
                              if not prefix or x.stem.lower().startswith(prefix.lower())))
        elif p.suffix.lower() == ".zip":
            dest = tmp_root / p.stem
            with zipfile.ZipFile(str(p)) as zf:
                zf.extractall(dest)
            add_path(dest)
        elif p.suffix.lower() == ".sm":
            if not prefix or p.stem.lower().startswith(prefix.lower()):
                out.append(p)

    for item in inputs:
        if item.startswith(("http://", "https://")):
            parsed = urlparse(item)
            qs = parse_qs(parsed.query)
            if parsed.path.endswith("getdata.php") and qs.get("mode") == ["sm"] and prefix:
                item = f"{parsed.scheme}://{parsed.netloc}/psplib/download_dataset.php?" + urlencode({
                    "format": "zip", "mode": "sm", "set": prefix
                })

            fname = tmp_root / "download"
            urllib.request.urlretrieve(item, str(fname))
            if zipfile.is_zipfile(str(fname)):
                dest = tmp_root / "download_unzipped"
                with zipfile.ZipFile(str(fname)) as zf:
                    zf.extractall(dest)
                add_path(dest)
            else:
                sm = tmp_root / "download.sm"
                fname.rename(sm)
                add_path(sm)
        else:
            add_path(Path(item))

    return sorted(dict.fromkeys(out))


# ----------------------------- output helpers ----------------------------

FIELDNAMES = [
    "instance", "psplib_set", "param_cell", "replicate", "nc", "rf_class", "rs_class",
    "jobs", "real_jobs", "num_resources", "horizon", "sum_duration", "avg_duration",
    "precedence_arcs", "precedence_density", "capacity_sum", "actual_rf", "cp_lb", "res_lb",
    "known_optimum", "known_gap", "matches_known_opt",
    "status", "makespan", "expanded", "generated", "duplicates",
    "pruned_dominance", "pruned_state_dominance", "pruned_active_schedule",
    "max_open", "runtime",
    "max_rss_mib",
]

# Keep FIELDNAMES as the shared/base schema imported by the B&B runner.  The
# final frontier lower bound is an A*-specific output field.
ASTAR_OUTPUT_FIELDNAMES = list(FIELDNAMES)
ASTAR_OUTPUT_FIELDNAMES.insert(
    ASTAR_OUTPUT_FIELDNAMES.index("expanded"), "final_lower_bound"
)
ASTAR_OUTPUT_FIELDNAMES.insert(
    ASTAR_OUTPUT_FIELDNAMES.index("max_rss_mib"), "cpu_time"
)


def peak_rss_mib() -> float:
    """Return this process's peak resident memory in MiB on Linux."""
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0


def make_row(inst: RCPSPInstance, res: SearchResult, attrs: Dict[str, object], optima: Dict[str, int]) -> Dict[str, object]:
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
        "final_lower_bound": ("" if res.final_lower_bound is None else
                              res.final_lower_bound),
        "expanded": res.expanded,
        "generated": res.generated,
        "duplicates": res.duplicates,
        "pruned_dominance": res.pruned_dominance,
        "pruned_state_dominance": res.pruned_state_dominance,
        "pruned_active_schedule": res.pruned_active_schedule,
        "max_open": res.max_open,
        "runtime": f"{res.runtime:.6f}",
        "cpu_time": ("" if res.cpu_time is None else f"{res.cpu_time:.6f}"),
        "max_rss_mib": f"{peak_rss_mib():.3f}",
    })
    return {k: row.get(k, "") for k in ASTAR_OUTPUT_FIELDNAMES}


def print_pretty(row: Dict[str, object]) -> None:
    print(f"\n[{row['instance']}]")
    print("  classification: " + ", ".join(f"{k}={row[k]}" for k in [
        "psplib_set", "param_cell", "replicate", "nc", "rf_class", "rs_class", "known_optimum"
    ]))
    print("  size/structure: " + ", ".join(f"{k}={row[k]}" for k in [
        "jobs", "real_jobs", "num_resources", "horizon", "precedence_arcs", "precedence_density"
    ]))
    print("  resources/bounds: " + ", ".join(f"{k}={row[k]}" for k in [
        "capacity_sum", "actual_rf", "cp_lb", "res_lb"
    ]))
    print("  search: " + ", ".join(f"{k}={row[k]}" for k in [
        "status", "makespan", "final_lower_bound", "known_gap",
        "matches_known_opt", "expanded",
        "generated", "duplicates", "pruned_dominance",
        "pruned_state_dominance", "pruned_active_schedule", "max_open",
        "runtime", "cpu_time", "max_rss_mib"
    ]))


# ----------------------------- command line -----------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Run A* with depth-first tie-breaking for PSPLIB RCPSP .sm files")
    ap.add_argument("--version", action="version", version=SCRIPT_VERSION)
    ap.add_argument("inputs", nargs="+", help=".sm files, directories, .zip files, or URL to PSPLIB data")
    ap.add_argument("--prefix", default="j30", help="only run files whose basename starts with this prefix; use '' for all")
    ap.add_argument("--limit", type=int, default=None, help="maximum number of instances to run")
    ap.add_argument("--time-limit", type=float, default=None, help="seconds per instance")
    ap.add_argument("--max-expansions", type=int, default=None, help="max expanded states per instance")
    ap.add_argument("--max-memory-mb", type=float, default=None,
                    help="soft per-process RSS limit in MiB; stops cleanly when reached (Linux)")
    ap.add_argument("--same-start-lexicographic", action="store_true",
                    help="prune non-lexicographic permutations of independent jobs with equal start times")
    ap.add_argument("--state-progress-dominance", action="store_true",
                    help="prune states dominated by earlier progress with the same started-job set")
    ap.add_argument("--exact-active-schedule", action="store_true",
                    help="prune a child when its inserted job can be left-shifted in the reconstructed partial schedule")
    ap.add_argument("--csv", default=None, help="optional output CSV path")
    ap.add_argument("--pretty", action="store_true", help="also print a readable multi-line summary per instance")
    ap.add_argument("--opt-file", default=None, help="known optimum file, e.g., j30opt.sm; auto-detected beside this script")
    ap.add_argument("--par-file", default=None, help="PSPLIB parameter-class file par.sm; auto-detected beside this script")
    ap.add_argument("--no-opt-download", action="store_true", help="do not try to download missing optima")
    ap.add_argument("--no-validate", action="store_true", help="skip final schedule validation")
    args = ap.parse_args()

    files = collect_sm_files(args.inputs, prefix=args.prefix)
    if args.limit is not None:
        files = files[: args.limit]
    if not files:
        raise SystemExit("No .sm files found. Check the path/URL and --prefix.")

    first_set, _, _ = parse_instance_name(files[0].stem)
    par_table = load_par_table(args.par_file)
    optima = load_optima(args.opt_file, first_set or args.prefix.lower(), not args.no_opt_download)

    csv_fh = None
    writer = None
    if args.csv:
        csv_path = Path(args.csv)
        if csv_path.parent and str(csv_path.parent) not in ("", "."):
            csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_fh = open(str(csv_path), "w", newline="")
        writer = csv.DictWriter(csv_fh, fieldnames=ASTAR_OUTPUT_FIELDNAMES)
        writer.writeheader()
        csv_fh.flush()

    print(",".join(ASTAR_OUTPUT_FIELDNAMES), flush=True)
    try:
        for path in files:
            inst = parse_psplib_sm(path)
            attrs = instance_attributes(inst, par_table)
            res = astar_depth_first_tie(
                inst,
                args.max_expansions,
                args.time_limit,
                same_start_lexicographic=args.same_start_lexicographic,
                max_memory_mb=args.max_memory_mb,
                state_progress_dominance=args.state_progress_dominance,
                exact_active_schedule=args.exact_active_schedule,
            )
            if res.status == "optimal" and res.schedule is not None and not args.no_validate:
                validate_schedule(inst, res.schedule)
            row = make_row(inst, res, attrs, optima)
            print(",".join(str(row[k]) for k in ASTAR_OUTPUT_FIELDNAMES), flush=True)
            if writer is not None:
                writer.writerow(row)
                csv_fh.flush()
            if args.pretty:
                print_pretty(row)
    finally:
        if csv_fh is not None:
            csv_fh.close()


if __name__ == "__main__":
    main()

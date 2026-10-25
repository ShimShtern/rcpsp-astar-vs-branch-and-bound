#!/bin/bash
# Run one solver configuration on the PSPLIB J30 instances, one process per
# instance, with each concurrent process pinned to its own CPU.
#
# Usage:
#   [MAX_JOBS=8] [CPU_LIST=0,1,...] [INSTANCES_FILE=list.txt] \
#   ./run_j30_batch.sh <output_dir> <runner> [runner options...]
#
# Pass the per-instance running options documented in EXPERIMENTS.md, without
# --prefix and --csv (this script adds them).  Examples:
#
#   # A* with same-start dominance and exact active-schedule pruning
#   ./run_j30_batch.sh astar_runs/exact_active run_rcpsp_astar_py36.py \
#       --same-start-lexicographic --exact-active-schedule \
#       --time-limit 7200 --max-memory-mb 65536
#
#   # Best-first B&B with the weak LP relaxation bound
#   ./run_j30_batch.sh bb_runs/weak_lp_best_first run_rcpsp_bb_py36.py \
#       --strategy best_first --same-start-lexicographic --active-schedule \
#       --no-duplicate-tracking --relaxation-bound --lp-precedence weak \
#       --lp-ordering integer --lp-diagnostics \
#       --time-limit 7200 --max-memory-mb 65536
#
# Results go to <output_dir>/results/<instance>.csv and logs to
# <output_dir>/logs/<instance>.log.  Rerunning the same command skips
# instances that already have a complete result.  When every instance has a
# result, the per-instance CSVs are combined into <output_dir>/j30_all.csv.
#
# MAX_JOBS (default 8) solvers run at once, on the CPUs in CPU_LIST (default
# 0..MAX_JOBS-1); see cpu_worker_pool.sh.  INSTANCES_FILE optionally lists the
# instances to run, one name per line (e.g. j301_1); default: all of j30.sm/.
set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"
. "$script_dir/cpu_worker_pool.sh"

if [ "$#" -lt 2 ]; then
    sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 1
fi
output_dir="$1"
runner="$2"
shift 2
runner_options=("$@")

max_jobs="${MAX_JOBS:-8}"
results_dir="$output_dir/results"
logs_dir="$output_dir/logs"
metadata="$output_dir/configuration.txt"
combined="$output_dir/j30_all.csv"

if [ ! -f "$runner" ]; then
    echo "Runner not found: $runner" >&2
    exit 1
fi
cpu_pool_configure "$max_jobs" || exit 1
mkdir -p "$results_dir" "$logs_dir"
cpu_pool_validate_metadata "$metadata" || exit 1

instances=()
if [ -n "${INSTANCES_FILE:-}" ]; then
    while IFS= read -r name; do
        name="${name%%#*}"
        name="$(echo "$name" | tr -d '[:space:]')"
        name="${name##*/}"
        name="${name%.sm}"
        [ -n "$name" ] && instances+=("j30.sm/$name.sm")
    done < "$INSTANCES_FILE"
else
    while IFS= read -r path; do
        instances+=("$path")
    done < <(ls j30.sm/j30*_*.sm | sort -V)
fi
if [ "${#instances[@]}" -eq 0 ]; then
    echo "No instances selected" >&2
    exit 1
fi

if [ ! -f "$metadata" ]; then
    {
        echo "created=$(date --iso-8601=seconds)"
        echo "host=$(hostname)"
        echo "python=$(python3 --version 2>&1)"
        echo "runner=$runner"
        echo "runner_sha256=$(sha256sum "$runner" | awk '{print $1}')"
        case "$runner" in
            *astar*) core=rcpsp_astar_core_py36.py ;;
            *) core=rcpsp_bb_core_parent_py36.py ;;
        esac
        echo "core=$core"
        echo "core_sha256=$(sha256sum "$core" | awk '{print $1}')"
        echo "runner_options=${runner_options[*]}"
        echo "max_jobs=$max_jobs"
        echo "cpu_list=$CPU_POOL_CPUS_CSV"
        echo "instance_count=${#instances[@]}"
    } > "$metadata"
fi

complete() {
    [ -f "$1" ] && [ "$(wc -l < "$1")" -ge 2 ]
}

echo "Output: $output_dir"
echo "Runner: $runner ${runner_options[*]}"
echo "Instances: ${#instances[@]}; workers: $max_jobs on CPUs $CPU_POOL_CPUS_CSV"

cpu_pool_reset || exit 1
for path in "${instances[@]}"; do
    name="$(basename "$path" .sm)"
    out="$results_dir/$name.csv"
    complete "$out" && continue
    cpu_pool_start "$logs_dir/$name.log" \
        python3 "$runner" "$path" --prefix "" "${runner_options[@]}" \
        --csv "$out" || exit 1
done
cpu_pool_wait || true

done_count=0
for path in "${instances[@]}"; do
    complete "$results_dir/$(basename "$path" .sm).csv" &&
        done_count=$((done_count + 1))
done
if [ "$CPU_POOL_FAILURES" -ne 0 ]; then
    echo "$CPU_POOL_FAILURES solver processes exited unsuccessfully; see $logs_dir" >&2
fi
if [ "$done_count" -ne "${#instances[@]}" ]; then
    echo "Incomplete: $done_count of ${#instances[@]} results. Rerun the same command to continue." >&2
    exit 1
fi

header=""
files=()
for path in "${instances[@]}"; do
    file="$results_dir/$(basename "$path" .sm).csv"
    files+=("$file")
    current="$(head -n 1 "$file" | tr -d '\r')"
    if [ -z "$header" ]; then
        header="$current"
    elif [ "$current" != "$header" ]; then
        echo "Cannot combine results with different columns: $file" >&2
        exit 1
    fi
done
awk 'FNR == 1 && NR != 1 {next} {print}' "${files[@]}" > "$combined"
echo "Finished: $done_count results; combined CSV: $combined"

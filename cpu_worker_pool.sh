#!/bin/bash
# Shared fixed-CPU worker pool for reproducible solver experiments.
# Source this file, call cpu_pool_configure MAX_JOBS, then use
# cpu_pool_start LOG COMMAND... and cpu_pool_wait.

cpu_pool_configure()
{
    CPU_POOL_SIZE="$1"
    local requested="${CPU_LIST:-}"
    local cpu=""
    local index=0
    local seen=""

    case "$CPU_POOL_SIZE" in
        ''|*[!0-9]*|0)
            echo "MAX_JOBS must be a positive integer" >&2
            return 1
            ;;
    esac
    if ! command -v taskset >/dev/null 2>&1; then
        echo "taskset is required for fixed-CPU experiments" >&2
        return 1
    fi

    # On the experiment server, CPUs 0--35 are distinct physical cores and
    # 36--71 are their sibling hardware threads.  The default therefore uses
    # the first MAX_JOBS physical cores.  CPU_LIST overrides this selection.
    if [ -z "$requested" ]; then
        requested="$(seq -s, 0 $((CPU_POOL_SIZE - 1)))"
    fi

    CPU_POOL_CPUS=()
    IFS=',' read -r -a CPU_POOL_CPUS <<< "$requested"
    if [ "${#CPU_POOL_CPUS[@]}" -ne "$CPU_POOL_SIZE" ]; then
        echo "CPU_LIST must contain exactly MAX_JOBS=$CPU_POOL_SIZE CPUs" >&2
        return 1
    fi

    for cpu in "${CPU_POOL_CPUS[@]}"; do
        case "$cpu" in
            ''|*[!0-9]*)
                echo "Invalid CPU in CPU_LIST: $cpu" >&2
                return 1
                ;;
        esac
        case ",$seen," in
            *,$cpu,*)
                echo "CPU_LIST contains duplicate CPU $cpu" >&2
                return 1
                ;;
        esac
        if ! taskset -c "$cpu" true >/dev/null 2>&1; then
            echo "CPU $cpu is unavailable to this launcher" >&2
            return 1
        fi
        seen="${seen:+$seen,}$cpu"
    done

    CPU_POOL_CPUS_CSV="$seen"
    CPU_POOL_PIDS=()
    CPU_POOL_FAILURES=0
    CPU_POOL_ACQUIRED_SLOT=-1
    for ((index = 0; index < CPU_POOL_SIZE; index++)); do
        CPU_POOL_PIDS[index]=""
    done
}

cpu_pool_reap_slot()
{
    local slot="$1"
    local pid="${CPU_POOL_PIDS[$slot]:-}"
    if [ -n "$pid" ]; then
        if ! wait "$pid"; then
            CPU_POOL_FAILURES=$((CPU_POOL_FAILURES + 1))
        fi
        CPU_POOL_PIDS[$slot]=""
    fi
}

cpu_pool_reset()
{
    local slot=0
    CPU_POOL_FAILURES=0
    CPU_POOL_ACQUIRED_SLOT=-1
    for ((slot = 0; slot < CPU_POOL_SIZE; slot++)); do
        if [ -n "${CPU_POOL_PIDS[$slot]:-}" ]; then
            echo "Cannot reset CPU pool while worker $slot is active" >&2
            return 1
        fi
        CPU_POOL_PIDS[$slot]=""
    done
}

cpu_pool_validate_metadata()
{
    local metadata="$1"
    local saved_cpus=""
    local saved_jobs=""
    [ -f "$metadata" ] || return 0

    saved_cpus="$(awk -F= '$1 == "cpu_list" {print $2; exit}' "$metadata")"
    saved_jobs="$(awk -F= '$1 == "max_jobs" {print $2; exit}' "$metadata")"
    if [ -z "$saved_cpus" ]; then
        echo "Existing run predates fixed-CPU metadata: $metadata" >&2
        echo "Use a new run tag to avoid mixing pinned and unpinned results." >&2
        return 1
    fi
    if [ "$saved_cpus" != "$CPU_POOL_CPUS_CSV" ] ||
            [ "$saved_jobs" != "$CPU_POOL_SIZE" ]; then
        echo "CPU configuration differs from existing run metadata: $metadata" >&2
        echo "Saved: MAX_JOBS=$saved_jobs CPU_LIST=$saved_cpus" >&2
        echo "Current: MAX_JOBS=$CPU_POOL_SIZE CPU_LIST=$CPU_POOL_CPUS_CSV" >&2
        return 1
    fi
}

cpu_pool_acquire()
{
    local slot=0
    local pid=""
    local state=""
    while :; do
        for ((slot = 0; slot < CPU_POOL_SIZE; slot++)); do
            pid="${CPU_POOL_PIDS[$slot]:-}"
            if [ -z "$pid" ]; then
                CPU_POOL_ACQUIRED_SLOT="$slot"
                return 0
            fi
            state="$(ps -o stat= -p "$pid" 2>/dev/null | awk '{print $1}')"
            if [ -z "$state" ] || [ "${state#Z}" != "$state" ]; then
                cpu_pool_reap_slot "$slot"
                CPU_POOL_ACQUIRED_SLOT="$slot"
                return 0
            fi
        done
        sleep 1
    done
}

cpu_pool_start()
{
    local log="$1"
    shift
    local slot=0
    local cpu=0

    cpu_pool_acquire || return 1
    slot="$CPU_POOL_ACQUIRED_SLOT"
    cpu="${CPU_POOL_CPUS[$slot]}"
    echo "CPU $cpu: $*"
    taskset -c "$cpu" env \
        OMP_NUM_THREADS=1 \
        OPENBLAS_NUM_THREADS=1 \
        MKL_NUM_THREADS=1 \
        NUMEXPR_NUM_THREADS=1 \
        "$@" > "$log" 2>&1 &
    CPU_POOL_PIDS[$slot]="$!"
}

cpu_pool_wait()
{
    local slot=0
    for ((slot = 0; slot < CPU_POOL_SIZE; slot++)); do
        cpu_pool_reap_slot "$slot"
    done
    [ "$CPU_POOL_FAILURES" -eq 0 ]
}

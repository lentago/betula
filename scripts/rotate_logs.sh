#!/usr/bin/env bash
# Rotate pipeline log files that are written by cron redirects.
# Runs daily from user_crontab. Cap: ~1 MB per file; keeps one backup (.log.1).
set -euo pipefail

readonly LOG_DIR="/home/pi/.firewalla/config"
readonly LOG_MAX_BYTES=1048576

rotate_one() {
    local logfile="$1"
    if [[ ! -f "$logfile" ]]; then
        return 0
    fi
    local size
    size=$(stat -c%s "$logfile")
    if (( size < LOG_MAX_BYTES )); then
        return 0
    fi
    mv "$logfile" "${logfile}.1"
}

rotate_one "${LOG_DIR}/bspool_cleanup.log"
rotate_one "${LOG_DIR}/device_lookup.log"
rotate_one "${LOG_DIR}/fluent-bit-restart.log"

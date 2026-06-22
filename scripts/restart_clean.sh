#!/usr/bin/env bash
# restart_clean.sh — stop running OpenHarness long-running processes and
# purge caches that would otherwise let a freshly-started process run stale
# bytecode or hold onto obsolete state.
#
# Idempotent: safe to run when nothing is up. Returns 0 on success.
#
# What it does (in order):
#   1. Stops the wolo gateway (main process + its cron-scheduler daemon).
#   2. Stops the solo gateway (main process + its cron-scheduler daemon).
#   3. Stops the onboard uvicorn server.
#   4. Kills any leaked cron-scheduler daemon whose PID file survived.
#   5. Removes Python bytecode caches so a restarted process recompiles.
#   6. Removes stray PID files that referenced dead processes.
#
# Usage:
#   scripts/restart_clean.sh [--only <app>]  only stop the specified app
#                                             (solo, wolo, or onboard;
#                                              repeatable for multiple apps)
#                          [--no-pyc]        skip pycache cleanup
#                          [--no-stop]       only clear caches; don't stop procs
#                          [--quiet]         suppress per-step output
#
# Examples:
#   scripts/restart_clean.sh                     # stop everything + clear caches
#   scripts/restart_clean.sh --only wolo         # stop only wolo gateway + daemon
#   scripts/restart_clean.sh --only solo --only onboard  # stop solo + onboard
#   scripts/restart_clean.sh --no-pyc            # stop everything, keep caches
#
# Exit codes:
#   0  cleanup done
#   1  a hard error occurred; partial cleanup may have happened.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

QUIET=0
DO_STOP=1
DO_PYC=1
ONLY_APPS=()

_args=("$@")
_i=0
while [ $_i -lt ${#_args[@]} ]; do
  _a="${_args[$_i]}"
  case "$_a" in
    --quiet|-q)   QUIET=1 ;;
    --no-stop)    DO_STOP=0 ;;
    --no-pyc)     DO_PYC=0 ;;
    --only=*)     ONLY_APPS+=("${_a#--only=}") ;;
    --only)
      _i=$((_i + 1))
      if [ $_i -ge ${#_args[@]} ]; then
        echo "--only requires an argument (solo|wolo|onboard)" >&2
        exit 2
      fi
      ONLY_APPS+=("${_args[$_i]}")
      ;;
    --help|-h)
      sed -n '2,35p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $_a" >&2
      exit 2
      ;;
  esac
  _i=$((_i + 1))
done

# Validate --only values
for app in "${ONLY_APPS[@]+"${ONLY_APPS[@]}"}"; do
  case "$app" in
    solo|wolo|onboard) ;;
    *)
      echo "Invalid --only value: $app (expected solo, wolo, or onboard)" >&2
      exit 2
      ;;
  esac
done

log() {
  if [ "$QUIET" -eq 0 ]; then
    printf "[restart_clean] %s\n" "$*"
  fi
}

# Return 0 if the given app should be stopped.
# When no --only flags are set, all apps are stopped (default behavior).
_should_stop() {
  local app="$1"
  if [ ${#ONLY_APPS[@]} -eq 0 ]; then
    return 0
  fi
  for a in "${ONLY_APPS[@]}"; do
    if [ "$a" = "$app" ]; then
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# 1-3. Stop long-running services via their own CLIs.
# ---------------------------------------------------------------------------

if [ "$DO_STOP" -eq 1 ]; then
  if _should_stop wolo; then
    log "stopping wolo gateway (main process + cron scheduler daemon)..."
    if command -v uv >/dev/null 2>&1; then
      uv run --quiet wolo gateway stop 2>/dev/null || true
    else
      (cd "$REPO_ROOT" && python -m wolo gateway stop 2>/dev/null || true)
    fi
  fi

  if _should_stop solo; then
    log "stopping solo gateway (main process + cron scheduler daemon)..."
    if command -v uv >/dev/null 2>&1; then
      uv run --quiet solo gateway stop 2>/dev/null || true
    else
      (cd "$REPO_ROOT" && python -m solo gateway stop 2>/dev/null || true)
    fi
  fi

  if _should_stop onboard; then
    log "stopping onboard server..."
    if command -v uv >/dev/null 2>&1; then
      uv run --quiet onboard stop 2>/dev/null || true
    else
      (cd "$REPO_ROOT" && python -m onboard stop 2>/dev/null || true)
    fi
  fi

  # -----------------------------------------------------------------------
  # 4. Belt & braces: any cron-scheduler daemon that slipped through?
  # -----------------------------------------------------------------------
  if _should_stop wolo; then
    pidfile="$HOME/.wolo/data/cron_scheduler.pid"
    if [ -f "$pidfile" ]; then
      pid="$(tr -d '[:space:]' < "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "killing leaked wolo cron daemon pid=$pid"
        kill -TERM "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.1
        done
        if kill -0 "$pid" 2>/dev/null; then
          kill -KILL "$pid" 2>/dev/null || true
        fi
      fi
      rm -f "$pidfile"
    fi
    # wolo gateway main PID file
    pidfile="$HOME/.wolo/gateway.pid"
    if [ -f "$pidfile" ]; then
      pid="$(tr -d '[:space:]' < "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "killing leftover wolo gateway pid=$pid"
        kill -TERM "$pid" 2>/dev/null || true
        sleep 0.3
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    fi
  fi

  if _should_stop solo; then
    pidfile="$HOME/.solo/data/cron_scheduler.pid"
    if [ -f "$pidfile" ]; then
      pid="$(tr -d '[:space:]' < "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "killing leaked solo cron daemon pid=$pid"
        kill -TERM "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.1
        done
        if kill -0 "$pid" 2>/dev/null; then
          kill -KILL "$pid" 2>/dev/null || true
        fi
      fi
      rm -f "$pidfile"
    fi
    pidfile="$HOME/.solo/gateway.pid"
    if [ -f "$pidfile" ]; then
      pid="$(tr -d '[:space:]' < "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "killing leftover solo gateway pid=$pid"
        kill -TERM "$pid" 2>/dev/null || true
        sleep 0.3
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    fi
  fi

  if _should_stop onboard; then
    pidfile="$HOME/.onboard/onboard.pid"
    if [ -f "$pidfile" ]; then
      pid="$(tr -d '[:space:]' < "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "killing leaked onboard pid=$pid"
        kill -TERM "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.1
        done
        if kill -0 "$pid" 2>/dev/null; then
          kill -KILL "$pid" 2>/dev/null || true
        fi
      fi
      rm -f "$pidfile"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 5. Purge Python bytecode caches.
# ---------------------------------------------------------------------------

if [ "$DO_PYC" -eq 1 ]; then
  log "purging __pycache__ / .pyc under $REPO_ROOT ..."

  EXCLUDES=""
  for p in \
    "$REPO_ROOT/.venv" \
    "$REPO_ROOT/.openharness-venv" \
    "$REPO_ROOT/node_modules" \
    "$REPO_ROOT/onboard/frontend/node_modules" \
    "$REPO_ROOT/dist" \
    "$REPO_ROOT/build"; do
    if [ -e "$p" ]; then
      EXCLUDES="$EXCLUDES -path $p -prune -o"
    fi
  done

  # shellcheck disable=SC2086
  find "$REPO_ROOT" $EXCLUDES -type d -name __pycache__ -print0 \
    | xargs -0 -r rm -rf 2>/dev/null || true

  # shellcheck disable=SC2086
  find "$REPO_ROOT" $EXCLUDES -type f -name '*.pyc' -print0 \
    | xargs -0 -r rm -f 2>/dev/null || true

  for d in .pytest_cache .mypy_cache .ruff_cache; do
    if [ -d "$REPO_ROOT/$d" ]; then
      rm -rf "$REPO_ROOT/$d"
      log "removed $d"
    fi
  done
fi

log "clean."
exit 0

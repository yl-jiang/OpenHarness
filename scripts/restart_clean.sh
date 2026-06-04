#!/usr/bin/env bash
# restart_clean.sh — stop every running OpenHarness long-running process and
# purge caches that would otherwise let a freshly-started process run stale
# bytecode or hold onto obsolete state.
#
# Idempotent: safe to run when nothing is up. Returns 0 on success.
#
# What it does (in order):
#   1. Stops the wolo gateway (main process + its cron-scheduler daemon).
#   2. Stops the solo gateway (main process + its cron-scheduler daemon).
#   3. Stops the onboard uvicorn server.
#   4. Kills any leaked cron-scheduler daemon whose PID file survived (belt &
#      braces — the service stop paths now call `stop_daemon`, but older
#      deployed versions did not).
#   5. Removes Python bytecode caches (`.pyc` files and `__pycache__` dirs)
#      so a restarted process recompiles from the current source on disk.
#   6. Removes stray PID files that referenced dead processes.
#
# Usage:
#   scripts/restart_clean.sh [--no-pyc]    skip pycache cleanup
#                          [--no-stop]     only clear caches; don't stop procs
#                          [--quiet]       suppress per-step output
#
# Exit codes:
#   0  cleanup done
#   1  a hard error occurred (a stop command crashed); partial cleanup may
#      have happened — inspect stderr.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

QUIET=0
DO_STOP=1
DO_PYC=1
for arg in "$@"; do
  case "$arg" in
    --quiet|-q)   QUIET=1 ;;
    --no-stop)    DO_STOP=0 ;;
    --no-pyc)     DO_PYC=0 ;;
    --help|-h)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

log() {
  if [ "$QUIET" -eq 0 ]; then
    printf "[restart_clean] %s\n" "$*"
  fi
}

# ---------------------------------------------------------------------------
# 1-3. Stop long-running services via their own CLIs.
# ---------------------------------------------------------------------------

if [ "$DO_STOP" -eq 1 ]; then
  log "stopping wolo gateway (main process + cron scheduler daemon)..."
  if command -v uv >/dev/null 2>&1; then
    uv run --quiet wolo gateway stop 2>/dev/null || true
    uv run --quiet solo gateway stop 2>/dev/null || true
    uv run --quiet onboard stop 2>/dev/null || true
  else
    # Fallback: direct python invocations.
    (cd "$REPO_ROOT" && python -m wolo gateway stop 2>/dev/null || true)
    (cd "$REPO_ROOT" && python -m solo gateway stop 2>/dev/null || true)
    (cd "$REPO_ROOT" && python -m onboard stop 2>/dev/null || true)
  fi

  # -----------------------------------------------------------------------
  # 4. Belt & braces: any cron-scheduler daemon that slipped through?
  #    Read PID files directly and SIGTERM whatever is still alive.
  # -----------------------------------------------------------------------
  for pidfile in \
    "$HOME/.wolo/data/cron_scheduler.pid" \
    "$HOME/.solo/data/cron_scheduler.pid" \
    "$HOME/.onboard/onboard.pid"; do
    if [ -f "$pidfile" ]; then
      pid="$(tr -d '[:space:]' < "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "killing leaked daemon pid=$pid (from $pidfile)"
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
  done

  # And the gateway main-process PID files (in case the CLI didn't clean up).
  for pidfile in "$HOME/.wolo/gateway.pid" "$HOME/.solo/gateway.pid"; do
    if [ -f "$pidfile" ]; then
      pid="$(tr -d '[:space:]' < "$pidfile" 2>/dev/null || true)"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "killing leftover gateway pid=$pid (from $pidfile)"
        kill -TERM "$pid" 2>/dev/null || true
        sleep 0.3
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    fi
  done
fi

# ---------------------------------------------------------------------------
# 5. Purge Python bytecode caches.
#    Rationale: when source is edited and a daemon process isn't fully killed,
#    the next import may still load stale `.pyc` from `__pycache__/`.
#    Removing them forces recompilation on next start.
# ---------------------------------------------------------------------------

if [ "$DO_PYC" -eq 1 ]; then
  log "purging __pycache__ / .pyc under $REPO_ROOT ..."

  # Trees to skip entirely (virtualenvs / node_modules / build artifacts).
  # Each entry becomes `-path X -prune -o` in the find expression.
  EXCLUDES=""
  for p in \
    "$REPO_ROOT/.venv" \
    "$REPO_ROOT/node_modules" \
    "$REPO_ROOT/onboard/frontend/node_modules" \
    "$REPO_ROOT/dist" \
    "$REPO_ROOT/build"; do
    if [ -e "$p" ]; then
      EXCLUDES="$EXCLUDES -path $p -prune -o"
    fi
  done

  # 5a. Remove `__pycache__` directories.
  # shellcheck disable=SC2086
  find "$REPO_ROOT" $EXCLUDES -type d -name __pycache__ -print0 \
    | xargs -0 -r rm -rf 2>/dev/null || true

  # 5b. Remove stray `.pyc` files.
  # shellcheck disable=SC2086
  find "$REPO_ROOT" $EXCLUDES -type f -name '*.pyc' -print0 \
    | xargs -0 -r rm -f 2>/dev/null || true

  # Tool caches that can hold stale analysis results
  for d in .pytest_cache .mypy_cache .ruff_cache; do
    if [ -d "$REPO_ROOT/$d" ]; then
      rm -rf "$REPO_ROOT/$d"
      log "removed $d"
    fi
  done
fi

log "clean."
exit 0

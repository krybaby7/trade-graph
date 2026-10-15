#!/usr/bin/env bash
# Scheduled public candle collection (Phase D1, docs/KRAKEN-CANDLE-COLLECTION.md).
#
#   run_candle_collection.sh short   BTC/USD and ETH/USD at 1 minute (a response reaches back only 12 hours)
#   run_candle_collection.sh full    every eligible USD pair at 5, 15 and 60 minutes
#
# Public Kraken data only: no key, no spending, no orders. One lock serialises every run, because two
# collectors at once are not coordinated and could pass Kraken's request counter. Each run keeps its JSON
# report; logs older than 30 days are deleted.
set -u -o pipefail

kind="${1:-}"
case "$kind" in
  short) options=(--intervals 1 --max-requests 20) ;;
  full) options=(--intervals 5,15,60 --max-requests 3000) ;;
  *) echo "usage: $0 short|full" >&2; exit 2 ;;
esac

data="${TRADE_GRAPH_MARKET_DIR:-$HOME/.local/share/trade-graph-market}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
read -r -a command <<< "${TRADE_GRAPH_CMD:-uv run --frozen trade-graph}"
wait_seconds="${LOCK_WAIT_SECONDS:-7200}"

umask 077
mkdir -p -m 700 "$data" "$data/logs"
exec 9> "$data/collect.lock"
if ! flock -w "$wait_seconds" 9; then
  echo "$(date -u +%FT%TZ) $kind skipped: another collection held the lock for ${wait_seconds}s" >> "$data/logs/history.log"
  exit 75
fi

started="$(date -u +%Y%m%dT%H%M%SZ)"
report="$data/logs/collect-$kind-$started.json"
cd "$repo" || exit 70
"${command[@]}" collect-kraken-candles --market-database "$data/market_data.sqlite" "${options[@]}" \
  > "$report" 2> "$report.err"
code=$?

summary="$(python3 -c '
import json, sys
try:
    r = json.load(open(sys.argv[1]))
    print("status=%s requests=%s inserted=%s stop=%s" % (
        r.get("status"), r.get("requests_total"), r.get("totals", {}).get("inserted"), r.get("stop_reason")))
except Exception:
    print("status=unreadable-report")
' "$report")"
echo "$(date -u +%FT%TZ) $kind exit=$code $summary report=$(basename "$report")" >> "$data/logs/history.log"
cp -f "$report" "$data/logs/last-$kind.json"
[ -s "$report.err" ] || rm -f "$report.err"
find "$data/logs" -name 'collect-*' -mtime +30 -delete 2> /dev/null
exit "$code"

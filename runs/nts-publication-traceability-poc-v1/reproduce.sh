#!/usr/bin/env bash
# Reproduce the nts-publication-traceability-poc-v1 study.
#
#   ./reproduce.sh                      # uses cached sources, tries the live ALURES download
#   OFFLINE=1 ./reproduce.sh            # reuse the last Gate 0 acquisition log, no network
#   FETCH=1 ./reproduce.sh              # first replace excerpts with PMC OA JATS text (network)
#   CRT_CHECKOUT=/path/to/CRT ./reproduce.sh   # also run the real CRT importer
#   SEIP_TEST_POSTGRES_URL=postgresql+asyncpg://user@127.0.0.1:5432/postgres ./reproduce.sh
#                                       # also validate migrations 0029/0030 on a DISPOSABLE server
#
# Prerequisites: Python >= 3.12 with `pip install -c backend/constraints.txt -e "backend[dev]"`,
# Node >= 22 only for the CRT step. The private source cache/pmc11731260.json (licence not
# verified, so not committed) is created by FETCH=1; without it case A is reported as skipped.
set -euo pipefail
RUN="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$RUN/../.." && pwd)"
PY="${PYTHON:-python3}"
cd "$REPO/backend"

if [ "${FETCH:-0}" = "1" ]; then
  for pmcid in PMC11731260 PMC13304911 PMC6392334; do
    "$PY" -m app.nts fetch --run-dir "$RUN" --pmcid "$pmcid" || echo "fetch of $pmcid failed (continuing with cached excerpt)"
  done
fi

ARGS=(run --run-dir "$RUN")
[ -n "${CRT_CHECKOUT:-}" ] && ARGS+=(--crt-checkout "$CRT_CHECKOUT")
[ "${OFFLINE:-0}" = "1" ] && ARGS+=(--offline)
"$PY" -m app.nts "${ARGS[@]}"

echo "--- Diggypaper packet validator (both packets, import and restricted modes)"
for d in source-evidence-packet source-evidence-packet-exploratory; do
  "$PY" "$REPO/docs/contracts/source-evidence-packet/v0.1.0/validator/sep_validate.py" "$RUN/$d" | head -5
done

echo "--- NTS tests"
"$PY" -m pytest tests/test_nts_*.py -q

if [ -n "${SEIP_TEST_POSTGRES_URL:-}" ]; then
  echo "--- PostgreSQL migrations 0029/0030 on a throwaway database"
  "$PY" -m pytest tests/test_finding_curation_postgres.py -q -rs
fi
if [ -n "${CRT_CHECKOUT:-}" ]; then
  echo "--- CRT importer baseline tests"
  (cd "$CRT_CHECKOUT" && node --test tests/source-evidence-import.test.mjs | tail -8)
fi

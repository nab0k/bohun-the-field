#!/bin/sh
set -eu
pg="${D2RC_PG_BIN:-$(pg_config --bindir)}"
python="${D2RC_PYTHON:-python3}"
cluster=$(mktemp -d "${TMPDIR:-/tmp}/d2rc-policy.XXXXXXXX")
trap '"$pg/pg_ctl" -D "$cluster/data" -m immediate stop >/dev/null 2>&1 || true; rm -r "$cluster"' EXIT
"$pg/initdb" -D "$cluster/data" -A trust --no-locale -E UTF8 >/dev/null
"$pg/pg_ctl" -D "$cluster/data" -l "$cluster/server.log" -o "-c listen_addresses= -k $cluster -p 55432" -w start >/dev/null
export D2RC_SOCKET="$cluster" PYTHONPATH=.
for scenario in short unknown daily billing; do
    export PGDATABASE="policy_$scenario" D2RC_SCENARIO="$scenario"
    "$pg/createdb" -h "$cluster" -p 55432 "$PGDATABASE"
    D2RC_PHASE=outage "$python" -B scripts/d2rc_cloud_probe.py
    for phase in failure restart restored; do
        D2RC_PHASE="$phase" "$python" -B scripts/check_d2rc_retry_policy.py
    done
done

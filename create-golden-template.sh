#!/bin/bash
# Creates (or refreshes) the golden template dump used for fast provisioning.
# Run this from the project root (same folder as app/, nginx/, templates/).
# Safe to re-run any time - it always regenerates templates/golden.dump fresh.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Working directory: $SCRIPT_DIR"

if docker ps -a --format '{{.Names}}' | grep -q "^postgres-golden$"; then
    echo "Found existing 'golden' customer containers."
    docker start postgres-golden > /dev/null 2>&1 || true
else
    echo "No 'golden' customer found. Provisioning one now (this will take ~1-2 min)..."

    # if an old golden.dump exists, provisioning would clone from it
    if [ -f "$SCRIPT_DIR/templates/golden.dump" ]; then
        echo "Moving aside existing golden.dump so this build is a real fresh install..."
        mv "$SCRIPT_DIR/templates/golden.dump" "$SCRIPT_DIR/templates/golden.dump.old"
    fi

    curl -s -X POST http://localhost:8000/provision \
        -H "Content-Type: application/json" \
        -d '{"customer_slug": "golden", "package": "starter"}' > /dev/null

    echo "Waiting for provisioning to complete..."
    while true; do
        state=$(curl -s http://localhost:8000/provision/status/golden | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','unknown'))" 2>/dev/null || echo "unknown")
        if [ "$state" == "done" ]; then
            echo "Golden customer provisioned."
            break
        fi
        if [ "$state" == "failed" ]; then
            echo "Golden provisioning FAILED. Check FastAPI logs. Aborting."
            exit 1
        fi
        echo "  ...still $state, waiting 5s"
        sleep 5
    done

    rm -f "$SCRIPT_DIR/templates/golden.dump.old"
fi

echo "Dumping golden database to templates/golden.dump ..."
docker exec postgres-golden pg_dump -U odoo -Fc golden > "$SCRIPT_DIR/templates/golden.dump"

echo "Verifying the dump actually has real content..."
table_count=$(docker exec postgres-golden psql -U odoo -d golden -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d ' ')

if [ "$table_count" -lt 50 ]; then
    echo ""
    echo "WARNING: golden database only has $table_count tables — this looks broken/incomplete"
    echo "(a real Odoo install normally has hundreds of tables)."
    echo "The dump was still saved, but provisioning from it will likely fail."
    echo "Consider tearing down 'golden' entirely and re-running this script."
    exit 1
fi

echo ""
echo "Done. Golden template ready at: $SCRIPT_DIR/templates/golden.dump ($table_count tables confirmed)"
ls -la "$SCRIPT_DIR/templates/golden.dump"

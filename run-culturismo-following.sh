#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Wrapper per lanciare config-culturismo-following.yml con filtro 5k-10k
# follower APPLICATO TEMPORANEAMENTE a filters.yml.
#
# Cosa fa:
#   1. Backup di filters.yml
#   2. Patch inline di min_followers=5000, max_followers=10000
#   3. Lancia il bot con la config culturismo
#   4. Ripristina filters.yml originale (SEMPRE, via trap EXIT)
#
# Uso:
#   ./run-culturismo-following.sh
# ---------------------------------------------------------------------------

set -e
cd "$(dirname "$0")"

ACCOUNT="marramattia_fmgpro"
FILTERS="accounts/${ACCOUNT}/filters.yml"
BACKUP="accounts/${ACCOUNT}/filters.yml.bak_culturismo"
CONFIG="accounts/${ACCOUNT}/config-culturismo-following.yml"

if [ ! -f "$FILTERS" ]; then
    echo "ERROR: $FILTERS non trovato." >&2
    exit 1
fi
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: $CONFIG non trovato." >&2
    exit 1
fi

# Backup + trap di restore (garantisce ripristino anche su Ctrl+C o crash)
cp "$FILTERS" "$BACKUP"
restore_filters() {
    if [ -f "$BACKUP" ]; then
        mv "$BACKUP" "$FILTERS"
        echo "[$(date '+%F %T')] filters.yml ripristinato dal backup."
    fi
}
trap restore_filters EXIT INT TERM

# Patch: min_followers -> 5000, max_followers -> 10000.
# Cambio solo la prima occorrenza (root-level key nel filters.yml).
sed -i -E 's/^(\s*min_followers:\s*)[0-9]+/\15000/' "$FILTERS"
sed -i -E 's/^(\s*max_followers:\s*)[0-9]+/\110000/' "$FILTERS"

echo "[$(date '+%F %T')] filters.yml patchato temporaneamente:"
grep -E "min_followers|max_followers" "$FILTERS" || true

# Attiva venv se esiste (cross-platform: Windows Scripts/, Unix bin/)
if [ -f ".venv/Scripts/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/Scripts/activate
elif [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Carica secrets locali
if [ -f ".env.local" ]; then
    # shellcheck disable=SC1091
    source .env.local
fi

echo "[$(date '+%F %T')] Lancio bot su $CONFIG ..."
# NOTA: NON usare exec qui - romperebbe il trap EXIT (la shell verrebbe
# sostituita dal processo Python e il restore di filters.yml non partirebbe).
python run.py --config "$CONFIG"

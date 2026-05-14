#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Wrapper anti-ban: alterna automaticamente FOLLOW e UNFOLLOW a giorni alterni.
#
# Strategia:
#   - Giorni dispari (1,3,5,...) -> config.yml         (follow + like)
#   - Giorni pari    (2,4,6,...) -> config-unfollow.yml (solo unfollow non-followers @3gg)
#
# In aggiunta (lancio MANUALE quando vuoi):
#   ./run-bot.sh unfollow-followers   # sgancia chi ti ha seguito indietro ma sono passati >= 7gg dal mio follow
#   ./run-bot.sh unfollow-old         # sgancia TUTTI i followati dal bot da >= 2 giorni (pulizia periodica)
#
# Uso:
#   ./run-bot.sh                       # auto: usa il giorno odierno
#   ./run-bot.sh follow                # forza follow
#   ./run-bot.sh unfollow              # forza unfollow non-followers @3gg
#   ./run-bot.sh unfollow-followers    # forza unfollow followers @7gg (manuale)
#   ./run-bot.sh unfollow-old          # forza unfollow TUTTI i followati >= 2gg (pulizia)
#
# Cron suggerito (lancio automatico alle 09:00):
#   0 9 * * *  cd /Users/mattia/PycharmProjects/bot && ./run-bot.sh >> logs/cron.log 2>&1
# ---------------------------------------------------------------------------

set -e
cd "$(dirname "$0")"

ACCOUNT="simonebestagno"
MODE="${1:-auto}"

if [ "$MODE" = "auto" ]; then
    DAY=$(date +%-d)   # giorno del mese senza zero iniziale
    if (( DAY % 2 == 1 )); then
        MODE="follow"
    else
        MODE="unfollow"
    fi
fi

case "$MODE" in
    follow)
        CONFIG="accounts/${ACCOUNT}/config.yml"
        echo "[$(date '+%F %T')] >>> Modalita': FOLLOW + LIKE  (config.yml)"
        ;;
    unfollow)
        CONFIG="accounts/${ACCOUNT}/config-unfollow.yml"
        echo "[$(date '+%F %T')] >>> Modalita': UNFOLLOW NON-FOLLOWERS @3gg  (config-unfollow.yml)"
        ;;
    unfollow-followers)
        CONFIG="accounts/${ACCOUNT}/config-unfollow-followers.yml"
        echo "[$(date '+%F %T')] >>> Modalita': UNFOLLOW FOLLOWERS @7gg  (config-unfollow-followers.yml)"
        ;;
    unfollow-old)
        CONFIG="accounts/${ACCOUNT}/config-unfollow-old.yml"
        echo "[$(date '+%F %T')] >>> Modalita': UNFOLLOW OLD (tutti i followati >= 2gg)  (config-unfollow-old.yml)"
        ;;
    *)
        echo "Uso: $0 [auto|follow|unfollow|unfollow-followers|unfollow-old]" >&2
        exit 1
        ;;
esac

# Attiva venv se esiste
if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Carica secrets locali (GEMINI_API_KEY, ecc.) se presenti.
# Il file .env.local e' gitignored: ci tieni le chiavi senza rischio commit.
if [ -f ".env.local" ]; then
    # shellcheck disable=SC1091
    source .env.local
fi

exec python run.py --config "$CONFIG"


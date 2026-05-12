"""Smoke test per le 3 nuove feature (#6 / #3 / #9). Eseguire con:
    python test_features.py
Non e' un unit-test ufficiale: serve solo a verificare manualmente che
i moduli si comportino come previsto prima di lanciare il bot vero.
"""
from datetime import datetime

from GramAddict.core.ai_comment import (
    _detect_post_type,
    _is_duplicate,
)
from GramAddict.core.session_state import _resolve_weekday_multiplier
from GramAddict.core import engagement_protect

print("=== #6a Post type detection ===")
samples = [
    ("Squat profondi oggi, 5x5 con 100kg, gambe distrutte", "workout"),
    ("Colazione: 80g avena, 4 uova, latte di mandorla. Macros perfetti.", "food"),
    ("3 mesi di costanza, ecco i risultati. Da 95 a 82kg.", "progress"),
    ("La disciplina batte la motivazione. Mindset e costanza ogni giorno.", "motivational"),
    ("Bella giornata di sole oggi al parco", "generic"),
    ("Allenamento gambe oggi: squat panca e stacchi", "workout"),
]
for cap, expected in samples:
    got = _detect_post_type(cap)
    mark = "OK  " if got == expected else "FAIL"
    print(f"[{mark}] expected={expected:<14s} got={got:<14s} | {cap[:60]}")

print()
print("=== #6b Duplicate detection (Jaccard) ===")
history = [
    "Bella determinazione si vede limpegno",
    "Squat profondi ottima tecnica",
]
tests = [
    ("Bella determinazione, si capisce limpegno", True),   # duplicato lieve
    ("Carico solido oggi nessuna similarita", False),       # diverso
    ("Squat profondi ottima esecuzione", True),             # simile
    ("Ottimo carico nessun match", False),                  # diverso
]
for cand, expected_dup in tests:
    is_dup, sim = _is_duplicate(cand, history)
    mark = "OK  " if is_dup == expected_dup else "FAIL"
    print(f"[{mark}] expected_dup={expected_dup!s:<5} got={is_dup!s:<5} sim={sim:.2f} | {cand}")

print()
print("=== #9 Weekday multiplier ===")
today_name = datetime.now().strftime("%A")
today_idx = datetime.now().weekday()
print(f"Today is {today_name} (weekday={today_idx})")
config_str = "mon:1.0,tue:1.15,wed:1.15,thu:1.15,fri:0.9,sat:0.55,sun:0.5"
print(f"  with config '{config_str}': mult={_resolve_weekday_multiplier(config_str)}")
print(f"  with empty:               mult={_resolve_weekday_multiplier('')}")
print(f"  with None:                mult={_resolve_weekday_multiplier(None)}")
print(f"  with junk 'banana=lol':   mult={_resolve_weekday_multiplier('banana=lol')}")
print(f"  with negative 'mon:-1':   mult={_resolve_weekday_multiplier('mon:-1,tue:-1,wed:-1,thu:-1,fri:-1,sat:-1,sun:-1')}")
print(f"  with clamp 'all:5':       mult={_resolve_weekday_multiplier('mon:5,tue:5,wed:5,thu:5,fri:5,sat:5,sun:5')}")

print()
print("=== #3 Engagement protect ===")
acct = "accounts/simonebestagno"
ok = engagement_protect.add_engaged(
    "test_user_123", acct, source="unit-test", note="auto test"
)
print(f"add_engaged returned: {ok}")
print(f"is_engaged test_user_123:        {engagement_protect.is_engaged('test_user_123', acct)}")
print(f"is_engaged TEST_USER_123 (case): {engagement_protect.is_engaged('TEST_USER_123', acct)}")
print(f"is_engaged @test_user_123 (@):   {engagement_protect.is_engaged('@test_user_123', acct)}")
print(f"is_engaged ghost (not in list):  {engagement_protect.is_engaged('ghost', acct)}")
print(f"size: {engagement_protect.size(acct)}")
print()
print("DONE.")


"""Smoke test filters.yml di simonebestagno: parsing YAML + regex compile + simulazione skip."""
import re
import sys

import yaml


def main():
    with open("accounts/simonebestagno/filters.yml") as f:
        data = yaml.safe_load(f)

    print("=== filters.yml parsed OK ===")
    print(f"skip_username_patterns count: {len(data.get('skip_username_patterns', []))}")
    print(f"blacklist_words count: {len(data.get('blacklist_words', []))}")
    print(f"max_followers: {data.get('max_followers')}")
    print(f"max_followings: {data.get('max_followings')}")
    print(f"min_followers: {data.get('min_followers')}")
    print()

    # Compile-check regex
    patterns = []
    for p in data["skip_username_patterns"]:
        try:
            patterns.append((p, re.compile(p, re.IGNORECASE)))
        except re.error as e:
            print(f"❌ BAD REGEX: {p!r} -> {e}")
            sys.exit(1)
    print(f"✅ all {len(patterns)} regex compile cleanly")
    print()

    # Username skip simulation
    test_usernames = [
        ("mario_rossi_82",          "PASS"),
        ("marco_pt",                "SKIP"),
        ("coach_alessia",           "SKIP"),
        ("personal_trainer_milano", "SKIP"),
        ("giulia_fitness_addict",   "PASS"),  # "fitness" non bloccato in username
        ("giulio_fit",              "SKIP"),  # _fit$
        ("npc_athlete_2026",        "SKIP"),
        ("ifbbpro_marco",           "SKIP"),
        ("claudio_team_x",          "SKIP"),  # team_
        ("sara99",                  "PASS"),
        ("dario_pro",               "SKIP"),  # _pro$
        ("training_giulia",         "SKIP"),  # training
        ("nutritionguru",           "SKIP"),
        ("dietistamilano",          "SKIP"),
        ("alessandro_official",     "SKIP"),  # _official_ ? -> verify
        ("giorgia_runner",          "PASS"),
        ("matteo_powerlifter",      "PASS"),  # ok target
        ("ilaria_yoga_lover",       "PASS"),
        ("ambassadorelena",         "SKIP"),  # ambassad
        ("creator_emma",            "SKIP"),
    ]
    print("--- username regex tests ---")
    fails = 0
    for u, expected in test_usernames:
        matched_pat = next((p for p, r in patterns if r.search(u)), None)
        actual = "SKIP" if matched_pat else "PASS"
        ok = actual == expected
        mark = "✅" if ok else "❌"
        if not ok:
            fails += 1
        print(f"  {mark} {u:30} -> {actual:4} (expected {expected})  match={matched_pat!r}")

    print()
    # Bio blacklist simulation (\b word boundary, case-insensitive)
    test_bios = [
        ("Appassionato di palestra | Modena", "PASS"),
        ("Personal Trainer | Coach online", "SKIP"),
        ("PT certificato FIPE", "SKIP"),
        ("Coach IFBB Pro | Schede personalizzate", "SKIP"),
        ("Amante del bodybuilding natural, sotto pesi da 5 anni", "PASS"),
        ("Powerlifter natural | Modena | PR squat 200kg", "PASS"),  # "powerlifter" non bloccato (solo "powerlifter pro")
        ("Nutrizionista a Modena - consulenza online", "SKIP"),  # nutrizionista E consulenza online
        ("Studentessa universitaria | Crossfit lover", "PASS"),
        ("Brand Ambassador @nikefit", "SKIP"),
        ("Mamma di 2 | Crossfit | Yoga", "PASS"),
        ("Athlete IFBB Italy", "SKIP"),
        ("Dietista nutrizionista", "SKIP"),
        ("Operaio | Palestra 4 volte a settimana", "PASS"),
    ]
    print("--- bio blacklist tests (subset) ---")
    bl_words = data["blacklist_words"]
    bl_patterns = [
        re.compile(r"\b({0})\b".format(w), re.IGNORECASE) for w in bl_words
    ]
    bio_fails = 0
    for bio, expected in test_bios:
        cleaned = bio.lower()
        matches = [w for w, p in zip(bl_words, bl_patterns) if p.search(cleaned)]
        actual = "SKIP" if matches else "PASS"
        ok = actual == expected
        mark = "✅" if ok else "❌"
        if not ok:
            bio_fails += 1
        match_str = f" matches={matches[:3]}" if matches else ""
        print(f"  {mark} {bio:55} -> {actual:4} (expected {expected}){match_str}")

    print()
    if fails == 0 and bio_fails == 0:
        print("ALL OK")
    else:
        print(f"FAILURES: {fails} username, {bio_fails} bio")
        sys.exit(1)


if __name__ == "__main__":
    main()


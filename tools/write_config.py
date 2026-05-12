"""Genera il config.yml di simonebestagno (workaround a bug PSReadLine + file conflict IDE)."""
from pathlib import Path

CONFIG = """\
##############################################################################
# Simone Bestagno - Personal Trainer & Coach @ Fiorano Modenese
# Engagement + nuovi follower: 70% fitness consumer ITA + 30% iperlocale Modena.
##############################################################################

username: simonebestagno
app-id: com.instagram.android
use-cloned-app: false
allow-untested-ig-version: true
screen-sleep: true
screen-record: false
speed-multiplier: 1
debug: false
close-apps: false
kill-atx-agent: false
restart-atx-agent: false
disable-block-detection: false
disable-filters: false
dont-type: false
total-crashes-limit: 5
count-app-crashes: false
shuffle-jobs: true
truncate-sources: 2-5

# Riprende dove ha lasciato: niente piu' loop sugli stessi profili in cima.
resume-from-last-position: true
resume-anchor-search-limit: 50
resume-cooldown-days: 14
scroll-skip-start: 5-15

# Anti-burst guard (essenziale per la salute dell'account).
action-throttle-enabled: true
action-throttle-follow-min: 35-75
action-throttle-like-min: 12-25
action-throttle-comment-min: 90-240
action-throttle-pm-min: 180-360

##############################################################################
# Sources
# Strategia: 70% fitness consumer italiano (gente che VUOLE risultati, non
# colleghi PT) + 30% iperlocale Modena (potenziali clienti in presenza).
#
# I numeri "N-M" accanto a ciascun hashtag limitano quante interazioni
# fare PER QUELLA sorgente per sessione. La somma dei range fitness
# (~70-110) e' ~2.3x la somma dei range locali (~30-48).
##############################################################################

## Hashtag - post recenti (utenti freschi, attivi adesso)
## Top -> fitness consumer ITA (target: persone normali, non addetti ai lavori).
hashtag-posts-recent: [
  dimagrirecondieta 9-13,
  perderepeso 9-13,
  trasformazionefisica 8-12,
  allenamentoacasa 8-12,
  allenamentodonna 7-11,
  vitasanaitalia 7-11,
  alimentazionesana 6-10,
  motivazioneitalia 6-10,
  fioranomodenese 4-7,
  sassuolo 4-7,
  modena 3-6,
  maranello 3-5,
  formigine 3-5
]

## Hashtag - top likers (utenti con engagement alto, target ottimi).
hashtag-likers-recent: [
  dimagrire 8-12,
  dietaitaliana 7-10,
  fitnesschallenge 6-9,
  motivazionefitness 6-9
]

## Place: persone che taggano luoghi vicini (coaching in presenza).
place-posts-recent: [
  "Fiorano Modenese 5-8",
  "Sassuolo 5-8",
  "Modena, Italy 4-7"
]

watch-video-time: 12-28
watch-photo-time: 3-5
can-reinteract-after: 720
delete-interacted-users: false

##############################################################################
# Actions per utente
##############################################################################

interactions-count: 1-3
likes-count: 1-3
likes-percentage: 85
stories-count: 1-2
stories-percentage: 25-35
carousel-count: 1-2
carousel-percentage: 40-55

interact-percentage: 55-70

follow-percentage: 30-40
follow-limit: 30

# Commenti AI: ~25% dei profili visti riceve un commento (anti-spam alto).
comment-percentage: 20-30
max-comments-pro-user: 1

# DM follow-back AI: 3-5 al giorno, almeno 4h dopo il follow, max 5gg di latenza.
dm-followback: 3-5
dm-followback-min-hours: 4
dm-followback-max-days: 5
dm-followback-skipped-list-limit: 100

skipped-list-limit: 12-18
skipped-posts-limit: 5
fling-when-skipped: 0
min-following: 30

##############################################################################
# Total Limits per sessione (hard cap)
##############################################################################

total-likes-limit: 60-90
total-follows-limit: 22-30
total-watches-limit: 30-50
total-successful-interactions-limit: 35-50
total-interactions-limit: 100-130
total-comments-limit: 3-5
total-pm-limit: 3-5
total-scraped-limit: 0

##############################################################################
# Daily Budget (persistente tra restart). Warm-up safe per i primi 14 giorni.
##############################################################################

daily-follows-cap: 60
daily-likes-cap: 220
daily-unfollows-cap: 60
daily-comments-cap: 10
daily-pm-cap: 8

##############################################################################
# AI Comments (Gemini). Key letta da .env.local (GEMINI_API_KEY).
##############################################################################

ai-comments-enabled: true
ai-comments-model: gemini-2.5-flash-lite
ai-comments-language: Italian
ai-comments-prompt-hint: >
  L'autore del commento e' un personal trainer / coach a Fiorano Modenese
  (provincia di Modena). Tono colloquiale, peer-to-peer, mai venditore.
  Niente menzioni del coaching, niente CTA. Soltanto commento empatico e
  pertinente al contenuto del post (fitness, alimentazione, motivazione,
  trasformazione, vita quotidiana).

##############################################################################
# AI Direct Messages (DM follow-back).
# Stile: icebreaker generico fitness. Saluto + UNA domanda semplice del tipo
# "cosa ti aspetteresti di vedere sul mio profilo?" / "su cosa stai
# lavorando?". Niente personalizzazione invasiva, niente coaching, niente
# vendita. Volutamente NON leggiamo la bio (ai-dm-fetch-bio: false) per
# mantenere tono generico.
##############################################################################

ai-dm-enabled: true
ai-dm-model: gemini-2.5-flash
ai-dm-language: Italian
ai-dm-allow-emoji: true
ai-dm-fetch-bio: false
ai-dm-fallback-to-file: true
ai-dm-prompt-hint: >
  Sono Simone, personal trainer e coach. Sto scrivendo a una persona che
  mi ha appena ri-seguito su Instagram. Voglio SOLO rompere il ghiaccio
  con un saluto caldo e UNA domanda generica sul suo mondo fitness,
  alternando in modo casuale tra:
  (a) "cosa ti aspetteresti di vedere sul mio profilo?"
  (b) "cosa ti piacerebbe vedere piu' spesso: allenamenti, alimentazione,
       motivazione, mindset?"
  (c) "su cosa stai lavorando in palestra o a casa in questo periodo?"
  (d) "qual e' la cosa che ti costa di piu' nel restare costante?"
  (e) "c'e' un argomento fitness su cui vorresti piu' chiarezza?"
  Scegline UNA sola in modo casuale, e rendila colloquiale.
  NIENTE menzioni di coaching, NIENTE prezzi, NIENTE proposte di
  collaborazione, NIENTE link, NIENTE CTA. Massimo 2-3 frasi brevi.
  Il messaggio deve sembrare scritto di getto da un essere umano,
  non un template.

##############################################################################
# Ending Session Conditions
##############################################################################

end-if-likes-limit-reached: false
end-if-follows-limit-reached: true
end-if-watches-limit-reached: false
end-if-comments-limit-reached: false
end-if-pm-limit-reached: false

##############################################################################
# Scheduling (2 fasce orarie umane)
##############################################################################

working-hours: [10.15-12.30, 18.30-22.15]
time-delta: 8-15
repeat: 240-340
total-sessions: 1
"""

out = Path("accounts/simonebestagno/config.yml")
out.write_text(CONFIG, encoding="utf-8")
print(f"WROTE {out} ({out.stat().st_size} bytes)")

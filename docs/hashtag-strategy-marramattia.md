# Hashtag & Keyword Strategy – @marramattia_fmgpro

> Nicchia: **bodybuilding / gare / palestra / coaching fitness IT**
> Obiettivo: crescita organica del bacino targetizzato (audience attiva, non bot).
> Doc di riferimento per:
> - Caption / hashtag dei post del profilo (lato content)
> - Selezione `hashtag-likers-recent` / `hashtag-posts-recent` per il bot (lato bacino)

---

## 1. Logica a 3 livelli

Lavorare su **3 livelli di volume**, mai solo su uno:

| Livello | Volume IG | Funzione | Tipico tasso follow-back |
| --- | --- | --- | --- |
| **Generici forti** | Altissimo (>10M) | Reach iniziale, indicizzazione | basso (5-10%) |
| **Intermedi** | Medio (500k-5M) | Target qualificato | medio (15-25%) |
| **Niche bodybuilding** | Basso (<500k) | Audience super-mirata | alto (30-45%) |

Mix consigliato in caption post: **3 generici + 4 intermedi + 5-7 niche** = ~12-15 hashtag (mai 30, attiva flag spam).

---

## 2. Cluster di hashtag

### 🔥 Generici forti
`#bodybuilding` · `#palestra` · `#fitness` · `#workout` · `#training` · `#muscle`

### ⚡ Intermedi
`#allenamento` · `#forza` · `#motivazione` · `#personaltraining` · `#gymlife` · `#fitnessmotivation`

### 🎯 Niche bodybuilding (priorità per coach)
`#muscoli` · `#massa` · `#definizione` · `#gara` · `#prep` · `#ifbb` · `#classicphysique` · `#bodybuildingitalia` · `#palestraitalia`

---

## 3. Keyword da inserire nel testo (caption + Reel)

Non solo hashtag: queste **parole nel testo** alimentano la search interna di IG e
migliorano la SEO interna del profilo.

- bodybuilding
- gara · gare · preparazione gara · pre-contest
- massa · definizione
- pose · posing
- allenamento gambe · schiena · petto · spalle

Regola: la keyword deve essere **nel testo della caption**, non solo negli hashtag in
coda. Anche nei sottotitoli/voiceover dei Reel.

---

## 4. Set pronti per i post

### Set A — Post gara / prep
```
#bodybuilding #precontest #gara #definizione #prep
#classicphysique #ifbb #bodybuildingitalia #palestra #muscoli
```

### Set B — Post allenamento
```
#palestra #bodybuilding #allenamento #forza #workout
#gymlife #muscoli #fitnessitalia #motivazione #training
```

### Set C — Post posing / fisico
```
#posing #bodybuilding #physique #classicphysique #definizione
#muscle #palestraitalia #fititalia #gara #prep
```

---

## 5. Mapping → configurazione bot

Per le run config `hashtag-likers-recent` / `hashtag-posts-recent` partire dai
cluster **intermedi + niche**, evitando i generici (troppo rumore / troppi bot).

### Pool "growth" consigliato (rotare 5-6 alla volta):
```
palestra
palestrafitness
fitnessitalia
allenamento
schedaallenamento
bodybuildingitalia
palestraitalia
personaltraining
gymlife
forza
muscoli
definizione
prep
precontest
classicphysique
ifbbitalia
naturalbodybuilding
```

### Pool "conversion alta" (niche, follow-back top):
```
bodybuildingitalia
palestraitalia
ifbbitalia
naturalbodybuilding
classicphysique
precontest
prep
definizione
gara
posing
```

> **Nota anti-pattern**: ruotare la lista hashtag del bot ogni ~2-3 settimane
> per evitare che IG marchi il profilo come "always interacting with same
> hashtag pool". `truncate-sources: 10-12` in `config.yml` già fa una rotazione
> per-sessione automatica.

---

## 6. TL;DR per il bot (cosa cambia operativamente)

1. **Sostituire** `hashtag-likers-recent` in `config.yml` con un pool di **10-12 hashtag** dai cluster *intermedi + niche*.
2. **Aggiungere** `hashtag-posts-recent` (commento IA contestuale) con 5-6 hashtag *niche* ad alta conversione.
3. **Lato content** (gestito a mano dall'utente, non dal bot): usare i 3 set pronti sopra nei post propri.


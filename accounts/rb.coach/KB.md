# KB — Roberto Buonomo / RB Coaching

Base di conoscenza usata per costruire i prompt AI e scegliere le sorgenti di
questo account. Aggiornala se cambiano titoli, sede o servizi: i prompt in
`config.yml` (`ai-comments-prompt-hint`) vanno riallineati di conseguenza.

**Raccolta**: 19/08/2026, da fonti pubbliche (sito ufficiale, profili social).

## Persona

| Campo | Valore |
|---|---|
| Nome | Roberto Buonomo |
| Nato | 14/10/1993, Napoli |
| Base operativa | Milano |
| Ruoli | Atleta professionista IFBB, coach online, personal trainer, educatore alimentare |
| Formazione | Master in Economia e Finanza (2018) |
| Certificazioni | Personal Trainer, Educatore Alimentare, Functional Fitness Instructor |

Autodefinizione dal sito: *"Sono un trainer, coach online, Educatore Alimentare
e Atleta Professionista di Body Building"*.

Percorso: sportivo da bambino (nuoto, calcio, sci, tennis) fino a un infortunio
nel 2008 che chiude l'attività agonistica. Arriva in palestra per la
riabilitazione e la cosa diventa il mestiere.

## Palmares

- **2017** — Campione Italiano WABBA, categoria Junior BB (under 23)
- **2019** — Campione Italiano IFBB, categoria heavyweight fino a 100kg + titolo assoluto BB
- **2019** — Pro card IFBB a 25 anni (indicato come il più giovane professionista italiano in attività)
- **2019** — 2° posto Arnold Classic Barcelona, BB -100kg
- **2020** — BB Overall, Ben Weider Classic

Come coach ha portato atleti a finali e vittorie di categoria ai Campionati
Italiani IFBB (Bikini Junior, Men's Physique). Il team RB Coaching dichiara
**18 IFBB PRO Card** prodotte e **1 atleta qualificato Olympia**.

## Business

**FIT LAB Milano** — studio privato di cui è CEO.
Indirizzo: Corso di Porta Romana 72, Milano.

Servizi in studio:
1. **Personal Training Class** — piccoli gruppi da massimo 4-5 persone, con correzione della tecnica
2. **One-to-One** — sessione da un'ora con trainer dedicato
3. **Analisi antropometrica + programma** — bioimpedenza/plicometria, valutazione posturale, scheda su misura, indicazioni nutrizionali di un biologo, protocollo integrazione

**Coaching online** — scheda personalizzata, piano alimentare, piano
integrazione, supporto WhatsApp, check settimanali. Collabora con un biologo
nutrizionista per la parte nutrizionale.

Posizionamento: alternativa alle palestre affollate, per chi vuole seguire un
percorso serio con supervisione sulla tecnica. Frase ricorrente sul sito:
*"Sei seguito in ogni momento"*.

### Struttura: societa' di consulenza, non coach singolo

*Fonte: indicazione diretta del cliente, 27/08/2026. Non compare sul sito, che
descrive ancora Roberto come professionista singolo.*

RB Coaching e' una **societa' di consulenza di personal trainer**, non
l'attivita' di una persona sola:

- Roberto e' l'**head coach**: la guida tecnica e il riferimento del metodo
- altri **personal trainer collaborano** come parte del team
- lo scopo dichiarato e' migliorare le persone nel loro insieme: **stile di
  vita e sostenibilita' delle abitudini nel tempo**, non il singolo ciclo di
  allenamento

**Conseguenza diretta sul bot**: i personal trainer non sono piu' concorrenti
da escludere ma un pubblico da raggiungere, **in particolare a Milano**, perche'
sono potenziali collaboratori del team. Per questo in `filters.yml` sono state
tolte dalla blacklist le parole "personal trainer", "coach online",
"preparatore" e "istruttore", ed e' stato alzato il tetto follower da 4.000 a
15.000: con i valori precedenti un PT avviato non sarebbe mai stato raggiunto.

Il prompt dei commenti deve quindi reggere **due pubblici**: chi si allena, con
cui il registro resta semplice, e i colleghi professionisti, con cui si parla da
pari a pari e il gergo tecnico e' appropriato.

## Tono di voce

Professionale ma diretto. Precisione tecnica unita a spinta motivazionale.
Non fa il guru: parla di metodo, progressione, costanza. Sul sito usa formule
nette (*"Nulla di più sbagliato"*) per contrapporsi alla palestra generalista.

Nei commenti del bot questo si traduce in: registro tecnico **solo quando la
caption lo giustifica**, tono da pari a pari, mai vendita, mai menzione di
FIT LAB o dei servizi.

## Account social

| Piattaforma | Handle | Note |
|---|---|---|
| Instagram | `@rb.coach` | ROBERTO BUONOMO & COACHING TEAM — **account gestito dal bot** |
| Instagram | `@roberto_buonomo_ifbbpro` | profilo personale atleta (in whitelist, mai unfolloato) |
| Instagram | `@fitlab_milano` | studio (in whitelist + usato come sorgente) |
| Threads | `@roberto_buonomo_ifbbpro` | |
| TikTok | `@roberto.buonomo.ifbbpro` | |
| Sito | robertobuonomo.com | |

## Target del bot

Chi si allena seriamente o vuole iniziare, con interesse verso preparazione,
alimentazione e tecnica. Fascia follower 100-8000: sopra non converte in
clienti, sotto sono spesso account nuovi o inattivi.

Geografia: bacino nazionale del bodybuilding agonistico (federazioni, atleti
IFBB, coach) più il locale Milano/Lombardia via hashtag.

## Fonti

- https://www.robertobuonomo.com/chi-sono/
- https://www.robertobuonomo.com/servizi-fit-lab/
- https://www.instagram.com/rb.coach/
- https://www.threads.com/@roberto_buonomo_ifbbpro

# Commenti e profili seguiti prima del bot

## Obiettivo autorizzato

Sul branch `robertobuonomo`, per `rb.coach` e
`roberto_buonomo_ifbbpro`, impedire i commenti ai profili che Roberto seguiva
prima dell'utilizzo del bot. I profili seguiti successivamente DAL BOT possono
essere commentati, rispettando gli altri filtri, i limiti e le esclusioni dei
job. Il cliente ha chiarito che i follow manuali non devono essere ammessi.
Ha inoltre autorizzato esplicitamente Sol senza Astra: implementazione e
revisione dirette nella chat, senza delega.

## Baseline e riscontri

- Workspace: `C:\Users\mat.marra\PycharmProjects\instagram-bot-robertobuonomo`.
- Branch: `robertobuonomo`.
- HEAD: `b9964f7989b536abc5e2c106f750ddcc4fce60ba`.
- Albero Git pulito prima della creazione di questo documento.
- Entrambi i `filters.yml` impostano `skip_following: true`: oggi vengono
  saltati tutti i profili attualmente seguiti, senza distinzione temporale.
- `interact_blogger.py` sospende tale filtro per il job blogger, salvo
  `blogger-skip-following: true`. Quest'ultimo e' attivo nei config normale e
  alternato di IFBBPro; non e' attivo per rb.coach.
- Il filtro dispone gia' dello storage specifico dell'account.
- `Storage.add_interacted_user` sovrascrive `following_status` e `followed`
  anche durante successive interazioni senza follow. Questi campi da soli non
  sono una prova persistente dell'origine del follow.
- Non sono presenti file JSON di cronologia nei due account in questo
  workspace. Non e' stata interrogata o modificata la macchina di produzione.
- Sono presenti anche esclusioni autonome come
  `blogger-followers-no-comment: true`, da preservare.

## Contratto implementato

1. Introdurre una politica opzionale per account, disattivata per gli altri
   account, attiva nei due account di Roberto e conservata quando il job
   blogger sospende i filtri ordinari.
2. I profili attestati come seguiti prima del bot non ricevono commenti da
   alcun percorso. Una successiva interazione, unfollow o nuovo follow non
   cancella tale protezione.
3. Registrare una prova persistente di follow riuscito dal bot, senza
   cancellarla in interazioni successive. La visita, il like, il commento,
   lo stato del pulsante Instagram non sono da soli una prova di follow
   effettuato dal bot. Una richiesta inviata dal bot costituisce prova della
   sua origine; l'accesso ai post resta soggetto ai normali filtri di privacy.
4. I follow manuali restano esclusi anche se successivi all'avvio del bot.
   Non assumere che un profilo assente dalla cronologia fosse assente dalla
   lista dei seguiti prima del bot. In mancanza di prova, conservare
   l'esclusione dei profili gia' seguiti e documentare questo limite.
5. Recuperare l'evidenza dai vecchi record solo quando i dati attestano un
   follow riuscito; non inventare date iniziali e non usare `last_interaction`
   come data di follow.
6. Permettere i commenti ai nuovi seguiti ammessi senza introdurre nuovi
   like/follow/sticker ai profili grandi o modificare le esclusioni delle
   sorgenti. La protezione pre-bot deve prevalere anche sul job blogger.
7. Dati mancanti, malformati o non leggibili non devono aprire una deroga al
   divieto. Mantenere l'isolamento tra gli account e normalizzare gli handle.

## Ambito della modifica

Esecuzione diretta con Sol su richiesta del cliente. Ambito: `GramAddict/core/storage.py`,
`GramAddict/core/filter.py`, `GramAddict/core/interaction.py`,
`GramAddict/plugins/interact_blogger.py`, argomenti/config di riferimento se
necessari, config e filtri dei soli due account di Roberto, test mirati e
documentazione della politica. Non scrivere i file di cronologia reali,
avviare Instagram, fare richieste a provider IA, installare dipendenze
o fare deploy. Commit e push autorizzati dal cliente il 05/10/2026.
Preservare le modifiche altrui.

Accettazione: vecchio seguito escluso; follow nuovo documentato ammesso;
visita senza follow non autorizza; prove conservate dopo interazioni e riavvio;
precedenza della protezione pre-bot; stato sconosciuto escluso; job blogger
non aggira la politica; job con commenti disattivati resta tale; account
distinti; config normale e alternato coerenti; comportamento invariato quando
la politica e' disattivata. Test locali senza device e senza provider IA.

## Persistenza e limiti

`skip_following_before_bot: true` nei due `filters.yml` mantiene la protezione
su tutti i job, inclusi i config normale e alternato. Le osservazioni sono
salvate nello stato locale `accounts/<account>/following_origins.json`, separato
per account. I follow riusciti registrano `followed_by_bot: true` nella
cronologia delle interazioni e conservano anche l'evidenza legacy ancora
disponibile. Le visite senza follow conservano lo stato di follow/unfollow
per non togliere candidati dalla coda unfollow o aggirare il divieto di refollow.
I profili nuovi non ancora seguiti possono essere commentati
secondo le regole gia' presenti; non serve seguirli prima del commento.

Il primo profilo incontrato gia' seguito, senza prova di follow del bot, viene
protetto permanentemente. Le visite successive non cancellano la protezione.
Se le informazioni sui vecchi follow del bot erano gia' state sovrascritte
prima di questa modifica, non vengono ricostruite da date o supposizioni:
quei profili restano esclusi. File di origine malformati o scritture fallite
chiudono l'eccezione, senza sovrascrivere file malformati.

## Stato e verifica

Implementazione completata e rivista con Sol, senza Astra o worker Flash.
73 test mirati superati: `test/test_pre_bot_following.py`,
`test/test_supplements.py`, `test/test_ai_comment_quality.py`.
Il virtualenv principale contiene le dipendenze del bot ma non pytest;
la verifica usa Python 3.14 di sistema con pytest e il site-packages del
virtualenv tramite PYTHONPATH, senza installazioni. `git diff --check` passato.
Nessun run Instagram o deploy effettuato. La regola entra in
funzione dal prossimo avvio del bot con questo codice e questi filtri.

## Correzione dopo la segnalazione del 05/10/2026

Il cliente segnala l'apertura dei post di `valentinotozzi`, gia' seguito
prima del bot. I dati della macchina che esegue il bot mostrano
`following_status: none`, `followed: false`, 14 like, 12 commenti e origine
`not_following`; `skip_following_before_bot` e' attivo. Non e' quindi la prova
di un follow del bot a spiegare l'accesso: il profilo e' stato letto come non
seguito. Il frammento di log non include il testo effettivo del pulsante, per
cui il formato preciso che ha provocato la lettura non e' confermato.

Riproduzione locale: `getFollowButton` trasformava `Following `, `Following`
con newline e qualsiasi testo sconosciuto con prefisso `Follow` in `FOLLOW`.
Ora normalizza gli spazi e restituisce `NONE` per testi non riconosciuti,
facendo saltare il profilo invece di presumere che non sia seguito.

La lista per account `pre_bot_following` contiene i vecchi seguiti confermati
dal cliente e prevale sulle letture e sui record precedenti. Per `rb.coach`
contiene `valentinotozzi`, quindi vale sia per config normale sia alternato.
Il controllo salva la protezione permanente e blocca anche il gate finale del
commento. La correzione del pulsante vale per entrambi gli account.

I log `[following-policy]` mostrano pulsante e origine a ogni verifica,
senza registrare informazioni private. La prima riproduzione falliva in
12 casi su 14; dopo la correzione sono passati 125 test su filtri, commenti,
ricerca e navigazione, seguiti da ulteriori test con il record fornito dal
cliente e il gate finale.

Su ulteriore richiesta del cliente, per entrambi gli account protetti la
lettura dello stato usa esclusivamente il sottoalbero dell'intestazione del
profilo nel dump completo. Esclude suggerimenti e contatori dei seguiti e
non ripiega sui pulsanti Follow generici. Stati in conflitto, testi ignoti,
intestazione mancante o dump fallito restituiscono NONE: niente apertura dei
post o commenti. Questo puo' aumentare gli scarti se la versione di Instagram
non espone un'intestazione riconoscibile; la prudenza e' intenzionale.
I test verificano anche che entrambi i filtri attivino la lettura rigorosa.
Verifica finale: 138 test superati sui cinque moduli coinvolti e
`git diff --check` passato.
Nessuna verifica su device di produzione eseguita.

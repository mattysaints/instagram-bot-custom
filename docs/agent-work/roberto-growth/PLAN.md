# Crescita dei profili di Roberto

## Obiettivi confermati

- `rb.coach`: raggiungere sia persone comuni sia atleti. I percorsi di
  coaching sono distinti. Il contatto commerciale avviene in DM con il team.
  Il pubblico è nazionale, con attenzione anche a Milano.
- `roberto_buonomo_ifbbpro`: raggiungere persone interessate ai contenuti di
  bodybuilding e alla vita dell'atleta.
- Non seguire né commentare post su integratori o promozioni di altri marchi.
- Non commentare persone seguite prima dell'uso del bot. I profili seguiti
  successivamente dal bot possono ricevere commenti, se gli altri filtri lo
  consentono.
- Migliorare i commenti IA. Usare soltanto i contenuti effettivamente letti.
- Usare un tono naturale e diretto, come parlerebbe Roberto.
- Escludere anche i post su Tsunami Nutrition. Una menzione `@` senza segnali
  commerciali non basta per escludere un post.
- Se il testo del post non è leggibile, il follow può comunque avvenire. Il
  commento viene saltato quando manca una didascalia sufficiente.
- I soli hashtag non costituiscono una didascalia sufficiente. Servono tre
  parole di testo, esclusi hashtag e menzioni.
- I due account possono commentare lo stesso post, anche a distanza di tempo.
- Un profilo trovato tra i like di un luogo deve avere un segnale fitness nel
  nome, nel nome utente, nella biografia o nella categoria. Chi arriva dalle
  sorgenti fitness resta considerato anche con una biografia generica.

## Evidenze

- `skip_following_before_bot` è attivo per entrambi gli account. La verifica
  su emulatore del 5 ottobre ha riconosciuto un profilo già seguito. Non
  dimostra che tutte le versioni di Instagram siano riconosciute.
- Il controllo integratori cerca segnali testuali e marchi noti. Non copre
  tutti i marchi. Nel job `blogger` il filtro sulle pagine di integratori è
  sospeso. Se didascalia ed etichetta non sono visibili, il post passa.
- Il modello riceve la didascalia, non il contenuto della foto o del video.
  Non può identificare in modo affidabile un marchio presente solo nel media.
- Entrambi i file `config-alternato.yml` consentivano commenti predefiniti in
  caso di errore IA. Sono stati allineati alle rispettive configurazioni
  normali. Il commento viene saltato se l'IA non risponde.
- Il job `blogger-followers` ha i commenti disabilitati per entrambi gli
  account. Non significa che i follower di queste sorgenti non siano target.
- Le cronologie degli account di produzione non sono presenti nel workspace.
  Non è possibile calcolare conversioni, provenienza dei follower o qualità
  effettiva dei commenti dai dati locali.
- Il cliente non ha al momento dati recenti su visite, DM e clienti acquisiti.
- Lo Space IA è attivo e risponde al controllo di salute. I 58 eventi recenti
  disponibili non bastano a giudicare la qualità dei commenti generati.
- Sono disponibili contenuti pubblici indicizzati di entrambi i profili. Non
  è stato possibile aprire direttamente le pagine Instagram dal browser web.

## Ordine di lavoro

1. Rafforzare i controlli prima di follow e commenti. Il controllo deve valere
   anche per `blogger` e preservare la protezione dei seguiti precedenti.
   Distinguere i post promozionali dalle menzioni personali. Non dedurre che
   una sorgente sia seguita solo perché è presente in `blogger-followers`.
2. Allineare i comportamenti di commento nei file normale e alternato. È già
   stato fatto per prompt, commenti di riserva e controlli sulla didascalia.
3. Usare esempi reali di commenti graditi e non graditi per migliorare i
   prompt e i controlli. Valutare separatamente i due profili con le fixture
   già presenti. Nessun test a pagamento o pubblicazione automatica.
4. Misurare contatti utili e interesse per i contenuti prima di modificare
   volumi e sorgenti. Servono dati o un metodo condiviso con il cliente.

## Contratto di verifica per la modifica ai marchi

- Entrambi gli account e le configurazioni normale/alternata sono coperti.
- I post riconosciuti come promozionali o relativi a integratori non ricevono
  commenti né provocano il follow del profilo. Sono inclusi i post Tsunami.
- Nessun job sospende la protezione richiesta sui marchi. I profili già
  seguiti prima del bot restano protetti anche nel job `blogger`.
- Didascalia, etichetta di partnership, profilo e dati non leggibili hanno
  comportamenti espliciti. Nessuna garanzia è attribuita alla sola immagine.
- Sono necessari test per promozioni note, marchi diversi dagli integratori,
  falsi positivi, dati assenti e isolamento degli altri account.
- Non modificare i file di stato reali. Non inviare commenti o DM. Non fare
  commit, push o pubblicare lo Space senza autorizzazione specifica.

## Stato al 31 ottobre

- Le configurazioni normali e alternate usano un tono naturale e diretto. Le
  varianti dello stesso account usano le stesse istruzioni per l'IA. Il file
  di prova singola di rb.coach è allineato alle stesse impostazioni.
- Il validatore scarta le due forme generiche o tronche mostrate nello
  screenshot. Le didascalie assenti o troppo povere non generano commenti.
- Il controllo sui marchi è attivo in entrambi gli account. Rileva prodotti
  e marchi di integratori noti, indicazioni commerciali e partnership. Rileva
  anche marchi sportivi noti, come Nike, Adidas, Gymshark e Technogym, sia nei
  post sia nei nomi dei profili. Non è una lista completa dei marchi e non
  analizza foto o video. Una promozione priva di segnali leggibili può passare.
- La selezione per luogo è attiva per i job `place-likers` e viene verificata
  prima di like, follow e commenti. I job `place-posts` non sono attivi nei
  due account e richiedono un controllo precedente al like prima dell'uso.
- Sono passati 163 test mirati. Non sono state eseguite generazioni a
  pagamento né prove di pubblicazione su Instagram.
- Il lavoro è locale sul ramo `robertobuonomo`. La richiesta esplicita in chat
  di procedere senza Astra è stata rispettata: nessun worker o test di routing.
- Resta da misurare la qualità dei nuovi commenti con esempi reali approvati.
  Il cliente proverà personalmente il bot dopo la pubblicazione.

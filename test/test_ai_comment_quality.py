"""Test dei filtri di qualita' sui commenti AI (GramAddict/core/ai_comment.py).

Tutti i casi vengono dai log reali (1971 commenti generati tra giugno e
settembre 2026): il modello non vede la foto e su caption vuote o fatte di
sole emoji inventava dettagli fisici; il ragionamento interno dei modelli
reasoning mangiava i token e uscivano frammenti; a volte si rivolgeva a
"Mattia", cioe' a chi scrive.
"""
from GramAddict.core.ai_comment import (
    caption_is_usable,
    caption_skip_reason,
    clean_caption,
    comment_is_acceptable,
)


# --- clean_caption -----------------------------------------------------------
def test_toglie_il_more_di_instagram():
    assert clean_caption("Quasi avevo dimenticato l'emozione del 2 tempi 💥🚀… more") == (
        "Quasi avevo dimenticato l'emozione del 2 tempi 💥🚀"
    )
    assert clean_caption("Installing some new good habits... ✨... more") == (
        "Installing some new good habits... ✨"
    )


def test_toglie_handle_del_poster_anche_se_diverso_dal_target():
    # post in collaborazione: il primo autore non e' il target
    assert clean_caption(
        "gastronomiadaluciano_udine Carote, pomodoro, zucchine", target_username="altro"
    ) == "Carote, pomodoro, zucchine"
    assert clean_caption("tkd.drago.jesolo Taekwondo Drago Jesolo ❤️… more") == (
        "Taekwondo Drago Jesolo ❤️"
    )
    assert clean_caption("@marco.rossi_pt Nuovo blocco") == "Nuovo blocco"


def test_toglie_il_target_username_anche_senza_punti_o_cifre():
    assert clean_caption("frankdellisola Non sei mai solo in palestra.", "frankdellisola") == (
        "Non sei mai solo in palestra."
    )


def test_non_tocca_parole_italiane_minuscole():
    # "buongiorno", "ciao", "slow" hanno la forma di un handle ma non hanno . _ o cifre
    assert clean_caption("buongiorno Roma") == "buongiorno Roma"
    assert clean_caption("ciao a tutti") == "ciao a tutti"
    assert clean_caption("Slow life 💫") == "Slow life 💫"


def test_caption_vuota_o_none():
    assert clean_caption(None) == ""
    assert clean_caption("   ") == ""


# --- caption_is_usable -------------------------------------------------------
def test_caption_vuota_o_solo_emoji_non_e_usabile():
    for c in ["", "☃️☃️", "🥰❤️", "🩷", "<3", "🔰🔰", "#gym"]:
        assert not caption_is_usable(c), c


def test_caption_troppo_corta_non_e_usabile():
    for c in ["Mare", "MIA🏝️", "Dulcis in", "Slow life 💫"]:
        assert not caption_is_usable(c), c


def test_caption_con_testo_vero_e_usabile():
    for c in [
        "Mare = parole crociate",  # 3 parole vere: il modello ha qualcosa
        "Ultimo tramonto a Lisbona prima di tornare",
        "Check a 6 settimane dalla gara",
        "#Ottobrata #arezzo #domeniche #Italia",  # 4 hashtag "parlanti"
        "Deseando volver a subir a ese escenario",
    ]:
        assert caption_is_usable(c), c


def test_alfabeto_non_latino_si_salta():
    # il modello ne indovina il senso e in italiano esce una frase a caso
    # (nel banco: "чуть было не было" -> "Quasi scappato, che sollievo.")
    assert caption_skip_reason("чуть было не было") == "alfabeto non latino"
    assert caption_skip_reason("#اغاني#عراقية") == "alfabeto non latino"
    # un nome straniero dentro una frase italiana va benissimo
    assert caption_is_usable("Ultimo giorno a Санкт-Петербург, città incredibile")


def test_soli_hashtag_generici_si_saltano():
    # i commenti piu' generici del banco venivano da caption di soli hashtag
    assert caption_skip_reason("#gym #bodybuilding #workout") == "solo hashtag generici"
    assert caption_skip_reason("#love#family#relax") == "solo hashtag generici"
    assert caption_skip_reason("#instamood #instamoment #photo #my") == "solo hashtag generici"
    # ma 3+ hashtag "parlanti" danno un tema concreto
    assert caption_is_usable("#Ottobrata #arezzo #domeniche #Italia")
    # e basta una parola fuori dagli hashtag per tornare alla regola normale
    assert caption_is_usable("Domenica ad Arezzo #gym #love")


def test_temi_adult_si_saltano():
    assert caption_skip_reason("Sempre disponibile 🔥💋H24🔥 #uominibelli") == "tema adult/escort"
    assert caption_skip_reason("Link in bio, onlyfans aperto") == "tema adult/escort"
    assert caption_is_usable("Aperti h 9-20, chiusi la domenica, vi aspettiamo")


def test_min_words_configurabile():
    assert caption_is_usable("Mare = parole crociate", min_words=3)
    assert not caption_is_usable("Mare = parole crociate", min_words=4)
    assert caption_is_usable("", min_words=0)


# --- comment_is_acceptable ---------------------------------------------------
def test_frammenti_reali_dei_log_vengono_scartati():
    # sui 138 frammenti dei log questo filtro ne prende 123 con zero falsi
    # positivi. I troncati a meta' parola ("Bel percorso di ricom", "Una buona t")
    # non sono distinguibili senza dizionario: li chiude lo Space controllando
    # finish_reason == "length" (vedi ig-comment-space/app.py).
    for c in ["Bello il", "Benvenuto nel", "Ottima intensità per", "Ottima", "Ottima densità", "In effetti non"]:
        ok, why = comment_is_acceptable(c)
        assert not ok, c
        assert why


def test_commento_completo_passa():
    for c in [
        "Il tiramisù della nonna con sei uova non si discute.",
        "Quattordici pastel de nata in una settimana a Lisbona sono un record rispettabile",
        "Bruno ha già capito chi comanda sul divano",
    ]:
        ok, why = comment_is_acceptable(c)
        assert ok, (c, why)


def test_commento_che_finisce_senza_punto_ma_con_parola_piena_passa():
    ok, _ = comment_is_acceptable("Che squadra siete diventati in questo anno di Hyrox")
    assert ok


def test_commento_che_nomina_chi_scrive_viene_scartato():
    ok, why = comment_is_acceptable("Le abitudini sono tutto in prep, continua così Mattia", author_name="Mattia")
    assert not ok and "Mattia" in why
    # senza author_name configurato non si puo' sapere: passa
    ok, _ = comment_is_acceptable("Le abitudini sono tutto in prep, continua così Mattia")
    assert ok


def test_nome_dentro_altra_parola_non_scatta():
    ok, _ = comment_is_acceptable("La mattinata in valle deve essere stata rigenerante", author_name="Mattia")
    assert ok


# --- post-filtri consigliati dal panel di giudici -----------------------------
def test_scarta_commento_che_ricopia_la_caption():
    cap = "Ultimo tramonto a Lisbona prima di tornare. Pastel de nata numero 14 della settimana, zero rimpianti"
    ok, why = comment_is_acceptable(
        "Ultimo tramonto a Lisbona prima di tornare, che invidia.", caption=cap
    )
    assert not ok and "ricopia" in why
    # riprendere UNA parola o due va bene
    ok, _ = comment_is_acceptable("Quattordici pastel de nata sono un record rispettabile.", caption=cap)
    assert ok


def test_scarta_prima_persona():
    for c in ["Adoro l'idea di nuove abitudini, sembra un ottimo percorso.", "Per me il tiramisù è il dolce perfetto."]:
        ok, why = comment_is_acceptable(c)
        assert not ok and why == "prima persona", c


def test_scarta_handle_e_attacco_quel():
    ok, why = comment_is_acceptable("Stasera @thv ha cambiato ruolo guardando @gr.")
    assert not ok and "@" in why
    ok, why = comment_is_acceptable("Quel tramonto rosato rende tutto più bello.")
    assert not ok and "Quel" in why
    # "quel" a meta' frase non e' un tic da bot
    ok, _ = comment_is_acceptable("Con quel caffè lungo il tiramisù viene meglio.")
    assert ok


def test_scarta_commento_troppo_lungo():
    long = " ".join(["parola"] * 23) + "."
    ok, why = comment_is_acceptable(long)
    assert not ok and "troppo lungo" in why


def test_scarta_inglese_e_domande():
    # i modelli di riserva della cascata (qwen) li producono anche con language=Italian
    ok, why = comment_is_acceptable("Lifting 300 kg in a single day is intense.")
    assert not ok and "inglese" in why
    ok, why = comment_is_acceptable("Ottobre ad Arezzo porta un'aria diversa, ti invecchia?")
    assert not ok and "domanda" in why
    # parole inglesi prese in prestito dentro una frase italiana passano
    ok, _ = comment_is_acceptable("Impressionante il tuo 225lb snatch, speriamo in altri record presto.")
    assert ok


def test_prima_persona_solo_se_autoreferenziale():
    # "penso a te" su un post di malattia e' il commento giusto
    ok, _ = comment_is_acceptable("Ti abbraccio forte e penso a te in questi giorni duri")
    assert ok
    ok, why = comment_is_acceptable("Anche io ho fatto la stessa gara, per me è stata durissima.")
    assert not ok and why == "prima persona"


def test_polish_toglie_davvero():
    from GramAddict.core.ai_comment import polish_comment
    assert polish_comment("Una palestra così fa davvero la differenza.") == "Una palestra così fa la differenza."
    assert polish_comment("Il pensiero spinge davvero , tienilo vivo.") == "Il pensiero spinge, tienilo vivo."


def test_hint_per_chiamata_ha_la_variazione():
    from GramAddict.core.ai_comment import build_hint_for_call, _VARIATIONS
    h = build_hint_for_call("BASE")
    assert h.startswith("BASE")
    assert any(h == "BASE" or h == f"BASE {v}" for v in _VARIATIONS)

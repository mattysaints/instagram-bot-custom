"""Test del filtro integratori (GramAddict/core/supplements.py).

Richiesta di Roberto (16/09/2026): niente follow, like o commento a pagine di
integratori ne' a post che li promuovono. Il criterio e' a due segnali
(prodotto / brand / promo): qui si verifica che prenda brand, shop e post
sponsorizzati, e che lasci in pace chi parla di creatina in un racconto, il
coach che cita "integrazione" fra i servizi, il nutrizionista.
"""
from GramAddict.core.supplements import (
    supplement_page_reason,
    supplement_promo_reason,
)


# --- pagine di integratori ---------------------------------------------------
def test_brand_riconosciuto_da_username_o_nome():
    assert supplement_page_reason("prozisitalia", "Prozis Italia", "Official account")
    assert supplement_page_reason("yamamoto_italia", "Yamamoto Nutrition", "")
    assert supplement_page_reason("tsunaminutrition", "", "")
    assert supplement_page_reason("biotechusa_it", "BioTechUSA Italia", "")
    assert supplement_page_reason("net_integratori", "NET Integratori", "")
    assert supplement_page_reason("xyz.supps", "XYZ Supps", "Made in Italy")
    assert supplement_page_reason("marco", "Marco's Supplements", "")
    assert supplement_page_reason("ps_it", "Protein Shop Italia", "")


def test_categoria_business_vitamins_supplements():
    assert supplement_page_reason("anna", "Anna", "Mangio e mi alleno", "Vitamins/Supplements")
    assert supplement_page_reason("anna", "Anna", "", "Vitamine/Integratori")
    assert supplement_page_reason("luca", "Luca", "", "Personal trainer") is None
    assert supplement_page_reason("luca", "Luca", "", "Nutritionist") is None


def test_bio_con_due_segnali_e_una_pagina():
    # brand + codice sconto: promoter
    assert supplement_page_reason("gino_ifbb", "Gino", "Athlete @prozis | codice GINO10 -10%")
    # brand + team/athlete: sponsorizzato
    assert supplement_page_reason("kenta_bb", "Kenta", "Team ESN athlete")
    assert supplement_page_reason("sara", "Sara", "Atleta Yamamoto Nutrition")
    # prodotto + shop
    assert supplement_page_reason("shopfit", "Shop Fit", "Integratori per lo sport, spedizione gratuita")
    # prodotto + brand
    assert supplement_page_reason("x", "X", "Le migliori whey di Myprotein")


def test_bio_con_un_solo_segnale_non_basta():
    # il coach che cita integratori/integrazione fra i servizi
    assert supplement_page_reason("giulia.fit", "Giulia", "Consulenze su allenamento, dieta e integratori") is None
    assert supplement_page_reason("marco_pt", "Marco PT", "Scheda, dieta e integrazione. Scrivimi per info") is None
    # una persona che nomina un brand senza venderlo
    assert supplement_page_reason("gino", "Gino", "Ex dipendente Yamamoto, ora coach") is None
    # il nutrizionista che parla di vitamina D
    assert supplement_page_reason("luca", "Luca", "Nutrizionista sportivo | Vitamina D e magnesio") is None
    # "team" e "athlete" da soli
    assert supplement_page_reason("rb.coach", "RB Coaching Team", "IFBB PRO athlete | head coach") is None


def test_username_e_nomi_innocui():
    assert supplement_page_reason("bodybuilding_italia_community_", "Bodybuilding Italia Community", "") is None
    assert supplement_page_reason("lifeprogram_it", "Life Program", "coaching online") is None
    assert supplement_page_reason("anna.nutrition", "Anna Nutrition Coach", "nutrizionista") is None
    assert supplement_page_reason("olimpia_gym", "Olimpia Gym", "") is None
    assert supplement_page_reason("", "", "") is None
    assert supplement_page_reason(None, None, None) is None


def test_parole_e_marchi_extra_da_filters_yml():
    assert supplement_page_reason("x", "X", "Glucosamina in offerta", extra_words=["glucosamina"])
    assert supplement_page_reason("x", "X", "Glucosamina in offerta") is None
    assert supplement_page_reason("nomebrand_it", "", "", extra_brands=["nomebrand"])
    # una regex rotta non manda in crash il filtro
    assert supplement_page_reason("x", "X", "", extra_words=["(("]) is None


# --- post che promuovono integratori -----------------------------------------
def test_post_promozionali_si_riconoscono():
    for c in [
        "Nuova whey al pistacchio disponibile, codice ROB15 per lo sconto 💚",
        "Il mio pre-workout preferito @yamamoto_italia 🔥",
        "Codice ROB10 su prozis.com per il -10%",
        "New RAW Nutrition flavor just dropped, use code CBUM at checkout",
        "Post workout protein shake with @proteinworks 💪",
        "Team @tsunaminutrition da oggi 🙏",
        "Creatina monoidrato, link in bio per il 20% off",
        "Grazie @xyz_nutrition per la fornitura di bcaa #adv",
    ]:
        assert supplement_promo_reason(c), c


def test_etichetta_partnership_vale_come_promo():
    assert supplement_promo_reason(
        "Il mio pre-workout del mattino", header_label="Partnership retribuita con Yamamoto"
    )
    assert supplement_promo_reason("Morning pwo ☕", header_label="Paid partnership with ESN")
    # l'etichetta da sola, senza prodotto ne' brand, non basta
    assert supplement_promo_reason("Domenica di riposo", header_label="Partnership retribuita con Nike") is None
    # una location sotto al nome non e' un'etichetta di sponsorizzazione
    assert supplement_promo_reason("Creatina e via", header_label="Milano, Italy") is None


def test_post_che_non_promuovono_niente():
    for c in [
        "Oggi creatina e via, gamba pesante",
        "Grazie @prozisitalia per lo shooting",
        "Ultimi 3 posti per il percorso online di ottobre. Scrivimi METODO in DM",
        "Trust the process.It's worth it",
        "Pizza del sabato sera dentro le calorie, 800 kcal",
        "Ho promosso l'esame di anatomia, ora creatina e festa",
        "La vita ordinaria di chi si allena alle 6: caffè e creatina",
        "Il team di rb coaching cresce: benvenuta Anna",
        "Codice sconto sul mio percorso di coaching, scrivimi",
        "",
        None,
    ]:
        assert supplement_promo_reason(c) is None, c


def test_il_motivo_spiega_cosa_ha_trovato():
    motivo = supplement_promo_reason("Codice ROB10 su prozis.com")
    assert "brand" in motivo and "promo" in motivo
    motivo = supplement_page_reason("prozisitalia", "", "")
    assert "prozis" in motivo.lower()


# --- integrazione con Filter e con la lettura del post ------------------------
def _filtro(**conditions):
    from GramAddict.core.filter import Filter

    f = Filter.__new__(Filter)
    f.conditions = conditions
    f.storage = None
    return f


def test_filter_spento_non_scarta_nulla():
    assert _filtro().supplement_post_reason("Codice ROB10 su prozis.com") is None
    assert _filtro(skip_supplement_posts=False).supplement_post_reason("whey @prozis") is None
    assert not _filtro().skips_supplement_posts()


def test_filter_acceso_usa_parole_extra():
    f = _filtro(skip_supplement_posts=True, supplement_words=["glucosamina"])
    assert f.supplement_post_reason("Glucosamina in sconto, link in bio")
    assert f.supplement_post_reason("Domenica di riposo") is None


class _Vista:
    def __init__(self, testo):
        self._testo = testo

    def exists(self, *a, **k):
        return self._testo is not None

    def get_text(self, error=True):
        return self._testo


class _DeviceFinto:
    """Il selettore risponde per resource-id; l'albero completo e' una lista
    di nodi come quella di DeviceFacade.nodes_from_dump."""

    def __init__(self, selettore=None, dump=None):
        self._selettore = selettore or {}
        self._dump = dump or []

    def find(self, resourceId=None, **k):
        return _Vista(self._selettore.get(resourceId))

    def nodes_from_dump(self):
        return list(self._dump)

    def text_from_dump(self, rid, nodi=None):
        import re

        for n in nodi if nodi is not None else self._dump:
            if re.fullmatch(rid, n["resource_id"]) and n["bounds"]:
                return n["text"]
        return ""


def _resource_ids(monkeypatch):
    """interaction.ResourceID lo imposta load_config a bot avviato: qui lo
    si mette a mano, come farebbe il bot con app-id com.instagram.android."""
    from GramAddict.core import interaction
    from GramAddict.core.resources import ResourceID

    rid = ResourceID("com.instagram.android")
    monkeypatch.setattr(interaction, "ResourceID", rid, raising=False)
    return rid


def test_lettura_caption_dal_selettore_e_dal_dump(monkeypatch):
    from GramAddict.core.interaction import _leggi_caption_post

    rid = _resource_ids(monkeypatch)
    # selettore: caption con l'handle del poster davanti e il "... more" in coda
    dev = _DeviceFinto(
        selettore={
            rid.ROW_FEED_COMMENT_TEXTVIEW_LAYOUT: "gino_ifbb Pre-workout @yamamoto_italia 🔥… more",
            rid.SECONDARY_LABEL: "Partnership retribuita con Yamamoto",
        }
    )
    caption, label = _leggi_caption_post(dev, "gino_ifbb")
    assert caption == "Pre-workout @yamamoto_italia 🔥"
    assert label == "Partnership retribuita con Yamamoto"
    # il selettore nega, l'albero completo no
    dev = _DeviceFinto(
        dump=[
            {"resource_id": rid.ROW_FEED_COMMENT_TEXTVIEW_LAYOUT, "text": "gino_ifbb Creatina e via", "bounds": {}},
        ]
    )
    dev._dump[0]["bounds"] = {"left": 0, "top": 0, "right": 1, "bottom": 1}
    caption, label = _leggi_caption_post(dev, "gino_ifbb")
    assert caption == "Creatina e via"
    assert label == ""
    # niente da leggere: stringhe vuote, nessuna eccezione
    assert _leggi_caption_post(_DeviceFinto(), "x") == ("", "")


def test_post_promuove_integratori_rispetta_il_filtro(monkeypatch):
    from GramAddict.core.interaction import _post_promuove_integratori

    rid = _resource_ids(monkeypatch)
    dev = _DeviceFinto(
        selettore={rid.ROW_FEED_COMMENT_TEXTVIEW_LAYOUT: "gino Codice GINO10 su prozis.com"}
    )
    assert _post_promuove_integratori(dev, _filtro(skip_supplement_posts=True), "gino")
    assert _post_promuove_integratori(dev, _filtro(), "gino") is None
    assert _post_promuove_integratori(dev, None, "gino") is None
    # caption non visibile: il post passa (fail-open)
    assert _post_promuove_integratori(_DeviceFinto(), _filtro(skip_supplement_posts=True), "gino") is None


def test_check_profile_scarta_la_pagina_di_integratori(monkeypatch):
    from GramAddict.core import filter as filter_mod
    from GramAddict.core.filter import Profile, SkipReason
    from GramAddict.core.views import FollowStatus

    def profilo(bio, nome="Gino", categoria=""):
        p = Profile(
            mutual_friends=0,
            follow_button_text=FollowStatus.FOLLOW,
            is_restricted=False,
            is_private=False,
            has_business_category=bool(categoria),
            posts_count=30,
            biography=bio,
            link_in_bio=None,
            fullname=nome,
            business_category=categoria,
        )
        p.set_followers_and_following(2000, 500)
        return p

    scartati = {}

    def registra(username, profile_data, skip_reason=None):
        scartati[username] = skip_reason
        return skip_reason is not None

    f = _filtro(skip_supplement_pages=True, skip_following=True)
    monkeypatch.setattr(f, "return_check_profile", registra)
    # ProfileView.getFirstPostAgeDays non si chiama (last_post_max_age_days assente)
    monkeypatch.setattr(filter_mod, "args", type("A", (), {"disable_filters": False})(), raising=False)

    # pagina di integratori: skip con il suo motivo
    monkeypatch.setattr(f, "get_all_data", lambda device: profilo("Integratori per lo sport | Spedizione gratuita"))
    _, skipped = f.check_profile(device=None, username="shop_fit")
    assert skipped and scartati["shop_fit"] is SkipReason.SUPPLEMENT_PAGE

    # categoria business
    monkeypatch.setattr(f, "get_all_data", lambda device: profilo("", categoria="Vitamins/Supplements"))
    _, skipped = f.check_profile(device=None, username="vitashop")
    assert skipped and scartati["vitashop"] is SkipReason.SUPPLEMENT_PAGE

    # persona normale: passa
    monkeypatch.setattr(f, "get_all_data", lambda device: profilo("Mi alleno a Milano, amo la pizza"))
    _, skipped = f.check_profile(device=None, username="anna")
    assert not skipped and scartati["anna"] is None

    # filtro spento: la pagina passa
    f.conditions["skip_supplement_pages"] = False
    monkeypatch.setattr(f, "get_all_data", lambda device: profilo("Integratori per lo sport | Spedizione gratuita"))
    _, skipped = f.check_profile(device=None, username="shop_fit")
    assert not skipped

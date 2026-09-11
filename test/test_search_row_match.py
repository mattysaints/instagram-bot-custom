"""Test del gate che impedisce di aprire un account DIVERSO da quello cercato.

Caso reale che ha motivato il gate (log 06-07/2026): la sorgente configurata
`bodybuilding_italia_community` non esiste su Instagram; la ricerca proponeva
`bodybuilding_italia_community_` (stesso follower count nei log) e il vecchio
fallback `textStartsWith` cliccava quella riga. Risultato: 18 sessioni su un
account mai messo in config, follow attribuiti a una sorgente fantasma e
quarantena nav che non scattava mai.
"""
from GramAddict.core.views import (
    is_account_target,
    normalize_ig_username,
    search_row_is_target,
)


def test_handle_piu_lungo_non_e_il_target():
    # il caso reale: prefisso identico, account diverso
    assert not search_row_is_target(
        "bodybuilding_italia_community_", "bodybuilding_italia_community"
    )
    assert not search_row_is_target(
        "bodybuilding_italia_community", "bodybuilding_italia_community_"
    )


def test_handle_identico_e_il_target():
    assert search_row_is_target(
        "bodybuilding_italia_community_", "bodybuilding_italia_community_"
    )


def test_case_insensitive():
    assert search_row_is_target("Melissa_Denti", "melissa_denti")


def test_ignora_spazi_lucchetto_e_zero_width():
    assert search_row_is_target("  melissa_denti  ", "melissa_denti")
    assert search_row_is_target("melissa_denti \U0001f512", "melissa_denti")
    assert search_row_is_target("​melissa_denti​", "melissa_denti")


def test_ignora_chiocciola_iniziale():
    assert search_row_is_target("@melissa_denti", "melissa_denti")


def test_riga_vuota_o_assente_non_matcha():
    assert not search_row_is_target(None, "melissa_denti")
    assert not search_row_is_target("", "melissa_denti")
    assert not search_row_is_target("melissa_denti", "")


def test_hashtag_confrontato_col_cancelletto():
    assert search_row_is_target("#garebodybuilding", "#garebodybuilding")
    assert not search_row_is_target("#garebodybuildingitalia", "#garebodybuilding")


def test_normalize_ig_username():
    assert normalize_ig_username(None) == ""
    assert normalize_ig_username("  @Sara.Faccins  ") == "sara.faccins"


def test_is_account_target():
    assert is_account_target("blogger-followers")
    assert is_account_target("blogger-post-likers")
    assert is_account_target("unfollow-from-file")
    assert is_account_target("account")
    assert is_account_target(None)
    assert not is_account_target("hashtag-likers-recent")
    assert not is_account_target("hashtag-posts-top")
    assert not is_account_target("place-likers-recent")


# ---------------------------------------------------------------------------
# Test di comportamento di SearchView._check_current_view SENZA device: un finto
# DeviceFacade riproduce la semantica di find(text=..., textStartsWith=..., index=...)
# e registra su quale riga sarebbe finito il click.
# ---------------------------------------------------------------------------
import re  # noqa: E402

from GramAddict.core import views as views_module  # noqa: E402


class _Args:
    app_id = "com.instagram.android"
    dont_type = True


class _Config:
    args = _Args()


views_module.load_config(_Config())


class _FakeView:
    def __init__(self, texts, clicks):
        self._texts = list(texts)
        self._clicks = clicks

    def exists(self, *args, **kwargs):
        return bool(self._texts)

    def count_items(self):
        return len(self._texts)

    def get_text(self, error=True, index=None):
        if not self._texts:
            return ""
        return self._texts[0 if index is None else index]

    def click(self, *args, **kwargs):
        self._clicks.append(self._texts[0])


class _FakeDevice:
    """Righe di ricerca finte. Con ``exact_find_broken`` simuliamo il caso reale
    in cui la find esatta va a vuoto (lista ancora in ridisegno) e solo la
    textStartsWith trova la riga giusta: quella navigazione deve continuare a
    funzionare."""

    def __init__(self, rows, exact_find_broken=False):
        self.rows = list(rows)
        self.exact_find_broken = exact_find_broken
        self.clicks = []

    def find(self, index=None, **kwargs):
        if "text" in kwargs:
            wanted = kwargs["text"]
            matched = [] if self.exact_find_broken else [r for r in self.rows if r == wanted]
        elif "textStartsWith" in kwargs:
            prefix = kwargs["textStartsWith"]
            matched = [r for r in self.rows if r.startswith(prefix)]
        elif "textMatches" in kwargs:
            # UiSelector.textMatches = full match (Pattern.matches)
            pattern = re.compile(kwargs["textMatches"])
            matched = [r for r in self.rows if pattern.fullmatch(r)]
        else:
            matched = list(self.rows)
        if index is not None and len(matched) > 1:
            matched = [matched[index]] if index < len(matched) else []
        return _FakeView(matched, self.clicks)


def _check(rows, target, exact_find_broken=False):
    device = _FakeDevice(rows, exact_find_broken=exact_find_broken)
    found = views_module.SearchView(device)._check_current_view(
        target, "blogger-followers"
    )
    return found, device.clicks


def test_non_clicca_account_con_prefisso_uguale():
    # il caso reale: in config c'era 'bodybuilding_italia_community', su IG esiste
    # solo 'bodybuilding_italia_community_'
    found, clicks = _check(
        ["bodybuilding_italia_community_", "altro_account"],
        "bodybuilding_italia_community",
    )
    assert not found
    assert clicks == []


def test_clicca_la_riga_giusta_anche_se_non_e_la_prima():
    rows = ["bodybuilding_italia_community_", "bodybuilding_italia_community"]
    found, clicks = _check(rows, "bodybuilding_italia_community")
    assert found
    assert clicks == ["bodybuilding_italia_community"]


def test_navigazione_normale_funziona():
    found, clicks = _check(["melissa_denti", "melissa_dentista"], "melissa_denti")
    assert found
    assert clicks == ["melissa_denti"]


def test_fallback_startswith_ancora_valido_se_la_find_esatta_racconta_nulla():
    found, clicks = _check(
        ["melissa_denti"], "melissa_denti", exact_find_broken=True
    )
    assert found
    assert clicks == ["melissa_denti"]


def test_nessun_risultato_nessun_click():
    found, clicks = _check(["tuttaltro_account"], "melissa_denti")
    assert not found
    assert clicks == []


class _FakeDeviceWithBack(_FakeDevice):
    def __init__(self, rows):
        super().__init__(rows)
        self.backs = 0

    def back(self):
        self.backs += 1


def test_profilo_aperto_diverso_torna_indietro_e_fallisce():
    device = _FakeDeviceWithBack(["bodybuilding_italia_community_"])
    view = views_module.SearchView(device)
    ok = view._verify_opened_profile("bodybuilding_italia_community", "blogger-followers")
    assert not ok
    assert device.backs == 1
    # il fallimento e' colpa dell'handle in config -> lo strike di quarantena va contato
    assert views_module.last_nav_failure() == views_module.NAV_FAIL_WRONG_ACCOUNT


def test_profilo_aperto_corretto_passa():
    device = _FakeDeviceWithBack(["melissa_denti"])
    view = views_module.SearchView(device)
    assert view._verify_opened_profile("melissa_denti", "blogger-followers")
    assert device.backs == 0


def test_verifica_saltata_per_hashtag_e_place():
    device = _FakeDeviceWithBack(["#tuttaltro"])
    view = views_module.SearchView(device)
    assert view._verify_opened_profile("#garebodybuilding", "hashtag-likers-recent")
    assert view._verify_opened_profile("McFIT Torino", "place-likers-recent")
    assert device.backs == 0


def test_titolo_profilo_illeggibile_non_blocca():
    device = _FakeDeviceWithBack([])  # nessun action bar leggibile
    view = views_module.SearchView(device)
    assert view._verify_opened_profile("melissa_denti", "blogger-followers")
    assert device.backs == 0


def test_handle_scritto_con_maiuscole_viene_comunque_trovato():
    # text=/textStartsWith= sul device sono case-sensitive: la 5a sonda regex
    # recupera un handle scritto in config con capitalizzazione diversa.
    found, clicks = _check(["melissa_denti"], "Melissa_Denti", exact_find_broken=True)
    assert found
    assert clicks == ["melissa_denti"]


def test_exact_ci_re_neutralizza_il_punto():
    pattern = views_module.exact_ci_re("il.pump.quotidiano")
    assert re.fullmatch(pattern, "il.pump.quotidiano")
    assert not re.fullmatch(pattern, "ilXpumpXquotidiano")


def test_exact_ci_re_hashtag():
    pattern = views_module.exact_ci_re("#garebodybuilding")
    assert re.fullmatch(pattern, "#garebodybuilding")
    assert not re.fullmatch(pattern, "#garebodybuildingitalia")

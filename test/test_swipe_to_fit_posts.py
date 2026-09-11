"""Test di PostsViewList.swipe_to_fit_posts senza device.

Crash reale riprodotto qui (7 volte nei log storici, ognuno con chiusura e
riapertura di Instagram e +1 su total-crashes-limit): dopo un post la cui lista
likers non si carica, lo scroll al post successivo chiama get_bounds() sul media
container che non c'e' piu' -> UiObjectNotFound -> DeviceFacade.JsonRpcError
propagata fino a run_safely.

    File ".../handle_sources.py", line 548, in handle_likers
        PostsViewList(device).swipe_to_fit_posts(SwipeTo.NEXT_POST)
    File ".../views.py", line 1088, in swipe_to_fit_posts
        PostsViewList(self.device).swipe_to_fit_posts(SwipeTo.HALF_PHOTO)
    File ".../views.py", line 1067, in swipe_to_fit_posts
        ).get_bounds()["bottom"]
    DeviceFacade.JsonRpcError: -32001 ... UiObjectNotFoundException
"""
import uiautomator2

from GramAddict.core import views as views_module
from GramAddict.core.device_facade import DeviceFacade
from GramAddict.core.views import PostsViewList, SwipeTo


class _Args:
    app_id = "com.instagram.android"
    dont_type = True


class _Config:
    args = _Args()


views_module.load_config(_Config())
RID = views_module.ResourceID

SCREEN = {"displayWidth": 1080, "displayHeight": 2400}


def _jsonrpc_error():
    """Stessa eccezione del crash reale: UiObjectNotFound incartata da DeviceFacade."""
    return DeviceFacade.JsonRpcError(
        uiautomator2.exceptions.UiObjectNotFoundError(
            {
                "code": -32001,
                "message": "<androidx.test.uiautomator.UiObjectNotFoundException>",
                "data": "UiSelector[RESOURCE_ID_REGEX=...media_content_location]",
            },
            method="objInfo",
        )
    )


class _View:
    """bounds=None -> l'elemento non esiste. raises -> eccezione sui bounds
    (l'elemento c'era all'exists() ed e' sparito subito dopo: la race reale).
    uia2_bug=True -> riproduce il bug uiautomator2 #689: exists() risponde False
    anche se l'elemento e' nell'albero, e solo ignore_bug=True lo rivela
    (device_facade.py restituisce la stringa "BUG!")."""

    def __init__(self, bounds=None, raises=None, uia2_bug=False):
        self._bounds = bounds
        self._raises = raises
        self._uia2_bug = uia2_bug

    def exists(self, ui_timeout=None, ignore_bug=False):
        present = self._bounds is not None or self._raises is not None
        if present and self._uia2_bug:
            return "BUG!" if ignore_bug else False
        return present

    def get_bounds(self):
        if self._raises is not None:
            raise self._raises
        if self._bounds is None:
            raise _jsonrpc_error()
        return self._bounds


class _Device:
    def __init__(self, elements):
        # elements: {resource_id_pattern: _View}
        self.elements = elements
        self.swipes = []

    def get_info(self):
        return dict(SCREEN)

    def find(self, index=None, **kwargs):
        rid = kwargs.get("resourceIdMatches") or kwargs.get("resourceId") or ""
        for pattern, view in self.elements.items():
            if pattern == rid:
                return view
        return _View()

    def swipe_points(self, sx, sy, ex, ey, **kwargs):
        self.swipes.append((int(sx), int(sy), int(ex), int(ey)))


def _media(bottom, top=300, raises=None, uia2_bug=False):
    return _View(
        bounds={"top": top, "bottom": bottom, "left": 0, "right": 1080},
        raises=raises,
        uia2_bug=uia2_bug,
    )


def test_half_photo_senza_media_container_non_solleva():
    device = _Device({})  # nessun elemento: il post non e' piu' a schermo
    assert PostsViewList(device).swipe_to_fit_posts(SwipeTo.HALF_PHOTO) is False
    # ha comunque fatto uno scroll di ripiego, cosi' la lista avanza
    assert len(device.swipes) == 1
    sx, sy, ex, ey = device.swipes[0]
    assert sx == ex == SCREEN["displayWidth"] // 2  # verticale, non di traverso
    assert sy > ey  # verso il basso
    assert ey >= SCREEN["displayHeight"] * 0.3


def test_half_photo_con_geometria_ok_usa_i_bounds():
    device = _Device({RID.MEDIA_CONTAINER: _media(bottom=1600)})
    assert PostsViewList(device).swipe_to_fit_posts(SwipeTo.HALF_PHOTO) is True
    assert len(device.swipes) == 1
    sx, sy, ex, ey = device.swipes[0]
    assert sx == ex == SCREEN["displayWidth"] // 2
    assert sy == 1600 - 5
    assert ey == int(1600 * 0.5)


def test_media_che_sparisce_tra_exists_e_get_bounds_non_solleva():
    # e' esattamente il crash di produzione
    device = _Device({RID.MEDIA_CONTAINER: _View(raises=_jsonrpc_error())})
    assert PostsViewList(device).swipe_to_fit_posts(SwipeTo.HALF_PHOTO) is False
    assert len(device.swipes) == 1


def test_next_post_senza_gap_ne_media_non_solleva():
    device = _Device({})
    assert PostsViewList(device).swipe_to_fit_posts(SwipeTo.NEXT_POST) is False
    assert device.swipes, "deve comunque provare a scrollare"


def test_next_post_con_media_ma_senza_gap_non_solleva():
    device = _Device({RID.MEDIA_CONTAINER: _media(bottom=1600)})
    assert PostsViewList(device).swipe_to_fit_posts(SwipeTo.NEXT_POST) is False


def test_next_post_con_geometria_completa_usa_i_bounds():
    device = _Device(
        {
            RID.MEDIA_CONTAINER: _media(bottom=1600, top=400),
            RID.GAP_VIEW_AND_FOOTER_SPACE: _View(
                bounds={"top": 1700, "bottom": 1800, "left": 0, "right": 1080}
            ),
        }
    )
    assert PostsViewList(device).swipe_to_fit_posts(SwipeTo.NEXT_POST) is True
    sx, sy, ex, ey = device.swipes[-1]
    assert sx == ex == SCREEN["displayWidth"] // 2
    assert sy == 1800 - 5  # dal fondo del gap
    assert ey == int((1600 + 400) / 3 + 5)


def test_crash_dell_app_viene_propagato():
    device = _Device(
        {RID.MEDIA_CONTAINER: _View(raises=DeviceFacade.AppHasCrashed("crash"))}
    )
    try:
        PostsViewList(device).swipe_to_fit_posts(SwipeTo.HALF_PHOTO)
    except DeviceFacade.AppHasCrashed:
        return
    raise AssertionError("AppHasCrashed deve arrivare al recupero crash, non essere ingoiata")


class _DeviceWithBack(_Device):
    """Il primo back() 'riporta' la lista dei post: simula la chiusura del bottom
    sheet vuoto rimasto aperto quando i likers non si caricano."""

    def __init__(self, elements, elements_after_back=None):
        super().__init__(elements)
        self.elements_after_back = elements_after_back
        self.backs = 0

    def back(self):
        self.backs += 1
        if self.elements_after_back is not None:
            self.elements = self.elements_after_back


def test_ensure_on_posts_list_non_fa_nulla_se_siamo_sul_feed():
    device = _DeviceWithBack({RID.MEDIA_CONTAINER: _media(bottom=1600)})
    assert PostsViewList(device)._ensure_on_posts_list() is True
    assert device.backs == 0


def test_ensure_on_posts_list_torna_indietro_e_ritrova_il_feed():
    device = _DeviceWithBack(
        {}, elements_after_back={RID.MEDIA_CONTAINER: _media(bottom=1600)}
    )
    assert PostsViewList(device)._ensure_on_posts_list() is True
    assert device.backs == 1


def test_ensure_on_posts_list_si_arrende_dopo_i_back():
    device = _DeviceWithBack({})
    assert PostsViewList(device)._ensure_on_posts_list(max_backs=2) is False
    assert device.backs == 2  # non premiamo back all'infinito


def test_ensure_on_posts_list_accetta_anche_il_gap_view():
    # post senza media container leggibile (Reel/ads) ma feed presente
    device = _DeviceWithBack(
        {
            RID.GAP_VIEW_AND_FOOTER_SPACE: _View(
                bounds={"top": 1700, "bottom": 1800, "left": 0, "right": 1080}
            )
        }
    )
    assert PostsViewList(device)._ensure_on_posts_list() is True
    assert device.backs == 0


# ---------------------------------------------------------------------------
# safe_bounds e' l'helper condiviso: lo usa PostsViewList e lo usa _comment in
# interaction.py, che era la PRIMA causa di crash nei log (40 crash fatali,
# ultimo il 09/06) perche' leggeva tab bar e media container senza rete.
# ---------------------------------------------------------------------------
def test_safe_bounds_elemento_presente():
    view = _media(bottom=1600)
    assert views_module.safe_bounds(view, "media")["bottom"] == 1600


def test_safe_bounds_elemento_assente():
    assert views_module.safe_bounds(_View(), "media") is None


def test_safe_bounds_elemento_sparito_durante_la_lettura():
    assert views_module.safe_bounds(_View(raises=_jsonrpc_error()), "media") is None


def test_safe_bounds_non_ingoia_il_crash_dell_app():
    try:
        views_module.safe_bounds(_View(raises=DeviceFacade.AppHasCrashed("crash")), "media")
    except DeviceFacade.AppHasCrashed:
        return
    raise AssertionError("AppHasCrashed deve propagare")


def test_bug_uia2_exists_falso_negativo_non_deve_far_ripiegare_sullo_scroll_cieco():
    """uiautomator2 a volte risponde exists()=False su un elemento presente
    (bug #689). Senza ignore_bug il fix ripiegava sullo scroll cieco proprio dove
    il codice originale leggeva i bounds veri: era una regressione."""
    device = _Device({RID.MEDIA_CONTAINER: _media(bottom=1600, uia2_bug=True)})
    assert PostsViewList(device).swipe_to_fit_posts(SwipeTo.HALF_PHOTO) is True
    sx, sy, ex, ey = device.swipes[0]
    assert sy == 1600 - 5  # geometria vera, non 0.7 * altezza schermo
    assert ey == int(1600 * 0.5)


def test_correzione_action_bar():
    """Se il media container finisce sotto la action bar, lo swipe parte piu' in
    basso di quanto direbbero i soli bounds del media."""
    device = _Device(
        {
            RID.MEDIA_CONTAINER: _media(bottom=300, top=100),
            RID.ACTION_BAR_CONTAINER: _View(
                bounds={"top": 0, "bottom": 500, "left": 0, "right": 1080}
            ),
        }
    )
    assert PostsViewList(device).swipe_to_fit_posts(SwipeTo.HALF_PHOTO) is True
    _, sy, _, _ = device.swipes[0]
    assert sy == (300 + 500) - 5  # bottom del media + bottom della action bar

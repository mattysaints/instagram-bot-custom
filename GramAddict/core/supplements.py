"""Riconoscimento delle pagine di integratori e dei post che li promuovono.

Richiesta di Roberto (16/09/2026): nessun follow, like o commento a pagine di
integratori ne' a post in cui si promuovono integratori, su entrambi i profili
(@rb.coach e @roberto_buonomo_ifbbpro). Un IFBB Pro che mette un like o un
commento sotto a "codice ROB10 su prozis.com" sta facendo pubblicita' a un
brand che non e' il suo.

Due domande, due funzioni:
  - ``supplement_page_reason``  -> il PROFILO e' una pagina di integratori?
    (brand, shop, promoter). Si guarda username, nome, categoria business e
    bio. Se si', il profilo si salta per intero.
  - ``supplement_promo_reason`` -> il POST promuove integratori? Si guarda
    la caption (e l'etichetta "Partnership retribuita" sotto al nome). Se si',
    niente like ne' commento su quel post, e niente follow al profilo.

Il criterio e' a DUE SEGNALI, per non scartare chi parla di creatina in un
racconto o un coach che cita "integratori" fra i suoi servizi:
  - PRODOTTO: parola da scaffale (integratori, whey, creatina, pre-workout,
    bcaa, brucia grassi...). "integrazione" e "supplementazione" NON ci sono:
    sono il lessico normale di un coach ("scheda, dieta e integrazione").
  - BRAND: nome di un marchio noto (yamamoto, prozis, myprotein...), oppure
    un @handle che contiene nutrition/supplement/supps/whey/protein.
  - PROMO: segnale commerciale (codice sconto, 20% off, link in bio, shop,
    spedizione, ambassador, #adv, partnership retribuita...).
Un testo e' "integratori" se ha almeno due delle tre classi. Nel nome/username
di un profilo basta un solo segnale di prodotto o brand (chi si chiama
"XYZ Supplements" o "prozisitalia" e' un brand, non c'e' altro da capire).

Tutto e' testabile senza device: vedi test/test_supplements.py.
"""
import re
from typing import Iterable, List, Optional

# --- PRODOTTO ---------------------------------------------------------------
# Parole da scaffale. Confini di parola su entrambi i lati: "supplement" non
# deve prendere "supplementazione", "isolata" (casa isolata) non c'entra.
_PRODUCT_PATTERNS = [
    r"integrator[ei]",
    r"supplements?",
    r"supplementi",
    r"supps?",
    r"whey",
    r"protein[ae]?s?\s+(?:in\s+)?(?:polvere|powder)",
    r"protein\s+(?:shakes?|bars?)",
    r"frullat[oi]\s+proteic[oi]",
    r"barrett[ae]\s+proteic[ah]e?",
    r"casein[ae]?",
    r"pre[\s\-]?workout",
    r"pre[\s\-]?wo",
    r"pwo",
    r"bcaas?",
    r"eaas?",
    r"creatin[ae]",
    r"mass\s?gainers?",
    r"gainers?",
    r"brucia\s?grassi",
    r"fat\s?burners?",
    r"termogenic[oi]",
    r"thermogenics?",
    r"multivitaminic[oi]",
    r"multivitamins?",
    r"glutammin[ae]",
    r"glutamine",
    r"beta[\s\-]?alanin[ae]",
    r"citrullin[ae]",
    r"arginin[ae]",
    r"carnitin[ae]",
    r"omega\s?-?\s?3",
    r"aminoacid[io]",
    r"amino\s+acids?",
    r"aminos",
    r"zma",
    r"ashwagandha",
    r"elettroliti",
    r"electrolytes",
    r"collagene",
    r"collagen",
    r"nutraceutic\w*",
]
_PRODUCT_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(_PRODUCT_PATTERNS) + r")(?![\w])",
    re.IGNORECASE,
)

# --- BRAND ------------------------------------------------------------------
# Marchi noti in Italia e nel giro bodybuilding. Solo il confine SINISTRO e'
# obbligatorio: "prozis" deve prendere anche "prozisitalia" e "yamamoto" anche
# "yamamoto_italia" (username senza separatori). I nomi corti che sono anche
# parole comuni (bsn, esn, jym, olimp, amix) hanno il confine su entrambi i
# lati. Fra le due parole di un marchio si ammette uno spazio, un punto o un
# underscore: "tsunami nutrition", "tsunaminutrition", "tsunami_nutrition".
_SEP = r"[\s._\-]?"
_BRAND_PATTERNS = [
    r"yamamoto",
    r"prozis",
    rf"biotech{_SEP}usa",
    rf"tsunami{_SEP}nutrition",
    r"vitastrong",
    r"myprotein",
    rf"optimum{_SEP}nutrition",
    r"muscletech",
    rf"raw{_SEP}nutrition",
    r"gymbeam",
    r"foodspring",
    rf"net{_SEP}integratori",
    r"enervit",
    rf"named{_SEP}sport",
    rf"plus{_SEP}watt",
    r"\+watt",
    rf"iaf{_SEP}store",
    rf"anderson{_SEP}research",
    rf"4{_SEP}\+?{_SEP}nutrition",
    r"keforma",
    rf"ultimate{_SEP}italia",
    r"syform",
    r"volchem",
    rf"ethic{_SEP}sport",
    r"xcore",
    rf"gold{_SEP}nutrition",
    r"ryse",
    rf"alpha{_SEP}lion",
    rf"1st{_SEP}phorm",
    rf"transparent{_SEP}labs",
    r"nutrabolics",
    r"evogen",
    r"redcon1",
    r"cellucor",
    r"dymatize",
    r"rule\s?(?:1|one)\s?proteins?",
    r"kaged",
    rf"gorilla{_SEP}mind",
    rf"bucked{_SEP}up",
    rf"applied{_SEP}nutrition",
    r"per4m",
    r"scitec",
    r"weider",
    r"nutrend",
    r"nutriversum",
    rf"life{_SEP}pro{_SEP}nutrition",
    r"lifepro(?![a-z])",
    r"lifepro(?:italia|nutrition)",
    r"inkospor",
    r"eurosup",
    r"proaction",
    rf"why{_SEP}sport",
    rf"why{_SEP}nature",
    rf"bpi{_SEP}sports",
    rf"axe{_SEP}&?{_SEP}sledge",
    r"herbalife",
    rf"juice{_SEP}plus",
    r"quamtrax",
    rf"bulk{_SEP}powders",
    rf"protein{_SEP}works",
    r"vitamincenter",
    r"bodybuilding\.com(?![\w])",
    r"nutrizionesportiva\.\w+",
    # corti / parole comuni: confine su entrambi i lati
    r"bsn(?![\w])",
    r"esn(?![\w])",
    r"hsn(?![\w])",
    r"jym(?![\w])",
    r"olimp(?![\w])",
    r"amix(?![\w])",
]
_BRAND_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(_BRAND_PATTERNS) + r")",
    re.IGNORECASE,
)
# @handle che "parla" da brand: @xyz_nutrition, @supps.italia, @proteinworks.
# "nutritionist"/"nutrizionista" sono persone, non marchi.
_BRAND_HANDLE_RE = re.compile(
    r"@[\w.]*(?:nutrition(?!ist)|supplements?|supps?|integratori|whey|protein(?!e\b))[\w.]*",
    re.IGNORECASE,
)

# --- PROMO ------------------------------------------------------------------
# Segnali commerciali. Non bastano da soli (un coach che vende un percorso
# non e' un venditore di integratori): contano insieme a PRODOTTO o BRAND.
_PROMO_PATTERNS = [
    r"codice\s+(?:sconto|promo|coupon)",
    r"codice\s*[:\-]?\s*[A-Za-z]*\d[A-Za-z0-9]*",  # codice ROB10
    r"(?:discount|promo|coupon)\s*codes?",
    r"use\s+(?:my\s+|the\s+)?code",
    r"code\s*[:\-]?\s*[A-Za-z]*\d[A-Za-z0-9]*",  # code CBUM10
    r"scont[oi](?![\w])",
    r"discounts?(?![\w])",
    r"coupons?(?![\w])",
    r"promo(?![\w])",
    r"promozion\w*",
    r"offert[ae](?![\w])",
    r"offers?(?![\w])",
    r"on\s+sale(?![\w])",
    r"\d+\s?%\s?(?:off|di\s+sconto|sconto|discount)",
    r"link\s+in\s+bio",
    r"linkinbio",
    r"link\s+(?:nelle|in)\s+stor(?:ie|y|ies)",
    r"swipe\s+up",
    r"acquist\w*",
    r"ordina(?:lo|la|li|le)?(?![\w])",
    r"order\s+now",
    r"compra(?:lo|la|li|le)?(?![\w])",
    r"buy(?![\w])",
    r"shop(?:s|pa)?(?![\w])",
    r"stores?(?![\w])",
    r"e-?commerce",
    r"disponibil[ei]\s+(?:su|da|online|sul\s+sito|in\s+store|nei\s+negozi)",
    r"available\s+(?:on|at|now)",
    r"spedizion[ei]",
    r"shipping",
    r"ambassadors?(?![\w])",
    r"sponsor\w*",
    r"partnerships?(?![\w])",
    r"partner\s+(?:ufficiale|official|tecnico)",
    r"in\s+collaborazione\s+con",
    r"in\s+collab\w*\s+with",
    r"#adv?(?![\w])",
    r"#pubblicit[aà](?![\w])",
    r"#sponsored(?![\w])",
    r"#gifted(?![\w])",
    r"#suppliedby",
    r"gifted(?![\w])",
    r"paid\s+partnership",
    r"partnership\s+retribuita",
    r"sponsorizzat[oa]",
    r"athlete\s*@",
    r"atleta\s*@",
    r"team\s*@",
    r"powered\s+by",
    r"fueled\s+by",
]
# Questi non hanno un confine sinistro: "prozis.com" ha la "s" attaccata
# alla parola prima, e un link puo' stare incollato a qualunque cosa.
_PROMO_FREE_PATTERNS = [
    r"\w\.(?:com|net|shop|store|eu)(?![\w])",
    r"\w\.it(?![\w'’])",
    r"www\.",
    r"https?://",
]
_PROMO_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(_PROMO_PATTERNS) + r")"
    r"|(?:" + "|".join(_PROMO_FREE_PATTERNS) + r")",
    re.IGNORECASE,
)

# Parole di sponsorizzazione che valgono come PROMO solo accanto a un BRAND:
# "Team Yamamoto", "ESN athlete", "atleta Prozis". Da sole non dicono niente
# ("team" e "athlete" li scrive chiunque in bio), con un marchio di integratori
# vicino dicono che quel profilo lo promuove.
_SPONSOR_RE = re.compile(
    r"(?<![\w])(?:team|athletes?|atlet[aei]|rider|testimonial|supported\s+by)(?![\w])",
    re.IGNORECASE,
)
# Nel NOME di un profilo, "Protein Shop Italia" o "Nutrition Store" sono un
# negozio anche senza un marchio noto: parola da brand + parola da negozio.
_NAME_BRANDISH_RE = re.compile(
    r"(?<![\w])(?:nutrition(?!ist)|nutrizione|proteins?|supps?|supplement\w*|integrator\w*|vitamin\w*)(?![\w])",
    re.IGNORECASE,
)

# Etichetta sotto al nome nel post (secondary_label): "Paid partnership with X",
# "Partnership retribuita con X", "Sponsored", "Sponsorizzato".
_PARTNERSHIP_LABEL_RE = re.compile(
    r"partnership|sponsor", re.IGNORECASE
)

# Categoria business del profilo ("Vitamins/Supplements", "Vitamine/Integratori",
# "Health/Beauty" no: troppo larga).
_CATEGORY_RE = re.compile(
    r"supplement|integrator|vitamin|nutrizione\s+sportiva|sports?\s+nutrition",
    re.IGNORECASE,
)


def _compile_extra(words: Optional[Iterable[str]]) -> Optional[re.Pattern]:
    """Parole aggiunte dall'utente in filters.yml (supplement_words /
    supplement_brands): testo libero, confine sinistro obbligatorio, regex
    non valide ignorate una per una."""
    valid: List[str] = []
    for w in words or []:
        w = str(w or "").strip()
        if not w:
            continue
        try:
            re.compile(w)
        except re.error:
            continue
        valid.append(w)
    if not valid:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(valid) + r")", re.IGNORECASE)


def _norm(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def _signals(
    text: str,
    extra_products: Optional[re.Pattern] = None,
    extra_brands: Optional[re.Pattern] = None,
) -> dict:
    """Le tre classi di segnale trovate nel testo: {classe: prima parola trovata}."""
    found = {}
    m = _PRODUCT_RE.search(text) or (extra_products.search(text) if extra_products else None)
    if m:
        found["prodotto"] = m.group(0)
    m = _BRAND_RE.search(text) or _BRAND_HANDLE_RE.search(text) or (
        extra_brands.search(text) if extra_brands else None
    )
    if m:
        found["brand"] = m.group(0)
    m = _PROMO_RE.search(text)
    if m:
        found["promo"] = m.group(0)
    elif "brand" in found:
        m = _SPONSOR_RE.search(text)
        if m:
            found["promo"] = m.group(0)
    return found


def _describe(found: dict) -> str:
    return ", ".join(f"{k} '{v}'" for k, v in found.items())


def supplement_page_reason(
    username: Optional[str],
    fullname: Optional[str],
    biography: Optional[str],
    business_category: Optional[str] = None,
    extra_words: Optional[Iterable[str]] = None,
    extra_brands: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Perche' il profilo e' una pagina di integratori (None = non lo e').

    - username o nome con una parola di prodotto o un brand: e' il brand
      stesso o un suo shop ("Yamamoto Nutrition Italia", "netintegratori",
      "XYZ Supplements"); oppure una parola da brand accanto a una da negozio
      ("Protein Shop Italia");
    - categoria business "Vitamins/Supplements" (e simili);
    - bio con almeno DUE classi di segnale: prodotto + promo ("integratori
      per lo sport, spedizione gratuita"), brand + promo ("codice ROB10 su
      @prozisitalia", "Team Yamamoto athlete"), prodotto + brand.
    Una sola classe in bio non basta: "consulenze su allenamento, dieta e
    integratori" e' un coach, "grazie @prozis" e' una persona.
    """
    extra_p = _compile_extra(extra_words)
    extra_b = _compile_extra(extra_brands)

    category = _norm(business_category)
    if category and _CATEGORY_RE.search(category):
        return f"categoria '{category}'"

    for label, text in (("username", username), ("nome", fullname)):
        text = _norm(text)
        if not text:
            continue
        # nell'username i separatori sono . e _: li tolgo per leggere
        # "net_integratori" e "tsunami.nutrition" come parole intere
        flat = re.sub(r"[._]", " ", text) if label == "username" else text
        for candidate in (text, flat):
            found = _signals(candidate, extra_p, extra_b)
            hit = found.get("prodotto") or found.get("brand")
            if not hit and "promo" in found:
                m = _NAME_BRANDISH_RE.search(candidate)
                if m:
                    hit = f"{m.group(0)} + {found['promo']}"
            if hit:
                return f"{label} '{text}' ({hit})"

    bio = _norm(biography)
    if bio:
        found = _signals(bio, extra_p, extra_b)
        if len(found) >= 2:
            return f"bio: {_describe(found)}"
    return None


def supplement_promo_reason(
    caption: Optional[str],
    header_label: Optional[str] = None,
    extra_words: Optional[Iterable[str]] = None,
    extra_brands: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Perche' il post promuove integratori (None = non li promuove).

    Due classi di segnale su tre (prodotto, brand, promo) nella caption.
    L'etichetta "Partnership retribuita"/"Sponsored" sotto al nome vale come
    segnale promo. Un post che dice solo "oggi creatina e via" non e' una
    promozione; "il mio pre-workout @yamamoto_italia" e "codice ROB10 su
    prozis.com" si'.
    """
    text = _norm(caption)
    if not text:
        return None
    extra_p = _compile_extra(extra_words)
    extra_b = _compile_extra(extra_brands)
    found = _signals(text, extra_p, extra_b)
    label = _norm(header_label)
    if label and _PARTNERSHIP_LABEL_RE.search(label) and "promo" not in found:
        found["promo"] = f"etichetta '{label}'"
    if len(found) >= 2:
        return _describe(found)
    return None

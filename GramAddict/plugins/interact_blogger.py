import logging
from contextlib import contextmanager
from functools import partial
from random import seed

from colorama import Style

from GramAddict.core.decorators import run_safely
from GramAddict.core.handle_sources import handle_blogger, handle_blogger_from_file
from GramAddict.core.interaction import (
    interact_with_user,
    is_follow_limit_reached_for_source,
)
from GramAddict.core.plugin_loader import Plugin
from GramAddict.core.utils import get_value, init_on_things, sample_sources

logger = logging.getLogger(__name__)

# I filtri di filters.yml sono GLOBALI: valgono per ogni job. Con
# max_followers a 8000 il job `blogger` non commenterebbe mai i profili
# grandi, perche' interact_with_user li scarta prima di fare qualsiasi cosa.
# Qui i limiti di dimensione vengono sospesi per la durata del job e
# ripristinati subito dopo.
_SIZE_FIELDS = (
    "min_followers",
    "max_followers",
    "min_followings",
    "max_followings",
    "min_posts",
    "last_post_max_age_days",
)
# potency_ratio non usa None come "nessun limite" ma le sentinelle 0 e 999.
# skip_following / skip_follower: sul job blogger in modalita' commento vanno
# spenti. Il profilo personale SEGUE i big della sua lista (sono i suoi pari),
# e con skip_following attivo il job apriva kuba, mauro_sassi, silvialikki,
# li leggeva e concludeva "You follow @..., skip": zero commenti in una
# sessione intera. Commentare sotto a chi gia' segui e' esattamente il
# comportamento naturale, non un'anomalia da filtrare.
# NB: se invece il cliente NON vuole commenti sotto ai profili che gia'
# segue (li conosce, il commento del bot e' riconoscibile), c'e'
# --blogger-skip-following: rimette skip_following a true per il solo job
# blogger e quei big vengono aperti e saltati con "You follow @..., skip".
_POTENCY_NEUTRAL = {
    "min_potency_ratio": 0,
    "max_potency_ratio": 999,
    "skip_following": False,
    "skip_follower": False,
    # I big della lista `blogger` li ha scelti il cliente a mano: sono
    # pre-approvati. I filtri di bio servono a scartare sconosciuti spammosi
    # pescati dalle liste, non loro -- e quasi tutti i big hanno un "codice
    # sconto" in bio: con blacklist_words attiva malatidipalestraofficial
    # (193K) veniva scartato per la parola "sconto" e non commentato.
    "blacklist_words": [],
    "mandatory_words": [],
    "skip_business": False,
    "skip_if_link_in_bio": False,
}


@contextmanager
def _size_filters_suspended(profile_filter, keep_skip_following=False):
    """Sospende i filtri di dimensione, poi li rimette esattamente com'erano.

    Con keep_skip_following=True (--blogger-skip-following) skip_following
    resta attivo: i big che l'account segue gia' vengono saltati invece che
    commentati.
    """
    conditions = getattr(profile_filter, "conditions", None)
    if not conditions:
        yield
        return

    overrides = dict(_POTENCY_NEUTRAL)
    if keep_skip_following:
        overrides["skip_following"] = True

    watched = list(_SIZE_FIELDS) + list(overrides)
    saved = {k: conditions[k] for k in watched if k in conditions}
    try:
        for k in _SIZE_FIELDS:
            conditions.pop(k, None)
        conditions.update(overrides)
        yield
    finally:
        for k in watched:
            conditions.pop(k, None)
        conditions.update(saved)


def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")



# Script Initialization
seed()


class InteractBloggerPostLikers(Plugin):
    """Handles the functionality of interacting with a blogger"""

    def __init__(self):
        super().__init__()
        self.description = "Handles the functionality of interacting with a blogger"
        self.arguments = [
            {
                "arg": "--blogger",
                "nargs": "+",
                "help": "interact a specified blogger",
                "metavar": ("blogger1", "blogger2"),
                "default": None,
                "operation": True,
            },
            {
                "arg": "--interact-from-file",
                "nargs": "+",
                "help": "filenames of the list of users [*.txt]",
                "metavar": ("filename1.txt", "filename2.txt"),
                "default": None,
                "operation": True,
            },
            {
                "arg": "--unfollow-from-file",
                "nargs": "+",
                "help": "filenames of the list of users [*.txt]",
                "metavar": ("filename1.txt", "filename2.txt"),
                "default": None,
                "operation": True,
            },
            {
                "arg": "--blogger-reinteract-after",
                "nargs": None,
                "help": (
                    "hours that have to pass before commenting the same big again, "
                    "for the `blogger` job ONLY. Overrides --can-reinteract-after, "
                    "which stays in charge of every other job (followers, hashtag, "
                    "place). Unset = use --can-reinteract-after."
                ),
                "metavar": "48",
                "default": None,
            },
            {
                "arg": "--blogger-comment-only",
                "nargs": None,
                "help": (
                    "on the `blogger` job, only comment: no like, no follow, no story "
                    "stickers, and profile size filters are suspended so big accounts "
                    "are not skipped (default: true)"
                ),
                "metavar": "true|false",
                "default": "true",
            },
            {
                "arg": "--blogger-skip-following",
                "nargs": None,
                "help": (
                    "on the `blogger` job in comment-only mode, keep the "
                    "skip_following filter ON: bigs the account already follows "
                    "are opened and skipped instead of commented (default: false)"
                ),
                "metavar": "true|false",
                "default": "false",
            },
        ]

    def run(self, device, configs, storage, sessions, profile_filter, plugin):
        class State:
            def __init__(self):
                pass

            is_job_completed = False

        self.device_id = configs.args.device
        self.sessions = sessions
        self.session_state = sessions[-1]
        self.args = configs.args
        self.current_mode = plugin

        # Handle sources
        if plugin == "interact-from-file":
            sources = [f for f in self.args.interact_from_file if f.strip()]
        elif plugin == "unfollow-from-file":
            sources = [f for f in self.args.unfollow_from_file if f.strip()]
        else:
            sources = [s for s in self.args.blogger if s.strip()]

        # In modalita' commento l'unico scopo del job e' il commento: una volta
        # esaurito il budget commenti della sessione, ogni big che resta costa
        # un'apertura profilo + un like e vale UNA interazione riuscita
        # (session_state.add_interaction conta per profilo, non per azione),
        # sottraendola ai job che seguono. Meglio chiudere qui. E' la stessa
        # logica gia' applicata post per post in interaction.py.
        comment_only_job = plugin == "blogger" and _as_bool(
            getattr(self.args, "blogger_comment_only", None), default=True
        )

        for source in sample_sources(sources, self.args.truncate_sources, storage=storage, job_name=plugin):
            (
                active_limits_reached,
                unfollow_limits_reached,
                actions_limit_reached,
            ) = self.session_state.check_limit(limit_type=self.session_state.Limit.ALL)
            if plugin == "unfollow-from-file":
                limit_reached = unfollow_limits_reached or actions_limit_reached
            else:
                limit_reached = active_limits_reached or actions_limit_reached

            if comment_only_job and self.session_state.check_limit(
                limit_type=self.session_state.Limit.COMMENTS, output=False
            ):
                logger.info(
                    "Budget commenti esaurito: chiudo il job blogger senza "
                    "consumare gli altri big.",
                    extra={"color": f"{Style.BRIGHT}"},
                )
                break

            self.state = State()
            logger.info(f"Handle {source}", extra={"color": f"{Style.BRIGHT}"})

            # Init common things
            (
                on_interaction,
                stories_percentage,
                likes_percentage,
                follow_percentage,
                comment_percentage,
                pm_percentage,
                _,
            ) = init_on_things(source, self.args, self.sessions, self.session_state)

            @run_safely(
                device=device,
                device_id=self.device_id,
                sessions=self.sessions,
                session_state=self.session_state,
                screen_record=self.args.screen_record,
                configs=configs,
            )
            def job():
                self.handle_blogger(
                    device,
                    source,
                    plugin,
                    storage,
                    profile_filter,
                    on_interaction,
                    stories_percentage,
                    likes_percentage,
                    follow_percentage,
                    comment_percentage,
                    pm_percentage,
                )
                self.state.is_job_completed = True

            def job_file():
                self.handle_blogger_from_file(
                    device,
                    source,
                    plugin,
                    storage,
                    profile_filter,
                    on_interaction,
                    stories_percentage,
                    likes_percentage,
                    follow_percentage,
                    comment_percentage,
                    pm_percentage,
                )
                self.state.is_job_completed = True

            while not self.state.is_job_completed and not limit_reached:
                if plugin == "blogger":
                    job()
                else:
                    job_file()

            if limit_reached:
                logger.info("Ending session.")
                self.session_state.check_limit(
                    limit_type=self.session_state.Limit.ALL, output=True
                )
                break

    def handle_blogger(
        self,
        device,
        username,
        current_job,
        storage,
        profile_filter,
        on_interaction,
        stories_percentage,
        likes_percentage,
        follow_percentage,
        comment_percentage,
        pm_percentage,
    ):
        # Sui profili grandi vogliamo solo il commento pubblico: like e follow
        # non hanno senso (non ricambiano) e le storie degli account grandi non
        # sono il posto dove farsi notare. Il resto del bot continua a fare
        # like/follow/sticker sui profili piccoli, che sono il target vero.
        comment_only = _as_bool(
            getattr(self.args, "blogger_comment_only", None), default=True
        )
        if comment_only:
            logger.info(
                "Job blogger in modalita' commento: 1 like + 1 commento, "
                "niente follow, niente storie."
            )
            # ATTENZIONE, non mettere likes_percentage a 0. In interaction.py
            # TUTTO il blocco che apre i post e commenta (righe ~206-350) sta
            # dentro `if can_like(session_state, likes_percentage):`, e can_like
            # con percentuale 0 e' sempre False: il commento non verrebbe mai
            # raggiunto e il job non farebbe assolutamente nulla.
            # Il like non e' evitabile senza toccare il core, ed e' comunque il
            # comportamento piu' naturale: commentare un post senza metterci
            # like e' un pattern da bot.
            likes_percentage = 100
            follow_percentage = 0
            stories_percentage = 0
            comment_percentage = 100

        interaction = partial(
            interact_with_user,
            my_username=self.session_state.my_username,
            # Sui big il like serve solo ad aprire il post per commentare.
            # Si apre fino a 3 post, ma interact_with_user si ferma al primo
            # commento riuscito: il 3 serve per i post con i commenti
            # limitati, dove prima il job chiudeva senza aver fatto nulla.
            likes_count="3" if comment_only else self.args.likes_count,
            likes_percentage=likes_percentage,
            stories_percentage=stories_percentage,
            follow_percentage=follow_percentage,
            comment_percentage=comment_percentage,
            pm_percentage=pm_percentage,
            profile_filter=profile_filter,
            args=self.args,
            session_state=self.session_state,
            scraping_file=self.args.scrape_to_file,
            current_mode=self.current_mode,
        )
        source_follow_limit = (
            get_value(self.args.follow_limit, None, 15)
            if self.args.follow_limit is not None
            else None
        )
        is_follow_limit_reached = partial(
            is_follow_limit_reached_for_source,
            session_state=self.session_state,
            follow_limit=source_follow_limit,
            source=username,
        )

        if comment_only:
            # senza questo, un account con piu' follower di max_followers
            # verrebbe scartato da check_profile e non commenteremmo nulla
            skip_known = _as_bool(
                getattr(self.args, "blogger_skip_following", None), default=False
            )
            with _size_filters_suspended(
                profile_filter, keep_skip_following=skip_known
            ):
                handle_blogger(
                    self,
                    device,
                    self.session_state,
                    username,
                    current_job,
                    storage,
                    profile_filter,
                    on_interaction,
                    interaction,
                    is_follow_limit_reached,
                )
        else:
            handle_blogger(
                self,
                device,
                self.session_state,
                username,
                current_job,
                storage,
                profile_filter,
                on_interaction,
                interaction,
                is_follow_limit_reached,
            )

    def handle_blogger_from_file(
        self,
        device,
        current_filename,
        current_job,
        storage,
        profile_filter,
        on_interaction,
        stories_percentage,
        likes_percentage,
        follow_percentage,
        comment_percentage,
        pm_percentage,
    ):
        interaction = partial(
            interact_with_user,
            my_username=self.session_state.my_username,
            likes_count=self.args.likes_count,
            likes_percentage=likes_percentage,
            stories_percentage=stories_percentage,
            follow_percentage=follow_percentage,
            comment_percentage=comment_percentage,
            pm_percentage=pm_percentage,
            profile_filter=profile_filter,
            args=self.args,
            session_state=self.session_state,
            scraping_file=self.args.scrape_to_file,
            current_mode=self.current_mode,
        )
        source_follow_limit = (
            get_value(self.args.follow_limit, None, 15)
            if self.args.follow_limit is not None
            else None
        )
        is_follow_limit_reached = partial(
            is_follow_limit_reached_for_source,
            session_state=self.session_state,
            follow_limit=source_follow_limit,
            source=current_filename,
        )

        handle_blogger_from_file(
            self,
            device,
            current_filename,
            current_job,
            storage,
            on_interaction,
            interaction,
            is_follow_limit_reached,
        )

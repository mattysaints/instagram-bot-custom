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
# potency_ratio non usa None come "nessun limite" ma le sentinelle 0 e 999
_POTENCY_NEUTRAL = {"min_potency_ratio": 0, "max_potency_ratio": 999}


@contextmanager
def _size_filters_suspended(profile_filter):
    """Sospende i filtri di dimensione, poi li rimette esattamente com'erano."""
    conditions = getattr(profile_filter, "conditions", None)
    if not conditions:
        yield
        return

    watched = list(_SIZE_FIELDS) + list(_POTENCY_NEUTRAL)
    saved = {k: conditions[k] for k in watched if k in conditions}
    try:
        for k in _SIZE_FIELDS:
            conditions.pop(k, None)
        conditions.update(_POTENCY_NEUTRAL)
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
                "Job blogger in modalita' solo-commento: niente like, follow o storie."
            )
            likes_percentage = 0
            follow_percentage = 0
            stories_percentage = 0
            comment_percentage = 100

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
            source=username,
        )

        if comment_only:
            # senza questo, un account con piu' follower di max_followers
            # verrebbe scartato da check_profile e non commenteremmo nulla
            with _size_filters_suspended(profile_filter):
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

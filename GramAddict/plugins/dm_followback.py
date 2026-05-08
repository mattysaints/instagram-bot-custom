"""DM-Followback plugin.

Sends a private message ONLY to users who have followed me back after the bot
followed them. The flow is:

1. Navigate to my own profile -> Followers list (sorted Latest first).
2. For each follower row visible on screen:
   - skip if user is in whitelist or blacklist
   - skip if user is NOT in interacted_users.json (= we never followed them,
     so this is a follower acquired organically -> we don't want to spam them)
   - skip if user.followed != True (= we never actually followed them, e.g.
     skipped during interaction)
   - skip if user.pm_sent == True (= we already DM'd them in a previous run)
   - skip if last_interaction is more recent than --dm-followback-min-hours
     (avoid the "instant DM 5s after follow-back" pattern that screams bot)
   - skip if last_interaction is older than --dm-followback-max-days
     (avoid DMing very old contacts where the conversation context is lost)
3. Open profile -> tap "Message" -> send a random line from pm_list.txt.
4. Persist pm_sent=True in interacted_users.json so we never DM the same
   user twice.

Hard caps respected:
  * total-pm-limit (per session)        -> via SessionState.check_limit(PM)
  * daily-pm-cap   (across sessions)    -> via clip on total-pm-limit upstream
  * action-throttle-pm-min              -> via get_throttler() inside _send_PM
  * end-if-pm-limit-reached             -> via existing session loop logic

Anti-ban defaults: very conservative count (3-6 DMs/run); the throttler ensures
~5-9 minutes between two consecutive DMs system-wide.
"""

import logging
from datetime import datetime, timedelta

from colorama import Fore

from GramAddict.core.ai_dm import generate_dm as ai_generate_dm
from GramAddict.core.ai_dm import is_enabled as ai_dm_is_enabled
from GramAddict.core.decorators import run_safely
from GramAddict.core.device_facade import Timeout
from GramAddict.core.interaction import _send_PM
from GramAddict.core.plugin_loader import Plugin
from GramAddict.core.resources import ClassName
from GramAddict.core.resources import ResourceID as resources
from GramAddict.core.storage import FollowingStatus
from GramAddict.core.utils import (
    get_value,
    inspect_current_view,
    random_sleep,
    save_crash,
)
from GramAddict.core.views import (
    Direction,
    ProfileView,
    UniversalActions,
)

logger = logging.getLogger(__name__)


class DmFollowback(Plugin):
    """Send a DM to users who followed me back after the bot followed them."""

    def __init__(self):
        super().__init__()
        self.description = (
            "Send a DM to users who followed me back after the bot followed them."
        )
        self.arguments = [
            {
                "arg": "--dm-followback",
                "nargs": None,
                "help": (
                    "send a private message to at most N users who followed you "
                    "back after the bot followed them. Range supported, e.g. 3-6"
                ),
                "metavar": "3-6",
                "default": None,
                "operation": True,
            },
            {
                "arg": "--dm-followback-min-hours",
                "nargs": None,
                "help": (
                    "minimum hours that must have passed since the bot followed "
                    "the user before we send the DM (anti-bot delay). Default 2."
                ),
                "metavar": "2",
                "default": "2",
            },
            {
                "arg": "--dm-followback-max-days",
                "nargs": None,
                "help": (
                    "maximum days since the bot followed the user. Older follows "
                    "won't be DM'd (stale context). Default 7."
                ),
                "metavar": "7",
                "default": "7",
            },
            {
                "arg": "--dm-followback-skipped-list-limit",
                "nargs": None,
                "help": (
                    "stop scrolling the followers list after iterating this many "
                    "rows without finding a valid candidate. Default 80."
                ),
                "metavar": "80",
                "default": "80",
            },
        ]

    # -------------------------------------------------------------------------

    def run(self, device, configs, storage, sessions, profile_filter, plugin):
        class State:
            def __init__(self):
                pass

            dm_sent_count = 0
            is_job_completed = False

        self.args = configs.args
        self.device_id = configs.args.device
        self.state = State()
        self.session_state = sessions[-1]
        self.sessions = sessions
        self.ResourceID = resources(self.args.app_id)

        target_count = get_value(
            self.args.dm_followback,
            "DM-followback target: {}",
            5,
        )
        if target_count <= 0:
            logger.info("dm-followback target is 0, skip job.")
            return

        # Hard floor: never exceed the per-session PM limit (already clipped by
        # daily-pm-cap upstream in SessionState.apply_daily_budget()).
        try:
            session_pm_left = max(
                0,
                int(self.session_state.args.current_pm_limit)
                - int(self.session_state.totalPm),
            )
        except Exception:
            session_pm_left = target_count

        if session_pm_left <= 0:
            logger.warning(
                "[dm-followback] Session PM limit already reached, nothing to do."
            )
            return

        count = min(target_count, session_pm_left)
        if count < target_count:
            logger.info(
                f"[dm-followback] Clipping target {target_count} -> {count} "
                f"to respect remaining session PM budget."
            )

        try:
            min_hours = float(get_value(self.args.dm_followback_min_hours, None, 2))
        except Exception:
            min_hours = 2.0
        try:
            max_days = float(get_value(self.args.dm_followback_max_days, None, 7))
        except Exception:
            max_days = 7.0
        try:
            skipped_list_limit = int(
                get_value(self.args.dm_followback_skipped_list_limit, None, 80)
            )
        except Exception:
            skipped_list_limit = 80

        logger.info(
            f"[dm-followback] target={count}, min_hours={min_hours}, "
            f"max_days={max_days}, skipped_list_limit={skipped_list_limit}",
            extra={"color": f"{Fore.CYAN}"},
        )

        @run_safely(
            device=device,
            device_id=self.device_id,
            sessions=self.sessions,
            session_state=self.session_state,
            screen_record=self.args.screen_record,
            configs=configs,
        )
        def job():
            self._iterate_followers(
                device,
                storage,
                count - self.state.dm_sent_count,
                min_hours,
                max_days,
                skipped_list_limit,
                plugin,
            )
            logger.info(
                f"[dm-followback] DM sent: {self.state.dm_sent_count}/{count}, finish.",
                extra={"color": f"{Fore.CYAN}"},
            )
            self.state.is_job_completed = True
            device.back()

        while not self.state.is_job_completed and (
            self.state.dm_sent_count < count
        ):
            job()

    # -------------------------------------------------------------------------

    def _iterate_followers(
        self,
        device,
        storage,
        count_left,
        min_hours,
        max_days,
        skipped_list_limit,
        job_name,
    ):
        if count_left <= 0:
            return

        # Open my followers list.
        if not ProfileView(device).navigateToFollowers():
            logger.error("[dm-followback] Cannot open followers list.")
            return

        # Sort by Latest (newest follow-backs first => most relevant to DM).
        self._sort_followers_latest(device)

        my_username = self.session_state.my_username
        now = datetime.now()
        min_delta = timedelta(hours=min_hours)
        max_delta = timedelta(days=max_days)

        checked = set()
        rows_iterated = 0
        prev_screen_iterated = []
        screen_repeats = 0

        while True:
            screen_iterated = []
            user_list = device.find(
                resourceIdMatches=self.ResourceID.USER_LIST_CONTAINER,
            )
            if not user_list.exists(Timeout.LONG):
                logger.warning("[dm-followback] Followers list not visible. Abort.")
                return

            row_height, _ = inspect_current_view(user_list)

            for item in user_list:
                cur_row_height = item.get_height()
                if cur_row_height < row_height:
                    continue
                user_info_view = item.child(index=1)
                user_name_view = user_info_view.child(index=0).child()
                if not user_name_view.exists():
                    break
                username = user_name_view.get_text()
                if not username:
                    continue
                screen_iterated.append(username)
                if username in checked:
                    continue
                checked.add(username)
                rows_iterated += 1

                # ---- pre-flight filters (no profile open) ---------------
                if username == my_username:
                    continue
                if storage.is_user_in_whitelist(username):
                    logger.info(f"[dm-followback] @{username} whitelisted. Skip.")
                    continue
                if storage.is_user_in_blacklist(username):
                    logger.info(f"[dm-followback] @{username} blacklisted. Skip.")
                    continue

                user_data = storage.interacted_users.get(username)
                if user_data is None:
                    logger.debug(
                        f"[dm-followback] @{username} never interacted with by bot. Skip."
                    )
                    continue
                if not user_data.get("followed", False):
                    logger.debug(
                        f"[dm-followback] @{username} bot never followed them. Skip."
                    )
                    continue
                if user_data.get("pm_sent", False):
                    logger.debug(
                        f"[dm-followback] @{username} already DM'd. Skip."
                    )
                    continue
                # Time window check.
                try:
                    last_int = datetime.strptime(
                        user_data.get("last_interaction"),
                        "%Y-%m-%d %H:%M:%S.%f",
                    )
                    elapsed = now - last_int
                except Exception:
                    elapsed = None

                if elapsed is not None:
                    if elapsed < min_delta:
                        logger.info(
                            f"[dm-followback] @{username} followed too recently "
                            f"({elapsed} < {min_delta}). Skip."
                        )
                        continue
                    if elapsed > max_delta:
                        logger.info(
                            f"[dm-followback] @{username} followed too long ago "
                            f"({elapsed} > {max_delta}). Skip."
                        )
                        continue

                # Optional: confirm we're still tracking them as FOLLOWED
                # (i.e. we haven't unfollowed them later).
                fs = storage.get_following_status(username)
                if fs not in (FollowingStatus.FOLLOWED, FollowingStatus.REQUESTED):
                    logger.debug(
                        f"[dm-followback] @{username} status={fs.name}. Skip."
                    )
                    continue

                # ---- session limits re-check ---------------------------
                if self.session_state.check_limit(
                    limit_type=self.session_state.Limit.PM, output=False
                ):
                    logger.info(
                        "[dm-followback] PM session limit reached. Stop.",
                        extra={"color": f"{Fore.CYAN}"},
                    )
                    return

                # ---- open profile and DM -------------------------------
                logger.info(
                    f"[dm-followback] @{username} -> open profile and DM.",
                    extra={"color": f"{Fore.GREEN}"},
                )
                username_view = device.find(
                    resourceId=self.ResourceID.FOLLOW_LIST_USERNAME,
                    className=ClassName.TEXT_VIEW,
                    text=username,
                )
                if not username_view.exists():
                    logger.warning(
                        f"[dm-followback] Cannot click @{username} row, skip."
                    )
                    continue
                username_view.click_retry()

                # Estrai context dal profilo aperto per personalizzare il DM AI.
                # Tutto best-effort: qualsiasi campo None e' tollerato dal prompt.
                ai_message = None
                if ai_dm_is_enabled(self.args):
                    try:
                        ai_message = self._build_ai_dm(device, username)
                    except Exception as e:
                        logger.warning(
                            f"[dm-followback] AI DM generation failed for "
                            f"@{username}: {e}"
                        )
                        ai_message = None

                # Se AI ha generato -> usa quello. Altrimenti fallback policy.
                if ai_message:
                    logger.info(
                        f"[dm-followback] AI DM ({len(ai_message)} chars) -> @{username}",
                        extra={"color": f"{Fore.MAGENTA}"},
                    )
                    message_override = ai_message
                else:
                    # ai-dm-fallback-to-file: se 'false', SKIP. Default 'true' -> usa pm_list.txt.
                    fallback_enabled = self._fallback_enabled()
                    if not fallback_enabled and ai_dm_is_enabled(self.args):
                        logger.info(
                            f"[dm-followback] AI failed and fallback disabled. "
                            f"Skip @{username}, back to list."
                        )
                        device.back()
                        random_sleep(1, 2)
                        continue
                    message_override = None  # _send_PM usera' pm_list.txt

                pm_ok = False
                try:
                    pm_ok = _send_PM(
                        device,
                        self.session_state,
                        my_username,
                        swipe_amount=0,
                        private=False,
                        message_override=message_override,
                    )
                except Exception as e:
                    logger.error(f"[dm-followback] _send_PM crashed: {e}")
                    save_crash(device)

                if pm_ok:
                    storage.add_interacted_user(
                        username,
                        self.session_state.id,
                        pm_sent=True,
                        job_name=job_name,
                        target=user_data.get("target"),
                    )
                    self.state.dm_sent_count += 1
                    logger.info(
                        f"[dm-followback] DM #{self.state.dm_sent_count} -> @{username} OK.",
                        extra={"color": f"{Fore.GREEN}"},
                    )
                    random_sleep(2, 5)
                else:
                    logger.warning(
                        f"[dm-followback] DM to @{username} FAILED."
                    )

                # Back to the followers list (the profile view was opened).
                device.back()
                random_sleep(1, 3)

                if self.state.dm_sent_count >= count_left:
                    return
                if rows_iterated >= skipped_list_limit:
                    logger.info(
                        f"[dm-followback] Reached row scan cap "
                        f"({rows_iterated}/{skipped_list_limit}). Stop."
                    )
                    return

            # Scroll handling.
            if screen_iterated == prev_screen_iterated:
                screen_repeats += 1
                if screen_repeats >= 2:
                    logger.info(
                        "[dm-followback] No more new followers visible. Stop."
                    )
                    return
            else:
                screen_repeats = 0
                prev_screen_iterated = screen_iterated

            list_view = device.find(resourceId=self.ResourceID.LIST)
            if list_view.exists():
                list_view.scroll(Direction.DOWN)
                random_sleep(1, 2)
            else:
                UniversalActions(device)._swipe_points(
                    direction=Direction.UP, delta_y=600
                )
                random_sleep(1, 2)

            if rows_iterated >= skipped_list_limit:
                logger.info(
                    f"[dm-followback] Reached row scan cap "
                    f"({rows_iterated}/{skipped_list_limit}). Stop."
                )
                return

    # -------------------------------------------------------------------------

    def _sort_followers_latest(self, device) -> bool:
        """Sort followers list by 'Latest' (newest follow first) if possible."""
        sort_button = device.find(
            resourceId=self.ResourceID.SORTING_ENTRY_ROW_OPTION,
        )
        if not sort_button.exists(Timeout.MEDIUM):
            logger.debug(
                "[dm-followback] Sort button not found, continue without sorting."
            )
            return False
        sort_button.click()
        sort_options = device.find(
            resourceId=self.ResourceID.FOLLOW_LIST_SORTING_OPTIONS_RECYCLER_VIEW
        )
        if not sort_options.exists(Timeout.MEDIUM):
            logger.debug(
                "[dm-followback] Sort options not found, continue without sorting."
            )
            return False
        logger.info("[dm-followback] Sort followers: Latest first.")
        sort_options.child(textContains="Latest").click()
        random_sleep(1, 2)
        return True

    # -------------------------------------------------------------------------

    def _build_ai_dm(self, device, target_username: str):
        """Estrae context dal profilo aperto e chiama il generator AI.

        Restituisce la stringa generata, o None su qualsiasi fallimento
        (timeout, AI down, output non conforme, profilo che non rende).
        Tutto best-effort: ogni step in try/except per non far crashare il
        bot se Instagram cambia layout o l'utente ha bio vuota.
        """
        # Toggle per saltare la lettura della bio (piu' veloce, meno personalizzato).
        fetch_bio_str = str(getattr(self.args, "ai_dm_fetch_bio", "true")).strip().lower()
        fetch_bio = fetch_bio_str not in ("false", "0", "no", "n", "off")

        full_name = None
        bio = None
        last_caption = None  # placeholder: estrazione caption disabilitata di
        # default per non aprire post (rallenta + rischio extra-actions). Si
        # puo' aggiungere in futuro se serve maggiore personalizzazione.

        try:
            profile_view = ProfileView(device)
            if fetch_bio:
                # Aspetta che il profilo sia renderizzato (header username carica
                # per ultimo): se non e' pronto, evitiamo letture parziali.
                random_sleep(1, 2)
                try:
                    full_name = profile_view.getFullName()
                except Exception as e:
                    logger.debug(f"[dm-followback] getFullName failed: {e}")
                    full_name = None
                try:
                    bio = profile_view.getProfileBiography()
                except Exception as e:
                    logger.debug(f"[dm-followback] getProfileBiography failed: {e}")
                    bio = None
        except Exception as e:
            logger.debug(f"[dm-followback] profile context extraction failed: {e}")

        if full_name:
            logger.debug(f"[dm-followback] @{target_username} fullname='{full_name}'")
        if bio:
            logger.debug(
                f"[dm-followback] @{target_username} bio='{bio[:120]}"
                f"{'...' if len(bio) > 120 else ''}'"
            )

        return ai_generate_dm(
            self.args,
            target_username=target_username,
            full_name=full_name,
            bio=bio,
            last_post_caption=last_caption,
        )

    def _fallback_enabled(self) -> bool:
        """Risolve la policy ai-dm-fallback-to-file: default True."""
        raw = getattr(self.args, "ai_dm_fallback_to_file", "true")
        if raw is None:
            return True
        return str(raw).strip().lower() not in ("false", "0", "no", "n", "off")





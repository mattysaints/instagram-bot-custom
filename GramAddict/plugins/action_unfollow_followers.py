import logging
import random
from datetime import datetime
from enum import Enum, unique
from typing import Optional

from colorama import Fore

from GramAddict.core.decorators import run_safely
from GramAddict.core.device_facade import DeviceFacade, Timeout
from GramAddict.core.action_throttler import ActionType, get_throttler
from GramAddict.core.plugin_loader import Plugin
from GramAddict.core.resources import ClassName
from GramAddict.core.resources import ResourceID as resources
from GramAddict.core.scroll_end_detector import ScrollEndDetector
from GramAddict.core.storage import FollowingStatus
from GramAddict.core.utils import (
    get_value,
    inspect_current_view,
    open_instagram_with_url,
    random_sleep,
    save_crash,
)
from GramAddict.core.views import (
    Direction,
    FollowingView,
    ProfileView,
    UniversalActions,
)

logger = logging.getLogger(__name__)

FOLLOWING_REGEX = "^Following|^Requested"
UNFOLLOW_REGEX = "^Unfollow"


class ActionUnfollowFollowers(Plugin):
    """Handles the functionality of unfollowing your followers"""

    def __init__(self):
        super().__init__()
        self.description = "Handles the functionality of unfollowing your followers"
        self.arguments = [
            {
                "arg": "--unfollow",
                "nargs": None,
                "help": "unfollow at most given number of users. Only users followed by this script will be unfollowed. The order is from oldest to newest followings. It can be a number (e.g. 10) or a range (e.g. 10-20)",
                "metavar": "10-20",
                "default": None,
                "operation": True,
            },
            {
                "arg": "--unfollow-non-followers",
                "nargs": None,
                "help": "unfollow at most given number of users, that don't follow you back. Only users followed by this script will be unfollowed. The order is from oldest to newest followings. It can be a number (e.g. 10) or a range (e.g. 10-20)",
                "metavar": "10-20",
                "default": None,
                "operation": True,
            },
            {
                "arg": "--unfollow-any-non-followers",
                "nargs": None,
                "help": "unfollow at most given number of users, that don't follow you back. The order is from oldest to newest followings. It can be a number (e.g. 10) or a range (e.g. 10-20)",
                "metavar": "10-20",
                "default": None,
                "operation": True,
            },
            {
                "arg": "--unfollow-any-followers",
                "nargs": None,
                "help": "unfollow at most given number of users, that follow you back. The order is from oldest to newest followings. It can be a number (e.g. 10) or a range (e.g. 10-20)",
                "metavar": "10-20",
                "default": None,
                "operation": True,
            },
            {
                "arg": "--unfollow-any",
                "nargs": None,
                "help": "unfollow at most given number of users. The order is from oldest to newest followings. It can be a number (e.g. 10) or a range (e.g. 10-20)",
                "metavar": "10-20",
                "default": None,
                "operation": True,
            },
            {
                "arg": "--min-following",
                "nargs": None,
                "help": "minimum amount of followings, after reaching this amount unfollow stops",
                "metavar": "100",
                "default": 0,
            },
            {
                "arg": "--sort-followers-newest-to-oldest",
                "help": "sort the followers from newest to oldest instead of vice-versa (default)",
                "action": "store_true",
            },
            {
                "arg": "--unfollow-delay",
                "nargs": None,
                "help": "unfollow users followed by the bot after x amount of days",
                "metavar": "3",
                "default": "0",
            },
            {
                "arg": "--unfollow-via-search",
                "nargs": None,
                "help": (
                    "instead of scrolling the Following list, look up each "
                    "candidate (taken from interacted_users.json) one by one "
                    "via the in-list search bar and unfollow them. Way faster "
                    "on large following lists. Accepts true/false. Default: true."
                ),
                "metavar": "true",
                "default": "true",
            },
        ]

    def run(self, device, configs, storage, sessions, profile_filter, plugin):
        class State:
            def __init__(self):
                pass

            unfollowed_count = 0
            is_job_completed = False

        self.args = configs.args
        self.device_id = configs.args.device
        self.state = State()
        self.session_state = sessions[-1]
        self.sessions = sessions
        self.unfollow_type = plugin
        self.ResourceID = resources(self.args.app_id)

        count_arg = get_value(
            getattr(self.args, self.unfollow_type.replace("-", "_")),
            "Unfollow count: {}",
            10,
        )

        count = min(
            count_arg,
            self.session_state.my_following_count - int(self.args.min_following),
        )
        if count < 1:
            logger.warning(
                f"Now you're following {self.session_state.my_following_count} accounts, {'less then' if count <0 else 'equal to'} min following allowed (you set min-following: {self.args.min_following}). No further unfollows are required. Finish."
            )
            return
        elif self.session_state.my_following_count < count_arg:
            logger.warning(
                f"You can't unfollow {count_arg} accounts, because you are following {self.session_state.my_following_count} accounts. For that reason only {count} unfollows can be performed."
            )
        elif count < count_arg:
            logger.warning(
                f"You can't unfollow {count_arg} accounts, because you set min-following to {self.args.min_following} and you have {self.session_state.my_following_count} followers. For that reason only {count} unfollows can be performed."
            )

        if self.unfollow_type == "unfollow":
            self.unfollow_type = UnfollowRestriction.FOLLOWED_BY_SCRIPT
        elif self.unfollow_type == "unfollow-non-followers":
            self.unfollow_type = UnfollowRestriction.FOLLOWED_BY_SCRIPT_NON_FOLLOWERS
        elif self.unfollow_type == "unfollow-any-non-followers":
            self.unfollow_type = UnfollowRestriction.ANY_NON_FOLLOWERS
        elif self.unfollow_type == "unfollow-any-followers":
            self.unfollow_type = UnfollowRestriction.ANY_FOLLOWERS
        else:
            self.unfollow_type = UnfollowRestriction.ANY

        @run_safely(
            device=device,
            device_id=self.device_id,
            sessions=self.sessions,
            session_state=self.session_state,
            screen_record=self.args.screen_record,
            configs=configs,
        )
        def job():
            self.unfollow(
                device,
                count - self.state.unfollowed_count,
                self.on_unfollow,
                storage,
                self.unfollow_type,
                self.session_state.my_username,
                plugin,
            )
            logger.info(
                f"Unfollowed {self.state.unfollowed_count}, finish.",
                extra={"color": f"{Fore.CYAN}"},
            )
            self.state.is_job_completed = True
            device.back()

        while not self.state.is_job_completed and (self.state.unfollowed_count < count):
            job()

    def unfollow(
        self,
        device,
        count,
        on_unfollow,
        storage,
        unfollow_restriction,
        my_username,
        job_name,
    ):
        skipped_list_limit = get_value(self.args.skipped_list_limit, None, 15)
        skipped_fling_limit = get_value(self.args.fling_when_skipped, None, 0)
        posts_end_detector = ScrollEndDetector(
            repeats_to_end=2,
            skipped_list_limit=skipped_list_limit,
            skipped_fling_limit=skipped_fling_limit,
        )

        # Step 1 (preferred): drive unfollow by opening each candidate's
        # profile via Instagram deep-link (https://www.instagram.com/<user>/)
        # and unfollowing from the profile screen. Only meaningful when we
        # restrict unfollows to users previously followed by the bot
        # (otherwise we have no candidate list to iterate).
        use_search = self._is_truthy(getattr(self.args, "unfollow_via_search", "true"))
        if use_search and unfollow_restriction in (
            UnfollowRestriction.FOLLOWED_BY_SCRIPT,
            UnfollowRestriction.FOLLOWED_BY_SCRIPT_NON_FOLLOWERS,
        ):
            done = self.unfollow_via_search(
                device,
                count,
                on_unfollow,
                storage,
                unfollow_restriction,
                my_username,
                job_name,
            )
            if done >= count:
                return
            # Some budget left -> fall through to scroll-based flow as backup
            logger.info(
                f"Deep-link unfollow done ({done}/{count}). "
                "Falling back to scroll-based iteration for the remainder.",
                extra={"color": f"{Fore.CYAN}"},
            )
            count -= done

        ProfileView(device).navigateToFollowing()
        self.iterate_over_followings(
            device,
            count,
            on_unfollow,
            storage,
            unfollow_restriction,
            my_username,
            posts_end_detector,
            job_name,
        )

    @staticmethod
    def _is_truthy(val) -> bool:
        if isinstance(val, bool):
            return val
        if val is None:
            return False
        return str(val).strip().lower() in ("1", "true", "yes", "y", "on")

    def _build_unfollow_candidates(
        self, storage, unfollow_restriction, max_candidates
    ):
        """
        Return a shuffled list of usernames eligible for unfollow, drawn
        from `storage.interacted_users`. Filters by following_status,
        whitelist and unfollow-delay. Does NOT pre-check follow-back state
        (that requires an in-app check) - we let the per-user flow do it.
        """
        delay_days = get_value(self.args.unfollow_delay, None, 0)
        # Throttle re-checks: if we already probed a user in the last
        # RECHECK_DAYS days and decided NOT to unfollow them (because they
        # follow us back, or the UI check failed), skip them this session.
        # Abbassato da 5 a 2: con 5gg e backlog di ~100 utenti si svuotava
        # la coda in 1 sessione e poi rimaneva a 0 candidati per 5 giorni.
        RECHECK_DAYS = 2
        eligible_statuses = {
            FollowingStatus.FOLLOWED,
            FollowingStatus.REQUESTED,
            # Re-try users we already attempted to unfollow in case of soft-ban
            FollowingStatus.UNFOLLOWED,
        }
        candidates = []
        skipped_whitelist = 0
        skipped_status = 0
        skipped_delay = 0
        skipped_recheck = 0
        for username, _ in storage.interacted_users.items():
            if storage.is_user_in_whitelist(username):
                skipped_whitelist += 1
                continue
            status = storage.get_following_status(username)
            if status == FollowingStatus.NOT_IN_LIST:
                skipped_status += 1
                continue
            if status not in eligible_statuses:
                skipped_status += 1
                continue
            _, last_interaction = storage.check_user_was_interacted(username)
            if not storage.can_be_unfollowed(last_interaction, delay_days):
                skipped_delay += 1
                continue
            # Skip users we already probed recently and decided to keep
            last_check, _ = storage.get_last_unfollow_check(username)
            if last_check is not None and (
                datetime.now() - last_check
            ).days < RECHECK_DAYS:
                skipped_recheck += 1
                continue
            candidates.append((username, last_interaction or datetime.min))
        logger.info(
            f"🔍 unfollow candidates: {len(candidates)} OK | "
            f"skip_status={skipped_status} skip_delay={skipped_delay} "
            f"skip_recheck={skipped_recheck}(RECHECK={RECHECK_DAYS}d) "
            f"skip_whitelist={skipped_whitelist}"
        )
        # Oldest interactions first - those are the safer ones to drop.
        candidates.sort(key=lambda x: x[1])
        # But shuffle the head a bit so we don't always hit the very same users
        # in the same order across sessions: shuffle the first 3*max window.
        window = candidates[: max_candidates * 3]
        random.shuffle(window)
        rest = candidates[max_candidates * 3 :]
        ordered = [u for u, _ in window + rest]
        return ordered

    def unfollow_via_search(
        self,
        device,
        count,
        on_unfollow,
        storage,
        unfollow_restriction,
        my_username,
        job_name,
    ) -> int:
        """
        For each candidate username taken from interacted_users.json, open
        their profile directly via Instagram deep-link
        (https://www.instagram.com/<user>/) and perform the unfollow flow.
        This sidesteps the in-list search bar (which is unreliable on
        emulators) and avoids any scroll on the Following list.

        Returns how many unfollows were performed.
        """
        candidates = self._build_unfollow_candidates(
            storage, unfollow_restriction, count
        )
        if not candidates:
            logger.info(
                "No candidates available in interacted_users.json for search-based unfollow.",
                extra={"color": f"{Fore.YELLOW}"},
            )
            return 0
        logger.info(
            f"Deep-link unfollow: {len(candidates)} candidates available, target {count}.",
            extra={"color": f"{Fore.CYAN}"},
        )

        unfollowed = 0
        skipped_already_gone = 0
        skipped_following_back = 0
        skipped_other = 0
        for username in candidates:
            if unfollowed >= count:
                break
            if self.session_state.check_limit(
                limit_type=self.session_state.Limit.UNFOLLOWS, output=True
            ):
                logger.info("Total unfollows limit reached. Finish.")
                break

            outcome = self._open_and_unfollow_one(
                device,
                username,
                storage,
                unfollow_restriction,
                my_username,
                job_name,
            )
            if outcome == "unfollowed":
                storage.add_interacted_user(
                    username,
                    self.session_state.id,
                    unfollowed=True,
                    job_name=job_name,
                    target=None,
                )
                on_unfollow()
                unfollowed += 1
            elif outcome == "not_in_list":
                skipped_already_gone += 1
            elif outcome == "follows_back":
                storage.mark_unfollow_check(username, "follows_back")
                skipped_following_back += 1
            else:
                storage.mark_unfollow_check(username, "error")
                skipped_other += 1
        logger.info(
            f"Deep-link unfollow finished: {unfollowed} unfollowed, "
            f"{skipped_already_gone} already gone from your following list, "
            f"{skipped_following_back} skipped because they follow you back, "
            f"{skipped_other} other skips. Target was {count}.",
            extra={"color": f"{Fore.CYAN}"},
        )
        return unfollowed

    def _open_profile_via_deeplink(self, device, username: str) -> bool:
        """
        Open the target user's profile by issuing an Instagram deep-link via
        `am start`. Way more reliable than typing on the in-list search bar
        on emulators. Returns True if the profile screen rendered.
        """
        url = f"https://www.instagram.com/{username}/"
        if not open_instagram_with_url(url):
            logger.warning(f"Deep-link to @{username} failed.")
            return False
        # Some emulators / OEM ROMs still pop up the "Open with..." chooser
        # the very first time even when we pin the package. Auto-dismiss it
        # by picking Instagram + "Always" - after the first time the system
        # remembers the choice and the chooser stops appearing.
        self._dismiss_open_with_chooser(device)
        # Wait for the profile to render. We probe a few stable widgets that
        # are always present on a profile page (own or someone else's).
        for _ in range(3):
            random_sleep(1, 2, modulable=False)
            header = device.find(
                resourceIdMatches=(
                    f"{self.ResourceID.ROW_PROFILE_HEADER_FOLLOWING_CONTAINER}"
                    f"|{self.ResourceID.ROW_PROFILE_HEADER_FOLLOWERS_CONTAINER}"
                )
            )
            if header.exists(Timeout.MEDIUM):
                return True
        logger.warning(
            f"Profile of @{username} did not render after deep-link "
            "(possibly account deleted, banned, or you got blocked)."
        )
        return False

    def _dismiss_open_with_chooser(self, device) -> None:
        """
        If the Android "Open with..." disambiguation dialog appeared, pick
        Instagram and tell the system to ALWAYS use it for future deep-links.
        No-op when the dialog is not on screen.
        """
        chooser = device.find(
            resourceIdMatches=(
                "android:id/resolver_list"
                "|com.android.internal:id/resolver_list"
                "|android:id/contentPanel"
            )
        )
        if not chooser.exists(Timeout.SHORT):
            return
        logger.info(
            "Detected Android 'Open with...' chooser - selecting Instagram (Always).",
            extra={"color": f"{Fore.CYAN}"},
        )
        # Tap on the Instagram entry in the list (case-insensitive match).
        ig_entry = device.find(textMatches="(?i)^Instagram$")
        if ig_entry.exists(Timeout.SHORT):
            ig_entry.click()
            random_sleep(0, 1, modulable=False)
        # Press the "Always" button so the chooser doesn't reappear next time.
        always_btn = device.find(
            resourceIdMatches=(
                "android:id/button_always"
                "|com.android.internal:id/button_always"
            )
        )
        if not always_btn.exists(Timeout.SHORT):
            always_btn = device.find(textMatches="(?i)^Always$")
        if always_btn.exists(Timeout.SHORT):
            always_btn.click()
            random_sleep(0, 1, modulable=False)

    def _profile_state(self, device):
        """
        Inspect the currently-open profile and return one of:
            "following"    -> there's a "Following" / "Requested" button visible
                              (you are following / have a pending request)
            "not_following"-> there's a "Follow" / "Follow back" button (you
                              are NOT following them)
            "unknown"      -> couldn't determine (UI not loaded, weird state)
        """
        following_btn = device.find(
            classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
            clickable=True,
            textMatches=FOLLOWING_REGEX,
        )
        if following_btn.exists(Timeout.SHORT):
            return "following"
        follow_btn = device.find(
            classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
            clickable=True,
            textMatches="^Follow( back)?$",
        )
        if follow_btn.exists(Timeout.SHORT):
            return "not_following"
        return "unknown"

    def _profile_follows_me(self, device, my_username: str) -> Optional[bool]:
        """
        Ground-truth follow-back check (language-independent).

        To know whether the TARGET follows ME, we look at the people the
        target is following (their "Following" tab) and search for my
        username there. If I appear, they follow me.

        From the currently-open profile of the target user:
          1. Open their Following tab
          2. Type `my_username` in the in-list search bar
          3. Look for a row whose username TextView equals `my_username`
          4. Navigate back to the profile

        Returns:
            True  -> my_username appears in target's following list
                    (they follow us)
            False -> target's following list loaded but my_username NOT
                    present (they do NOT follow us)
            None  -> UI failure, could not determine (caller should skip)
        """
        if not my_username:
            return None

        logger.info(
            f"🔎 Checking if @{my_username} is in their FOLLOWING list...",
            extra={"color": f"{Fore.CYAN}"},
        )

        # 1. Open Following tab on the currently-open profile
        if not ProfileView(device).navigateToFollowing():
            logger.warning("Could not open target's Following tab.")
            return None

        # Wait for the followers list to render at least one row
        any_row = device.find(
            resourceId=self.ResourceID.FOLLOW_LIST_USERNAME,
            className=ClassName.TEXT_VIEW,
        )
        if not any_row.exists(Timeout.LONG):
            logger.warning("Following list did not load in time.")
            # Try to go back so the caller stays on a sane screen
            try:
                profile_sentinel_id = (
                    f"{self.ResourceID.ROW_PROFILE_HEADER_FOLLOWING_CONTAINER}"
                    f"|{self.ResourceID.ROW_PROFILE_HEADER_FOLLOWERS_CONTAINER}"
                )
                for _ in range(3):
                    if device.find(
                        resourceIdMatches=profile_sentinel_id
                    ).exists(Timeout.SHORT):
                        break
                    device.back()
                    random_sleep(0, 1, modulable=False)
            except Exception:
                pass
            return None

        # 2. Use the in-list search bar to filter to exactly my_username.
        #    This is a single-username query (deterministic) - far faster
        #    and more reliable than scrolling the whole followers list.
        #
        #    IMPORTANT: Instagram shows "mutual / people you know" at the top
        #    of the target's Following list. If we don't filter, we'd find
        #    our OWN username there as a suggestion and conclude "they follow
        #    us" even when they don't. So we REQUIRE the search bar to be
        #    actually filtered before accepting a positive match.
        result: Optional[bool] = None
        try:
            search_field = device.find(
                resourceId=self.ResourceID.ROW_SEARCH_EDIT_TEXT,
                className=ClassName.EDIT_TEXT,
            )
            filter_applied = False
            if search_field.exists(Timeout.SHORT):
                search_field.click()
                random_sleep(0, 1, modulable=False)
                # Try set_text first (atomic paste, triggers live-filter)
                try:
                    search_field.set_text(my_username)
                except Exception:
                    try:
                        device.deviceV2.send_keys(my_username, clear=True)
                    except Exception:
                        logger.warning(
                            "Could not type my username in following search bar."
                        )
                random_sleep(1, 2, modulable=False)

                # Readback: confirm the EditText actually contains our query.
                try:
                    typed = (search_field.get_text() or "").strip().lstrip("@")
                except Exception:
                    typed = ""
                if typed.casefold() == my_username.casefold():
                    filter_applied = True
                else:
                    logger.warning(
                        f"Search bar readback mismatch (got='{typed}', "
                        f"expected='{my_username}'). Cannot trust filter."
                    )

            if not filter_applied:
                # Without a working filter we cannot trust the visible rows
                # (top of list may include our own username as "mutual /
                # people you may know"). Skip the candidate this round.
                logger.warning(
                    "Following-list search bar could not be filtered; "
                    "skipping follow-back decision (no action this round)."
                )
                result = None
            else:
                # 3. After confirmed filtering, look for a row with exactly
                #    my_username. We also verify that the visible row count
                #    after filtering is small (<=3): if the list is still
                #    showing many rows, the live-filter didn't kick in.
                visible_rows = device.find(
                    resourceId=self.ResourceID.FOLLOW_LIST_USERNAME,
                    className=ClassName.TEXT_VIEW,
                )
                try:
                    row_count = visible_rows.count_items()
                except Exception:
                    row_count = None

                my_row = device.find(
                    resourceId=self.ResourceID.FOLLOW_LIST_USERNAME,
                    className=ClassName.TEXT_VIEW,
                    text=my_username,
                )
                found = bool(my_row.exists(Timeout.SHORT))

                if found and row_count is not None and row_count > 3:
                    # Filter likely not applied even though readback said so;
                    # we may be matching a "mutual" suggestion at top of an
                    # unfiltered list. Be conservative: treat as unknown.
                    logger.warning(
                        f"Search readback OK but {row_count} rows still "
                        "visible (filter did not converge). Skipping "
                        "follow-back decision."
                    )
                    result = None
                else:
                    result = found
        finally:
            # 4. Always navigate back to the profile so the caller can press
            #    "Following" and continue the unfollow flow.
            #    The keyboard may still be open after set_text; the first
            #    `back` typically closes the keyboard, the second leaves
            #    the Following list and returns to the profile header.
            #    We verify by probing a profile-header sentinel and issue
            #    extra `back` taps if needed (capped to avoid runaway).
            try:
                profile_sentinel_id = (
                    f"{self.ResourceID.ROW_PROFILE_HEADER_FOLLOWING_CONTAINER}"
                    f"|{self.ResourceID.ROW_PROFILE_HEADER_FOLLOWERS_CONTAINER}"
                )
                for _ in range(4):
                    on_profile = device.find(
                        resourceIdMatches=profile_sentinel_id
                    ).exists(Timeout.SHORT)
                    if on_profile:
                        break
                    device.back()
                    random_sleep(0, 1, modulable=False)
            except Exception:
                pass

        return result

    def _open_and_unfollow_one(
        self,
        device,
        username,
        storage,
        unfollow_restriction,
        my_username,
        job_name,
    ) -> str:
        """
        Open the candidate's profile via deep-link, decide whether they are
        eligible to be unfollowed (depending on `unfollow_restriction`), and
        do it.

        Returns one of:
            "unfollowed"     -> we actually unfollowed them (counts)
            "not_in_list"    -> we are not following them anymore on IG
                                (already unfollowed manually / blocked) -
                                state reconciled, no count
            "follows_back"   -> they follow us back; for non-followers job
                                we skip - no count
            "error"          -> UI failure / could not decide - no count
        """
        logger.info(
            f"🔎 Opening @{username} profile (deep-link)...",
            extra={"color": f"{Fore.CYAN}"},
        )
        if not self._open_profile_via_deeplink(device, username):
            return "error"

        state = self._profile_state(device)
        if state == "unknown":
            logger.warning(
                f"Could not detect Following/Follow button on @{username} "
                "profile. Skipping (no state changes)."
            )
            return "error"
        if state == "not_following":
            logger.info(
                f"@{username}: you are not following them anymore on Instagram. "
                f"Reconciling local state.",
                extra={"color": f"{Fore.YELLOW}"},
            )
            storage.add_interacted_user(
                username,
                self.session_state.id,
                unfollowed=True,
                job_name=job_name,
                target=None,
            )
            return "not_in_list"

        # state == "following" -> proceed
        if unfollow_restriction == UnfollowRestriction.FOLLOWED_BY_SCRIPT_NON_FOLLOWERS:
            follows_me = self._profile_follows_me(device, my_username)
            if follows_me is True:
                logger.info(
                    f"Skip @{username}: they follow you back "
                    f"(@{my_username} found in their following list).",
                    extra={"color": f"{Fore.YELLOW}"},
                )
                return "follows_back"
            if follows_me is None:
                logger.info(
                    f"Skip @{username}: cannot determine follow-back state."
                )
                return "error"
            # follows_me is False -> safe to unfollow
            logger.info(
                f"@{username} does NOT follow you back "
                f"(@{my_username} absent from their following). Proceeding to unfollow.",
                extra={"color": f"{Fore.CYAN}"},
            )
            # Re-verify we're back on the profile (not on the Following
            # list / keyboard / some other screen) before pressing the
            # Following button. If we're not, reopen the profile.
            recheck = self._profile_state(device)
            if recheck != "following":
                logger.info(
                    f"Not on @{username} profile after follow-back check "
                    f"(state={recheck}); reopening via deep-link."
                )
                if not self._open_profile_via_deeplink(device, username):
                    return "error"
                # We just verified they don't follow us a moment ago;
                # confirm the profile shows "Following" before clicking.
                if self._profile_state(device) != "following":
                    return "error"

        ok = self._do_unfollow_on_open_profile(device, username)
        if ok:
            logger.info(
                f"✅ Unfollowed @{username}.",
                extra={"color": f"{Fore.GREEN}"},
            )
            return "unfollowed"
        return "error"

    def _do_unfollow_on_open_profile(self, device: DeviceFacade, username: str) -> bool:
        """
        Press the Following button on the currently-open profile, confirm.
        Does NOT navigate back: the deep-link flow opens a new profile each
        time, so we leave the screen as-is.
        Returns True on success.
        """
        unfollow_button = device.find(
            classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
            clickable=True,
            textMatches=FOLLOWING_REGEX,
        )
        attempts = 2
        for _ in range(attempts):
            if unfollow_button.exists():
                break
            scrollable = device.find(classNameMatches=ClassName.VIEW_PAGER)
            if scrollable.exists():
                scrollable.scroll(Direction.UP)
            unfollow_button = device.find(
                classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
                clickable=True,
                textMatches=FOLLOWING_REGEX,
            )
        if not unfollow_button.exists():
            logger.error("Cannot find Following button on profile.")
            save_crash(device)
            return False
        get_throttler().wait_if_needed(ActionType.UNFOLLOW)
        unfollow_button.click()
        logger.info(f"Unfollow @{username}.", extra={"color": f"{Fore.YELLOW}"})

        confirm_unfollow_button = None
        for _ in range(2):
            confirm_unfollow_button = device.find(
                resourceId=self.ResourceID.FOLLOW_SHEET_UNFOLLOW_ROW
            )
            if confirm_unfollow_button.exists(Timeout.SHORT):
                break
        if not confirm_unfollow_button or not confirm_unfollow_button.exists():
            logger.error("Cannot confirm unfollow.")
            save_crash(device)
            return False
        confirm_unfollow_button.click()
        random_sleep(0, 1, modulable=False)

        # Private-account extra confirm
        private_unfollow_button = device.find(
            classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
            textMatches=UNFOLLOW_REGEX,
        )
        if private_unfollow_button.exists(Timeout.SHORT):
            private_unfollow_button.click()

        UniversalActions.detect_block(device)
        return True

    def on_unfollow(self):
        self.state.unfollowed_count += 1
        self.session_state.totalUnfollowed += 1

    def sort_followings_by_date(self, device, newest_to_oldest=False) -> bool:
        sort_button = device.find(
            resourceId=self.ResourceID.SORTING_ENTRY_ROW_OPTION,
        )
        if not sort_button.exists(Timeout.MEDIUM):
            logger.error(
                "Cannot find button to sort followings. Continue without sorting."
            )
            return False
        sort_button.click()

        sort_options_recycler_view = device.find(
            resourceId=self.ResourceID.FOLLOW_LIST_SORTING_OPTIONS_RECYCLER_VIEW
        )
        if not sort_options_recycler_view.exists(Timeout.MEDIUM):
            logger.error(
                "Cannot find options to sort followings. Continue without sorting."
            )
            return False
        if newest_to_oldest:
            logger.info("Sort followings by date: from newest to oldest.")
            sort_options_recycler_view.child(textContains="Latest").click()
        else:
            logger.info("Sort followings by date: from oldest to newest.")
            sort_options_recycler_view.child(textContains="Earliest").click()
        return True

    def iterate_over_followings(
        self,
        device,
        count,
        on_unfollow,
        storage,
        unfollow_restriction,
        my_username,
        posts_end_detector,
        job_name,
    ):
        # Wait until list is rendered
        sorted = False
        for _ in range(2):
            user_lst = device.find(
                resourceId=self.ResourceID.FOLLOW_LIST_CONTAINER,
                className=ClassName.LINEAR_LAYOUT,
            )
            user_lst.wait(Timeout.LONG)

            sort_container_obj = device.find(
                resourceId=self.ResourceID.SORTING_ENTRY_ROW_OPTION
            )
            if sort_container_obj.exists() and not sorted:
                sorted = self.sort_followings_by_date(
                    device, self.args.sort_followers_newest_to_oldest
                )
                continue

            top_tab_obj = device.find(
                resourceId=self.ResourceID.UNIFIED_FOLLOW_LIST_TAB_LAYOUT
            )
            if sort_container_obj.exists(Timeout.SHORT) and top_tab_obj.exists(
                Timeout.SHORT
            ):
                sort_container_bounds = sort_container_obj.get_bounds()["top"]
                list_tab_bounds = top_tab_obj.get_bounds()["bottom"]
                delta = sort_container_bounds - list_tab_bounds
                UniversalActions(device)._swipe_points(
                    direction=Direction.DOWN,
                    start_point_y=sort_container_bounds,
                    delta_y=delta - 50,
                )
            else:
                UniversalActions(device)._swipe_points(
                    direction=Direction.DOWN, delta_y=380
                )

            if sort_container_obj.exists() and not sorted:
                self.sort_followings_by_date(
                    device, self.args.sort_followers_newest_to_oldest
                )
                sorted = True
        checked = {}
        unfollowed_count = 0
        total_unfollows_limit_reached = False
        posts_end_detector.notify_new_page()
        prev_screen_iterated_followings = []
        while True:
            screen_iterated_followings = []
            unfollows_in_this_view = 0
            logger.info("Iterate over visible followings.")
            user_list = device.find(
                resourceIdMatches=self.ResourceID.USER_LIST_CONTAINER,
            )
            row_height, n_users = inspect_current_view(user_list)
            for item in user_list:
                cur_row_height = item.get_height()
                if cur_row_height < row_height:
                    continue
                user_info_view = item.child(index=1)
                user_name_view = user_info_view.child(index=0).child()
                if not user_name_view.exists():
                    logger.info(
                        "Next item not found: probably reached end of the screen.",
                        extra={"color": f"{Fore.GREEN}"},
                    )
                    break

                username = user_name_view.get_text()
                screen_iterated_followings.append(username)
                if username not in checked:
                    checked[username] = None

                    if storage.is_user_in_whitelist(username):
                        logger.info(f"@{username} is in whitelist. Skip.")
                        continue

                    if unfollow_restriction in [
                        UnfollowRestriction.FOLLOWED_BY_SCRIPT,
                        UnfollowRestriction.FOLLOWED_BY_SCRIPT_NON_FOLLOWERS,
                    ]:
                        following_status = storage.get_following_status(username)
                        _, last_interaction = storage.check_user_was_interacted(
                            username
                        )
                        if following_status == FollowingStatus.NOT_IN_LIST:
                            logger.info(
                                f"@{username} has not been followed by this bot. Skip."
                            )
                            continue
                        elif not storage.can_be_unfollowed(
                            last_interaction,
                            get_value(self.args.unfollow_delay, None, 0),
                        ):
                            logger.info(
                                f"@{username} has been followed less then {self.args.unfollow_delay} days ago. Skip."
                            )
                            continue
                        elif following_status == FollowingStatus.UNFOLLOWED:
                            logger.info(
                                f"You have already unfollowed @{username} on {last_interaction}. Probably you got a soft ban at some point. Try again... Following status: {following_status.name}."
                            )
                        elif following_status not in (
                            FollowingStatus.FOLLOWED,
                            FollowingStatus.REQUESTED,
                        ):
                            logger.info(
                                f"Skip @{username}. Following status: {following_status.name}."
                            )
                            continue

                    if unfollow_restriction in [
                        UnfollowRestriction.ANY,
                        UnfollowRestriction.ANY_NON_FOLLOWERS,
                    ]:
                        following_status = storage.get_following_status(username)
                        if following_status == FollowingStatus.UNFOLLOWED:
                            logger.info(
                                f"Skip @{username}. Following status: {following_status.name}."
                            )
                            continue
                    if unfollow_restriction in [
                        UnfollowRestriction.ANY,
                        UnfollowRestriction.FOLLOWED_BY_SCRIPT,
                    ]:
                        unfollowed = FollowingView(device).do_unfollow_from_list(
                            user_row=item, username=username
                        )
                    else:
                        unfollowed = self.do_unfollow(
                            device,
                            username,
                            my_username,
                            unfollow_restriction
                            in [
                                UnfollowRestriction.FOLLOWED_BY_SCRIPT_NON_FOLLOWERS,
                                UnfollowRestriction.ANY_NON_FOLLOWERS,
                                UnfollowRestriction.ANY_FOLLOWERS,
                            ],
                            job_name == "unfollow-any-followers",
                        )

                    if unfollowed:
                        storage.add_interacted_user(
                            username,
                            self.session_state.id,
                            unfollowed=True,
                            job_name=job_name,
                            target=None,
                        )
                        on_unfollow()
                        unfollowed_count += 1
                        unfollows_in_this_view += 1
                        total_unfollows_limit_reached = self.session_state.check_limit(
                            limit_type=self.session_state.Limit.UNFOLLOWS,
                            output=True,
                        )
                    if unfollowed_count >= count or total_unfollows_limit_reached:
                        return
                else:
                    logger.debug(f"Already checked {username}.")

            # Track empty views (no unfollow performed) to bail out of dead-scroll loops
            # (e.g. Instagram "Latest" sort that does not load older follows past a cap).
            if unfollows_in_this_view == 0:
                posts_end_detector.notify_skipped_all()
                if posts_end_detector.is_skipped_limit_reached():
                    return
            else:
                posts_end_detector.reset_skipped_all()

            if screen_iterated_followings != prev_screen_iterated_followings:
                prev_screen_iterated_followings = screen_iterated_followings
                logger.info("Need to scroll now.", extra={"color": f"{Fore.GREEN}"})
                list_view = device.find(
                    resourceId=self.ResourceID.LIST,
                )
                list_view.scroll(Direction.DOWN)
            else:
                load_more_button = device.find(
                    resourceId=self.ResourceID.ROW_LOAD_MORE_BUTTON
                )
                if load_more_button.exists():
                    load_more_button.click()
                    random_sleep()
                    if load_more_button.exists():
                        logger.warning(
                            "Can't iterate over the list anymore, you may be soft-banned and cannot perform this action (refreshing follower list)."
                        )
                        return
                    list_view.scroll(Direction.DOWN)
                else:
                    logger.info(
                        "Reached the following list end, finish.",
                        extra={"color": f"{Fore.GREEN}"},
                    )
                    return

    def do_unfollow(
        self,
        device: DeviceFacade,
        username,
        my_username,
        check_if_is_follower,
        unfollow_followers=False,
    ):
        """
        :return: whether unfollow was successful
        """
        username_view = device.find(
            resourceId=self.ResourceID.FOLLOW_LIST_USERNAME,
            className=ClassName.TEXT_VIEW,
            text=username,
        )
        if not username_view.exists():
            logger.error(f"Cannot find @{username}, skip.")
            return False
        username_view.click_retry()

        is_following_you = self.check_is_follower(device, username, my_username)
        if is_following_you is not None:
            if check_if_is_follower and is_following_you:
                if not unfollow_followers:
                    logger.info(f"Skip @{username}. This user is following you.")
                    logger.info("Back to the followings list.")
                    device.back()
                    return False
                else:
                    logger.info(f"@{username} is following you, unfollow. 😈")
            unfollow_button = device.find(
                classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
                clickable=True,
                textMatches=FOLLOWING_REGEX,
            )
            # I don't know/remember the origin of this, if someone does - let's document it
            attempts = 2
            for _ in range(attempts):
                if unfollow_button.exists():
                    break

                scrollable = device.find(classNameMatches=ClassName.VIEW_PAGER)
                if scrollable.exists():
                    scrollable.scroll(Direction.UP)
                unfollow_button = device.find(
                    classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
                    clickable=True,
                    textMatches=FOLLOWING_REGEX,
                )

            if not unfollow_button.exists():
                logger.error("Cannot find Following button.")
                save_crash(device)
            logger.debug("Unfollow button click.")
            get_throttler().wait_if_needed(ActionType.UNFOLLOW)
            unfollow_button.click()
            logger.info(f"Unfollow @{username}.", extra={"color": f"{Fore.YELLOW}"})

            # Weirdly enough, this is a fix for after you unfollow someone that follows
            # you back - the next person you unfollow the button is missing on first find
            # additional find - finds it. :shrug:
            confirm_unfollow_button = None
            attempts = 2
            for _ in range(attempts):
                confirm_unfollow_button = device.find(
                    resourceId=self.ResourceID.FOLLOW_SHEET_UNFOLLOW_ROW
                )
                if confirm_unfollow_button.exists(Timeout.SHORT):
                    break

            if not confirm_unfollow_button or not confirm_unfollow_button.exists():
                logger.error("Cannot confirm unfollow.")
                save_crash(device)
                device.back()
                return False
            logger.debug("Confirm unfollow.")
            confirm_unfollow_button.click()

            random_sleep(0, 1, modulable=False)

            # Check if private account confirmation
            private_unfollow_button = device.find(
                classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
                textMatches=UNFOLLOW_REGEX,
            )
            if private_unfollow_button.exists(Timeout.SHORT):
                logger.debug("Confirm unfollow private account.")
                private_unfollow_button.click()

            UniversalActions.detect_block(device)
        else:
            logger.info("Back to the followings list.")
            device.back()
            return False
        logger.info("Back to the followings list.")
        device.back()
        return True

    def check_is_follower(self, device, username, my_username):
        """
        Legacy follow-back check used by the scroll-based fallback flow.
        Opens the target's Following tab and looks up my_username among the
        first visible rows. Not 100% reliable on big lists (false negatives
        possible), but kept for backward compatibility.

        For the deep-link flow we use `_profile_follows_me` instead, which
        looks at the "Follows you" badge on the target's profile header.

        Returns:
            True  -> the target follows me back
            False -> the target does NOT follow me back (or my_username not
                     in the first visible rows)
            None  -> could not determine
        """
        logger.info(
            f"Check if @{username} is following you.",
            extra={"color": f"{Fore.GREEN}"},
        )

        if not ProfileView(device).navigateToFollowing():
            logger.info("Can't load profile in time. Skip.")
            return None

        rows = device.find(
            resourceId=self.ResourceID.FOLLOW_LIST_USERNAME,
            className=ClassName.TEXT_VIEW,
        )
        if rows.exists(Timeout.LONG):
            my_username_view = device.find(
                resourceId=self.ResourceID.FOLLOW_LIST_USERNAME,
                className=ClassName.TEXT_VIEW,
                text=my_username,
            )
            result = my_username_view.exists()
            logger.info("Back to the profile.")
            device.back()
            return result
        logger.info("Can't load profile followers in time. Skip.")
        device.back()
        return None


@unique
class UnfollowRestriction(Enum):
    ANY = 0
    FOLLOWED_BY_SCRIPT = 1
    FOLLOWED_BY_SCRIPT_NON_FOLLOWERS = 2
    ANY_NON_FOLLOWERS = 3
    ANY_FOLLOWERS = 4

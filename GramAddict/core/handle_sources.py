import logging
import os
from functools import partial
from os import path

from atomicwrites import atomic_write
from colorama import Fore

from GramAddict.core.device_facade import Direction, Timeout
from GramAddict.core.navigation import (
    nav_to_blogger,
    nav_to_feed,
    nav_to_hashtag_or_place,
    nav_to_post_likers,
)
from GramAddict.core.resources import ClassName
from GramAddict.core.storage import FollowingStatus
from GramAddict.core.utils import (
    get_value,
    inspect_current_view,
    random_choice,
    random_sleep,
)
from GramAddict.core.views import (
    FollowingView,
    LikeMode,
    OpenedPostView,
    Owner,
    PostsViewList,
    ProfileView,
    SwipeTo,
    TabBarView,
    UniversalActions,
    case_insensitive_re,
)

logger = logging.getLogger(__name__)


def _get_scroll_skip_start(args, label: str) -> int:
    """Read --scroll-skip-start from args and resolve to an int (supports ranges).

    Returns 0 if the option is missing/disabled.
    """
    raw = getattr(args, "scroll_skip_start", None)
    if raw is None or str(raw) == "0":
        return 0
    try:
        n = get_value(str(raw), f"Skip first {{}} {label} from source list", 0)
    except Exception as e:
        logger.debug(f"scroll-skip-start parse error: {e}")
        return 0
    if n is None:
        return 0
    return max(0, int(n))


def _resume_enabled(args) -> bool:
    return bool(getattr(args, "resume_from_last_position", False))


def _resume_search_limit(args) -> int:
    raw = getattr(args, "resume_anchor_search_limit", None)
    if raw is None:
        return 50
    try:
        return max(1, int(get_value(str(raw), None, 50)))
    except Exception:
        return 50


def _resume_cooldown_days(args) -> int:
    raw = getattr(args, "resume_cooldown_days", None)
    if raw is None:
        return 14
    try:
        return max(0, int(get_value(str(raw), None, 14)))
    except Exception:
        return 14


def _hot_zone_params(args):
    """Return (screens_threshold, flings_per_jump, max_jumps). 0 screens disables the feature."""
    def _read(name, default):
        raw = getattr(args, name, None)
        if raw is None:
            return default
        try:
            return max(0, int(get_value(str(raw), None, default)))
        except Exception:
            return default

    return (
        _read("hot_zone_screens", 3),
        _read("hot_zone_jump_flings", 4),
        _read("hot_zone_max_jumps", 2),
    )


def _seek_anchor_in_followers(
    self_obj, device, list_view, anchors, max_scrolls: int
):
    """Scroll the followers list down looking for any of the given anchor usernames.

    Returns (found: bool, anchor: Optional[str], scrolls_done: int).
    Stops early if the list does not advance (end reached / list shorter than anchor position).
    """
    if not anchors:
        return False, None, 0
    anchors_set = set(anchors)
    prev_first = None
    for i in range(max_scrolls):
        try:
            user_list = device.find(
                resourceIdMatches=self_obj.ResourceID.USER_LIST_CONTAINER,
            )
            visible_usernames = []
            first_username = None
            try:
                for it in user_list:
                    uname_view = it.child(index=1).child(index=0).child()
                    if not uname_view.exists():
                        continue
                    name = uname_view.get_text()
                    visible_usernames.append(name)
                    if first_username is None:
                        first_username = name
            except Exception:
                pass

            for name in visible_usernames:
                if name in anchors_set:
                    # uno scroll extra per saltare oltre l'anchor stesso
                    list_view.scroll(Direction.DOWN)
                    random_sleep(0.3, 0.8, modulable=False)
                    return True, name, i + 1

            # se non muove piu', siamo a fondo lista
            if first_username is not None and prev_first == first_username:
                return False, None, i
            prev_first = first_username

            list_view.scroll(Direction.DOWN)
            random_sleep(0.3, 0.9, modulable=False)
        except Exception as e:
            logger.debug(f"_seek_anchor_in_followers interrupted: {e}")
            return False, None, i
    return False, None, max_scrolls


def interact(
    storage,
    is_follow_limit_reached,
    username,
    interaction,
    device,
    session_state,
    current_job,
    target,
    on_interaction,
):
    can_follow = False
    if is_follow_limit_reached is not None:
        can_follow = not is_follow_limit_reached() and storage.get_following_status(
            username
        ) in [FollowingStatus.NONE, FollowingStatus.NOT_IN_LIST]

    (
        interaction_succeed,
        followed,
        requested,
        scraped,
        pm_sent,
        number_of_liked,
        number_of_watched,
        number_of_comments,
    ) = interaction(device, username=username, can_follow=can_follow)

    add_interacted_user = partial(
        storage.add_interacted_user,
        session_id=session_state.id,
        job_name=current_job,
        target=target,
    )

    add_interacted_user(
        username,
        followed=followed,
        is_requested=requested,
        scraped=scraped,
        liked=number_of_liked,
        watched=number_of_watched,
        commented=number_of_comments,
        pm_sent=pm_sent,
    )
    # Per-source quality tracking: count this follow against the source/target
    # so future sessions can weight selection towards higher FBR sources.
    if followed:
        stats = getattr(storage, "source_stats", None)
        if stats is not None and target:
            try:
                stats.register_follow(current_job, target)
            except Exception as e:
                logger.debug(f"[source-stats] register_follow failed: {e}")
    return on_interaction(
        succeed=interaction_succeed,
        followed=followed,
        scraped=scraped,
    )


def handle_blogger(
    self,
    device,
    session_state,
    blogger,
    current_job,
    storage,
    profile_filter,
    on_interaction,
    interaction,
    is_follow_limit_reached,
):
    if not nav_to_blogger(device, blogger, session_state.my_username):
        return
    can_interact = False
    if storage.is_user_in_blacklist(blogger):
        logger.info(f"@{blogger} is in blacklist. Skip.")
    else:
        interacted, interacted_when = storage.check_user_was_interacted(blogger)
        if interacted:
            if storage.was_unfollowed_before(blogger):
                logger.info(
                    f"@{blogger}: previously unfollowed - will NOT be re-followed. Skip."
                )
            else:
                can_reinteract = storage.can_be_reinteract(
                    interacted_when, get_value(self.args.can_reinteract_after, None, 0)
                )
                logger.info(
                    f"@{blogger}: already interacted on {interacted_when:%Y/%m/%d %H:%M:%S}. {'Interacting again now' if can_reinteract else 'Skip'}."
                )
                if can_reinteract:
                    can_interact = True
        else:
            can_interact = True

    if can_interact:
        logger.info(
            f"@{blogger}: interact",
            extra={"color": f"{Fore.YELLOW}"},
        )
        if not interact(
            storage=storage,
            is_follow_limit_reached=is_follow_limit_reached,
            username=blogger,
            interaction=interaction,
            device=device,
            session_state=session_state,
            current_job=current_job,
            target=blogger,
            on_interaction=on_interaction,
        ):
            return


def handle_blogger_from_file(
    self,
    device,
    parameter_passed,
    current_job,
    storage,
    on_interaction,
    interaction,
    is_follow_limit_reached,
):
    need_to_refresh = True
    on_following_list = False
    limit_reached = False

    filename: str = os.path.join(storage.account_path, parameter_passed.split(" ")[0])
    try:
        amount_of_users = get_value(parameter_passed.split(" ")[1], None, 10)
    except IndexError:
        amount_of_users = 10
        logger.warning(
            f"You didn't passed how many users should be processed from the list! Default is {amount_of_users} users."
        )
    if path.isfile(filename):
        with open(filename, "r", encoding="utf-8") as f:
            usernames = [line.replace(" ", "") for line in f if line != "\n"]
        len_usernames = len(usernames)
        if len_usernames < amount_of_users:
            amount_of_users = len_usernames
        logger.info(
            f"In {filename} there are {len_usernames} entries, {amount_of_users} users will be processed."
        )
        not_found = []
        processed_users = 0
        try:
            for line, username_raw in enumerate(usernames, start=1):
                username = username_raw.strip()
                can_interact = False
                if current_job == "unfollow-from-file":
                    unfollowed = do_unfollow_from_list(
                        device, username, on_following_list
                    )
                    on_following_list = True
                    if unfollowed:
                        storage.add_interacted_user(
                            username, self.session_state.id, unfollowed=True
                        )
                        self.session_state.totalUnfollowed += 1
                        limit_reached = self.session_state.check_limit(
                            limit_type=self.session_state.Limit.UNFOLLOWS
                        )
                        processed_users += 1
                    else:
                        not_found.append(username_raw)
                    if limit_reached:
                        logger.info("Unfollows limit reached.")
                        break
                    if processed_users == amount_of_users:
                        logger.info(
                            f"{processed_users} users have been unfollowed, going to the next job."
                        )
                        break
                else:
                    if storage.is_user_in_blacklist(username):
                        logger.info(f"@{username} is in blacklist. Skip.")
                    else:
                        (
                            interacted,
                            interacted_when,
                        ) = storage.check_user_was_interacted(username)
                        if interacted:
                            can_reinteract = storage.can_be_reinteract(
                                interacted_when,
                                get_value(self.args.can_reinteract_after, None, 0),
                            )
                            logger.info(
                                f"@{username}: already interacted on {interacted_when:%Y/%m/%d %H:%M:%S}. {'Interacting again now' if can_reinteract else 'Skip'}."
                            )
                            if can_reinteract:
                                can_interact = True
                        else:
                            can_interact = True

                    if not can_interact:
                        continue
                    if need_to_refresh:
                        search_view = TabBarView(device).navigateToSearch()
                    profile_view = search_view.navigate_to_target(username, current_job)
                    need_to_refresh = False
                    if not profile_view:
                        not_found.append(username_raw)
                        continue

                    if not interact(
                        storage=storage,
                        is_follow_limit_reached=is_follow_limit_reached,
                        username=username,
                        interaction=interaction,
                        device=device,
                        session_state=self.session_state,
                        current_job=current_job,
                        target=username,
                        on_interaction=on_interaction,
                    ):
                        return
                    device.back()
                    processed_users += 1
                    if processed_users == amount_of_users:
                        logger.info(
                            f"{processed_users} users have been interracted, going to the next job."
                        )
                        return
        finally:
            if not_found:
                with open(
                    f"{os.path.splitext(filename)[0]}_not_found.txt",
                    mode="a+",
                    encoding="utf-8",
                ) as f:
                    f.writelines(not_found)
            if self.args.delete_interacted_users and len_usernames != 0:
                with atomic_write(filename, overwrite=True, encoding="utf-8") as f:
                    f.writelines(usernames[line:])
    else:
        logger.warning(
            f"File {filename} not found. You have to specify the right relative path from this point: {os.getcwd()}"
        )
        return

    logger.info(f"Interact with users in {filename} completed.")
    device.back()


def do_unfollow_from_list(device, username, on_following_list):
    if not on_following_list:
        ProfileView(device).click_on_avatar()
        if ProfileView(device).navigateToFollowing() and UniversalActions(
            device
        ).search_text(username):
            return FollowingView(device).do_unfollow_from_list(username)
    else:
        if username is not None:
            UniversalActions(device).search_text(username)
        return FollowingView(device).do_unfollow_from_list(username)


def handle_likers(
    self,
    device,
    session_state,
    target,
    current_job,
    storage,
    profile_filter,
    posts_end_detector,
    on_interaction,
    interaction,
    is_follow_limit_reached,
):
    if (
        current_job == "blogger-post-likers"
        and not nav_to_post_likers(device, target, session_state.my_username)
        or current_job != "blogger-post-likers"
        and not nav_to_hashtag_or_place(device, target, current_job)
    ):
        logger.warning(f"⛔ handle_likers: navigazione fallita per {target!r} ({current_job}). Sorgente saltata.")
        return False
    logger.info(f"📋 handle_likers: inizio scansione likers di {target!r} ({current_job})")
    post_description = ""
    nr_same_post = 0
    nr_same_posts_max = 3
    while True:
        flag, post_description, _, _, _, _ = PostsViewList(device)._check_if_last_post(
            post_description, current_job
        )
        has_likers, number_of_likers = PostsViewList(device)._find_likers_container()
        if flag:
            nr_same_post += 1
            logger.info(f"Warning: {nr_same_post}/{nr_same_posts_max} repeated posts.")
            if nr_same_post == nr_same_posts_max:
                logger.info(
                    f"Scrolled through {nr_same_posts_max} posts with same description and author. Finish.",
                    extra={"color": f"{Fore.CYAN}"},
                )
                break
        else:
            nr_same_post = 0

        if (
            has_likers
            and profile_filter.is_num_likers_in_range(number_of_likers)
            and number_of_likers != 1
        ):
            logger.info(f"👍 Post di {target!r}: {number_of_likers} likers — apro lista.")
            PostsViewList(device).open_likers_container()
        else:
            if not has_likers:
                logger.info(f"⏭️  Post di {target!r}: nessun likers visibile — skip post.")
            elif number_of_likers == 1:
                logger.info(f"⏭️  Post di {target!r}: solo 1 liker — skip post.")
            else:
                logger.info(f"⏭️  Post di {target!r}: {number_of_likers} likers fuori range filtro — skip post.")
            PostsViewList(device).swipe_to_fit_posts(SwipeTo.NEXT_POST)
            continue

        posts_end_detector.notify_new_page()

        likes_list_view = OpenedPostView(device)._getListViewLikers()
        if likes_list_view is None:
            logger.warning(f"⚠️  Lista likers non caricata per post di {target!r}. Passo al post successivo.")
            PostsViewList(device).swipe_to_fit_posts(SwipeTo.NEXT_POST)
            continue
        prev_screen_iterated_likers = []

        # --- Random skip start on likers list ---
        skip_n = _get_scroll_skip_start(self.args, "likers")
        if skip_n > 0:
            logger.info(
                f"Skipping first {skip_n} likers of {target} (random start).",
                extra={"color": f"{Fore.CYAN}"},
            )
            actually_skipped = 0
            prev_first = None
            for i in range(skip_n):
                try:
                    uc = OpenedPostView(device)._getUserContainer()
                    first_uname = None
                    if uc is not None:
                        try:
                            for it in uc:
                                uname_view = OpenedPostView(device)._getUserName(it)
                                if uname_view.exists():
                                    first_uname = uname_view.get_text()
                                    break
                        except Exception:
                            pass
                    likes_list_view.scroll(Direction.DOWN)
                    random_sleep(0.3, 0.9, modulable=False)
                    if first_uname is not None and prev_first == first_uname:
                        logger.info(
                            f"Likers list didn't move while skipping (skipped {actually_skipped}/{skip_n}). Stop.",
                            extra={"color": f"{Fore.CYAN}"},
                        )
                        break
                    prev_first = first_uname
                    actually_skipped += 1
                except Exception as e:
                    logger.debug(f"Skip-start (likers) interrupted: {e}")
                    break
            logger.info(
                f"Skipped {actually_skipped} likers, starting iteration here.",
                extra={"color": f"{Fore.CYAN}"},
            )

        while True:
            logger.info("Iterate over visible likers.")
            screen_iterated_likers = []
            opened = False
            user_container = OpenedPostView(device)._getUserContainer()
            if user_container is None:
                logger.warning("Likers list didn't load :(")
                return
            row_height, n_users = inspect_current_view(user_container)
            try:
                for item in user_container:
                    cur_row_height = item.get_height()
                    if cur_row_height < row_height:
                        continue
                    element_opened = False
                    username_view = OpenedPostView(device)._getUserName(item)
                    if not username_view.exists(Timeout.MEDIUM):
                        logger.info(
                            "Next item not found: probably reached end of the screen.",
                            extra={"color": f"{Fore.GREEN}"},
                        )
                        break

                    username = username_view.get_text()
                    screen_iterated_likers.append(username)
                    posts_end_detector.notify_username_iterated(username)
                    can_interact = False
                    if storage.is_user_in_blacklist(username):
                        logger.info(f"@{username} is in blacklist. Skip.")
                    else:
                        (
                            interacted,
                            interacted_when,
                        ) = storage.check_user_was_interacted(username)
                        if interacted:
                            if storage.was_unfollowed_before(username):
                                logger.info(
                                    f"@{username}: previously unfollowed - will NOT be re-followed. Skip."
                                )
                            else:
                                can_reinteract = storage.can_be_reinteract(
                                    interacted_when,
                                    get_value(self.args.can_reinteract_after, None, 0),
                                )
                                logger.info(
                                    f"@{username}: already interacted on {interacted_when:%Y/%m/%d %H:%M:%S}. {'Interacting again now' if can_reinteract else 'Skip'}."
                                )
                                if can_reinteract:
                                    can_interact = True
                        else:
                            can_interact = True

                    if can_interact:
                        logger.info(
                            f"@{username}: interact",
                            extra={"color": f"{Fore.YELLOW}"},
                        )
                        element_opened = username_view.click_retry()

                        if element_opened and not interact(
                            storage=storage,
                            is_follow_limit_reached=is_follow_limit_reached,
                            username=username,
                            interaction=interaction,
                            device=device,
                            session_state=session_state,
                            current_job=current_job,
                            target=target,
                            on_interaction=on_interaction,
                        ):
                            return
                    if element_opened:
                        opened = True
                        logger.info("Back to likers list.")
                        device.back()

            except IndexError:
                logger.info(
                    "Cannot get next item: probably reached end of the screen.",
                    extra={"color": f"{Fore.GREEN}"},
                )
                break
            go_back = False
            if screen_iterated_likers == prev_screen_iterated_likers:
                logger.info(
                    "Iterated exactly the same likers twice.",
                    extra={"color": f"{Fore.GREEN}"},
                )
                go_back = True
            if go_back:
                prev_screen_iterated_likers.clear()
                prev_screen_iterated_likers += screen_iterated_likers
                logger.info(
                    f"Back to {target}'s posts list.",
                    extra={"color": f"{Fore.GREEN}"},
                )
                device.back()
                logger.info("Going to the next post.")
                PostsViewList(device).swipe_to_fit_posts(SwipeTo.NEXT_POST)
                break
            if posts_end_detector.is_fling_limit_reached():
                logger.info(
                    "Reached fling limit. Fling to see other likers.",
                    extra={"color": f"{Fore.GREEN}"},
                )
                likes_list_view.fling(Direction.DOWN)
            else:
                logger.info(
                    "Scroll to see other likers.",
                    extra={"color": f"{Fore.GREEN}"},
                )
                likes_list_view.scroll(Direction.DOWN)

            prev_screen_iterated_likers.clear()
            prev_screen_iterated_likers += screen_iterated_likers
            if posts_end_detector.is_the_end():
                device.back()
                PostsViewList(device).swipe_to_fit_posts(SwipeTo.NEXT_POST)
                break
            if not opened:
                logger.info(
                    "All likers skipped.",
                    extra={"color": f"{Fore.GREEN}"},
                )
                posts_end_detector.notify_skipped_all()
                if posts_end_detector.is_skipped_limit_reached():
                    posts_end_detector.reset_skipped_all()
                    return


def handle_posts(
    self,
    device,
    session_state,
    target,
    current_job,
    storage,
    profile_filter,
    on_interaction,
    interaction,
    is_follow_limit_reached,
    interact_percentage,
    scraping_file,
):
    skipped_posts_limit = get_value(
        self.args.skipped_posts_limit,
        "Skipped post limit: {}",
        5,
    )
    if current_job == "feed":
        if scraping_file:
            logger.warning(
                "Scraping and interacting with own feed doesn't make any sense. Skip."
            )
            return
        nav_to_feed(device)
        count_feed_limit = get_value(
            self.args.feed,
            "Feed interact count: {}",
            10,
        )
        count = 0
        PostsViewList(device)._refresh_feed()
    elif not nav_to_hashtag_or_place(device, target, current_job):
        return

    post_description = ""
    likes_failed = 0
    nr_same_post = 0
    nr_same_posts_max = 3
    nr_consecutive_already_interacted = 0
    already_liked_count = 0
    already_liked_count_limit = 20
    post_view_list = PostsViewList(device)
    opened_post_view = OpenedPostView(device)
    # --- Random skip start on posts (hashtag/place). Disabled for feed. ---
    if current_job != "feed":
        skip_n = _get_scroll_skip_start(self.args, "posts")
        if skip_n > 0:
            logger.info(
                f"Skipping first {skip_n} posts of {target} (random start).",
                extra={"color": f"{Fore.CYAN}"},
            )
            actually_skipped = 0
            for i in range(skip_n):
                try:
                    post_view_list.swipe_to_fit_posts(SwipeTo.NEXT_POST)
                    random_sleep(0.4, 1.0, modulable=False)
                    actually_skipped += 1
                except Exception as e:
                    logger.debug(f"Skip-start (posts) interrupted: {e}")
                    break
            logger.info(
                f"Skipped {actually_skipped} posts, starting iteration here.",
                extra={"color": f"{Fore.CYAN}"},
            )
    while True:
        (
            is_same_post,
            post_description,
            username,
            is_ad,
            is_hashtag,
            has_tags,
        ) = post_view_list._check_if_last_post(post_description, current_job)
        has_likers, number_of_likers = post_view_list._find_likers_container()
        already_liked, _ = opened_post_view._is_post_liked()
        if not (is_ad or is_hashtag):
            if already_liked_count == already_liked_count_limit:
                logger.info(
                    f"Limit of {already_liked_count_limit} already liked posts limit reached, finish."
                )
                break
            if is_same_post:
                nr_same_post += 1
                logger.info(
                    f"Warning: {nr_same_post}/{nr_same_posts_max} repeated posts."
                )
                if nr_same_post == nr_same_posts_max:
                    logger.info(
                        f"Scrolled through {nr_same_posts_max} posts with same description and author. Finish."
                    )
                    break
            else:
                nr_same_post = 0
            if already_liked:
                logger.info(
                    "Post already liked, SKIP.", extra={"color": f"{Fore.CYAN}"}
                )
                already_liked_count += 1
            elif random_choice(interact_percentage):
                can_interact = False
                if storage.is_user_in_blacklist(username):
                    logger.info(f"@{username} is in blacklist. Skip.")
                else:
                    likes_in_range = profile_filter.is_num_likers_in_range(
                        number_of_likers
                    )
                    if current_job != "feed":
                        interacted, interacted_when = storage.check_user_was_interacted(
                            username
                        )
                        if interacted:
                            if storage.was_unfollowed_before(username):
                                logger.info(
                                    f"@{username}: previously unfollowed - will NOT be re-followed. Skip."
                                )
                                nr_consecutive_already_interacted += 1
                            else:
                                can_reinteract = storage.can_be_reinteract(
                                    interacted_when,
                                    get_value(self.args.can_reinteract_after, None, 0),
                                )
                                logger.info(
                                    f"@{username}: already interacted on {interacted_when:%Y/%m/%d %H:%M:%S}. {'Interacting again now' if can_reinteract else 'Skip'}."
                                )
                                if can_reinteract:
                                    can_interact = True
                                    nr_consecutive_already_interacted = 0
                                else:
                                    nr_consecutive_already_interacted += 1
                        else:
                            can_interact = True
                            nr_consecutive_already_interacted = 0
                    else:
                        can_interact = True

                if nr_consecutive_already_interacted == skipped_posts_limit:
                    logger.info(
                        f"Reached the limit of already interacted {skipped_posts_limit}. Going to the next source/job!"
                    )
                    break
                if can_interact and (likes_in_range or not has_likers):
                    logger.info(
                        f"@{username}: interact", extra={"color": f"{Fore.YELLOW}"}
                    )
                    if scraping_file is None:
                        opened_post_view.start_video()
                        if not session_state.check_limit(
                            limit_type=session_state.Limit.LIKES, output=True
                        ):
                            if has_tags:
                                post_view_list._like_in_post_view(LikeMode.SINGLE_CLICK)
                            else:
                                post_view_list._like_in_post_view(LikeMode.DOUBLE_CLICK)
                            UniversalActions.detect_block(device)
                            liked = post_view_list._check_if_liked()
                            if not liked:
                                post_view_list._like_in_post_view(
                                    LikeMode.SINGLE_CLICK, already_watched=True
                                )
                                UniversalActions.detect_block(device)
                                liked = post_view_list._check_if_liked()
                            if liked:
                                session_state.totalLikes += 1
                                if current_job == "feed":
                                    count += 1
                                    logger.info(
                                        f"Interacted feed bloggers: {count}/{count_feed_limit}"
                                    )
                                    likes_limit = self.session_state.check_limit(
                                        limit_type=self.session_state.Limit.LIKES
                                    )
                                    success_limit = self.session_state.check_limit(
                                        limit_type=self.session_state.Limit.SUCCESS
                                    )
                                    total_limit = self.session_state.check_limit(
                                        limit_type=self.session_state.Limit.TOTAL
                                    )
                                    if likes_limit or success_limit or total_limit:
                                        logger.info("Limit reached, finish.")
                                        break
                                    if count >= count_feed_limit:
                                        logger.info(
                                            f"Interacted {count} bloggers in feed, finish."
                                        )
                                        break
                            else:
                                likes_failed += 1
                    if current_job != "feed":
                        opened, _, _ = post_view_list._post_owner(
                            current_job, Owner.OPEN, username
                        )
                        if opened:
                            if not interact(
                                storage=storage,
                                is_follow_limit_reached=is_follow_limit_reached,
                                username=username,
                                interaction=interaction,
                                device=device,
                                session_state=session_state,
                                current_job=current_job,
                                target=target,
                                on_interaction=on_interaction,
                            ):
                                break
                            device.back()
            else:
                logger.info(
                    f"Skipped because your interact % is {interact_percentage}/100 and {username}'s post was unlucky!"
                )
        if likes_failed == 10:
            logger.warning("You failed to do 10 likes! Soft-ban?!")
            return
        post_view_list.swipe_to_fit_posts(SwipeTo.HALF_PHOTO)
        post_view_list.swipe_to_fit_posts(SwipeTo.NEXT_POST)
    TabBarView(device).navigateToProfile()


def handle_followers(
    self,
    device,
    session_state,
    username,
    current_job,
    storage,
    on_interaction,
    interaction,
    is_follow_limit_reached,
    scroll_end_detector,
    profile_filter=None,
):
    is_myself = username == session_state.my_username
    if not nav_to_blogger(device, username, current_job):
        return

    iterate_over_followers(
        self,
        device,
        interaction,
        is_follow_limit_reached,
        storage,
        on_interaction,
        is_myself,
        scroll_end_detector,
        session_state,
        current_job,
        username,
        profile_filter=profile_filter,
    )


def iterate_over_followers(
    self,
    device,
    interaction,
    is_follow_limit_reached,
    storage,
    on_interaction,
    is_myself,
    scroll_end_detector,
    session_state,
    current_job,
    target,
    profile_filter=None,
):
    device.find(
        resourceId=self.ResourceID.FOLLOW_LIST_CONTAINER,
        className=ClassName.LINEAR_LAYOUT,
    ).wait(Timeout.LONG)

    def scrolled_to_top():
        row_search = device.find(
            resourceId=self.ResourceID.ROW_SEARCH_EDIT_TEXT,
            className=ClassName.EDIT_TEXT,
        )
        return row_search.exists()

    # --- Resume + Skip start: avoid always analyzing the same users at top of list ---
    explored = getattr(storage, "explored_segments", None) if not is_myself else None
    resumed_from_anchor = False
    if explored is not None and _resume_enabled(self.args):
        try:
            cooldown = _resume_cooldown_days(self.args)
            if explored.should_resume(current_job, target, cooldown):
                anchors = explored.get_anchors_fallback(current_job, target)
                if anchors:
                    list_view = device.find(
                        resourceId=self.ResourceID.LIST,
                        className=ClassName.LIST_VIEW,
                    )
                    if list_view.exists():
                        max_scrolls = _resume_search_limit(self.args)
                        logger.info(
                            f"[resume] Looking for last anchor of @{target} (up to {max_scrolls} scrolls). Candidates: {anchors[:3]}{'...' if len(anchors)>3 else ''}",
                            extra={"color": f"{Fore.CYAN}"},
                        )
                        found, matched, scrolls_done = _seek_anchor_in_followers(
                            self, device, list_view, anchors, max_scrolls
                        )
                        if found:
                            logger.info(
                                f"[resume] Found anchor @{matched} after {scrolls_done} scrolls -> resuming from here.",
                                extra={"color": f"{Fore.CYAN}"},
                            )
                            explored.reset_anchor_misses(current_job, target)
                            resumed_from_anchor = True
                        else:
                            misses = explored.register_anchor_miss(
                                current_job, target
                            )
                            logger.info(
                                f"[resume] Anchor not found after {scrolls_done} scrolls (miss #{misses}). Falling back to scroll-skip-start.",
                                extra={"color": f"{Fore.CYAN}"},
                            )
            else:
                # should_resume() ha ritornato False. Due possibili cause:
                #   1) sorgente vergine (no anchor, no exhausted_at)
                #   2) sorgente esaurita ed entro il cooldown
                # Distinguiamo i due casi nel log per non confondere l'utente.
                _rec = explored._get_record(current_job, target) if hasattr(explored, "_get_record") else {}
                if _rec.get("exhausted_at"):
                    logger.info(
                        f"[resume] Source @{target} is in cooldown after exhaustion. Skipping resume.",
                        extra={"color": f"{Fore.CYAN}"},
                    )
                else:
                    logger.info(
                        f"[resume] Source @{target} has no anchor yet (virgin or reset). Falling back to scroll-skip-start.",
                        extra={"color": f"{Fore.CYAN}"},
                    )
        except Exception as e:
            logger.warning(f"[resume] Error while seeking anchor: {e}")

    if not is_myself and not resumed_from_anchor:
        skip_n = _get_scroll_skip_start(self.args, "followers")
        # Per sorgenti MAI viste prima (record vuoto), un random skip troppo
        # grande puo' portare oltre la fine della lista (rendering vuoto =
        # "no followers iterated"). Cap a 10 per il primo contatto.
        # Se la sorgente ha gia' fallito 1+ volte come "first visit empty",
        # azzera lo skip per partire dal TOP della lista.
        if skip_n > 0 and explored is not None:
            try:
                rec_iters = 0
                rec_anchor = None
                empty_visits = 0
                if hasattr(explored, "_get_record"):
                    _rec = explored._get_record(current_job, target)
                    rec_iters = int(_rec.get("total_iterations", 0))
                    rec_anchor = _rec.get("last_anchor")
                    empty_visits = int(
                        _rec.get("consecutive_empty_first_visits", 0)
                    )
                is_virgin = rec_iters == 0 and rec_anchor is None
                if is_virgin and empty_visits >= 1:
                    logger.info(
                        f"[skip-start] @{target} previously failed empty page "
                        f"x{empty_visits}: starting from TOP (skip=0).",
                        extra={"color": f"{Fore.CYAN}"},
                    )
                    skip_n = 0
                elif is_virgin and skip_n > 10:
                    capped = min(skip_n, 10)
                    logger.info(
                        f"[skip-start] First time on @{target}: capping skip "
                        f"{skip_n} -> {capped} to avoid overshooting list end.",
                        extra={"color": f"{Fore.CYAN}"},
                    )
                    skip_n = capped
            except Exception as e:
                logger.debug(f"first-time skip cap check failed: {e}")
        if skip_n > 0:
            list_view = device.find(
                resourceId=self.ResourceID.LIST, className=ClassName.LIST_VIEW
            )
            if list_view.exists():
                logger.info(
                    f"Skipping first {skip_n} followers of @{target} (random start).",
                    extra={"color": f"{Fore.CYAN}"},
                )
                actually_skipped = 0
                prev_first_username = None
                for i in range(skip_n):
                    try:
                        user_list = device.find(
                            resourceIdMatches=self.ResourceID.USER_LIST_CONTAINER,
                        )
                        first_username = None
                        try:
                            for it in user_list:
                                uname_view = it.child(index=1).child(index=0).child()
                                if uname_view.exists():
                                    first_username = uname_view.get_text()
                                    break
                        except Exception:
                            pass
                        list_view.scroll(Direction.DOWN)
                        random_sleep(0.3, 0.9, modulable=False)
                        if (
                            first_username is not None
                            and prev_first_username == first_username
                        ):
                            logger.info(
                                f"List didn't move while skipping (skipped {actually_skipped}/{skip_n}). Stop.",
                                extra={"color": f"{Fore.CYAN}"},
                            )
                            break
                        prev_first_username = first_username
                        actually_skipped += 1
                    except Exception as e:
                        logger.debug(f"Skip-start interrupted: {e}")
                        break
                logger.info(
                    f"Skipped {actually_skipped} followers, starting iteration here.",
                    extra={"color": f"{Fore.CYAN}"},
                )

    # --- Hot-zone state ---
    hz_screens_threshold, hz_flings_per_jump, hz_max_jumps = _hot_zone_params(
        self.args
    )
    hot_zone_enabled = (
        not is_myself
        and hz_screens_threshold > 0
        and hz_flings_per_jump > 0
    )
    consecutive_zero_fresh = 0
    jumps_done = 0

    while True:
        logger.info("Iterate over visible followers.")
        screen_iterated_followers = []
        screen_skipped_followers_count = 0
        screen_fresh_count = 0
        scroll_end_detector.notify_new_page()
        user_list = device.find(
            resourceIdMatches=self.ResourceID.USER_LIST_CONTAINER,
        )
        row_height, n_users = inspect_current_view(user_list)
        try:
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
                screen_iterated_followers.append(username)
                scroll_end_detector.notify_username_iterated(username)

                # --- Pre-flight username filter (no profile open) ---
                if profile_filter is not None:
                    pre_skip = profile_filter.pre_filter_username(username)
                    if pre_skip:
                        logger.info(
                            f"@{username} pre-filter skip: {pre_skip}.",
                            extra={"color": f"{Fore.CYAN}"},
                        )
                        screen_skipped_followers_count += 1
                        continue

                can_interact = False
                if storage.is_user_in_blacklist(username):
                    logger.info(f"@{username} is in blacklist. Skip.")
                else:
                    interacted, interacted_when = storage.check_user_was_interacted(
                        username
                    )
                    if interacted:
                        if storage.was_unfollowed_before(username):
                            logger.info(
                                f"@{username}: previously unfollowed - will NOT be re-followed. Skip."
                            )
                            screen_skipped_followers_count += 1
                        else:
                            can_reinteract = storage.can_be_reinteract(
                                interacted_when,
                                get_value(self.args.can_reinteract_after, None, 0),
                            )
                            logger.info(
                                f"@{username}: already interacted on {interacted_when:%Y/%m/%d %H:%M:%S}. {'Interacting again now' if can_reinteract else 'Skip'}."
                            )
                            if can_reinteract:
                                can_interact = True
                            else:
                                screen_skipped_followers_count += 1
                    else:
                        can_interact = True
                        screen_fresh_count += 1

                if can_interact:
                    logger.info(
                        f"@{username}: interact", extra={"color": f"{Fore.YELLOW}"}
                    )
                    element_opened = user_name_view.click_retry()

                    if element_opened:
                        if not interact(
                            storage=storage,
                            is_follow_limit_reached=is_follow_limit_reached,
                            username=username,
                            interaction=interaction,
                            device=device,
                            session_state=session_state,
                            current_job=current_job,
                            target=target,
                            on_interaction=on_interaction,
                        ):
                            return
                    if element_opened:
                        logger.info("Back to followers list")
                        device.back()

        except IndexError:
            logger.info(
                "Cannot get next item: probably reached end of the screen.",
                extra={"color": f"{Fore.GREEN}"},
            )

        # --- Hot-zone detector: troppi schermi consecutivi senza utenti FRESH ---
        if hot_zone_enabled and len(screen_iterated_followers) > 0:
            if screen_fresh_count == 0:
                consecutive_zero_fresh += 1
                logger.info(
                    f"[hot-zone] No fresh users on this screen ({consecutive_zero_fresh}/{hz_screens_threshold}).",
                    extra={"color": f"{Fore.CYAN}"},
                )
            else:
                if consecutive_zero_fresh > 0 or jumps_done > 0:
                    logger.info(
                        f"[hot-zone] Fresh users found ({screen_fresh_count}). Reset hot-zone counters.",
                        extra={"color": f"{Fore.CYAN}"},
                    )
                consecutive_zero_fresh = 0
                jumps_done = 0

            if consecutive_zero_fresh >= hz_screens_threshold:
                if jumps_done >= hz_max_jumps:
                    logger.info(
                        f"[hot-zone] Already attempted {jumps_done} jumps on @{target} without finding fresh users. Abandoning source.",
                        extra={"color": f"{Fore.CYAN}"},
                    )
                    if (
                        explored is not None
                        and _resume_enabled(self.args)
                        and len(screen_iterated_followers) > 0
                    ):
                        try:
                            explored.set_anchor(
                                current_job, target, screen_iterated_followers[-1]
                            )
                        except Exception as e:
                            logger.debug(f"set_anchor (hot-zone abandon) failed: {e}")
                    return
                jumps_done += 1
                logger.info(
                    f"[hot-zone] Hot zone detected: doing {hz_flings_per_jump} flings to escape (jump {jumps_done}/{hz_max_jumps}).",
                    extra={"color": f"{Fore.CYAN}"},
                )
                lv = device.find(
                    resourceId=self.ResourceID.LIST,
                    className=ClassName.LIST_VIEW,
                )
                if lv.exists():
                    for _f in range(hz_flings_per_jump):
                        try:
                            lv.fling(Direction.DOWN)
                            random_sleep(0.5, 1.0, modulable=False)
                        except Exception as e:
                            logger.debug(f"[hot-zone] fling interrupted: {e}")
                            break
                consecutive_zero_fresh = 0
                # Salta la logica scroll standard: ricomincia il while con la
                # nuova posizione di lista raggiunta dai fling.
                continue

        # Salva l'ultimo username della schermata come anchor (resume).
        if (
            not is_myself
            and explored is not None
            and _resume_enabled(self.args)
            and len(screen_iterated_followers) > 0
        ):
            try:
                explored.set_anchor(
                    current_job, target, screen_iterated_followers[-1]
                )
            except Exception as e:
                logger.debug(f"set_anchor failed: {e}")

        if is_myself and scrolled_to_top():
            logger.info("Scrolled to top, finish.", extra={"color": f"{Fore.GREEN}"})
            return
        elif len(screen_iterated_followers) > 0:
            load_more_button = device.find(
                resourceId=self.ResourceID.ROW_LOAD_MORE_BUTTON
            )
            load_more_button_exists = load_more_button.exists()

            if scroll_end_detector.is_the_end():
                if (
                    not is_myself
                    and explored is not None
                    and _resume_enabled(self.args)
                ):
                    try:
                        marked = explored.mark_exhausted(current_job, target)
                        if marked:
                            logger.info(
                                f"[resume] @{target} list exhausted -> marked. Will be skipped for cooldown.",
                                extra={"color": f"{Fore.CYAN}"},
                            )
                    except Exception as e:
                        logger.debug(f"mark_exhausted failed: {e}")
                return

            need_swipe = screen_skipped_followers_count == len(
                screen_iterated_followers
            )
            list_view = device.find(
                resourceId=self.ResourceID.LIST, className=ClassName.LIST_VIEW
            )
            if not list_view.exists():
                logger.error(
                    "Cannot find the list of followers. Trying to press back again."
                )
                device.back()
                list_view = device.find(
                    resourceId=self.ResourceID.LIST,
                    className=ClassName.LIST_VIEW,
                )

            if is_myself:
                logger.info("Need to scroll now", extra={"color": f"{Fore.GREEN}"})
                list_view.scroll(Direction.UP)
            else:
                pressed_retry = False
                if load_more_button_exists:
                    retry_button = load_more_button.child(
                        className=ClassName.IMAGE_VIEW,
                        descriptionMatches=case_insensitive_re("Retry"),
                    )
                    if retry_button.exists():
                        random_sleep()
                        """It exist but can disappear without pressing on it"""
                        if retry_button.exists():
                            logger.info('Press "Load" button and wait few seconds.')
                            retry_button.click_retry()
                            random_sleep(5, 10, modulable=False)
                            pressed_retry = True

                if need_swipe and not pressed_retry:
                    scroll_end_detector.notify_skipped_all()
                    if scroll_end_detector.is_skipped_limit_reached():
                        return
                    if scroll_end_detector.is_fling_limit_reached():
                        logger.info(
                            "Limit of all followers skipped reached, let's fling.",
                            extra={"color": f"{Fore.GREEN}"},
                        )
                        list_view.fling(Direction.DOWN)
                    else:
                        logger.info(
                            "All followers skipped, let's scroll.",
                            extra={"color": f"{Fore.GREEN}"},
                        )
                        list_view.scroll(Direction.DOWN)
                else:
                    logger.info("Need to scroll now", extra={"color": f"{Fore.GREEN}"})
                    list_view.scroll(Direction.DOWN)
        else:
            logger.info(
                "No followers were iterated, finish.",
                extra={"color": f"{Fore.GREEN}"},
            )
            if (
                not is_myself
                and explored is not None
                and _resume_enabled(self.args)
            ):
                try:
                    # Tracker per sorgenti vergini che continuano a fallire
                    # con empty page: dopo N fallimenti consecutivi, marcamo
                    # exhausted comunque (non esistono utenti scrollabili).
                    rec_iters = 0
                    if hasattr(explored, "_get_record"):
                        _rec = explored._get_record(current_job, target)
                        rec_iters = int(_rec.get("total_iterations", 0))
                    if rec_iters == 0 and hasattr(
                        explored, "mark_first_visit_empty"
                    ):
                        empty_count = explored.mark_first_visit_empty(
                            current_job, target
                        )
                        logger.info(
                            f"[empty-page] @{target} virgin source empty page "
                            f"#{empty_count}/3.",
                            extra={"color": f"{Fore.CYAN}"},
                        )
                        if empty_count >= 3:
                            # 3 fallimenti consecutivi su sorgente vergine =
                            # blogger fantasma (lista followers inaccessibile)
                            explored.mark_exhausted(
                                current_job, target, force=True
                            )
                            # mark_exhausted con iters==0 e' bloccato dal guard.
                            # Forziamo settando direttamente exhausted_at.
                            try:
                                _rec["exhausted_at"] = (
                                    __import__("datetime").datetime.now().isoformat(
                                        timespec="seconds"
                                    )
                                )
                                explored._flush()
                            except Exception:
                                pass
                            logger.info(
                                f"[empty-page] @{target} reached 3 empty "
                                f"failures: marked exhausted (ghost blogger).",
                                extra={"color": f"{Fore.CYAN}"},
                            )
                            return

                    marked = explored.mark_exhausted(
                        current_job, target, force=True
                    )
                    if marked:
                        logger.info(
                            f"[resume] @{target} reached empty page -> marked exhausted.",
                            extra={"color": f"{Fore.CYAN}"},
                        )
                    else:
                        logger.info(
                            f"[resume] @{target} empty page rejected by guard. "
                            f"Source preserved for next session.",
                            extra={"color": f"{Fore.CYAN}"},
                        )
                except Exception as e:
                    logger.debug(f"mark_exhausted failed: {e}")
            return

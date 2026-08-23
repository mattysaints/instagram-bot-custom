import logging
import os
import re
from argparse import Namespace
from datetime import datetime
from os import path
from random import choice, randint, shuffle, uniform
from time import sleep, time
from typing import Optional, Tuple, List

import emoji
import spintax
from colorama import Fore, Style

from GramAddict.core import storage
from GramAddict.core import ai_comment
from GramAddict.core.action_throttler import ActionType, get_throttler
from GramAddict.core.device_facade import (
    DeviceFacade,
    Location,
    Mode,
    SleepTime,
    Timeout,
)
from GramAddict.core.report import print_scrape_report, print_short_report
from GramAddict.core.resources import ClassName
from GramAddict.core.resources import ResourceID as resources
from GramAddict.core.session_state import SessionState
from GramAddict.core.utils import (
    append_to_file,
    get_value,
    random_choice,
    random_sleep,
    save_crash,
)
from GramAddict.core.views import (
    CurrentStoryView,
    Direction,
    MediaType,
    PostsGridView,
    ProfileView,
    UniversalActions,
    case_insensitive_re,
)

logger = logging.getLogger(__name__)

# "Question sticker. Cosa alleni oggi?. Double tap to reply."
_STICKER_DESC_RE = re.compile(
    r"^\s*Question sticker\.\s*(.*?)\.?\s*(?:Double tap to reply\.?)?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def load_config(config):
    global args
    global configs
    global ResourceID
    args = config.args
    configs = config
    ResourceID = resources(config.args.app_id)


def interact_with_user(
    device,
    username,
    my_username,
    likes_count,
    likes_percentage,
    stories_percentage,
    can_follow,
    follow_percentage,
    comment_percentage,
    pm_percentage,
    profile_filter,
    args,
    session_state,
    scraping_file,
    current_mode,
) -> Tuple[bool, bool, bool, bool, bool, int, int, int]:
    """
    :return: (whether interaction succeed, whether @username was followed during the interaction, if you scraped that account, if you sent a PM, number of liked, number of watched, number of commented)
    """
    number_of_liked = 0
    number_of_watched = 0
    number_of_commented = 0
    comment_done = interacted = followed = scraped = sent_pm = False
    logger.debug("Checking profile..")
    start_time = time()
    profile_data, skipped = profile_filter.check_profile(device, username)
    if username == my_username:
        logger.info("It's you, skip.")
        return (
            interacted,
            followed,
            profile_data.is_private,
            scraped,
            sent_pm,
            number_of_liked,
            number_of_watched,
            number_of_commented,
        )

    if skipped:
        delta = format(time() - start_time, ".2f")
        logger.debug(f"Profile checked in {delta}s")
        return (
            interacted,
            followed,
            profile_data.is_private,
            scraped,
            sent_pm,
            number_of_liked,
            number_of_watched,
            number_of_commented,
        )

    profile_view = ProfileView(device)
    delta = format(time() - start_time, ".2f")
    logger.debug(f"Profile checked in {delta}s")
    if profile_data.is_private or (profile_data.posts_count == 0):
        private_empty = "Private" if profile_data.is_private else "Empty"
        logger.info(f"{private_empty} account.")
        if (
            pm_percentage != 0
            and can_send_PM(session_state, pm_percentage)
            and profile_filter.can_pm_to_private_or_empty
        ):
            sent_pm = _send_PM(
                device, session_state, my_username, 0, profile_data.is_private
            )
            if sent_pm:
                interacted = True
        can_follow_private_or_empty = profile_filter.can_follow_private_or_empty()
        if can_follow and can_follow_private_or_empty:
            if scraping_file is None:
                followed = _follow(
                    device, username, follow_percentage, args, session_state, 0
                )
                if followed:
                    interacted = True
                return (
                    interacted,
                    followed,
                    profile_data.is_private,
                    scraped,
                    sent_pm,
                    number_of_liked,
                    number_of_watched,
                    number_of_commented,
                )
        else:
            if not can_follow_private_or_empty:
                logger.info(
                    "follow_private_or_empty is disabled in filters. Skip.",
                    extra={"color": f"{Fore.GREEN}"},
                )
            else:
                logger.info(
                    "Your follow-percentage is not 100%, not following this time. Skip.",
                    extra={"color": f"{Fore.GREEN}"},
                )
            return (
                interacted,
                followed,
                profile_data.is_private,
                scraped,
                sent_pm,
                number_of_liked,
                number_of_watched,
                number_of_commented,
            )

    # handle the scraping mode
    if scraping_file is not None:
        append_to_file(scraping_file, username)
        logger.info(
            f"Added @{username} at {scraping_file}",
            extra={"color": f"{Style.BRIGHT}{Fore.GREEN}"},
        )
        scraped = True
        return (
            interacted,
            followed,
            profile_data.is_private,
            scraped,
            sent_pm,
            number_of_liked,
            number_of_watched,
            number_of_commented,
        )

    # if not in scarping mode, we will interact
    number_of_watched = _watch_stories(
        device,
        profile_view,
        username,
        stories_percentage,
        args,
        session_state,
        skip_stickers=_blogger_comment_only(args, current_mode),
    )
    swipe_amount = 0

    if number_of_watched >= 1:
        interacted = True
    if can_like(session_state, likes_percentage):
        # rete di sicurezza: se le storie (o qualunque altra cosa) hanno
        # lasciato lo schermo altrove, qui si torna al profilo prima di
        # cercare la griglia dei post
        _back_to_profile(device)
        if profile_data.posts_count > 3:
            swipe_amount = ProfileView(device).swipe_to_fit_posts()
        else:
            logger.debug(
                f"We don't need to scroll, there is/are only {profile_data.posts_count} post(s)."
            )
        if swipe_amount == -1:
            return (
                interacted,
                followed,
                profile_data.is_private,
                scraped,
                sent_pm,
                number_of_liked,
                number_of_watched,
                number_of_commented,
            )

        likes_value = get_value(likes_count, "Likes count: {}", 2)
        (
            _,
            _,
            _,
            can_comment_job,
        ) = profile_filter.can_comment(current_mode)
        if can_comment_job and comment_percentage != 0:
            max_comments_pro_user = get_value(
                args.max_comments_pro_user, "Max comment count: {}", 1
            )
        if likes_value > 12:
            logger.error("Max number of likes per user is 12.")
            likes_value = 12

        start_time = time()
        full_rows, columns_last_row = profile_view.count_photo_in_view()
        end_time = format(time() - start_time, ".2f")
        photos_indices = list(range(full_rows * 3 + columns_last_row))

        if len(photos_indices) == profile_data.posts_count and len(photos_indices) > 1:
            del photos_indices[-1]
            logger.debug(
                "This is a temporary fix, for avoid bot to crash we have removed the last picture form the list."
            )

        logger.info(
            f"There {f'is {len(photos_indices)} post' if len(photos_indices)<=1 else f'are {len(photos_indices)} posts'} fully visible. Calculated in {end_time}s"
        )
        if current_mode in [
            "hashtag-posts-recent",
            "hashtag-posts-top",
            "place-posts-recent",
            "place-posts-top",
            "feed",
        ]:
            # in these jobs we did a like already at the post
            photos_indices = photos_indices[1:]
            # sometimes we liked not the last picture, have to introduce the already liked thing..

        if likes_value > len(photos_indices):
            logger.info(
                f"Only {len(photos_indices)} {'photo' if len(photos_indices)<=1 else 'photos'} available."
            )
        else:
            shuffle(photos_indices)
            photos_indices = photos_indices[:likes_value]
            photos_indices = sorted(photos_indices)
        post_grid_view = PostsGridView(device)
        comment_attempted = False
        for i in range(len(photos_indices)):
            # Job blogger in modalita' commento: i 3 post servono solo a
            # trovarne uno commentabile. Se il limite commenti della sessione
            # e' gia' raggiunto nessun post lo sara': si resta al primo post
            # (un like, come prima) invece di regalarne tre al big.
            if (
                i > 0
                and _blogger_comment_only(args, current_mode)
                and session_state.check_limit(
                    limit_type=session_state.Limit.COMMENTS, output=False
                )
            ):
                logger.info(
                    "Comment limit reached: not opening more posts of this big profile."
                )
                break
            photo_index = photos_indices[i]
            row = photo_index // 3
            column = photo_index - row * 3
            logger.info(f"Open post #{i + 1} ({row + 1} row, {column + 1} column).")
            opened_post_view, media_type, obj_count = post_grid_view.navigateToPost(
                row, column
            )

            like_succeed = False
            if opened_post_view is None:
                # navigateToPost ha gia' scritto perche': un post che non si
                # apre e' un post saltato, non un crash da mettere in uno zip.
                logger.info("Post non aperto: passo al successivo.")
                continue
            already_liked, _ = opened_post_view._is_post_liked()
            if already_liked:
                logger.info("Post already liked!")
            elif opened_post_view and already_liked is not None:
                if media_type in (MediaType.REEL, MediaType.IGTV, MediaType.VIDEO):
                    opened_post_view.start_video()
                    video_opened = opened_post_view.open_video()
                    if video_opened:
                        opened_post_view.watch_media(media_type)
                        get_throttler().wait_if_needed(ActionType.LIKE)
                        like_succeed = opened_post_view.like_video()
                        logger.debug("Closing video...")
                        device.back()
                    else:
                        # Niente schermo intero: il post e' comunque aperto
                        # nella vista feed, con il cuoricino sotto al media.
                        # Prima si rinunciava al like (e al conteggio
                        # dell'interazione) per un dettaglio di layout.
                        logger.info(
                            "Video not in full screen: liking from the post view."
                        )
                        opened_post_view.watch_media(media_type)
                        get_throttler().wait_if_needed(ActionType.LIKE)
                        like_succeed = opened_post_view.like_post(single_click=True)
                elif media_type in (MediaType.CAROUSEL, MediaType.PHOTO):
                    if media_type == MediaType.CAROUSEL:
                        _browse_carousel(device, obj_count)
                    opened_post_view.watch_media(media_type)
                    get_throttler().wait_if_needed(ActionType.LIKE)
                    # Per il carosello passiamo is_carousel=True cosi' like_post
                    # usa single-click sul cuoricino invece del doppio-tap (che
                    # dopo _browse_carousel rischia di cambiare slide).
                    like_succeed = opened_post_view.like_post(
                        is_carousel=(media_type == MediaType.CAROUSEL)
                    )
                if like_succeed:
                    register_like(device, session_state)
                    number_of_liked += 1
                else:
                    logger.warning("Fail to like post. Let's continue...")
                if comment_percentage != 0 and can_comment(
                    media_type, profile_filter, current_mode
                ):
                    if number_of_commented < max_comments_pro_user:
                        comment_done = _comment(
                            device,
                            my_username,
                            comment_percentage,
                            args,
                            session_state,
                            media_type,
                            target_username=username,
                        )
                        if comment_done:
                            number_of_commented += 1
                        elif comment_done is None:
                            comment_attempted = True
                    else:
                        logger.info(
                            f"You've already did {max_comments_pro_user} {'comment' if max_comments_pro_user<=1 else 'comments'} for this user!"
                        )
            else:
                logger.warning("Can't find the post element!")
                save_crash(device)
            if like_succeed or comment_done:
                interacted = True

            if not opened_post_view or (not like_succeed and not already_liked):
                reason = "open" if not opened_post_view else "like"
                logger.info(
                    f"Could not {reason} media. Posts count: {profile_data.posts_count}."
                )
            logger.info("Back to profile.")
            while not post_grid_view._get_post_view().exists():
                logger.debug("We are in the wrong place...")
                device.back()
            device.back()
            # Job blogger in modalita' commento: il like serve solo ad aprire
            # il post, quello che conta e' il commento. Se e' andato, basta
            # cosi'; se il post aveva i commenti limitati (virginactiveit,
            # 22/08) si prova il successivo invece di chiudere a zero.
            if _blogger_comment_only(args, current_mode) and (
                number_of_commented >= 1 or comment_attempted
            ):
                logger.info(
                    "Comment done: one post is enough on a big profile."
                    if number_of_commented >= 1
                    else "Comment posted but not verified: not risking a second one on this profile."
                )
                break

    if pm_percentage != 0 and can_send_PM(session_state, pm_percentage):
        sent_pm = _send_PM(device, session_state, my_username, swipe_amount)
        swipe_amount = 0
        if sent_pm:
            interacted = True
    if can_follow:
        # "Warm follow" gate: a follow preceded by real engagement (likes,
        # comments, watched stories, PM) converts to a follow-back far better
        # than a cold follow. When follow_only_if_engaged is enabled we skip the
        # follow for users we didn't actually engage with this interaction.
        # Note: this gate applies only to the normal (public, non-empty) path;
        # private/empty accounts are handled earlier and governed by the
        # follow_private_or_empty filter, since engaging them is impossible.
        do_follow = True
        if getattr(args, "follow_only_if_engaged", False):
            engagement = (
                number_of_liked
                + number_of_commented
                + number_of_watched
                + (1 if sent_pm else 0)
            )
            min_engagement = get_value(
                getattr(args, "follow_min_engagement", "1"), None, 1
            )
            if engagement < min_engagement:
                do_follow = False
                logger.info(
                    f"Skip follow for @{username}: engagement {engagement} < "
                    f"required {min_engagement} (follow_only_if_engaged is on).",
                    extra={"color": f"{Fore.CYAN}"},
                )
        if do_follow:
            followed = _follow(
                device,
                username,
                follow_percentage,
                args,
                session_state,
                swipe_amount,
            )
            if followed:
                interacted = True

    return (
        interacted,
        followed,
        profile_data.is_private,
        scraped,
        sent_pm,
        number_of_liked,
        number_of_watched,
        number_of_commented,
    )


def can_send_PM(session_state: SessionState, pm_percentage: int) -> bool:
    pm_chance = randint(1, 100)
    return not session_state.check_limit(
        limit_type=session_state.Limit.PM, output=True
    ) and (pm_chance <= pm_percentage)


def can_like(session_state: SessionState, likes_percentage: int) -> bool:
    likes_chance = randint(1, 100)
    return not session_state.check_limit(
        limit_type=session_state.Limit.LIKES, output=True
    ) and (likes_chance <= likes_percentage)


def can_comment(media_type: MediaType, profile_filter, current_mode) -> bool:
    (
        can_comment_photos,
        can_comment_videos,
        can_comment_carousels,
        can_comment_job,
    ) = profile_filter.can_comment(current_mode)
    if can_comment_job:
        if media_type == MediaType.PHOTO and can_comment_photos:
            return True
        elif (
            media_type in (MediaType.VIDEO, MediaType.IGTV, MediaType.REEL)
            and can_comment_videos
        ):
            return True
        elif media_type == MediaType.CAROUSEL and can_comment_carousels:
            return True
    logger.warning(
        f"Can't comment this {media_type} because filters are: can_comment_photos = {can_comment_photos}, can_comment_videos = {can_comment_videos}, can_comment_carousels = {can_comment_carousels}, can_comment_{current_mode} = {can_comment_job}. Check your filters.yml."
    )
    return False


def register_like(device, session_state):
    UniversalActions.detect_block(device)
    logger.debug("Like succeed.")
    session_state.totalLikes += 1


def is_follow_limit_reached_for_source(session_state, follow_limit, source):
    if follow_limit is None:
        return False

    followed_count = session_state.totalFollowed.get(source)
    return followed_count is not None and followed_count >= follow_limit


def _on_interaction(
    source,
    succeed,
    followed,
    scraped,
    interactions_limit,
    likes_limit,
    sessions,
    session_state,
    args,
):
    session_state = sessions[-1]
    session_state.add_interaction(source, succeed, followed, scraped)

    can_continue = True

    inside_working_hours, _ = SessionState.inside_working_hours(
        args.working_hours, args.time_delta_session
    )
    if not inside_working_hours:
        can_continue = False
    else:
        successful_interactions_count = session_state.successfulInteractions.get(source)
        if (
            successful_interactions_count
            and successful_interactions_count >= interactions_limit
        ):
            logger.info(
                "Reached interaction limit for that source, going to the next one..",
                extra={"color": f"{Fore.CYAN}"},
            )
            can_continue = False

        if args.scrape_to_file is not None:
            if session_state.check_limit(
                limit_type=session_state.Limit.SCRAPED, output=True
            ):
                logger.info(
                    "Reached scraped limit, finish.", extra={"color": f"{Fore.CYAN}"}
                )
                can_continue = False
        else:
            if (
                session_state.check_limit(
                    limit_type=session_state.Limit.LIKES, output=False
                )
                and args.end_if_likes_limit_reached
            ):
                logger.info(
                    "Reached liked limit, finish.", extra={"color": f"{Fore.CYAN}"}
                )
                can_continue = False

            if (
                session_state.check_limit(
                    limit_type=session_state.Limit.FOLLOWS, output=False
                )
                and args.end_if_follows_limit_reached
            ):
                logger.info(
                    "Reached followed limit, finish.", extra={"color": f"{Fore.CYAN}"}
                )
                can_continue = False

            if (
                session_state.check_limit(
                    limit_type=session_state.Limit.WATCHES, output=False
                )
                and args.end_if_watches_limit_reached
            ):
                logger.info(
                    "Reached watched limit, finish.", extra={"color": f"{Fore.CYAN}"}
                )
                can_continue = False

            if (
                session_state.check_limit(
                    limit_type=session_state.Limit.PM, output=False
                )
                and args.end_if_pm_limit_reached
            ):
                logger.info(
                    "Reached pm limit, finish.", extra={"color": f"{Fore.CYAN}"}
                )
                can_continue = False

            if (
                session_state.check_limit(
                    limit_type=session_state.Limit.COMMENTS, output=False
                )
                and args.end_if_comments_limit_reached
            ):
                logger.info(
                    "Reached comments limit, finish.", extra={"color": f"{Fore.CYAN}"}
                )
                can_continue = False

            if session_state.check_limit(
                limit_type=session_state.Limit.TOTAL, output=False
            ):
                logger.info(
                    "Reached total interaction limit, finish.",
                    extra={"color": f"{Fore.CYAN}"},
                )
                can_continue = False
            if session_state.check_limit(
                limit_type=session_state.Limit.SUCCESS, output=False
            ):
                logger.info(
                    "Reached total successfully interaction limit, finish.",
                    extra={"color": f"{Fore.CYAN}"},
                )
                can_continue = False

    if (can_continue and succeed) or scraped:
        if scraped:
            print_scrape_report(source, session_state)
        else:
            print_short_report(source, session_state)

    return can_continue


def _browse_carousel(device: DeviceFacade, obj_count: int) -> None:
    carousel_percentage = get_value(configs.args.carousel_percentage, None, 0)
    carousel_count = get_value(configs.args.carousel_count, None, 1)
    if carousel_percentage > randint(0, 100) and carousel_count > 1:
        media_obj = device.find(resourceIdMatches=ResourceID.CAROUSEL_MEDIA_GROUP)
        logger.info("Watching photos/videos in carousel.")
        if obj_count < carousel_count:
            logger.info(f"There are only {obj_count} media(s) in this carousel!")
            carousel_count = obj_count
        if media_obj.exists():
            media_obj_bounds = media_obj.get_bounds()
            n = 1
            while n < carousel_count:
                if media_obj.child(
                    resourceIdMatches=ResourceID.CAROUSEL_IMAGE_MEDIA_GROUP
                ).exists():
                    watch_photo_time = get_value(
                        configs.args.watch_photo_time,
                        "Watching photo for {}s.",
                        0,
                        its_time=True,
                    )
                    sleep(watch_photo_time)
                elif media_obj.child(
                    resourceIdMatches=ResourceID.CAROUSEL_VIDEO_MEDIA_GROUP
                ).exists():
                    watch_video_time = get_value(
                        configs.args.watch_video_time,
                        "Watching video for {}s.",
                        0,
                        its_time=True,
                    )
                    sleep(watch_video_time)
                start_point_y = (
                    (media_obj_bounds["bottom"] + media_obj_bounds["top"])
                    / 2
                    * uniform(0.85, 1.15)
                )
                start_point_x = uniform(0.85, 1.10) * (
                    media_obj_bounds["right"] * 5 / 6
                )
                delta_x = media_obj_bounds["right"] * uniform(0.5, 0.7)
                UniversalActions(device)._swipe_points(
                    start_point_y=start_point_y,
                    start_point_x=start_point_x,
                    delta_x=delta_x,
                    direction=Direction.LEFT,
                )
                n += 1


def _is_true(valore) -> bool:
    if isinstance(valore, bool):
        return valore
    return str(valore).strip().lower() in ("1", "true", "yes", "y", "on")


def _blogger_comment_only(args, current_mode) -> bool:
    """True se siamo nel job blogger in modalita' "solo commento" (default)."""
    return current_mode == "blogger" and _is_true(
        getattr(args, "blogger_comment_only", "true")
    )


def _clean_caption(testo: str) -> str:
    """Ripulisce la didascalia letta dal post prima di mandarla allo Space.

    Instagram tronca le didascalie lunghe e chiude con "… more" (o "… altro"
    con il telefono in italiano). Quel marcatore finiva nel prompt e il
    modello lo prendeva per contenuto: su jayde_lifts ha scritto "That
    'more' at the end says it all". Via il marcatore, via i puntini di
    troncamento, spazi normalizzati.
    """
    t = (testo or "").strip()
    t = re.sub(r"(\s*(\.{3}|…)\s*)?(?<!\w)(more|altro|leggi tutto|read more)\s*$", "", t, flags=re.IGNORECASE)
    # Via le menzioni: il modello le ripete, e un commento che nomina (o
    # peggio tagga) un terzo account su un post altrui e' esattamente cio'
    # che fa sembrare spam un profilo. Visto due volte il 23/08:
    # "...costanza del percorso con @sustainablebb" e "quella vista da
    # melijaz_pereira369...". La menzione non aggiunge niente al senso.
    t = re.sub(r"@[A-Za-z0-9._]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _ripulisci_commento(testo: str) -> str:
    """Ultimo controllo sul commento prima di scriverlo. Se contiene la
    menzione di un altro account lo si scarta e basta: toglierla lascerebbe
    una frase monca ("...la costanza del percorso con"), che e' peggio del
    commento di riserva. Chi chiama, davanti a una stringa vuota, usa
    comments_list.txt."""
    t = (testo or "").strip()
    if "@" not in t:
        return t
    logger.info(
        f"[ai-comment] scartato, menziona un altro account: '{t}'. "
        "Uso il commento di riserva."
    )
    return ""


_FOLLOW_DAL_DUMP = (
    ("followback", r"(?i)^follow back$"),
    ("following", r"(?i)^(following|requested)$"),
    ("follow", r"(?i)^follow$"),
)


def _bottone_follow_dal_dump(device: DeviceFacade):
    """Cerca nell'albero completo il bottone Follow / Following / Follow Back.
    Restituisce (nodo, stato): stato dice cosa si e' trovato, il nodo serve
    solo quando c'e' da toccare 'Follow'."""
    nodi = device.nodes_from_dump()
    for stato, regex in _FOLLOW_DAL_DUMP:
        nodo = DeviceFacade.node_in_dump(nodi, text_regex=regex)
        if nodo is not None and nodo.get("clickable"):
            return nodo, stato
    return None, None


def _bounds_o_dump(device: DeviceFacade, resource_id: str):
    """Bounds di un elemento, senza far esplodere niente se non c'e':
    prima il selettore, poi l'albero completo, infine None."""
    try:
        vista = device.find(resourceIdMatches=resource_id)
        if vista.exists():
            return vista.get_bounds()
    except DeviceFacade.JsonRpcError as e:
        logger.debug(f"_bounds_o_dump({resource_id}): selettore fallito: {e}")
    return device.bounds_from_dump(resource_id)


def _comment(
    device: DeviceFacade,
    my_username: str,
    comment_percentage: int,
    args,
    session_state: SessionState,
    media_type: MediaType,
    target_username: Optional[str] = None,
) -> Optional[bool]:
    if not session_state.check_limit(
        limit_type=session_state.Limit.COMMENTS, output=False
    ):
        if not random_choice(comment_percentage):
            return False
        universal_actions = UniversalActions(device)
        # we have to do a little swipe for preventing get the previous post comments button (which is covered by top bar, but present in hierarchy!!)
        universal_actions._swipe_points(
            direction=Direction.DOWN, delta_y=randint(150, 250)
        )
        # get_bounds() su un elemento che il selettore non trova ALZA
        # un'eccezione: qui arrivava fino a bot_flow, che salvava un crash e
        # ricominciava la sorgente (personale, 23/08 11:23). Serve solo a
        # decidere se fare uno swipe in piu': se le misure non si leggono,
        # si tira dritto.
        tab_bar_b = _bounds_o_dump(device, ResourceID.TAB_BAR)
        media_b = _bounds_o_dump(device, ResourceID.MEDIA_CONTAINER)
        if (
            tab_bar_b is not None
            and media_b is not None
            and int(tab_bar_b["top"]) - int(media_b["bottom"]) < 150
        ):
            universal_actions._swipe_points(
                direction=Direction.DOWN, delta_y=randint(150, 250)
            )
        # look at hashtag of comment
        for _ in range(2):
            comment_button = device.find(
                resourceId=ResourceID.ROW_FEED_BUTTON_COMMENT,
            )
            if comment_button.exists():
                # Best-effort: estrai la caption del post PRIMA di aprire la
                # comment thread. Una volta dentro la comment view la caption
                # in cima e' una riga "ROW_COMMENT_*", non piu' la
                # ROW_FEED_COMMENT_TEXTVIEW_LAYOUT del feed/post-view, e
                # rischieremmo di confonderla con un commento di altri.
                # Tutto try/except: se la caption non c'e' (post senza
                # didascalia, o resourceId cambiato in nuove versioni IG),
                # passiamo stringa vuota e l'AI commentera' "sul media".
                post_caption = ""
                if ai_comment.is_enabled(args):
                    try:
                        caption_view = device.find(
                            resourceId=ResourceID.ROW_FEED_COMMENT_TEXTVIEW_LAYOUT,
                        )
                        if caption_view.exists():
                            cap_text = caption_view.get_text() or ""
                            # In IG la caption e' tipicamente "username  caption-text"
                            # se l'username e' all'inizio, lo strippiamo.
                            if target_username and cap_text.lower().startswith(
                                target_username.lower()
                            ):
                                cap_text = cap_text[len(target_username):].strip()
                            post_caption = _clean_caption(cap_text)
                            if post_caption:
                                logger.info(
                                    f"[ai-comment] caption captured ({len(post_caption)} chars): "
                                    f"{post_caption[:60]}{'...' if len(post_caption) > 60 else ''}"
                                )
                    except Exception as e:
                        logger.debug(f"[ai-comment] caption extraction skipped: {e}")

                logger.info("Open comments of post.")
                # Re-check existence: between the first check and the click
                # the button may have scrolled off screen or IG refreshed the
                # post view, causing a JsonRpcError on a stale selector.
                if not comment_button.exists():
                    logger.info(
                        "Comment button disappeared before click, skipping comment.",
                    )
                    break
                comment_button.click()
                comment_box = device.find(
                    resourceId=ResourceID.LAYOUT_COMMENT_THREAD_EDITTEXT,
                    enabled="true",
                )
                if comment_box.exists():
                    # 1) prova AI; 2) se None, fallback al txt (a meno che
                    #    l'utente abbia esplicitamente disabilitato il
                    #    fallback, nel qual caso saltiamo il commento).
                    comment = None
                    if ai_comment.is_enabled(args):
                        comment = ai_comment.generate_comment(
                            args=args,
                            caption=post_caption,
                            target_username=target_username,
                            media_type=getattr(media_type, "name", str(media_type)),
                        )
                        comment = _ripulisci_commento(comment)
                        if comment:
                            logger.info(
                                f"[ai-comment] generated: '{comment}'",
                                extra={"color": f"{Fore.MAGENTA}"},
                            )
                        else:
                            logger.info(
                                "[ai-comment] generation failed/blocked; falling back to comments_list.txt"
                            )
                    if comment is None:
                        # rispetta l'opt-out hard del fallback al file
                        if (
                            ai_comment.is_enabled(args)
                            and getattr(args, "ai_comments_fallback_to_file", True) is False
                        ):
                            logger.info(
                                "[ai-comment] fallback-to-file disabled; skipping comment."
                            )
                            UniversalActions.close_keyboard(device)
                            device.back()
                            return False
                        comment = load_random_comment(my_username, media_type)
                    if comment is None:
                        UniversalActions.close_keyboard(device)
                        device.back()
                        return False
                    logger.info(
                        f"Write comment: {comment}", extra={"color": f"{Fore.CYAN}"}
                    )
                    comment_box.set_text(
                        comment, Mode.PASTE if args.dont_type else Mode.TYPE
                    )

                    post_button = device.find(
                        resourceId=ResourceID.LAYOUT_COMMENT_THREAD_POST_BUTTON_CLICK_AREA
                    )
                    get_throttler().wait_if_needed(ActionType.COMMENT)
                    post_button.click()
                else:
                    logger.info("Comments on this post have been limited.")
                    universal_actions.close_keyboard(device)
                    device.back()
                    return False

                universal_actions.detect_block(device)
                universal_actions.close_keyboard(device)
                # Verifica multi-strategia: dopo il click su "Post", IG nelle
                # versioni 300+ a volte non espone piu' un singolo node text
                # con "username comment" tutto attaccato (lo splittano in 2
                # TextView separati). La vecchia verify dava sempre
                # "Failed to check if comment succeed" -> totalComments mai
                # incrementato -> daily-cap aggirato (rischio ban).
                #
                # Strategie in cascata (la prima che matcha conferma):
                #  A) match esatto "{username} {comment}"  (vecchio path)
                #  B) match per username + first 30 chars del commento
                #  C) match della comment_box: se DOPO il click la textbox
                #     e' SVUOTATA e di nuovo enabled, quasi sempre IG ha
                #     accettato il commento (in caso di errore IG mantiene
                #     il testo per permettere retry).
                comment_confirmed = False
                # ---- A: full text match ----
                posted_text = device.find(text=f"{my_username} {comment}")
                when_posted = posted_text.sibling(
                    resourceId=ResourceID.ROW_COMMENT_SUB_ITEMS_BAR
                ).child(resourceId=ResourceID.ROW_COMMENT_TEXTVIEW_TIME_AGO)
                if posted_text.exists(Timeout.SHORT) and when_posted.exists(
                    Timeout.SHORT
                ):
                    logger.info(
                        "Comment succeed (verify A: full match).",
                        extra={"color": f"{Fore.GREEN}"},
                    )
                    comment_confirmed = True

                # ---- B: prefix match (commenti lunghi vengono troncati) ----
                if not comment_confirmed:
                    snippet = comment[:30].strip()
                    # contains-match su un nodo che contiene lo snippet
                    snippet_node = device.find(textContains=snippet)
                    if snippet_node.exists(Timeout.SHORT):
                        logger.info(
                            f"Comment succeed (verify B: snippet '{snippet[:20]}...' found).",
                            extra={"color": f"{Fore.GREEN}"},
                        )
                        comment_confirmed = True

                # ---- C: textbox cleared (heuristic) ----
                if not comment_confirmed:
                    cleared_box = device.find(
                        resourceId=ResourceID.LAYOUT_COMMENT_THREAD_EDITTEXT,
                        enabled="true",
                    )
                    if cleared_box.exists(Timeout.SHORT):
                        try:
                            current_txt = cleared_box.get_text(error=False) or ""
                        except Exception:
                            current_txt = ""
                        # IG svuota la textbox SOLO quando il commento e' stato
                        # accettato. Se contiene ancora il nostro testo o un
                        # placeholder ("Add a comment..."), non e' confermato.
                        if not current_txt or current_txt.strip().lower() in (
                            "",
                            "add a comment...",
                            "aggiungi un commento...",
                            "comment...",
                        ):
                            logger.info(
                                "Comment succeed (verify C: textbox cleared).",
                                extra={"color": f"{Fore.GREEN}"},
                            )
                            comment_confirmed = True

                if comment_confirmed:
                    session_state.totalComments += 1
                else:
                    logger.warning(
                        "Failed to check if comment succeed (all 3 verify strategies failed)."
                    )

                logger.info("Go back to post view.")
                device.back()
                # None = "Post" premuto ma commento non verificato: per le
                # statistiche non conta, ma chi chiama NON deve commentare un
                # altro post dello stesso profilo (il commento potrebbe
                # esserci eccome).
                return True if comment_confirmed else None
            else:
                like_button = device.find(
                    resourceId=ResourceID.ROW_FEED_BUTTON_LIKE,
                )
                if like_button.exists():
                    logger.info("This post has comments disabled.")
                    return False
                universal_actions._swipe_points(
                    direction=Direction.DOWN, delta_y=randint(150, 250)
                )
    return False


def _send_PM(
    device,
    session_state: SessionState,
    my_username: str,
    swipe_amount: int,
    private: bool = False,
) -> bool:
    universal_actions = UniversalActions(device)
    if private:
        options = device.find(
            classNameMatches=ClassName.FRAME_LAYOUT,
            descriptionMatches=case_insensitive_re("^Options$"),
        )
        if options.exists(Timeout.SHORT):
            options.click()
        else:
            return False
        send_pm = device.find(
            classNameMatches=ClassName.BUTTON,
            textMatches=case_insensitive_re("^Send Message$"),
        )
        if send_pm.exists(Timeout.SHORT):
            send_pm.click()
        else:
            return False
    else:
        coordinator_layout = device.find(resourceId=ResourceID.COORDINATOR_ROOT_LAYOUT)
        if coordinator_layout.exists() and swipe_amount != 0:
            universal_actions._swipe_points(
                direction=Direction.UP, delta_y=swipe_amount
            )
        message_button = device.find(
            classNameMatches=ClassName.BUTTON_OR_TEXTVIEW_REGEX,
            enabled=True,
            textMatches="Message",
        )
        if message_button.exists(Timeout.SHORT):
            message_button.click()
        else:
            logger.warning("Cannot find the button for sending PMs!")
            return False
    message_box = device.find(
        resourceId=ResourceID.ROW_THREAD_COMPOSER_EDITTEXT,
        className=ClassName.EDIT_TEXT,
        enabled="true",
    )

    if message_box.exists():
        message = load_random_message(my_username)
        if message is None:
            logger.warning(
                "If you don't want to comment set 'pm-percentage: 0' in your config.yml."
            )
            device.back()
            return False
        nl = "\n"
        nlv = "\\n"
        logger.info(
            f"Write private message: {message.replace(nl, nlv)}",
            extra={"color": f"{Fore.CYAN}"},
        )
        message_box.set_text(message, Mode.PASTE if args.dont_type else Mode.TYPE)
        send_button = device.find(
            resourceId=ResourceID.ROW_THREAD_COMPOSER_BUTTON_SEND,
        )
        if send_button.exists():
            get_throttler().wait_if_needed(ActionType.PM)
            send_button.click()
            universal_actions.detect_block(device)
            universal_actions.close_keyboard(device)
            posted_text = device.find(text=f"{message}")
            message_sending_icon = device.find(
                resourceId=ResourceID.ACTION_ICON, className=ClassName.IMAGE_VIEW
            )
            if message_sending_icon.exists():
                random_sleep()
            if posted_text.exists(Timeout.MEDIUM) and not message_sending_icon.exists():
                logger.info("PM send succeed.", extra={"color": f"{Fore.GREEN}"})
                session_state.totalPm += 1
                pm_confirmed = True
            else:
                logger.warning("Failed to check if PM send succeed.")
                pm_confirmed = False
            logger.info("Go back to profile view.")
            device.back()
            return pm_confirmed
        else:
            logger.warning("Can't find SEND button!")
            universal_actions.close_keyboard(device)
            device.back()
            return False
    else:
        logger.info("PM to this user have been limited.")
        universal_actions.close_keyboard(device)
        device.back()
        return False


def _load_and_clean_txt_file(
    my_username: str, txt_filename: str
) -> Optional[List[str]]:
    def nonblank_lines(f):
        for ln in f:
            line = ln.rstrip()
            if line:
                yield line

    lines = []
    file_name = os.path.join(storage.ACCOUNTS, my_username, txt_filename)
    if path.isfile(file_name):
        try:
            with open(file_name, "r", encoding="utf-8") as f:
                for line in nonblank_lines(f):
                    lines.append(line)
                if lines:
                    return lines
                logger.warning(f"{file_name} is empty! Check your account folder.")
                return None
        except Exception as e:
            logger.error(f"Error: {e}.")
            return None
    logger.warning(f"{file_name} not found! Check your account folder.")
    return None


def load_random_message(my_username: str) -> Optional[str]:
    lines = _load_and_clean_txt_file(my_username, storage.FILENAME_MESSAGES)
    if lines is not None:
        random_message = choice(lines)
        return emoji.emojize(
            spintax.spin(random_message.replace("\\n", "\n")),
            use_aliases=True,
        )
    return None


def load_random_comment(my_username: str, media_type: MediaType) -> Optional[str]:
    lines = _load_and_clean_txt_file(my_username, storage.FILENAME_COMMENTS)
    if lines is None:
        return None
    try:
        photo_header = lines.index("%PHOTO")
        video_header = lines.index("%VIDEO")
        carousel_header = lines.index("%CAROUSEL")
    except ValueError:
        logger.warning(
            f"You didn't follow the rules for sections in your {storage.FILENAME_COMMENTS} txt file! Look at config example."
        )
        return None
    photo_comments = lines[photo_header + 1 : video_header]
    video_comments = lines[video_header + 1 : carousel_header]
    carousel_comments = lines[carousel_header + 1 :]
    random_comment = ""
    if media_type == MediaType.PHOTO:
        random_comment = choice(photo_comments) if len(photo_comments) > 0 else ""
    elif media_type in (MediaType.VIDEO, MediaType.IGTV, MediaType.REEL):
        random_comment = choice(video_comments) if len(video_comments) > 0 else ""
    elif media_type == MediaType.CAROUSEL:
        random_comment = choice(carousel_comments) if len(carousel_comments) > 0 else ""
    if random_comment != "":
        return emoji.emojize(spintax.spin(random_comment), use_aliases=True)
    else:
        return None


def _follow(device, username, follow_percentage, args, session_state, swipe_amount):
    if not session_state.check_limit(
        limit_type=session_state.Limit.FOLLOWS, output=False
    ):
        # Hard safety: never re-follow a user we already unfollowed before
        # (either by the bot or reconciled from a manual unfollow). This is
        # a last-line-of-defense check; handle_sources should normally skip
        # these users much earlier in the pipeline.
        storage = getattr(session_state, "storage", None)
        if storage is not None and storage.was_unfollowed_before(username):
            logger.info(
                f"@{username}: previously unfollowed - refusing to follow again. Skip.",
                extra={"color": f"{Fore.YELLOW}"},
            )
            return False
        follow_chance = randint(1, 100)
        if follow_chance > follow_percentage:
            return False
        universal_actions = UniversalActions(device)
        coordinator_layout = device.find(resourceId=ResourceID.COORDINATOR_ROOT_LAYOUT)
        if coordinator_layout.exists(Timeout.MEDIUM) and swipe_amount != 0:
            universal_actions._swipe_points(
                direction=Direction.UP, delta_y=swipe_amount
            )

        FOLLOW_REGEX = "^Follow$"
        follow_button = device.find(
            clickable=True,
            textMatches=case_insensitive_re(FOLLOW_REGEX),
        )
        UNFOLLOW_REGEX = "^Following|^Requested"
        unfollow_button = device.find(
            clickable=True,
            textMatches=case_insensitive_re(UNFOLLOW_REGEX),
        )
        FOLLOWBACK_REGEX = "^Follow Back$"
        followback_button = device.find(
            clickable=True,
            textMatches=case_insensitive_re(FOLLOWBACK_REGEX),
        )

        if followback_button.exists():
            logger.info(
                f"@{username} already follows you.",
                extra={"color": f"{Fore.GREEN}"},
            )
            return False
        elif unfollow_button.exists():
            logger.info(
                f"You already follow @{username}.", extra={"color": f"{Fore.GREEN}"}
            )
            return False
        elif follow_button.exists():
            max_tries = 3
            for n in range(max_tries):
                if n == 0:
                    get_throttler().wait_if_needed(ActionType.FOLLOW)
                follow_button.click()
                if device.find(
                    textMatches=UNFOLLOW_REGEX,
                    clickable=True,
                ).exists(Timeout.SHORT):
                    logger.info(f"Followed @{username}", extra={"color": Fore.GREEN})
                    universal_actions.detect_block(device)
                    return True
                else:
                    if n < max_tries - 1:
                        logger.debug(
                            "Looks like the click on the button didn't work, try again."
                        )
            logger.warning(
                f"Looks like I was not able to follow @{username}, maybe you got soft-banned for this action!",
                extra={"color": Fore.RED},
            )
            universal_actions.detect_block(device)
        else:
            # Il selettore non ha visto nessuno dei tre bottoni. Prima di
            # dichiarare il fallimento si guarda l'albero completo: e' lo
            # stesso caso gia' visto su avatar, liste e contenitori, dove la
            # query nega un elemento che a schermo c'e'. (rb.coach, 23/08:
            # due volte in una giornata, ogni volta con uno zip di crash
            # salvato per un bottone.)
            nodo, stato = _bottone_follow_dal_dump(device)
            if stato in ("following", "followback"):
                logger.info(
                    f"@{username}: {'ti segue gia' if stato == 'followback' else 'lo segui gia'}, "
                    "letto dall'albero completo."
                )
                return False
            if nodo is not None:
                logger.info("Bottone Follow negato dal selettore ma presente nel dump: lo tocco.")
                get_throttler().wait_if_needed(ActionType.FOLLOW)
                device.tap_node(nodo, "sul bottone Follow")
                if device.find(
                    textMatches=UNFOLLOW_REGEX,
                    clickable=True,
                ).exists(Timeout.MEDIUM):
                    logger.info(f"Followed @{username}", extra={"color": Fore.GREEN})
                    universal_actions.detect_block(device)
                    return True
            # Niente bottone nemmeno nell'albero: non e' un guasto del bot,
            # e' una schermata dove il follow non si puo' fare. Si dice cosa
            # c'era e si va avanti, senza zip di crash.
            logger.warning(
                "Nessun bottone Follow/Following/Follow Back su questa schermata: "
                f"salto il follow. [{device.screen_summary(device.nodes_from_dump(), max_testi=8, max_ids=15)}]"
            )

    else:
        logger.info("Reached total follows limit, not following.")
    return False


def _try_answer_question_sticker(
    device: DeviceFacade,
    args: Namespace,
    session_state: SessionState,
    author: str,
) -> bool:
    """Se la storia a schermo ha un BOX DOMANDE, genera e invia una risposta.

    Ritorna True se ha risposto. Pensata per essere chiamata mentre si e' gia'
    dentro il viewer delle storie: non naviga e non apre niente.

    Resource-id verificati su IG 300.0.0.29.110 (19/08/2026). Nota: la
    content-desc dice "Double tap to reply", ma quella e' la dicitura per
    TalkBack: con uiautomator serve un CLICK SINGOLO, il doppio tap viene
    letto come due tap e fa avanzare la storia.
    """
    from GramAddict.core import ai_sticker

    if not ai_sticker.is_enabled(args):
        return False

    app_id = args.app_id
    container = device.find(resourceId=f"{app_id}:id/question_sticker_container_view")
    if not container.exists():
        return False

    view = device.find(resourceId=f"{app_id}:id/question_sticker_view")
    desc = view.ui_info().get("contentDescription", "") if view.exists() else ""
    m = _STICKER_DESC_RE.match(desc or "")
    question = (m.group(1) if m else (desc or "")).strip().rstrip(".")
    if not question:
        logger.debug("[sticker] sticker presente ma domanda illeggibile, salto.")
        return False

    logger.info(f"[sticker] @{author} chiede: '{question}'")
    result = ai_sticker.generate_sticker_reply(
        args,
        sticker_type=ai_sticker.STICKER_QUESTION,
        prompt_text=question,
        author_username=author,
    )
    reply = result.get("reply")
    if not reply:
        if result.get("refused"):
            logger.info("[sticker] tema sensibile, non rispondo.")
        return False

    container.click()
    random_sleep(1, 2, modulable=False)
    field = device.find(resourceId=f"{app_id}:id/question_sticker_answer")
    if not field.exists(Timeout.MEDIUM):
        logger.info("[sticker] campo di risposta non comparso, annullo.")
        cancel = device.find(resourceId=f"{app_id}:id/cancel_button")
        if cancel.exists():
            cancel.click()
        else:
            device.back()
        return False

    field.set_text(reply)
    random_sleep(1, 2, modulable=False)

    # il bottone Send compare SOLO a campo pieno: se manca, il testo non e'
    # stato scritto davvero e inviare non ha senso
    send = device.find(resourceId=f"{app_id}:id/question_sticker_send_button")
    if not send.exists(Timeout.MEDIUM):
        logger.info("[sticker] bottone Send assente, annullo senza inviare.")
        cancel = device.find(resourceId=f"{app_id}:id/cancel_button")
        if cancel.exists():
            cancel.click()
        else:
            device.back()
        return False

    send.click()
    random_sleep(2, 3, modulable=False)
    logger.info(
        f"[sticker] risposto a @{author}: '{reply}'",
        extra={"color": f"{Style.BRIGHT}"},
    )
    return True


def _try_answer_poll_sticker(
    device: DeviceFacade,
    args: Namespace,
    session_state: SessionState,
    author: str,
) -> bool:
    """Se la storia a schermo ha un SONDAGGIO, sceglie un'opzione e vota.

    Resource-id verificati su IG 300.0.0.29.110 (19/08/2026):
        poll_v2_sticker              contenitore
        poll_v2_sticker_title        testo della domanda
        poll_v2_sticker_answer_text  una per opzione, nell'ordine mostrato

    Nota: nessuno di questi nodi ha clickable=true, quindi si vota toccando
    l'area dell'opzione. uiautomator invia comunque il tap al centro dei
    bounds, anche su una view non cliccabile.
    """
    from GramAddict.core import ai_sticker

    if not ai_sticker.is_enabled(args):
        return False

    app_id = args.app_id
    if not device.find(resourceId=f"{app_id}:id/poll_v2_sticker").exists():
        return False

    # Se il sondaggio mostra le percentuali significa che abbiamo gia' votato:
    # Instagram le rivela solo dopo il voto (e marca l'opzione scelta con le
    # varianti *_white). Senza questo controllo il bot ritenterebbe a ogni
    # passaggio sulla stessa storia.
    if device.find(
        resourceId=f"{app_id}:id/poll_v2_sticker_result_percentage"
    ).exists():
        logger.debug("[sticker] sondaggio gia' votato, salto.")
        return False

    title_view = device.find(resourceId=f"{app_id}:id/poll_v2_sticker_title")
    question = title_view.get_text(error=False) if title_view.exists() else ""
    if not question:
        logger.debug("[sticker] sondaggio senza titolo leggibile, salto.")
        return False

    # le opzioni sono piu' di una: serve l'oggetto u2 nativo per indicizzarle
    opts_sel = device.deviceV2(
        resourceId=f"{app_id}:id/poll_v2_sticker_answer_text"
    )
    try:
        n_opts = opts_sel.count
    except Exception as e:
        logger.debug(f"[sticker] non riesco a contare le opzioni: {e}")
        return False
    if n_opts < 2:
        logger.debug(f"[sticker] sondaggio con {n_opts} opzioni, salto.")
        return False

    # Testi E coordinate vanno letti ORA: la chiamata allo Space dura qualche
    # centinaio di ms e la storia nel frattempo puo' avanzare. Se poi si
    # ririsolvesse il selettore per indice si otterrebbe UiObjectNotFound, o
    # peggio si cliccherebbe sull'opzione di un'altra storia.
    options, centers = [], []
    for i in range(n_opts):
        try:
            info = opts_sel[i].info
            options.append((info.get("text") or "").strip())
            b = info["bounds"]
            centers.append(
                ((b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2)
            )
        except Exception:
            options.append("")
            centers.append(None)
    if not all(options) or not all(centers):
        logger.debug("[sticker] opzioni illeggibili, salto.")
        return False

    logger.info(f"[sticker] sondaggio di @{author}: '{question}' -> {options}")
    result = ai_sticker.generate_sticker_reply(
        args,
        sticker_type=ai_sticker.STICKER_POLL,
        prompt_text=question,
        options=options,
        author_username=author,
    )
    choice = result.get("choice")
    if choice is None:
        if result.get("refused"):
            logger.info("[sticker] sondaggio su tema sensibile, non voto.")
        return False
    if not isinstance(choice, int) or not (0 <= choice < n_opts):
        logger.info(f"[sticker] scelta {choice!r} fuori range, non voto.")
        return False

    # La storia potrebbe essere cambiata durante la chiamata: se il titolo non
    # e' piu' lo stesso, il voto finirebbe sul sondaggio sbagliato.
    title_now = device.find(resourceId=f"{app_id}:id/poll_v2_sticker_title")
    if not title_now.exists() or title_now.get_text(error=False) != question:
        logger.info("[sticker] la storia e' cambiata durante la generazione, non voto.")
        return False

    x, y = centers[choice]
    try:
        device.deviceV2.click(x, y)
    except Exception as e:
        logger.info(f"[sticker] click sull'opzione fallito: {e}")
        return False
    random_sleep(2, 3, modulable=False)
    logger.info(
        f"[sticker] votato '{options[choice]}' nel sondaggio di @{author}",
        extra={"color": f"{Style.BRIGHT}"},
    )
    return True


def _try_answer_sticker(
    device: DeviceFacade,
    args: Namespace,
    session_state: SessionState,
    author: str,
) -> bool:
    """Prova a rispondere allo sticker della storia a schermo, se ce n'e' uno.

    Gestisce box domande e sondaggi. Quiz e slider non sono ancora mappati e
    vengono ignorati senza errori.
    """
    if _try_answer_question_sticker(device, args, session_state, author):
        return True
    return _try_answer_poll_sticker(device, args, session_state, author)


# Pulsanti con cui si chiude un dialogo modale generico di Instagram o di
# Android (errore di caricamento storia, avviso, richiesta). Solo testi
# "neutri": niente "Allow", "Accept", "Follow" o simili, che cambierebbero
# qualcosa sull'account.
_DIALOG_BUTTON_RE = r"(?i)^(ok|okay|close|chiudi|dismiss|got it|ho capito|capito|not now|non ora|cancel|annulla)$"


def _dismiss_dialog(device, nodi) -> bool:
    """Se nel dump c'e' un pulsante di chiusura di un dialogo, lo preme.
    Restituisce True se ha premuto qualcosa."""
    btn = device.node_in_dump(nodi, text_regex=_DIALOG_BUTTON_RE)
    if btn is None:
        return False
    logger.info(
        f"Dialogo a schermo: premo '{btn['text']}' per chiuderlo. "
        f"[{device.screen_summary(nodi)}]"
    )
    device.tap_node(btn, motivo=f"(pulsante '{btn['text']}')")
    return True


def _back_to_profile(device, tentativi: int = 4) -> bool:
    """Riporta a schermo il profilo dopo il viewer delle storie (o un dialogo
    rimasto aperto). Restituisce True se il profilo e' a schermo.

    Il controllo degli sticker apre le storie del profilo. Se il viewer carica
    lentamente (o la storia e' gia' sparita) il codice usciva con un solo
    device.back() condizionato a un elemento che spesso non c'era ancora: il
    viewer restava aperto e TUTTO quello che veniva dopo falliva -- griglia
    dei post non trovata, "Maybe a private/empty profile", "Unable to find
    action bar". In una giornata: ~33 profili validi, zero azioni.

    ATTENZIONE a come si riconosce "sono sul profilo": NON con l'avatar in
    alto. Dopo l'espansione della bio la pagina e' scrollata e l'avatar e'
    fuori schermo: una prima versione di questa funzione lo cercava, non lo
    trovava, e premeva indietro 4 volte uscendo da un profilo perfettamente
    valido. Si usano elementi che restano visibili a qualunque scroll: il
    titolo della action bar e le schede dei post.

    Il riconoscimento passa dall'albero completo (dump), non dal selettore:
    nel processo del bot il selettore a volte nega elementi presenti, e un
    falso "profilo assente" qui costa un back di troppo. Con il dump in mano
    si decide cosi':
      - profilo presente -> fatto;
      - viewer delle storie presente -> indietro;
      - pulsante di un dialogo (OK/Chiudi/Non ora) -> lo si preme. Caso
        visto su dj.lug (rb.coach): la storia non caricava, a schermo
        restava solo "OK", il bot non lo riconosceva, scrollava a vuoto e
        poi perdeva anche la lista follower;
      - lista follower presente -> non siamo sul profilo ma su una schermata
        nota: si lascia cosi' e si risponde False;
      - dump vuoto (uiautomator in difficolta') -> si aspetta, senza toccare;
      - altrimenti -> un back, perche' il dump dice con certezza che NON
        siamo sul profilo.
    """
    profilo_re = "|".join([
        ResourceID.ACTION_BAR_TITLE,
        ResourceID.PROFILE_TABS_CONTAINER,
        ResourceID.PROFILE_HEADER_AVATAR_CONTAINER_TOP_LEFT_STUB,
        ResourceID.ROW_PROFILE_HEADER_IMAGEVIEW,
    ])
    viewer_re = "|".join([
        ResourceID.REEL_VIEWER_MEDIA_CONTAINER,
        ResourceID.REEL_VIEWER_TITLE,
        ResourceID.REEL_VIEWER_ROOT,
    ])
    for _ in range(tentativi):
        if device.find(resourceIdMatches=profilo_re).exists(Timeout.SHORT):
            return True
        nodi = device.nodes_from_dump()
        if not nodi:
            logger.debug("Screen dump empty: waiting before deciding anything.")
            sleep(3)
            continue
        if device.node_in_dump(nodi, profilo_re) is not None:
            logger.debug("Profile not seen by the selector but present in the dump.")
            return True
        if device.node_in_dump(nodi, viewer_re) is not None:
            logger.debug("Story viewer still open: pressing back.")
            device.back()
            continue
        if _dismiss_dialog(device, nodi):
            continue
        if device.node_in_dump(nodi, ResourceID.LIST) is not None:
            logger.debug("Followers list on screen, not the profile: leaving it as is.")
            return False
        logger.debug(
            "Neither profile nor story viewer recognized: pressing back once. "
            f"[{device.screen_summary(nodi)}]"
        )
        device.back()
    if device.find(resourceIdMatches=profilo_re).exists(Timeout.SHORT):
        return True
    return device.node_in_dump(device.nodes_from_dump(), profilo_re) is not None


def _watch_stories(
    device: DeviceFacade,
    profile_view: ProfileView,
    username: str,
    stories_percentage: int,
    args: Namespace,
    session_state: SessionState,
    skip_stickers: bool = False,
) -> int:
    from GramAddict.core import ai_sticker

    want_watch = random_choice(stories_percentage)
    sticker_pct = get_value(
        getattr(args, "sticker_check_percentage", None) or "0", None, 0
    )
    want_sticker = ai_sticker.is_enabled(args) and random_choice(sticker_pct)
    # Il job blogger in modalita' commento promette "niente storie": vale
    # anche per la ricerca del box domande, che apriva comunque le storie dei
    # big (lucamastra_fit, 22/08) spendendo un minuto e rischiando di
    # lasciare il viewer aperto. Sui big lo sticker non ha senso: non e' la
    # loro storia il posto dove farsi notare.
    if skip_stickers:
        want_sticker = False
    if not want_watch and not want_sticker:
        return 0
    # Il limite WATCHES governa solo il "guardare": cercare un box domande non
    # consuma quel budget, quindi non blocca l'apertura delle storie.
    if want_sticker or not session_state.check_limit(
        limit_type=session_state.Limit.WATCHES, output=True
    ):

        def watch_story() -> bool:
            if session_state.check_limit(
                limit_type=session_state.Limit.WATCHES, output=False
            ):
                return False
            logger.debug("Watching stories...")
            session_state.totalWatched += 1
            nonlocal stories_counter
            stories_counter += 1
            for _ in range(7):
                random_sleep(0.5, 1, modulable=False, log=False)
                if story_view.getUsername().strip().casefold() != username.casefold():
                    return False
            like_story()
            return True

        def like_story():
            obj = device.find(resourceIdMatches=ResourceID.TOOLBAR_LIKE_BUTTON)
            if obj.exists():
                if not obj.get_selected():
                    obj.click()
                    logger.info("Story has been liked!")
                else:
                    logger.info("Story is already liked!")
            else:
                logger.info("There is no like button!")

        stories_ring = profile_view.StoryRing()
        live_marker = profile_view.live_marker()
        if live_marker.exists():
            logger.info(f"{username} is making a live.")
            return 0
        if stories_ring.exists():
            stories_to_watch: int = get_value(
                args.stories_count, "Stories count: {}.", 1
            )
            if want_sticker:
                # con stories-count a 0 il ciclo non girerebbe e lo sticker
                # non verrebbe mai cercato oltre la prima storia
                stories_to_watch = max(stories_to_watch, 3)
            stories_counter = 0
            logger.debug("Open the story container.")
            stories_ring.click(sleep=SleepTime.DEFAULT)
            story_view = CurrentStoryView(device)
            story_frame = story_view.getStoryFrame()
            story_frame.wait(Timeout.MEDIUM)
            story_username = story_view.getUsername()
            # Sull'emulatore il viewer delle storie ci mette anche 10-20 s a
            # mostrare il titolo: con un solo tentativo l'username risultava
            # vuoto, si prendeva il ramo "storia non disponibile" e, peggio, il
            # viewer restava aperto (vedi _back_to_profile). Qualche secondo
            # di attesa in piu' qui evita entrambe le cose.
            for _ in range(4):
                if story_username and story_username != "BUG!":
                    break
                sleep(3)
                story_username = story_view.getUsername()
            if (
                story_username == "BUG!"
                or story_username.strip().casefold() == username.casefold()
            ):
                start = datetime.now()
                if want_sticker:
                    _try_answer_sticker(device, args, session_state, username)
                try:
                    if want_watch and not watch_story():
                        return stories_counter
                except Exception as e:
                    logger.debug(f"Exception: {e}")
                    logger.debug(
                        "Ignore this error! Stories ended while we were interacting with it."
                    )
                for _ in range(stories_to_watch - 1):
                    try:
                        logger.debug("Going to the next story...")
                        story_frame.click(
                            mode=Location.RIGHTEDGE,
                            sleep=SleepTime.ZERO,
                            crash_report_if_fails=False,
                        )
                        if want_sticker:
                            _try_answer_sticker(
                                device, args, session_state, username
                            )
                        if want_watch and not watch_story():
                            break
                    except Exception as e:
                        logger.debug(f"Exception: {e}")
                        logger.debug(
                            "Ignore this error! Stories ended while we were interacting with it."
                        )
                        break
                for _ in range(4):
                    if (
                        story_view.getUsername().strip().casefold()
                        == username.casefold()
                    ):
                        device.back()
                    else:
                        break
                _back_to_profile(device)
                session_state.check_limit(
                    limit_type=session_state.Limit.WATCHES, output=True
                )
                logger.info(
                    f"Watched stories for {(datetime.now()-start).total_seconds():.2f}s."
                )
                return stories_counter
            else:
                # Evento BENIGNO (storia scaduta/sparita/privata o ring non
                # apribile): NON e' un crash da dumpare. Niente save_crash, solo
                # recupero della view per non lasciare il flusso disallineato
                # (era una con-causa del crash scroll su lista follower persa).
                logger.info(
                    "Story non disponibile (chiusa/sparita), salto le storie."
                )
                logger.debug(f"Story username: {story_username}")
                # non basta un back condizionato a story_frame: se il viewer
                # e' ancora in caricamento quel frame non c'e' e si resta
                # dentro la storia. Si torna al profilo con verifica.
                _back_to_profile(device)
                return 0
        return 0
    else:
        logger.info("Reached total watch limit, not watching stories.")
        return 0

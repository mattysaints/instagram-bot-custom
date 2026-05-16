import logging
import sys

from colorama import Fore

from GramAddict.core.device_facade import Timeout
from GramAddict.core.views import (
    HashTagView,
    PlacesView,
    PostsGridView,
    ProfileView,
    TabBarView,
    UniversalActions,
)

logger = logging.getLogger(__name__)


def check_if_english(device):
    """check if app is in English"""
    logger.debug("Checking if app is in English..")
    post, follower, following = ProfileView(device)._getSomeText()
    if None in {post, follower, following}:
        logger.warning(
            "Failed to check your Instagram language. Be sure to set it to English or the bot won't work!"
        )
    elif post == "posts" and follower == "followers" and following == "following":
        logger.debug("Instagram in English.")
    else:
        logger.error("Please change the language manually to English!")
        sys.exit(1)
    return ProfileView(device, is_own_profile=True)


def nav_to_blogger(device, username, current_job):
    """navigate to blogger (followers list or posts)"""
    _to_followers = bool(current_job.endswith("followers"))
    _to_following = bool(current_job.endswith("following"))
    if username is None:
        profile_view = TabBarView(device).navigateToProfile()
        if _to_followers:
            logger.info("Open your followers.")
            profile_view.navigateToFollowers()
        elif _to_following:
            logger.info("Open your following.")
            profile_view.navigateToFollowing()
    else:
        search_view = TabBarView(device).navigateToSearch()
        if not search_view.navigate_to_target(username, current_job):
            return False

        profile_view = ProfileView(device, is_own_profile=False)
        if _to_followers:
            logger.info(f"Open @{username} followers.")
            profile_view.navigateToFollowers()
        elif _to_following:
            logger.info(f"Open @{username} following.")
            profile_view.navigateToFollowing()

    return True


def nav_to_hashtag_or_place(device, target, current_job):
    """navigate to hashtag/place/feed list"""
    logger.info(f"🔎 nav_to_hashtag_or_place: target={target!r} job={current_job!r}")
    search_view = TabBarView(device).navigateToSearch()
    if not search_view.navigate_to_target(target, current_job):
        logger.warning(f"🔎 navigate_to_target fallita per {target!r} — hashtag/place saltato.")
        return False

    TargetView = HashTagView if current_job.startswith("hashtag") else PlacesView

    if current_job.endswith("recent"):
        logger.info(f"🔎 Switching to Recent tab per {target!r}.")
        recent_tab = TargetView(device)._getRecentTab()
        if recent_tab.exists(Timeout.TINY):
            recent_tab.click()
            logger.info(f"🔎 Recent tab cliccata per {target!r}.")
        else:
            logger.warning(
                f"🔎 Recent tab NON trovata per {target!r} in questo layout IG. "
                "Continuo con i post visibili (probabilmente Top)."
            )

        if UniversalActions(device)._check_if_no_posts():
            logger.warning(f"🔎 Nessun post visibile per {target!r} dopo reload. Skip.")
            UniversalActions(device)._reload_page()
            if UniversalActions(device)._check_if_no_posts():
                logger.warning(f"🔎 Ancora nessun post per {target!r} dopo reload. Skip definitivo.")
                return False

    result_view = TargetView(device)._getRecyclerView()
    if not result_view.exists():
        logger.warning(f"🔎 RecyclerView non trovata per {target!r}. Skip.")
        return False
    FistImageInView = TargetView(device)._getFistImageView(result_view)
    if FistImageInView.exists():
        logger.info(f"🔎 Prima immagine trovata per {target!r}, apro.")
        FistImageInView.click()
        return True
    else:
        logger.warning(
            f"🔎 Nessuna immagine (IMAGE_BUTTON) trovata nel RecyclerView di {target!r}. "
            "Hashtag probabilmente vuoto o layout cambiato. Skip."
        )
        return False


def nav_to_post_likers(device, username, my_username):
    """navigate to blogger post likers"""
    if username == my_username:
        TabBarView(device).navigateToProfile()
    else:
        search_view = TabBarView(device).navigateToSearch()
        if not search_view.navigate_to_target(username, "account"):
            return False
    profile_view = ProfileView(device)
    is_private = profile_view.isPrivateAccount()
    posts_count = profile_view.getPostsCount()
    is_empty = posts_count == 0
    if is_private or is_empty:
        private_empty = "Private" if is_private else "Empty"
        logger.info(f"{private_empty} account.", extra={"color": f"{Fore.GREEN}"})
        return False
    logger.info(f"Opening the first post of {username}.")
    ProfileView(device).swipe_to_fit_posts()
    PostsGridView(device).navigateToPost(0, 0)
    return True


def nav_to_feed(device):
    TabBarView(device).navigateToHome()

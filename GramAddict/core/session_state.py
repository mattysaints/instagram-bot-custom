import logging
import uuid
from datetime import datetime, timedelta
from enum import Enum, auto
from json import JSONEncoder

from GramAddict.core.utils import get_value

logger = logging.getLogger(__name__)


def _safe_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# Mapping da datetime.weekday() (0=lunedi) alle key accettate in config.
# Accettiamo sia "mon/tue/.../sun" sia "monday/tuesday/..." case-insensitive.
_WEEKDAY_ALIASES = [
    ("mon", "monday"),
    ("tue", "tuesday"),
    ("wed", "wednesday"),
    ("thu", "thursday"),
    ("fri", "friday"),
    ("sat", "saturday"),
    ("sun", "sunday"),
]


def _resolve_weekday_multiplier(raw) -> float:
    """Parse di una stringa "mon:1.0,tue:1.1,...".

    Ritorna il multiplier (>0) per il giorno corrente, oppure 1.0 se non
    impostato/malformato/manca la key di oggi.

    Tollerante: ignora token malformati silenziosamente (logghiamo a
    DEBUG, non rumoroso). Non solleva mai: in caso di edge case strani
    il bot deve girare come prima.
    """
    if raw is None or str(raw).strip() == "":
        return 1.0
    try:
        text = str(raw).strip()
        # supporta sia "mon:1.0,tue:1.1" sia "mon=1.0;tue=1.1" sia mix
        # con spazi. Splittiamo permissivamente.
        normalized = (
            text.replace(";", ",")
            .replace("=", ":")
            .replace(" ", "")
        )
        today_idx = datetime.now().weekday()  # 0=lun, 6=dom
        short_key, long_key = _WEEKDAY_ALIASES[today_idx]
        for token in normalized.split(","):
            if ":" not in token:
                continue
            k, v = token.split(":", 1)
            k = k.lower()
            if k in (short_key, long_key):
                try:
                    mult = float(v)
                except ValueError:
                    logger.debug(
                        f"[daily-budget] weekday multiplier '{v}' is not "
                        f"a number, ignoring."
                    )
                    return 1.0
                # clamp di sanita': 0.1 .. 3.0 (oltre = quasi sicuro typo)
                if mult <= 0:
                    return 1.0
                return max(0.1, min(3.0, mult))
        return 1.0
    except Exception as e:
        logger.debug(f"[daily-budget] weekday parse failed: {e}")
        return 1.0


class SessionState:
    id = None
    args = {}
    my_username = None
    my_posts_count = None
    my_followers_count = None
    my_following_count = None
    totalInteractions = {}
    successfulInteractions = {}
    totalFollowed = {}
    totalLikes = 0
    totalComments = 0
    totalPm = 0
    totalWatched = 0
    totalUnfollowed = 0
    removedMassFollowers = []
    totalScraped = 0
    totalCrashes = 0
    startTime = None
    finishTime = None
    actions_count_in_session = 0  # Track fatigue: increases with each action
    session_fatigue_multiplier = 1.0  # Multiplier for delays based on action count

    def __init__(self, configs):
        self.id = str(uuid.uuid4())
        self.args = configs.args
        self.my_username = None
        self.my_posts_count = None
        self.my_followers_count = None
        self.my_following_count = None
        self.totalInteractions = {}
        self.successfulInteractions = {}
        self.totalFollowed = {}
        self.totalLikes = 0
        self.totalComments = 0
        self.totalPm = 0
        self.totalWatched = 0
        self.totalUnfollowed = 0
        self.removedMassFollowers = []
        self.totalScraped = {}
        self.totalCrashes = 0
        self.startTime = datetime.now()
        self.finishTime = None
        self.actions_count_in_session = 0
        self.session_fatigue_multiplier = 1.0

    def add_interaction(self, source, succeed, followed, scraped):
        if self.totalInteractions.get(source) is None:
            self.totalInteractions[source] = 1
        else:
            self.totalInteractions[source] += 1

        if self.successfulInteractions.get(source) is None:
            self.successfulInteractions[source] = 1 if succeed else 0
        else:
            if succeed:
                self.successfulInteractions[source] += 1

        if self.totalFollowed.get(source) is None:
            self.totalFollowed[source] = 1 if followed else 0
        else:
            if followed:
                self.totalFollowed[source] += 1
        if self.totalScraped.get(source) is None:
            self.totalScraped[source] = 1 if scraped else 0
            self.successfulInteractions[source] = 1 if scraped else 0
        else:
            if scraped:
                self.totalScraped[source] += 1
                self.successfulInteractions[source] += 1

    def track_action(self):
        """Track an action to simulate progressive fatigue.

        After ~50 actions, bot starts slowing down as if human is getting tired.
        Uses soft curve: light slowdown until action 50, then gradually increases.
        """
        self.actions_count_in_session += 1

        # Fatigue curve: after ~50 actions, humans slow down
        # 1-50 actions: multiplier stays at 1.0
        # 50-100: multiplier gradually increases to 1.15
        # 100+: multiplier increases further to 1.3+
        if self.actions_count_in_session > 50:
            # Gentle curve: each action adds a small fatigue penalty
            fatigue_factor = min(0.3, (self.actions_count_in_session - 50) / 250)
            self.session_fatigue_multiplier = 1.0 + fatigue_factor
        else:
            self.session_fatigue_multiplier = 1.0

    def reset_session_fatigue(self):
        """Reset fatigue counters for a new session."""
        self.actions_count_in_session = 0
        self.session_fatigue_multiplier = 1.0

    def set_limits_session(
        self,
    ):
        """set the limits for current session"""
        self.args.current_likes_limit = get_value(
            self.args.total_likes_limit, None, 300
        )
        self.args.current_follow_limit = get_value(
            self.args.total_follows_limit, None, 50
        )
        self.args.current_unfollow_limit = get_value(
            self.args.total_unfollows_limit, None, 50
        )
        self.args.current_comments_limit = get_value(
            self.args.total_comments_limit, None, 10
        )
        self.args.current_pm_limit = get_value(self.args.total_pm_limit, None, 10)
        self.args.current_watch_limit = get_value(
            self.args.total_watches_limit, None, 50
        )
        self.args.current_success_limit = get_value(
            self.args.total_successful_interactions_limit, None, 100
        )
        self.args.current_total_limit = get_value(
            self.args.total_interactions_limit, None, 1000
        )
        self.args.current_scraped_limit = get_value(
            self.args.total_scraped_limit, None, 200
        )
        self.args.current_crashes_limit = get_value(
            self.args.total_crashes_limit, None, 5
        )

    def apply_daily_budget(self, daily_budget) -> None:
        """Clip per-session limits to whatever remains of the daily caps.

        Called *after* :meth:`set_limits_session`. If a daily cap is reached,
        the corresponding ``current_*_limit`` is forced to 0, which makes the
        existing session-level checks short-circuit naturally (no follow / like
        / etc.) without changing the rest of GramAddict's flow.

        Parameters
        ----------
        daily_budget : DailyBudget
            Persistent counter loaded from ``accounts/<user>/daily_budget.json``.
        """
        if daily_budget is None:
            return

        # Apply weekday-specific multiplier BEFORE caching the caps. Idea:
        # users set their base daily-*-cap in config and optionally a
        # weekday modifier (`daily-caps-weekday-multiplier`) tipo
        # "mon:1.0,tue:1.1,wed:1.1,thu:1.1,fri:0.9,sat:0.6,sun:0.5".
        # Si pesca la chiave dell'oggi (locale) e si moltiplicano tutti i
        # cap di un fattore unico. Cosi' weekend = bot piu' calmo, picco
        # martedi'-giovedi' (giorni a piu' traffico IG = bot meno
        # visibile). Se la stringa e' vuota / malformata, multiplier=1.0.
        weekday_mult = _resolve_weekday_multiplier(
            getattr(self.args, "daily_caps_weekday_multiplier", None)
        )

        def _scale(val: int) -> int:
            if val <= 0:
                return val  # 0 means "disabled" -> stay disabled
            return max(1, int(round(val * weekday_mult)))

        caps = {
            "follows": _scale(_safe_int(getattr(self.args, "daily_follows_cap", 0))),
            "likes": _scale(_safe_int(getattr(self.args, "daily_likes_cap", 0))),
            "unfollows": _scale(_safe_int(getattr(self.args, "daily_unfollows_cap", 0))),
            "comments": _scale(_safe_int(getattr(self.args, "daily_comments_cap", 0))),
            "pms": _scale(_safe_int(getattr(self.args, "daily_pm_cap", 0))),
        }
        if weekday_mult != 1.0:
            logger.info(
                f"[daily-budget] Weekday multiplier today: x{weekday_mult:.2f} "
                f"-> caps scaled accordingly."
            )
        # If user disabled all caps, nothing to do.
        if not any(v > 0 for v in caps.values()):
            return

        mapping = {
            "follows": "current_follow_limit",
            "likes": "current_likes_limit",
            "unfollows": "current_unfollow_limit",
            "comments": "current_comments_limit",
            "pms": "current_pm_limit",
        }

        logger.info("[daily-budget] Today's usage so far:")
        for action, cap in caps.items():
            used = daily_budget.used(action)
            if cap <= 0:
                logger.info(f"  - {action:<10} {used} (no daily cap)")
                continue
            remaining = max(0, cap - used)
            attr = mapping[action]
            session_lim = _safe_int(getattr(self.args, attr, 0))
            new_lim = min(session_lim, remaining) if session_lim else remaining
            new_lim = max(0, new_lim)
            setattr(self.args, attr, str(new_lim))
            mark = "REACHED" if remaining == 0 else "OK"
            logger.info(
                f"  - {action:<10} {used}/{cap}  remaining={remaining}  "
                f"session_limit {session_lim} -> {new_lim}  [{mark}]"
            )

    def check_limit(self, limit_type=None, output=False):
        """Returns True if limit reached - else False"""
        limit_type = SessionState.Limit.ALL if limit_type is None else limit_type
        # check limits
        total_likes = self.totalLikes >= int(self.args.current_likes_limit)
        total_followed = sum(self.totalFollowed.values()) >= int(
            self.args.current_follow_limit
        )
        total_unfollowed = self.totalUnfollowed >= int(self.args.current_unfollow_limit)
        total_comments = self.totalComments >= int(self.args.current_comments_limit)
        total_pm = self.totalPm >= int(self.args.current_pm_limit)
        total_watched = self.totalWatched >= int(self.args.current_watch_limit)
        total_successful = sum(self.successfulInteractions.values()) >= int(
            self.args.current_success_limit
        )
        total_interactions = sum(self.totalInteractions.values()) >= int(
            self.args.current_total_limit
        )

        total_scraped = sum(self.totalScraped.values()) >= int(
            self.args.current_scraped_limit
        )

        total_crashes = self.totalCrashes >= int(self.args.current_crashes_limit)

        session_info = [
            "Checking session limits:",
            f"- Total Likes:\t\t\t\t{'Limit Reached' if total_likes else 'OK'} ({self.totalLikes}/{self.args.current_likes_limit})",
            f"- Total Comments:\t\t\t\t{'Limit Reached' if total_comments else 'OK'} ({self.totalComments}/{self.args.current_comments_limit})",
            f"- Total PM:\t\t\t\t\t{'Limit Reached' if total_pm else 'OK'} ({self.totalPm}/{self.args.current_pm_limit})",
            f"- Total Followed:\t\t\t\t{'Limit Reached' if total_followed else 'OK'} ({sum(self.totalFollowed.values())}/{self.args.current_follow_limit})",
            f"- Total Unfollowed:\t\t\t\t{'Limit Reached' if total_unfollowed else 'OK'} ({self.totalUnfollowed}/{self.args.current_unfollow_limit})",
            f"- Total Watched:\t\t\t\t{'Limit Reached' if total_watched else 'OK'} ({self.totalWatched}/{self.args.current_watch_limit})",
            f"- Real Interactions (like/follow/cmt/dm):\t{'Limit Reached' if total_successful else 'OK'} ({sum(self.successfulInteractions.values())}/{self.args.current_success_limit})",
            f"- Profiles Visited (visits, also skipped):\t{'Limit Reached' if total_interactions else 'OK'} ({sum(self.totalInteractions.values())}/{self.args.current_total_limit})",
            f"- Total Crashes:\t\t\t\t{'Limit Reached' if total_crashes else 'OK'} ({self.totalCrashes}/{self.args.current_crashes_limit})",
            f"- Total Successful Scraped Users:\t\t{'Limit Reached' if total_scraped else 'OK'} ({sum(self.totalScraped.values())}/{self.args.current_scraped_limit})",
        ]

        if limit_type == SessionState.Limit.ALL:
            if output is not None:
                if output:
                    for line in session_info:
                        logger.info(line)
                else:
                    for line in session_info:
                        logger.debug(line)

            return (
                total_likes
                and self.args.end_if_likes_limit_reached
                or total_followed
                and self.args.end_if_follows_limit_reached
                or total_watched
                and self.args.end_if_watches_limit_reached
                or total_comments
                and self.args.end_if_comments_limit_reached
                or total_pm
                and self.args.end_if_pm_limit_reached,
                total_unfollowed,
                total_interactions or total_successful or total_scraped,
            )

        elif limit_type == SessionState.Limit.LIKES:
            if output:
                logger.info(session_info[1])
            else:
                logger.debug(session_info[1])
            return total_likes

        elif limit_type == SessionState.Limit.COMMENTS:
            if output:
                logger.info(session_info[2])
            else:
                logger.debug(session_info[2])
            return total_comments

        elif limit_type == SessionState.Limit.PM:
            if output:
                logger.info(session_info[3])
            else:
                logger.debug(session_info[3])
            return total_pm

        elif limit_type == SessionState.Limit.FOLLOWS:
            if output:
                logger.info(session_info[4])
            else:
                logger.debug(session_info[4])
            return total_followed

        elif limit_type == SessionState.Limit.UNFOLLOWS:
            if output:
                logger.info(session_info[5])
            else:
                logger.debug(session_info[5])
            return total_unfollowed

        elif limit_type == SessionState.Limit.WATCHES:
            if output:
                logger.info(session_info[6])
            else:
                logger.debug(session_info[6])
            return total_watched

        elif limit_type == SessionState.Limit.SUCCESS:
            if output:
                logger.info(session_info[7])
            else:
                logger.debug(session_info[7])
            return total_successful

        elif limit_type == SessionState.Limit.TOTAL:
            if output:
                logger.info(session_info[8])
            else:
                logger.debug(session_info[8])
            return total_interactions

        elif limit_type == SessionState.Limit.CRASHES:
            if output:
                logger.info(session_info[9])
            else:
                logger.debug(session_info[9])
            return total_crashes

        elif limit_type == SessionState.Limit.SCRAPED:
            if output:
                logger.info(session_info[10])
            else:
                logger.debug(session_info[10])
            return total_scraped

    @staticmethod
    def inside_working_hours(working_hours, delta_sec):
        def time_in_range(start, end, x):
            if start <= end:
                return start <= x <= end
            else:
                return start <= x or x <= end

        in_range = False
        time_left_list = []
        current_time = datetime.now()
        delta = timedelta(seconds=delta_sec)
        for n in working_hours:
            today = current_time.strftime("%Y-%m-%d")
            inf_value = f"{n.split('-')[0]} {today}"
            inf = datetime.strptime(inf_value, "%H.%M %Y-%m-%d") + delta
            sup_value = f"{n.split('-')[1]} {today}"
            sup = datetime.strptime(sup_value, "%H.%M %Y-%m-%d") + delta
            if sup - inf + timedelta(minutes=1) == timedelta(
                days=1
            ) or sup - inf + timedelta(minutes=1) == timedelta(days=0):
                logger.debug("Whole day mode.")
                return True, 0
            if time_in_range(inf.time(), sup.time(), current_time.time()):
                in_range = True
                return in_range, 0
            else:
                time_left = inf - current_time
                if time_left >= timedelta(0):
                    time_left_list.append(time_left)
                else:
                    time_left_list.append(time_left + timedelta(days=1))

        return (
            in_range,
            min(time_left_list) if len(time_left_list) > 1 else time_left_list[0],
        )

    def is_finished(self):
        return self.finishTime is not None

    class Limit(Enum):
        ALL = auto()
        LIKES = auto()
        COMMENTS = auto()
        PM = auto()
        FOLLOWS = auto()
        UNFOLLOWS = auto()
        WATCHES = auto()
        SUCCESS = auto()
        TOTAL = auto()
        SCRAPED = auto()
        CRASHES = auto()


class SessionStateEncoder(JSONEncoder):
    def default(self, session_state: SessionState):
        return {
            "id": session_state.id,
            "total_interactions": sum(session_state.totalInteractions.values()),
            "successful_interactions": sum(
                session_state.successfulInteractions.values()
            ),
            "total_followed": sum(session_state.totalFollowed.values()),
            "total_likes": session_state.totalLikes,
            "total_comments": session_state.totalComments,
            "total_pm": session_state.totalPm,
            "total_watched": session_state.totalWatched,
            "total_unfollowed": session_state.totalUnfollowed,
            "total_scraped": session_state.totalScraped,
            "start_time": str(session_state.startTime),
            "finish_time": str(session_state.finishTime),
            "args": session_state.args.__dict__,
            "profile": {
                "posts": session_state.my_posts_count,
                "followers": session_state.my_followers_count,
                "following": session_state.my_following_count,
            },
        }

import logging
import sys
import traceback
from datetime import datetime
from http.client import HTTPException, RemoteDisconnected
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout as RequestsReadTimeout
from socket import timeout

from colorama import Fore, Style
from uiautomator2.exceptions import UiObjectNotFoundError, GatewayError

try:
    from adbutils.errors import AdbTimeout, AdbError
except ImportError:
    AdbTimeout = OSError
    AdbError = OSError

from GramAddict.core.device_facade import DeviceFacade
from GramAddict.core.report import print_full_report
from GramAddict.core.utils import (
    check_if_crash_popup_is_there,
    close_instagram,
    open_instagram,
    random_sleep,
    save_crash,
    stop_bot,
)
from GramAddict.core.views import TabBarView

logger = logging.getLogger(__name__)


def run_safely(device, device_id, sessions, session_state, screen_record, configs):
    def actual_decorator(func):
        def wrapper(*args, **kwargs):
            session_state = sessions[-1]
            try:
                func(*args, **kwargs)
            except KeyboardInterrupt:
                try:
                    # Catch Ctrl-C and ask if user wants to pause execution
                    logger.info(
                        "CTRL-C detected . . .",
                        extra={"color": f"{Style.BRIGHT}{Fore.YELLOW}"},
                    )
                    logger.info(
                        f"-------- PAUSED: {datetime.now().strftime('%H:%M:%S')} --------",
                        extra={"color": f"{Style.BRIGHT}{Fore.YELLOW}"},
                    )
                    logger.info(
                        "NOTE: This is a rudimentary pause. It will restart the action, while retaining session data.",
                        extra={"color": Style.BRIGHT},
                    )
                    logger.info(
                        "Press RETURN to resume or CTRL-C again to Quit: ",
                        extra={"color": Style.BRIGHT},
                    )

                    input("")

                    logger.info(
                        f"-------- RESUMING: {datetime.now().strftime('%H:%M:%S')} --------",
                        extra={"color": f"{Style.BRIGHT}{Fore.YELLOW}"},
                    )
                    TabBarView(device).navigateToProfile()
                except KeyboardInterrupt:
                    stop_bot(device, sessions, session_state)

            except DeviceFacade.AppHasCrashed:
                logger.warning("App has crashed / has been closed!")
                restart(
                    device,
                    sessions,
                    session_state,
                    configs,
                    normal_crash=False,
                    print_traceback=False,
                )

            except (
                DeviceFacade.JsonRpcError,
                IndexError,
                HTTPException,
                RemoteDisconnected,
                RequestsConnectionError,
                RequestsReadTimeout,
                GatewayError,
                OSError,
                AdbTimeout,
                AdbError,
                timeout,
                UiObjectNotFoundError,
            ):
                restart(
                    device,
                    sessions,
                    session_state,
                    configs,
                )

            except Exception as e:
                logger.error(traceback.format_exc())
                for exception_line in traceback.format_exception_only(type(e), e):
                    logger.critical(
                        f"'{exception_line}' -> This kind of exception will stop the bot (no restart)."
                    )
                try:
                    logger.info(
                        f"List of running apps: {', '.join(device.deviceV2.app_list_running())}"
                    )
                except Exception:
                    logger.warning("Could not list running apps (device unreachable).")
                try:
                    save_crash(device)
                except Exception as se:
                    logger.warning(f"save_crash failed in except handler: {se}")
                try:
                    close_instagram(device)
                except Exception:
                    pass
                print_full_report(sessions, configs.args.scrape_to_file)
                sessions.persist(directory=session_state.my_username)
                raise e from e

        return wrapper

    return actual_decorator


def restart(
    device: DeviceFacade,
    sessions,
    session_state,
    configs,
    normal_crash: bool = True,
    print_traceback: bool = True,
):
    if print_traceback:
        logger.error(traceback.format_exc())
        try:
            save_crash(device)
        except Exception as e:
            logger.warning(f"save_crash failed during restart (device unreachable?): {e}")
    try:
        logger.info(
            f"List of running apps: {', '.join(device.deviceV2.app_list_running())}."
        )
    except Exception:
        logger.warning("Could not list running apps (device unreachable).")
    if configs.args.count_app_crashes or normal_crash:
        session_state.totalCrashes += 1
        if session_state.check_limit(
            limit_type=session_state.Limit.CRASHES, output=True
        ):
            logger.error(
                "Reached crashes limit. Bot has crashed too much! Please check what's going on."
            )
            stop_bot(device, sessions, session_state)
        logger.info("Something unexpected happened. Let's try again.")
    try:
        close_instagram(device)
    except Exception as e:
        logger.warning(f"close_instagram failed during restart: {e}")
    try:
        check_if_crash_popup_is_there(device)
    except Exception:
        pass
    random_sleep()
    try:
        opened = open_instagram(device)
    except Exception as e:
        logger.warning(f"open_instagram failed during restart: {e}")
        opened = False
    if not opened:
        print_full_report(sessions, configs.args.scrape_to_file)
        sessions.persist(directory=session_state.my_username)
        sys.exit(2)
    try:
        TabBarView(device).navigateToProfile()
    except Exception as e:
        logger.warning(f"navigateToProfile failed during restart: {e}")

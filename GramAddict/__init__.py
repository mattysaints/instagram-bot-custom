"""Human-like Instagram bot powered by UIAutomator2"""

__version__ = "3.2.12"
__tested_ig_version__ = "300.0.0.29.110"

from GramAddict.runtime_env import bootstrap_runtime_env

bootstrap_runtime_env()

from GramAddict.core.bot_flow import start_bot


def run(**kwargs):
    start_bot(**kwargs)

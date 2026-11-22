"""Sentry error reporting.

Reads SENTRY_DSN from Streamlit secrets. If it's missing (e.g. running
locally without it), the app runs exactly as before with no reporting.

How errors reach Sentry: Streamlit catches app errors itself (to show the
red error box), so they never reach Sentry on their own, and on Streamlit
Cloud they aren't written to the normal log either. So we hook Streamlit's
internal "log this uncaught error" step: every app error passes through it,
and we send a copy to Sentry before Streamlit carries on as normal.
"""
import logging

import sentry_sdk
import streamlit as st

_LOG = logging.getLogger(__name__)


def _hook_streamlit_errors() -> None:
    try:
        from streamlit import error_util
    except ImportError:
        _LOG.warning("Sentry: streamlit.error_util not found; app errors won't be reported")
        return

    original = getattr(error_util, "_log_uncaught_app_exception", None)
    if original is None or getattr(original, "_sentry_hooked", False):
        if original is None:
            _LOG.warning("Sentry: Streamlit error hook not found; app errors won't be reported")
        return

    def _log_and_report(ex):
        try:
            sentry_sdk.capture_exception(ex)
        except Exception:
            pass  # never let reporting break the app
        return original(ex)

    _log_and_report._sentry_hooked = True
    error_util._log_uncaught_app_exception = _log_and_report


@st.cache_resource(show_spinner=False)
def init_sentry() -> bool:
    """Start Sentry once per server process (cached across reruns)."""
    dsn = st.secrets.get("SENTRY_DSN")
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment="production",
        send_default_pii=False,   # don't send IPs / user details
        traces_sample_rate=0.0,   # errors only, no performance tracing
    )
    _hook_streamlit_errors()
    # One info message each time the app (re)starts: confirms the connection.
    sentry_sdk.capture_message("App started - Sentry connected", level="info")
    return True

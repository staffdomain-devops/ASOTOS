"""Tenacity retry decorators for HubSpot and Anthropic API calls."""
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
)

_RETRY_STOP = stop_after_attempt(6) | stop_after_delay(60)
_RETRY_WAIT = wait_random_exponential(min=1, max=60)


def _is_retryable_hubspot(exc):
    try:
        from hubspot.crm.contacts.exceptions import ApiException
        if isinstance(exc, ApiException):
            return exc.status in (429, 500, 502, 503, 504)
    except ImportError:
        pass
    return False


def _is_retryable_anthropic(exc):
    try:
        from anthropic import RateLimitError, APIStatusError
        if isinstance(exc, RateLimitError):
            return True
        if isinstance(exc, APIStatusError):
            return exc.status_code >= 500
    except ImportError:
        pass
    return False


hubspot_retry = retry(
    retry=retry_if_exception(_is_retryable_hubspot),
    stop=_RETRY_STOP,
    wait=_RETRY_WAIT,
    reraise=True,
)

anthropic_retry = retry(
    retry=retry_if_exception(_is_retryable_anthropic),
    stop=_RETRY_STOP,
    wait=_RETRY_WAIT,
    reraise=True,
)

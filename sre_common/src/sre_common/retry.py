import asyncio
import random
import logging
import functools
from typing import Callable, Any, TypeVar

logger = logging.getLogger("sre_retry")

T = TypeVar("T")

def is_transient_error(exc: Exception) -> bool:
    """Helper to detect if an exception is transient (network timeout, rate limit 429, or 5xx server error)."""
    exc_str = str(exc)
    exc_name = exc.__class__.__name__
    
    # 1. Check for google-genai errors or Google core exceptions
    # ResourceExhausted (429), ServiceUnavailable (503), DeadlineExceeded (504), Aborted (409)
    if any(x in exc_name for x in ("ResourceExhausted", "ServiceUnavailable", "DeadlineExceeded", "Aborted", "GoogleAPICallError")):
        return True
    
    # Check for HTTP-like error codes in properties of custom exceptions (e.g. google.genai.errors.APIError)
    status_code = getattr(exc, "status_code", None)
    if status_code in (429, 500, 502, 503, 504):
        return True
        
    # 2. Check for httpx / network errors
    try:
        import httpx
        if isinstance(exc, httpx.HTTPError):
            if isinstance(exc, httpx.HTTPStatusError):
                if exc.response.status_code in (429, 500, 502, 503, 504):
                    return True
            else:
                # Other httpx errors (TimeoutException, ConnectError, NetworkError) are transient
                return True
    except ImportError:
        pass

    # 3. Check for specific status codes or error types in generic exceptions
    message_lower = exc_str.lower()
    if any(x in message_lower for x in ("429", "502", "503", "504", "resource exhausted", "rate limit", "quota exceeded", "deadline exceeded", "service unavailable")):
        return True
        
    return False

def retry_async(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    jitter: bool = True
):
    """Decorator to retry asynchronous functions on transient errors with exponential backoff and jitter."""
    def decorator(func: Callable[..., Any]):
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_retries or not is_transient_error(exc):
                        raise
                    
                    actual_delay = delay
                    if jitter:
                        actual_delay *= random.uniform(0.5, 1.5)
                        
                    logger.warning(
                        f"Transient error in '{func.__name__}' on attempt {attempt + 1}/{max_retries + 1}. "
                        f"Retrying in {actual_delay:.2f}s... Error: {exc}"
                    )
                    await asyncio.sleep(actual_delay)
                    delay *= backoff_factor
        return wrapper
    return decorator

def retry_sync(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    jitter: bool = True
):
    """Decorator to retry synchronous functions on transient errors with exponential backoff and jitter."""
    def decorator(func: Callable[..., Any]):
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import time
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_retries or not is_transient_error(exc):
                        raise
                    
                    actual_delay = delay
                    if jitter:
                        actual_delay *= random.uniform(0.5, 1.5)
                        
                    logger.warning(
                        f"Transient error in '{func.__name__}' on attempt {attempt + 1}/{max_retries + 1}. "
                        f"Retrying in {actual_delay:.2f}s... Error: {exc}"
                    )
                    time.sleep(actual_delay)
                    delay *= backoff_factor
        return wrapper
    return decorator

"""HTTP fetcher and HTML cleaner for web page snapshots."""

import logging

import httpx
import trafilatura
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

# BS4 fallback: tags that do not contribute to visible content
_NOISE_TAGS = [
    "script", "style", "nav", "header", "footer", 
    "noscript", "aside", "iframe", "svg", "form", 
    "button", "canvas", "dialog", "template"
]


def fetch_and_clean(url: str) -> tuple[str, str]:
    """Fetch a URL and return (html_raw, text_clean).

    text_clean is the visible text with noise tags removed, suitable for
    diff computation and embedding generation.

    Raises:
        httpx.HTTPStatusError: if the server returns a non-2xx status.
        httpx.TimeoutException: if the request exceeds FETCH_TIMEOUT seconds.
        httpx.RequestError: for network-level errors (DNS, connection refused, etc.).
    """
    with httpx.Client(
        follow_redirects=True, timeout=settings.fetch_timeout, verify=settings.fetch_verify_ssl
    ) as client:
        response = client.get(url)
        response.raise_for_status()

    html_raw = response.text
    text_clean = _extract_clean_text(html_raw)

    logger.debug("Fetched %s — %d chars raw, %d chars clean", url, len(html_raw), len(text_clean))
    return html_raw, text_clean


def _extract_clean_text(html: str) -> str:
    """Extract main content from HTML.

    Uses trafilatura (article/main-content extraction) as the primary strategy.
    Falls back to BeautifulSoup noise-tag stripping when trafilatura returns nothing
    (e.g. very short pages, non-article layouts, or plain text responses).
    """
    extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
    if extracted:
        return extracted

    logger.debug("trafilatura returned empty — falling back to BS4")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)

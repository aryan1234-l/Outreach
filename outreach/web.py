"""Free web search (DuckDuckGo etc. via `ddgs`) and polite page fetching."""
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; StudentSocietyOutreachBot/1.0)"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SKIP_EMAIL = ("example.", "sentry", "wixpress", ".png", ".jpg", ".webp", "@2x")
CONTACT_HINTS = ("contact", "about", "partner", "sponsor", "careers", "campus", "community")
SKIP_DOMAINS = (
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "wikipedia.org", "glassdoor.", "naukri.com", "indeed.", "ambitionbox.com", "crunchbase.com",
    "zaubacorp.com", "justdial.com", "quora.com", "reddit.com", "medium.com",
)


def search(query, max_results=8, region="in-en"):
    from ddgs import DDGS

    for attempt in range(3):
        try:
            return DDGS().text(query, region=region, max_results=max_results) or []
        except Exception as e:  # rate limits / backend hiccups
            print(f"  search failed ({e}); retrying")
            time.sleep(5 * (attempt + 1))
    return []


def domain(url):
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def is_company_site(url):
    d = domain(url)
    return bool(d) and not any(s in d for s in SKIP_DOMAINS)


def fetch(url, max_chars=6000):
    """Return (visible text, BeautifulSoup) or ("", None) on failure."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        if not r.ok or "text/html" not in r.headers.get("content-type", ""):
            return "", None
    except requests.RequestException:
        return "", None
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return text[:max_chars], soup


def public_emails(text, soup):
    found = set(EMAIL_RE.findall(text))
    if soup:
        for a in soup.select('a[href^="mailto:"]'):
            found.add(a["href"][7:].split("?")[0])
    return sorted(e for e in found if not any(s in e.lower() for s in SKIP_EMAIL))


def company_pages(url, max_extra=2):
    """Fetch a company's homepage plus a couple of contact/about/partner pages."""
    home = f"{urlparse(url).scheme or 'https'}://{urlparse(url).netloc}"
    text, soup = fetch(home)
    pages = [(home, text)]
    emails = set(public_emails(text, soup))
    if soup:
        links = []
        for a in soup.find_all("a", href=True):
            link = urljoin(home, a["href"])
            label = (a.get_text() + " " + a["href"]).lower()
            if domain(link) == domain(home) and any(h in label for h in CONTACT_HINTS):
                if link not in links and link.rstrip("/") != home:
                    links.append(link)
        for link in links[:max_extra]:
            t, s = fetch(link, max_chars=3000)
            if t:
                pages.append((link, t))
                emails.update(public_emails(t, s))
            time.sleep(1)
    return pages, sorted(emails)

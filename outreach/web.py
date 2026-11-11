"""Free web search (DuckDuckGo etc. via `ddgs`), polite page fetching, contact discovery."""
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; StudentSocietyOutreachBot/1.0)"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SKIP_EMAIL = ("example.", "sentry", "wixpress", ".png", ".jpg", ".webp", "@2x", "noreply", "no-reply",
              "donotreply", "privacy", "abuse", "legal", "gdpr", "security@", "dpo@")
# Link text / URL words that point at pages worth reading, best first.
PAGE_HINTS = ("sponsor", "partner", "campus", "student", "university", "community", "developer",
              "program", "contact", "about", "career")
COMMON_PATHS = ("/contact", "/contact-us", "/partners", "/partnerships", "/sponsorship", "/community",
                "/students", "/campus", "/about", "/careers")
# Preferred mailbox names for sponsorship outreach, best first.
EMAIL_PREF = ("sponsor", "partner", "campus", "university", "student", "community", "devrel",
              "developer", "marketing", "events", "brand", "hr", "talent", "career", "recruit",
              "hello", "contact", "info", "business", "sales", "team", "support")
SKIP_DOMAINS = (
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "wikipedia.org", "glassdoor.", "naukri.com", "indeed.", "ambitionbox.com", "crunchbase.com",
    "zaubacorp.com", "justdial.com", "quora.com", "reddit.com", "medium.com", "github.com",
    "tracxn.com", "zoominfo.com", "rocketreach.co", "apollo.io", "g2.com", "play.google.com",
    "apps.apple.com",
)
SPONSOR_WORDS = re.compile(r"sponsor|partner|hackathon|fest|college|campus|student|university|devfest|gdg",
                           re.I)


def search(query, max_results=8, region="in-en"):
    from ddgs import DDGS

    for attempt in range(3):
        try:
            return DDGS().text(query, region=region, max_results=max_results) or []
        except Exception as e:  # rate limits / backend hiccups
            if "no results" in str(e).lower():
                return []
            print(f"  search failed ({e}); retrying")
            time.sleep(5 * (attempt + 1))
    return []


def domain(url):
    host = urlparse(url if "//" in url else "https://" + url).netloc.lower()
    return host.removeprefix("www.")


def is_company_site(url):
    d = domain(url)
    return bool(d) and "." in d and not any(s in d for s in SKIP_DOMAINS)


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


def public_emails(text, soup=None):
    found = set(EMAIL_RE.findall(text or ""))
    if soup:
        for a in soup.select('a[href^="mailto:"]'):
            found.add(a["href"][7:].split("?")[0].strip())
    return {e.strip(".").lower() for e in found if not any(s in e.lower() for s in SKIP_EMAIL)}


def rank_emails(emails, company_domain=""):
    """Company-domain addresses first, then by how sponsorship-relevant the mailbox name is."""
    def key(e):
        local, _, dom = e.partition("@")
        same = 0 if company_domain and (dom == company_domain or dom.endswith("." + company_domain)) else 1
        pref = next((i for i, p in enumerate(EMAIL_PREF) if p in local), len(EMAIL_PREF))
        return (same, pref, len(e))
    return sorted(set(emails), key=key)


def _hint_rank(label):
    return next((i for i, h in enumerate(PAGE_HINTS) if h in label), None)


def company_pages(url, max_extra=4):
    """Fetch a company's homepage plus its most useful sponsor/contact/about pages.

    Returns (pages, emails): pages = [(url, text)], emails = ranked public addresses.
    """
    parts = urlparse(url if "//" in url else "https://" + url)
    home = f"{parts.scheme or 'https'}://{parts.netloc}"
    text, soup = fetch(home)
    pages = [(home, text)]
    emails = set(public_emails(text, soup))

    links = {}
    if soup:
        for a in soup.find_all("a", href=True):
            link = urljoin(home, a["href"]).split("#")[0].rstrip("/")
            if domain(link) != domain(home) or link == home:
                continue
            rank = _hint_rank((a.get_text() + " " + a["href"]).lower())
            if rank is not None:
                links[link] = min(rank, links.get(link, 99))
    for path in COMMON_PATHS:  # sites that hide these pages in JS menus
        links.setdefault(home + path, 50 + COMMON_PATHS.index(path))

    for link, _ in sorted(links.items(), key=lambda kv: kv[1]):
        if len(pages) > max_extra:
            break
        t, s = fetch(link, max_chars=3000)
        if t and t[:200] != text[:200]:  # skip soft-404s that return the homepage
            pages.append((link, t))
            emails |= public_emails(t, s)
        time.sleep(0.5)
    return pages, rank_emails(emails, domain(home))


def search_contacts(company, company_domain):
    """Look for public addresses on the company's own domain mentioned anywhere on the web."""
    found = set()
    for q in (f'"@{company_domain}" partnerships OR sponsorship OR marketing OR campus',
              f'{company} sponsorship contact email'):
        for r in search(q, max_results=6):
            for e in public_emails(f"{r.get('title', '')} {r.get('body', '')}"):
                if e.endswith("@" + company_domain) or e.endswith("." + company_domain):
                    found.add(e)
        time.sleep(1)
    return rank_emails(found, company_domain)


def sponsorship_evidence(company, max_items=5):
    """Search snippets suggesting the company has sponsored student/tech events before."""
    out, seen = [], set()
    key = company.lower().split()[0]
    for q in (f"{company} sponsor hackathon", f"{company} sponsored college fest students"):
        for r in search(q, max_results=8):
            blob = f"{r.get('title', '')} {r.get('body', '')}"
            href = r.get("href", "")
            if href in seen or key not in blob.lower() or not SPONSOR_WORDS.search(blob):
                continue
            seen.add(href)
            out.append(f"- {r.get('title', '').strip()}: {r.get('body', '').strip()[:220]} ({href})")
            if len(out) >= max_items:
                return out
        time.sleep(1)
    return out

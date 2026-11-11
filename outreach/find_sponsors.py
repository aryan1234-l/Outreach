"""Find new likely sponsors, starting from the sheet of past sponsors.

    python -m outreach.find_sponsors --past data/past_sponsors.csv --max-candidates 30

Output: output/prospects.csv (appended as it goes, so you can stop and resume).
Nobody is contacted here; a human reviews the sheet and sets status=approved.
"""
import argparse
import csv
import json
import re
import pathlib
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # works as script or module
from outreach import web
from outreach.llm import ROOT, chat_json, load_config, load_prompt, resolve, start_server

OUT = ROOT / "output" / "prospects.csv"
FIELDS = [
    "company", "website", "what_they_do", "fit_score", "reason", "suggested_ask",
    "contact_emails", "contact_role", "found_via", "status", "notes",
]
SUFFIXES = r"\b(pvt|private|ltd|limited|inc|llp|llc|corp|corporation|technologies|technology|solutions|india)\b"


def norm(name):
    name = re.sub(SUFFIXES, "", name.lower())
    return re.sub(r"[^a-z0-9]", "", name)


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def company_col(rows):
    for col in rows[0].keys():
        if col.strip().lower() in ("company", "company name", "organization", "organisation", "name", "sponsor"):
            return col
    raise SystemExit(f"Past sponsors sheet needs a 'company' column. Found: {list(rows[0].keys())}")


def society_blurb(cfg):
    return json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, indent=2, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--past", default=str(ROOT / "data" / "past_sponsors.csv"))
    ap.add_argument("--max-candidates", type=int, default=30, help="companies to research this run")
    ap.add_argument("--queries", type=int, default=12, help="search queries to generate")
    ap.add_argument("--results-per-query", type=int, default=5)
    args, _ = ap.parse_known_args()

    cfg = load_config()
    past = read_csv(resolve(args.past))
    col = company_col(past)
    known = {norm(r[col]) for r in past if r.get(col)}

    OUT.parent.mkdir(exist_ok=True)
    if OUT.exists():
        known |= {norm(r["company"]) for r in read_csv(OUT)}
    else:
        with open(OUT, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, FIELDS).writeheader()

    start_server()

    # 1. LLM turns past sponsors into search queries
    past_text = "\n".join(json.dumps(r, ensure_ascii=False) for r in past[:60])
    q = chat_json(
        load_prompt("queries_system"),
        f"Our society:\n{society_blurb(cfg)}\n\nPast sponsors/contacts:\n{past_text}\n\n"
        f"Write {args.queries} search queries.",
    )
    queries = [s for s in q.get("queries", []) if isinstance(s, str)][: args.queries]
    print(f"{len(queries)} queries:", *queries, sep="\n  ")

    # 2. Search, read result pages, pull out candidate companies
    candidates, seen_urls = {}, set()
    extract_prompt = load_prompt("extract_system")
    for query in queries:
        if len(candidates) >= args.max_candidates:
            break
        print(f"\nSearching: {query}")
        for res in web.search(query, max_results=args.results_per_query):
            url = res.get("href", "")
            if not url or url in seen_urls or "linkedin.com" in url:
                continue
            seen_urls.add(url)
            text, _ = web.fetch(url)
            if len(text) < 200:
                continue
            found = chat_json(
                extract_prompt,
                f"Page URL: {url}\nPage title: {res.get('title', '')}\n\nPage text:\n{text}",
            ).get("companies", [])
            for c in found:
                name = (c.get("name") or "").strip()
                key = norm(name)
                if not key or key in known or key in candidates:
                    continue
                candidates[key] = {"name": name, "website": c.get("website") or "", "found_via": url}
                print(f"  + {name}")
            time.sleep(1)
    print(f"\n{len(candidates)} new candidate companies")

    # 3. Research each candidate on its own website and score the fit
    score_prompt = load_prompt("score_system")
    for i, cand in enumerate(list(candidates.values())[: args.max_candidates], 1):
        site = cand["website"]
        if not site or not web.is_company_site(site):
            hits = [h for h in web.search(f"{cand['name']} official website", max_results=5)
                    if web.is_company_site(h.get("href", ""))]
            site = hits[0]["href"] if hits else ""
        if site and not site.startswith("http"):
            site = "https://" + site
        if site:
            parts = urlparse(site)
            site = f"{parts.scheme}://{parts.netloc}"
        if not site:
            print(f"[{i}] {cand['name']}: no website found, skipped")
            continue

        pages, emails = web.company_pages(site)
        page_text = "\n\n".join(f"[{u}]\n{t}" for u, t in pages if t)
        if not page_text:
            print(f"[{i}] {cand['name']}: site unreachable, skipped")
            continue

        s = chat_json(
            score_prompt,
            f"Our society:\n{society_blurb(cfg)}\n\nCompany: {cand['name']}\nWebsite: {site}\n"
            f"Public emails found on their site: {emails or 'none'}\n\nTheir website text:\n{page_text[:9000]}",
        )
        # Only keep emails that really appear on their site (no made-up addresses).
        picked = [e for e in s.get("contact_emails", []) if e in emails]
        row = {
            "company": s.get("company") or cand["name"],
            "website": site,
            "what_they_do": s.get("what_they_do", ""),
            "fit_score": s.get("fit_score", ""),
            "reason": s.get("reason", ""),
            "suggested_ask": s.get("suggested_ask", ""),
            "contact_emails": "; ".join(picked),
            "contact_role": s.get("contact_role", ""),
            "found_via": cand["found_via"],
            "status": "new",
            "notes": "",
        }
        with open(OUT, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, FIELDS).writerow(row)
        print(f"[{i}] {row['company']}: fit {row['fit_score']}/10, emails: {row['contact_emails'] or '-'}")

    print(f"\nDone. Review {OUT}, set status=approved on the ones worth contacting.")


if __name__ == "__main__":
    main()

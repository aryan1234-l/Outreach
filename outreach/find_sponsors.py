"""Find new likely sponsors, starting from the sheet of past sponsors.

    python -m outreach.find_sponsors --past data/past_sponsors.csv --max-candidates 30

Output: output/prospects.csv (appended as it goes, so you can stop and resume).
Nobody is contacted here; a human reviews the sheet and sets status=approved.
"""
import argparse
import csv
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # works as script or module
from outreach import web
from outreach.llm import ROOT, chat_json, load_config, load_prompt, resolve, start_server

OUT = ROOT / "output" / "prospects.csv"
FIELDS = [
    "company", "website", "fit_score", "what_they_do", "reason", "personal_hook", "past_sponsorships",
    "suggested_ask", "contact_emails", "contact_role", "found_via", "status", "notes",
]
SUFFIXES = r"\b(pvt|private|ltd|limited|inc|llp|llc|corp|corporation|technologies|technology|solutions|india)\b"


def norm(name):
    name = re.sub(SUFFIXES, "", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", name)


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, FIELDS, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)


def company_col(rows):
    for col in rows[0].keys():
        if col.strip().lower() in ("company", "company name", "organization", "organisation", "name", "sponsor"):
            return col
    raise SystemExit(f"Past sponsors sheet needs a 'company' column. Found: {list(rows[0].keys())}")


def society_blurb(cfg):
    return json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, indent=2, ensure_ascii=False)


def resolve_site(name, hint):
    if hint and web.is_company_site(hint):
        return hint
    for h in web.search(f"{name} official website", max_results=6):
        if web.is_company_site(h.get("href", "")):
            return h["href"]
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--past", default=str(ROOT / "data" / "past_sponsors.csv"))
    ap.add_argument("--max-candidates", type=int, default=30, help="companies to research this run")
    ap.add_argument("--queries", type=int, default=12, help="search queries to generate")
    ap.add_argument("--results-per-query", type=int, default=5)
    ap.add_argument("--min-save-score", type=int, default=1, help="don't save prospects scoring below this")
    args, _ = ap.parse_known_args()

    cfg = load_config()
    past = read_csv(resolve(args.past))
    col = company_col(past)
    known = {norm(r[col]) for r in past if r.get(col)}
    known_domains = {web.base_domain(r["website"]) for r in past if r.get("website")}

    OUT.parent.mkdir(exist_ok=True)
    if OUT.exists():
        existing = read_csv(OUT)
        known |= {norm(r["company"]) for r in existing}
        known_domains |= {web.base_domain(r["website"]) for r in existing if r.get("website")}
        with open(OUT, encoding="utf-8-sig") as fh:
            if fh.readline().strip().split(",") != FIELDS:
                write_csv(OUT, existing)  # upgrade old file to the current columns
    else:
        write_csv(OUT, [])

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
        if len(candidates) >= args.max_candidates * 2:
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
            try:
                found = chat_json(
                    extract_prompt,
                    f"Page URL: {url}\nPage title: {res.get('title', '')}\n\nPage text:\n{text}",
                ).get("companies", [])
            except RuntimeError as e:
                print(f"  (skipped page: {e})")
                continue
            for c in found:
                name = (c.get("name") or "").strip()
                key = norm(name)
                if not key or key in known or key in candidates:
                    continue
                candidates[key] = {"name": name, "website": c.get("website") or "", "found_via": url}
                print(f"  + {name}")
            time.sleep(1)
    print(f"\n{len(candidates)} new candidate companies")

    # 3. Research each candidate: own website, public contacts, sponsorship history -> score
    score_prompt = load_prompt("score_system")
    saved = 0
    for i, cand in enumerate(candidates.values(), 1):
        if saved >= args.max_candidates:
            break
        site = resolve_site(cand["name"], cand["website"])
        dom = web.domain(site) if site else ""
        if not dom:
            print(f"[{i}] {cand['name']}: no website found, skipped")
            continue
        base = web.base_domain(site)
        if base in known_domains:
            print(f"[{i}] {cand['name']}: already known ({base}), skipped")
            continue
        known_domains.add(base)

        pages, emails = web.company_pages(site)
        page_text = "\n\n".join(f"[{u}]\n{t}" for u, t in pages if t)
        if not page_text:
            print(f"[{i}] {cand['name']}: site unreachable, skipped")
            continue
        if not emails:
            emails = web.search_contacts(cand["name"], dom)
        evidence = web.sponsorship_evidence(cand["name"])

        try:
            s = chat_json(
                score_prompt,
                f"Our society:\n{society_blurb(cfg)}\n\nCompany: {cand['name']}\nWebsite: https://{dom}\n"
                f"Public emails found (best first): {emails or 'none'}\n\n"
                f"Web snippets about their past sponsorships:\n{chr(10).join(evidence) or 'none found'}\n\n"
                f"Their website text:\n{page_text[:10000]}",
            )
        except RuntimeError as e:
            print(f"[{i}] {cand['name']}: scoring failed ({e}), skipped")
            continue

        # Only keep emails that really appear on their site / the web (no made-up addresses).
        picked = [e.lower() for e in s.get("contact_emails", []) if e.lower() in emails] or emails[:2]
        try:
            score = int(float(s.get("fit_score", 0)))
        except (TypeError, ValueError):
            score = 0
        if score < args.min_save_score:
            print(f"[{i}] {cand['name']}: fit {score}/10, not saved")
            continue
        row = {
            "company": s.get("company") or cand["name"],
            "website": f"https://{dom}",
            "fit_score": score,
            "what_they_do": s.get("what_they_do", ""),
            "reason": s.get("reason", ""),
            "personal_hook": s.get("personal_hook", ""),
            "past_sponsorships": s.get("past_sponsorships", ""),
            "suggested_ask": s.get("suggested_ask", ""),
            "contact_emails": "; ".join(picked[:3]),
            "contact_role": s.get("contact_role", ""),
            "found_via": cand["found_via"],
            "status": "new",
            "notes": "",
        }
        with open(OUT, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, FIELDS, extrasaction="ignore", restval="").writerow(row)
        saved += 1
        print(f"[{i}] {row['company']}: fit {score}/10, emails: {row['contact_emails'] or '-'}"
              f"{', has sponsorship history' if row['past_sponsorships'] else ''}")

    print(f"\nDone: {saved} prospects saved. Review {OUT}, set status=approved on the ones worth contacting.")


if __name__ == "__main__":
    main()

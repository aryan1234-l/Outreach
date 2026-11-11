"""Write personalized sponsorship emails for approved prospects. Never sends anything.

Each email is written, then checked and corrected by a second "editor" pass
(prompts/review_system.md) for made-up facts, spam phrasing, length and greeting.

    python -m outreach.draft_emails                                   # status=approved rows
    python -m outreach.draft_emails --input data/society_test.csv --all   # society dry run

Output: output/drafts.csv (one row per company; set approved=yes to allow sending)
and output/drafts/<company>.txt for easy reading.
"""
import argparse
import csv
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # works as script or module
from outreach.llm import ROOT, chat_json, load_config, load_prompt, resolve, start_server

OUT_DIR = ROOT / "output"
DRAFT_FIELDS = ["company", "to", "subject", "body", "review_score", "confidence", "review_issues",
                "missing_info", "approved"]
PLACEHOLDER = re.compile(r"x{4,}|<[^>@]{2,40}>|\[[A-Za-z ]{2,20}\]")


def fill(template, cfg):
    for key, value in cfg.items():
        if isinstance(value, str):
            template = template.replace("{" + key + "}", value)
    return template


def local_checks(body, subject):
    issues = []
    words = len(body.split("Best,")[0].split())
    if words > 170:
        issues.append(f"long: {words} words")
    if PLACEHOLDER.search(body) or PLACEHOLDER.search(subject):
        issues.append("contains placeholder text")
    if "!" in subject or subject.isupper():
        issues.append("shouty subject")
    if "reply 'no'" not in body.lower():
        issues.append("missing opt-out line (sender will add it)")
    return issues


def already_sent():
    log = OUT_DIR / "sent_log.csv"
    if not log.exists():
        return set()
    with open(log, newline="", encoding="utf-8") as fh:
        return {r["company"].strip().lower() for r in csv.DictReader(fh) if r.get("mode") == "real"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(OUT_DIR / "prospects.csv"))
    ap.add_argument("--all", action="store_true", help="draft every row, not just status=approved")
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--redo", action="store_true", help="discard drafts.csv and redraft everything")
    ap.add_argument("--no-review", action="store_true", help="skip the editor pass (faster)")
    args, _ = ap.parse_known_args()

    cfg = load_config()
    brochure = (ROOT / "data" / "brochure.md").read_text(encoding="utf-8")
    brochure = re.sub(r"<!--.*?-->", "", brochure, flags=re.S).strip()
    writer_prompt = fill(load_prompt("outreach_system"), cfg)
    review_prompt = load_prompt("review_system")
    society = json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, ensure_ascii=False)

    with open(resolve(args.input), newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    # Keep existing drafts (and their approved=yes edits); only draft companies not done yet.
    drafts_csv = OUT_DIR / "drafts.csv"
    existing = []
    if drafts_csv.exists() and not args.redo:
        with open(drafts_csv, newline="", encoding="utf-8-sig") as fh:
            existing = list(csv.DictReader(fh))
    skip = {r["company"].strip().lower() for r in existing} | already_sent()

    def score_of(r):
        try:
            return int(float(r.get("fit_score") or 0))
        except ValueError:
            return 0

    todo = [
        r for r in rows
        if (args.all or r.get("status", "").strip().lower() == "approved")
        and score_of(r) >= args.min_score
        and r["company"].strip().lower() not in skip
    ]
    if not todo:
        raise SystemExit("Nothing new to draft. Set status=approved on some rows (or use --all for a test). "
                         "Already-drafted or already-emailed companies are skipped; --redo rewrites drafts.")

    start_server()
    (OUT_DIR / "drafts").mkdir(parents=True, exist_ok=True)
    out_rows = []
    for r in todo:
        prospect = {k: v for k, v in r.items() if k not in ("status", "notes", "found_via") and v}
        inputs = f"Prospect:\n{json.dumps(prospect, indent=2, ensure_ascii=False)}\n\nBrochure summary:\n{brochure}"
        try:
            d = chat_json(writer_prompt, inputs, max_tokens=900)
            subject, body = d.get("subject", ""), d.get("body", "")
            issues, review_score = [], ""
            if not args.no_review:
                rv = chat_json(
                    review_prompt,
                    f"{inputs}\n\nSociety config:\n{society}\n\nDRAFT\nSubject: {subject}\n\n{body}",
                    max_tokens=1100,
                )
                subject = rv.get("subject") or subject
                body = rv.get("body") or body
                issues = [str(i) for i in rv.get("issues", []) if i]
                review_score = rv.get("score", "")
        except RuntimeError as e:
            print(f"FAILED: {r['company']} ({e})")
            continue

        leftover = local_checks(body, subject)
        to = (r.get("contact_emails") or "").split(";")[0].strip()
        out_rows.append({
            "company": r["company"],
            "to": to,
            "subject": subject,
            "body": body,
            "review_score": review_score,
            "confidence": d.get("confidence", ""),
            "review_issues": "; ".join(["fixed: " + i for i in issues] + ["CHECK: " + i for i in leftover]),
            "missing_info": "; ".join(d.get("missing_info", []) or []),
            "approved": "no",  # a human reads the draft and changes this to "yes"
        })
        fname = re.sub(r"[^A-Za-z0-9]+", "_", r["company"]).strip("_") or "company"
        (OUT_DIR / "drafts" / f"{fname}.txt").write_text(
            f"To: {to or '(find contact)'}\nSubject: {subject}\n\n{body}\n\n"
            f"--- review score {review_score or '-'}/10; {out_rows[-1]['review_issues'] or 'no issues'}\n",
            encoding="utf-8",
        )
        flag = f"  <-- {', '.join(leftover)}" if leftover else ""
        print(f"Drafted: {r['company']} (review {review_score or '-'}/10){flag}")

    if not out_rows:
        raise SystemExit("No drafts written (all LLM calls failed). Check the LLM server log.")
    with open(drafts_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, DRAFT_FIELDS, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(existing + out_rows)
    print(f"\n{len(out_rows)} new drafts added to {drafts_csv}. Read each one, edit if needed, "
          "set approved=yes, then run outreach.send_emails.")


if __name__ == "__main__":
    main()

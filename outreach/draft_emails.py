"""Write personalized sponsorship emails for approved prospects. Never sends anything.

    python -m outreach.draft_emails                               # status=approved rows
    python -m outreach.draft_emails --input data/society_test.csv --all   # society dry run

Output: output/drafts.csv (To, Subject, Body: ready for a Gmail/Sheets mail merge)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(OUT_DIR / "prospects.csv"))
    ap.add_argument("--all", action="store_true", help="draft every row, not just status=approved")
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--redo", action="store_true", help="discard drafts.csv and redraft everything")
    args, _ = ap.parse_known_args()

    cfg = load_config()
    brochure = (ROOT / "data" / "brochure.md").read_text(encoding="utf-8")
    system = load_prompt("outreach_system")
    for key, value in cfg.items():
        if isinstance(value, str):
            system = system.replace("{" + key + "}", value)

    with open(resolve(args.input), newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    # Keep existing drafts (and their approved=yes edits); only draft companies not done yet.
    drafts_csv = OUT_DIR / "drafts.csv"
    existing = []
    if drafts_csv.exists() and not args.redo:
        with open(drafts_csv, newline="", encoding="utf-8-sig") as fh:
            existing = list(csv.DictReader(fh))
    have = {r["company"].strip().lower() for r in existing}

    todo = [
        r for r in rows
        if (args.all or r.get("status", "").strip().lower() == "approved")
        and int(float(r.get("fit_score") or 0)) >= args.min_score
        and r["company"].strip().lower() not in have
    ]
    if not todo:
        raise SystemExit("Nothing new to draft. Set status=approved on some rows (or use --all for a test). "
                         "Already-drafted companies are skipped; use --redo to rewrite all.")

    start_server()
    (OUT_DIR / "drafts").mkdir(parents=True, exist_ok=True)
    out_rows = []
    for r in todo:
        prospect = {k: v for k, v in r.items() if k not in ("status", "notes") and v}
        d = chat_json(
            system,
            f"Prospect:\n{json.dumps(prospect, indent=2, ensure_ascii=False)}\n\nBrochure summary:\n{brochure}",
            max_tokens=800,
        )
        to = r.get("contact_emails", "").split(";")[0].strip()
        out_rows.append({
            "company": r["company"],
            "to": to,
            "subject": d.get("subject", ""),
            "body": d.get("body", ""),
            "confidence": d.get("confidence", ""),
            "missing_info": "; ".join(d.get("missing_info", []) or []),
            "approved": "no",  # a human reads the draft and changes this to "yes"
        })
        fname = re.sub(r"[^A-Za-z0-9]+", "_", r["company"]).strip("_") or "company"
        (OUT_DIR / "drafts" / f"{fname}.txt").write_text(
            f"To: {to or '(find contact)'}\nSubject: {d.get('subject', '')}\n\n{d.get('body', '')}\n",
            encoding="utf-8",
        )
        print(f"Drafted: {r['company']} (confidence {d.get('confidence', '?')})")

    with open(drafts_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(out_rows[0].keys()), extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(existing + out_rows)
    print(f"\n{len(out_rows)} new drafts added to {drafts_csv}. Read each one, edit if needed, "
          "set approved=yes, then run outreach.send_emails.")


if __name__ == "__main__":
    main()

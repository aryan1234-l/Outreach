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
    args, _ = ap.parse_known_args()

    cfg = load_config()
    brochure = (ROOT / "data" / "brochure.md").read_text(encoding="utf-8")
    system = load_prompt("outreach_system")
    for key, value in cfg.items():
        if isinstance(value, str):
            system = system.replace("{" + key + "}", value)

    with open(resolve(args.input), newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    todo = [
        r for r in rows
        if (args.all or r.get("status", "").strip().lower() == "approved")
        and int(float(r.get("fit_score") or 0)) >= args.min_score
    ]
    if not todo:
        raise SystemExit("Nothing to draft. Set status=approved on some rows (or use --all for a test).")

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
        })
        fname = re.sub(r"[^A-Za-z0-9]+", "_", r["company"]).strip("_") or "company"
        (OUT_DIR / "drafts" / f"{fname}.txt").write_text(
            f"To: {to or '(find contact)'}\nSubject: {d.get('subject', '')}\n\n{d.get('body', '')}\n",
            encoding="utf-8",
        )
        print(f"Drafted: {r['company']} (confidence {d.get('confidence', '?')})")

    with open(OUT_DIR / "drafts.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n{len(out_rows)} drafts in {OUT_DIR / 'drafts.csv'}. A human reads and sends each one.")


if __name__ == "__main__":
    main()

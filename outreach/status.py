"""One-screen overview of the outreach pipeline.

    python -m outreach.status
"""
import csv
import datetime as dt
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # works as script or module
from outreach.llm import ROOT

OUT = ROOT / "output"


def rows(name):
    p = OUT / name
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main():
    prospects, drafts, log = rows("prospects.csv"), rows("drafts.csv"), rows("sent_log.csv")
    real = [r for r in log if r.get("mode") == "real"]
    now = dt.datetime.now()

    print("PROSPECTS", len(prospects))
    for status, n in Counter((r.get("status") or "new").lower() for r in prospects).most_common():
        print(f"  {status:<12}{n}")
    good = [r for r in prospects if r.get("fit_score", "").isdigit() and int(r["fit_score"]) >= 7]
    print(f"  fit >= 7    {len(good)}   with email: {sum(1 for r in prospects if r.get('contact_emails'))}")

    print("\nDRAFTS", len(drafts))
    print(f"  approved    {sum(1 for d in drafts if (d.get('approved') or '').lower() == 'yes')}")
    print(f"  need checks {sum(1 for d in drafts if 'CHECK:' in (d.get('review_issues') or ''))}")

    replied = [r for r in real if r.get("replied_at")]
    print(f"\nSENT {len(real)}  (test sends: {len(log) - len(real)})")
    print(f"  replied     {len(replied)}" + (f"  ({len(replied) * 100 // len(real)}%)" if real else ""))
    print(f"  bounced     {sum(1 for r in real if r.get('bounced_at'))}")
    print(f"  followed up {sum(1 for r in real if r.get('followup_at'))}")
    due = [r for r in real if not (r.get("replied_at") or r.get("bounced_at") or r.get("followup_at"))
           and now - dt.datetime.fromisoformat(r["sent_at"]) >= dt.timedelta(days=6)]
    print(f"  follow-up due {len(due)}")
    last24 = sum(1 for r in real for c in ("sent_at", "followup_at")
                 if r.get(c) and now - dt.datetime.fromisoformat(r[c]) < dt.timedelta(hours=24))
    print(f"  sent in last 24h {last24} / 30")
    if replied:
        print("\nREPLIES TO HANDLE:")
        for r in replied:
            print(f"  {r['company']} <{r['to']}> replied {r['replied_at'][:10]}")


if __name__ == "__main__":
    main()

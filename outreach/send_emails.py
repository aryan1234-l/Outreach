"""Send approved drafts through Gmail SMTP, slowly and only once per company.

    python -m outreach.send_emails                       # preview only (default)
    python -m outreach.send_emails --test-to you@x.com --send   # send all to yourself
    python -m outreach.send_emails --send                # real send

Needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD (env vars or Kaggle Secrets).
App password: Google Account -> Security -> 2-Step Verification on -> App passwords.

Safety rules (see README "How we deal with spam"):
  * only rows with approved=yes in output/drafts.csv
  * never the same company/address twice (output/sent_log.csv)
  * at most --daily-cap emails per 24h, 30-90 s random gap between emails
  * one recipient per email, plain text, brochure as a link (no attachments)
"""
import argparse
import csv
import datetime as dt
import os
import pathlib
import random
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # works as script or module
from outreach.llm import ROOT, load_config, resolve

OUT_DIR = ROOT / "output"
LOG = OUT_DIR / "sent_log.csv"
LOG_FIELDS = ["sent_at", "company", "to", "subject", "message_id", "mode"]
EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
OPT_OUT = "If this isn't relevant for you, just reply 'no' and we won't follow up."


def secret(name):
    if os.environ.get(name):
        return os.environ[name]
    try:
        from kaggle_secrets import UserSecretsClient

        return UserSecretsClient().get_secret(name)
    except Exception:
        return None


def read_log():
    if not LOG.exists():
        return []
    with open(LOG, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_log(row):
    new = not LOG.exists()
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def build_message(sender, sender_name, to, subject, body):
    if "reply 'no'" not in body.lower():
        body = body.rstrip() + "\n\n" + OPT_OUT
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[1])
    msg.set_content(body)
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(OUT_DIR / "drafts.csv"))
    ap.add_argument("--send", action="store_true", help="actually send (default is preview only)")
    ap.add_argument("--test-to", help="send every email to this address instead (for testing)")
    ap.add_argument("--limit", type=int, default=20, help="max emails this run")
    ap.add_argument("--daily-cap", type=int, default=30, help="max real emails in any 24h")
    ap.add_argument("--min-gap", type=int, default=30)
    ap.add_argument("--max-gap", type=int, default=90)
    args, _ = ap.parse_known_args()

    cfg = load_config()
    sender_name = f"{cfg.get('sender_name', '')} | {cfg.get('society_name', '')}".strip(" |")

    with open(resolve(args.input), newline="", encoding="utf-8-sig") as fh:
        drafts = list(csv.DictReader(fh))

    log = read_log()
    real_log = [r for r in log if r["mode"] == "real"]
    done = {(r["to"].lower()) for r in real_log} | {r["company"].lower() for r in real_log}
    now = dt.datetime.now()
    sent_24h = sum(1 for r in real_log if now - dt.datetime.fromisoformat(r["sent_at"]) < dt.timedelta(hours=24))
    room = args.limit if args.test_to else min(args.limit, args.daily_cap - sent_24h)

    queue, skipped = [], []
    for d in drafts:
        to = (d.get("to") or "").strip()
        why = None
        if (d.get("approved") or "").strip().lower() not in ("yes", "y", "true", "1"):
            why = "not approved"
        elif not d.get("subject") or not d.get("body"):
            why = "empty subject/body"
        elif not args.test_to and not EMAIL_OK.match(to):
            why = f"bad/missing address '{to}'"
        elif not args.test_to and (to.lower() in done or d["company"].lower() in done):
            why = "already contacted"
        if why:
            skipped.append((d.get("company", "?"), why))
        else:
            queue.append(d)

    for company, why in skipped:
        print(f"skip  {company}: {why}")
    if room <= 0:
        sys.exit(f"Daily cap reached ({sent_24h} sent in last 24h). Try again tomorrow.")
    queue = queue[:room]
    if not queue:
        sys.exit("Nothing to send. Set approved=yes in output/drafts.csv for drafts you've read.")

    mode = "test" if args.test_to else "real"
    print(f"\n{len(queue)} email(s) to send ({mode}); {sent_24h} real sent in last 24h.\n")
    for d in queue:
        to = args.test_to or d["to"].strip()
        print(f"--- To: {to}  ({d['company']})\nSubject: {d['subject']}\n\n{d['body']}\n")

    if not args.send:
        print("Preview only. Rerun with --send to send these.")
        return

    sender, password = secret("GMAIL_ADDRESS"), secret("GMAIL_APP_PASSWORD")
    if not sender or not password:
        sys.exit("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD (Kaggle: Add-ons -> Secrets).")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password.replace(" ", ""))
        for i, d in enumerate(queue):
            to = args.test_to or d["to"].strip()
            subject = ("[TEST] " if args.test_to else "") + d["subject"]
            msg = build_message(sender, sender_name, to, subject, d["body"])
            smtp.send_message(msg)
            append_log({
                "sent_at": dt.datetime.now().isoformat(timespec="seconds"),
                "company": d["company"], "to": to, "subject": subject,
                "message_id": msg["Message-ID"], "mode": mode,
            })
            print(f"sent  {d['company']} -> {to}")
            if i < len(queue) - 1:
                gap = random.randint(args.min_gap, args.max_gap)
                print(f"      waiting {gap}s")
                time.sleep(gap)
    print(f"\nDone. Log: {LOG}")


if __name__ == "__main__":
    main()

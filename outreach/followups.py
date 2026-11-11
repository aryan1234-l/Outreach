"""Check Gmail for replies/bounces, then send ONE polite follow-up to companies that went quiet.

    python -m outreach.followups                 # check replies + preview follow-ups due
    python -m outreach.followups --send          # also send them

Uses the same GMAIL_ADDRESS / GMAIL_APP_PASSWORD as send_emails (IMAP to read, SMTP to send).
Follow-ups are threaded as a reply to the first email ("Re: <subject>") and quote it.
Rules: only real sends, no reply, no bounce, no earlier follow-up, first email >= --after-days old,
shared daily cap with send_emails, random gap between sends.
"""
import argparse
import csv
import datetime as dt
import imaplib
import pathlib
import random
import smtplib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # works as script or module
from outreach.llm import load_config
from outreach.send_emails import (
    OUT_DIR, build_message, read_log, secret, sender_identity, sends_last_24h, write_log,
)

PUBLIC_MAIL = ("gmail.com", "yahoo.", "outlook.", "hotmail.", "live.", "icloud.com", "proton")

FOLLOWUP = """Hi {company} team,

Just following up on my note below about {event} at {college}. I know inboxes get busy, so even a quick "yes", "no", or "not this time" would really help us plan.

Happy to share the brochure or set up a short call whenever suits you.

Best,
{sender_name}
{sender_role}
{society_name}"""


def imap_since(iso):
    return dt.datetime.fromisoformat(iso).strftime("%d-%b-%Y")


def open_all_mail(imap):
    for box in ('"[Gmail]/All Mail"', '"[Google Mail]/All Mail"', "INBOX"):
        if imap.select(box, readonly=True)[0] == "OK":
            return box
    raise RuntimeError("Could not open Gmail mailbox")


def check_replies(imap, log):
    """Mark replied_at / bounced_at in the log. Returns number of changes."""
    changed = 0
    for r in log:
        if r.get("mode") != "real" or r.get("replied_at") or r.get("bounced_at"):
            continue
        since = imap_since(r["sent_at"])
        addr = r["to"].lower()
        dom = addr.split("@")[-1]
        # A reply may come from a colleague, so match the whole company domain (not for gmail etc.).
        sender = addr if any(p in dom for p in PUBLIC_MAIL) else "@" + dom
        typ, data = imap.search(None, "FROM", f'"{sender}"', "SINCE", since)
        if typ == "OK" and data[0].split():
            r["replied_at"] = dt.datetime.now().isoformat(timespec="seconds")
            print(f"reply   {r['company']} ({sender})")
            changed += 1
            continue
        typ, data = imap.search(None, "FROM", '"mailer-daemon"', "SINCE", since, "BODY", f'"{addr}"')
        if typ == "OK" and data[0].split():
            r["bounced_at"] = dt.datetime.now().isoformat(timespec="seconds")
            print(f"bounce  {r['company']} ({addr}): fix the address before contacting again")
            changed += 1
    return changed


def original_body(company):
    drafts = OUT_DIR / "drafts.csv"
    if drafts.exists():
        with open(drafts, newline="", encoding="utf-8-sig") as fh:
            for d in csv.DictReader(fh):
                if d["company"].strip().lower() == company.strip().lower():
                    return d.get("body", "")
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="send follow-ups (default: preview only)")
    ap.add_argument("--after-days", type=int, default=6, help="wait this long after the first email")
    ap.add_argument("--skip-check", action="store_true", help="don't check Gmail for replies first")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--daily-cap", type=int, default=30)
    ap.add_argument("--min-gap", type=int, default=30)
    ap.add_argument("--max-gap", type=int, default=90)
    args, _ = ap.parse_known_args()

    log = read_log()
    if not any(r.get("mode") == "real" for r in log):
        sys.exit("No real emails in output/sent_log.csv yet (or the log from the last session wasn't restored).")

    sender, password = secret("GMAIL_ADDRESS"), secret("GMAIL_APP_PASSWORD")
    if not args.skip_check:
        if not sender or not password:
            sys.exit("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD to check replies (or use --skip-check).")
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(sender, password.replace(" ", ""))
        open_all_mail(imap)
        n = check_replies(imap, log)
        imap.logout()
        write_log(log)
        print(f"Checked replies: {n} update(s).\n")

    cfg = load_config()
    now = dt.datetime.now()
    due = [
        r for r in log
        if r.get("mode") == "real" and not r.get("replied_at") and not r.get("bounced_at")
        and not r.get("followup_at")
        and now - dt.datetime.fromisoformat(r["sent_at"]) >= dt.timedelta(days=args.after_days)
    ]
    replied = sum(1 for r in log if r.get("replied_at"))
    sent = sum(1 for r in log if r.get("mode") == "real")
    print(f"Sent {sent}, replied {replied} ({replied * 100 // max(sent, 1)}%), follow-ups due: {len(due)}")

    room = min(args.limit, args.daily_cap - sends_last_24h(log))
    if not due:
        return
    if room <= 0:
        sys.exit("Daily cap reached. Try again tomorrow.")
    due = due[:room]

    msgs = []
    for r in due:
        body = FOLLOWUP.format(
            company=r["company"], event=cfg.get("upcoming_event", "our event"), college=cfg.get("college", ""),
            sender_name=cfg.get("sender_name", ""), sender_role=cfg.get("sender_role", ""),
            society_name=cfg.get("society_name", ""),
        )
        orig = original_body(r["company"])
        if orig:
            sent_on = dt.datetime.fromisoformat(r["sent_at"]).strftime("%a, %d %b %Y")
            body += f"\n\nOn {sent_on}, {cfg.get('sender_name', '')} wrote:\n" + "\n".join(
                "> " + line for line in orig.splitlines())
        subject = r["subject"] if r["subject"].lower().startswith("re:") else "Re: " + r["subject"]
        msgs.append((r, subject, body))
        print(f"--- Follow-up to {r['to']} ({r['company']}), first email {r['sent_at'][:10]}\n"
              f"Subject: {subject}\n\n{body[:600]}\n")

    if not args.send:
        print("Preview only. Rerun with --send to send these follow-ups.")
        return
    if not sender or not password:
        sys.exit("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password.replace(" ", ""))
        for i, (r, subject, body) in enumerate(msgs):
            msg = build_message(sender, sender_identity(cfg), r["to"], subject, body,
                                in_reply_to=r.get("message_id"))
            try:
                smtp.send_message(msg, from_addr=sender, to_addrs=[r["to"]])
            except smtplib.SMTPRecipientsRefused as e:
                print(f"FAIL  {r['company']}: {e.recipients}")
                continue
            r["followup_at"] = dt.datetime.now().isoformat(timespec="seconds")
            write_log(log)  # save after every send
            print(f"sent  follow-up -> {r['company']}")
            if i < len(msgs) - 1:
                time.sleep(random.randint(args.min_gap, args.max_gap))


if __name__ == "__main__":
    main()

# Outreach: sponsor finder, email drafter and Gmail sender

Built for GDG On Campus NIT Hamirpur. It starts from the sheet of past sponsors, finds **new** companies
likely to sponsor us, writes a personalized email for each one, and sends the approved ones through the
society's Gmail, slowly and only once per company.

Everything is free:
- **LLM:** Qwen3.6-35B-A3B running on Kaggle's free GPUs (2x T4) through llama.cpp
- **Search:** DuckDuckGo and other engines through the `ddgs` Python library (no API key)
- **Sending:** the society's Gmail account over SMTP with an app password

**Nothing goes out without a person approving it.** Someone approves each prospect, then reads and
approves each email. Only then can `send_emails` send it.

---

## Contents

1. [How it works](#how-it-works)
2. [Repo layout](#repo-layout)
3. [One-time setup](#one-time-setup)
4. [Fill in the inputs](#fill-in-the-inputs)
5. [Run it on Kaggle](#run-it-on-kaggle)
6. [Step-by-step usage](#step-by-step-usage)
7. [Output files explained](#output-files-explained)
8. [How we deal with spam](#how-we-deal-with-spam)
9. [Tuning the prompts](#tuning-the-prompts)
10. [Troubleshooting](#troubleshooting)
11. [Limitations](#limitations)
12. [Who does what](#who-does-what)

---

## How it works

```
 data/past_sponsors.csv
          │
          ▼
 ┌──────────────────────────── find_sponsors ─────────────────────────────┐
 │ 1. LLM reads past sponsors + config.json, writes search queries          │
 │ 2. Each query is searched on the web (ddgs)                              │
 │ 3. Each result page is fetched; LLM lists companies on it                │
 │ 4. Companies/domains already in past sponsors or earlier runs dropped    │
 │ 5. Each company's site is read: home + sponsor/partner/campus/contact    │
 │ 6. Contacts: emails on their site; if none, public @theirdomain emails   │
 │    found on the web. Ranked: sponsorship > campus > marketing > hr > info│
 │ 7. Evidence: web search for hackathons/fests they sponsored before       │
 │ 8. LLM scores fit 1-10, writes reason + a specific opening hook          │
 └───────────────────────────────────────────────────────────────────────────┘
          │
          ▼
 output/prospects.csv  ──►  HUMAN sets status = approved on good rows
          │
          ▼
 ┌──────────── draft_emails ─────────────┐
 │ writer: prospect + brochure → email    │──►  output/drafts.csv, output/drafts/*.txt
 │ editor: checks facts, spam words,      │     (review_score, review_issues per draft)
 │ length, greeting, opt-out → fixes it   │
 └────────────────────────────────────────┘
          │
          ▼
 HUMAN reads every draft, edits it, sets approved = yes
          │
          ▼
 ┌──────── send_emails ─────────┐
 │ preview by default            │──►  Gmail (max 30 per 24h, 30-90 s gaps)
 │ --send to actually send       │──►  output/sent_log.csv (never email twice)
 └──────────────────────────────┘
          │  6+ days later
          ▼
 ┌──────── followups ───────────────────────┐
 │ reads Gmail (IMAP): marks replies/bounces │
 │ ONE follow-up, threaded as "Re:", only to │──►  Gmail
 │ companies that haven't replied            │
 └───────────────────────────────────────────┘

 status  ──►  one-screen overview: found / approved / sent / replied / follow-ups due
```

Every LLM step has its own system prompt in `prompts/`, so the team can tune behaviour without touching
code.

---

## Repo layout

| Path | What it is |
|---|---|
| `config.json` | Society details (name, college, audience, sender). Used in every prompt. |
| `data/brochure.md` | Short summary of the Design Team's brochure: event, reach, tiers, link. |
| `data/past_sponsors.csv` | Previous sponsors sheet. **Not committed** (has contact details). |
| `data/past_sponsors.example.csv` | Example of the expected columns. |
| `data/society_test.csv` | Fake prospects made from our own members, for the end-to-end test. |
| `prompts/queries_system.md` | Prompt: past sponsors → search queries. |
| `prompts/extract_system.md` | Prompt: web page → list of companies on it. |
| `prompts/score_system.md` | Prompt: company website → fit score, reason, contact. |
| `prompts/outreach_system.md` | Prompt: prospect + brochure → email (the main system prompt). |
| `prompts/review_system.md` | Prompt: editor pass that checks and fixes every draft. |
| `outreach/llm.py` | Downloads and starts Qwen on Kaggle; `chat_json()` helper used by all steps. |
| `outreach/web.py` | Web search, page fetching, public email extraction. |
| `outreach/find_sponsors.py` | Step 1: find and score new prospects. |
| `outreach/draft_emails.py` | Step 2: write email drafts for approved prospects. |
| `outreach/send_emails.py` | Step 3: send approved drafts through Gmail. |
| `outreach/followups.py` | Step 4: detect replies/bounces in Gmail, send one threaded follow-up. |
| `outreach/status.py` | Overview of the whole pipeline. |
| `kaggle/run_e2e.py` | One script that runs the whole pipeline on Kaggle (test mode, no sending). |
| `output/` | Everything the tools produce. **Not committed.** |

---

## One-time setup

### 1. Kaggle account
- Sign up at kaggle.com and **verify your phone number** (required for GPU and internet).
- You get about 30 free GPU hours a week. One full run uses well under an hour.

### 2. Kaggle notebook
- **Create → New Notebook**.
- Right panel → **Session options**: Accelerator **GPU T4 x2**, Internet **On**.

### 3. Gmail app password (only needed for real sending)
Use the **society's** Gmail account, not a personal one.
1. myaccount.google.com → **Security** → turn on **2-Step Verification**.
2. Search **"App passwords"** in account settings → create one called "Outreach" → copy the
   16-character code.
3. In the Kaggle notebook: **Add-ons → Secrets → Add secret**:
   - `GMAIL_ADDRESS` = the society Gmail address
   - `GMAIL_APP_PASSWORD` = the 16-character code
4. Switch both secrets **on** for this notebook.

**Never put the password in the repo, a notebook cell, or a chat.** If it leaks, delete it in Google
account settings and create a new one.

---

## Fill in the inputs

### `config.json`
These go into every prompt and into the email signature, so write them exactly as you want them to
appear in the emails.

```json
{
  "society_name": "GDG On Campus NIT Hamirpur",
  "college": "National Institute of Technology Hamirpur",
  "city": "Hamirpur, Himachal Pradesh",
  "about": "Student developer community running workshops, hackathons and study jams.",
  "audience": "500+ members, 300+ per event",
  "tech_areas": "Android, Web, Cloud, AI/ML, Blockchain",
  "upcoming_event": "Labyrinth 2026, <dates>, ~500 participants",
  "sender_name": "<Your full name>",
  "sender_role": "Outreach Team, GDG On Campus NIT Hamirpur"
}
```

Use real numbers only. The model copies them straight into the emails.

### `data/brochure.md`
Summary of the Design Team's brochure. Keep it short; the model reads it for every email.

```markdown
Brochure link: https://drive.google.com/file/d/<real id>/view

Event: Labyrinth 2026, <dates>, <venue>
Reach: 500 participants, 1,200+ community members, <followers>
Sponsorship tiers:
- Title: ₹<amount> - <benefits>
- Gold: ₹<amount> - <benefits>
- Silver / In-kind: <amount or swag/credits> - <benefits>
What sponsors get: <logo placement, booth, talk slot, resume access, social posts>
```

Make the Drive link viewable by "Anyone with the link".

### `data/past_sponsors.csv`
Export the old sponsors sheet as CSV. Only `company` is required; the other columns help the model
understand what kind of companies to look for.

```csv
company,industry,what_they_gave,year,notes
Devfolio,Hackathon platform,Platform + prizes,2025,Hosted our hackathon
Postman,Developer tools,Swag + API credits,2025,Wanted developer adoption
```

If the sheet lists people, put the company they work at in `company` and their name/role in `notes`.
This file is git-ignored. On Kaggle, upload it as a **private dataset** or write it in a cell (see below).

### `data/society_test.csv`
Pretend some of our own members are companies, with **their own email** as the contact, so the test emails
go to us. Get their OK first. Keep `fit_score` between 1 and 10, and use `status` = `approved`.

```csv
company,website,what_they_do,fit_score,reason,suggested_ask,contact_emails,contact_role,status
Rijul Tech Labs,https://example.com,Android developer tools,8,Hires student Android interns,Gold sponsorship,member1@gmail.com,Founder,approved
Raman Cloud Co,https://example.com,Cloud hosting,4,Tech company,Swag,member2@gmail.com,,approved
```

Add one deliberately vague row (like the second one): its draft should come back with **low
confidence**. That shows the safety check works.

---

## Run it on Kaggle

### Option A: one-click end-to-end test
Paste into one cell:

```python
!wget -q https://raw.githubusercontent.com/aryan1234-l/Outreach/main/kaggle/run_e2e.py -O run_e2e.py
!python run_e2e.py
```

This clones the repo, starts Qwen, drafts the society test emails, previews sending (nothing is sent),
and runs a small sponsor search. Results are in `/kaggle/working/Outreach/output/`.

### Option B: step by step (normal use)

```python
# 1. Get the code (first time) or update it (later)
!git clone https://github.com/aryan1234-l/Outreach.git || (cd Outreach && git pull)
%cd /kaggle/working/Outreach
!pip -q install -r requirements.txt
```

```python
# 2. Past sponsors: copy from a Kaggle dataset...
!cp /kaggle/input/<your-dataset>/past_sponsors.csv data/past_sponsors.csv
```
```python
# ...or write it here
%%writefile data/past_sponsors.csv
company,industry,what_they_gave,year,notes
Devfolio,Hackathon platform,Platform + prizes,2025,Hosted our hackathon
```

```python
# 3. Start the LLM (first run downloads ~17 GB, takes 2-3 minutes)
!python -m outreach.llm
```

Then follow [Step-by-step usage](#step-by-step-usage).

The model server keeps running in the background for the rest of the session; every script reuses it.
When the Kaggle session ends, everything in `/kaggle/working` is deleted. **Download `output/` before
closing**, especially `sent_log.csv`.

---

## Step-by-step usage

### Step 0: society test (do this first, every time prompts change)
```python
!python -m outreach.draft_emails --input data/society_test.csv --all --redo
!cat output/drafts/*.txt
```
Check: only facts from the inputs, correct greeting, correct signature, real brochure link, vague row
gets low confidence. Then send the test emails **to yourself**:
```python
import pandas as pd
p = "output/drafts.csv"; df = pd.read_csv(p); df["approved"] = "yes"; df.to_csv(p, index=False)
!python -m outreach.send_emails --test-to yourname@gmail.com --send
```
Check how they look in the inbox (and that they didn't land in spam). Then clear the test drafts:
`!rm -rf output/drafts output/drafts.csv`

### Step 1: find sponsors
```python
!python -m outreach.find_sponsors --max-candidates 30
```

| Option | Default | Meaning |
|---|---|---|
| `--past` | `data/past_sponsors.csv` | Past sponsors sheet |
| `--queries` | 12 | How many search queries the LLM writes |
| `--results-per-query` | 5 | Search results read per query |
| `--max-candidates` | 30 | Max new companies researched this run |

It takes roughly 1-2 minutes per company. Rows are appended to `output/prospects.csv` as it goes, so you
can stop it any time. Rerunning skips companies already found.

Each company gets: its website plus up to 4 of its sponsor/partner/campus/contact pages read, public
contact emails (from its site, or `@theirdomain` addresses found on the web), and a search for
hackathons/fests it sponsored before. Rows scoring below `--min-save-score` (default 1) are not saved.

### Step 2: review prospects
Download `output/prospects.csv`, open it in Google Sheets, and sort by `fit_score`. For each row:
- Read `reason` and `personal_hook`. Do they make sense? Is the company real and active?
- `past_sponsorships` filled in = strongest leads. Contact these first.
- Set `status` to `approved` for the good ones (or `rejected`).
- If `contact_emails` is empty, find a public contact (company site, "Contact us" page) or note the
  `contact_role` to find on LinkedIn **manually**.

Upload it back to `output/prospects.csv` (Kaggle file browser → upload).

### Step 3: draft emails
```python
!python -m outreach.draft_emails --min-score 6
!cat output/drafts/*.txt
```

| Option | Meaning |
|---|---|
| `--input` | Prospects file (default `output/prospects.csv`) |
| `--all` | Draft every row, ignoring `status` (testing only) |
| `--min-score N` | Only rows with `fit_score >= N` |
| `--redo` | Throw away `drafts.csv` and redraft everything |
| `--no-review` | Skip the editor pass (faster, lower quality) |

Every draft goes through two LLM passes: the **writer** (`outreach_system.md`) and the **editor**
(`review_system.md`), which checks every fact against the inputs, the greeting, length, spam words, the
opt-out line and placeholders, and rewrites the email if needed. A final code check flags anything left
over as `CHECK: ...` in `review_issues`.

Rerunning only drafts new companies; existing drafts, your edits, and companies already emailed are
skipped.

### Step 4: approve drafts
Open `output/drafts.csv`. For each row:
- Read the `subject` and `body`. Would you reply to this?
- Look at `review_issues`: `fixed: ...` = the editor already corrected it; `CHECK: ...` = you must fix
  it.
- Fill anything in `missing_info` you can, or remove that part.
- Set `approved` to `yes`.

Drafts with `review_score` below 7 or low `confidence` usually need a rewrite or should be dropped.

### Step 5: send
```python
!python -m outreach.send_emails            # preview: prints exactly what would go out
!python -m outreach.send_emails --send     # real send
```

| Option | Default | Meaning |
|---|---|---|
| `--send` | off | Actually send. Without it, nothing is sent. |
| `--test-to ADDR` | - | Send every email to ADDR instead (subject gets `[TEST]`) |
| `--limit` | 20 | Max emails this run |
| `--daily-cap` | 30 | Max real emails in any 24 hours |
| `--min-gap` / `--max-gap` | 30 / 90 | Random wait between emails, in seconds |

The sender cleans each address (removes invisible characters, `mailto:`, `Name <...>`), skips rows that
aren't approved, have no valid address, or were already contacted, and logs every send.

### Step 6: replies and follow-ups (run every few days)
```python
!python -m outreach.followups            # checks Gmail for replies/bounces, previews follow-ups due
!python -m outreach.followups --send     # sends them
```

It logs into the society Gmail over IMAP (same app password) and:
- marks a company **replied** if any email arrived from their domain (or their exact address, for
  gmail/outlook addresses) after we wrote. Replied companies never get a follow-up.
- marks **bounced** if Gmail returned a delivery failure for that address. Fix the address by hand.
- sends **one** follow-up to companies that haven't replied 6+ days after the first email
  (`--after-days`). It's short, polite, threaded as `Re: <original subject>`, and quotes the first email.
  There is never a second follow-up.

| Option | Default | Meaning |
|---|---|---|
| `--send` | off | Send the follow-ups (otherwise preview) |
| `--after-days` | 6 | Days to wait after the first email |
| `--skip-check` | off | Don't read Gmail first (not recommended) |
| `--limit` / `--daily-cap` | 15 / 30 | Same caps as sending; first emails + follow-ups share the daily cap |

Then handle replies personally from the Gmail inbox, and update `status` in your prospects sheet:
`replied`, `declined`, `meeting`, `sponsored`.

### Step 7: status
```python
!python -m outreach.status
```
Shows prospects by status, how many have emails, approved drafts, drafts needing checks, sent, reply
rate, bounces, follow-ups due, sends in the last 24 h, and the list of replies to handle.

---

## Output files explained

| File | Columns | Notes |
|---|---|---|
| `output/prospects.csv` | company, website, fit_score, what_they_do, reason, personal_hook, past_sponsorships, suggested_ask, contact_emails, contact_role, found_via, status, notes | `found_via` = page where we found them. `contact_emails` only holds addresses actually found on their site or the web (best first), never guessed. |
| `output/drafts.csv` | company, to, subject, body, review_score, confidence, review_issues, missing_info, approved | Edit `body`/`subject` freely before approving. |
| `output/drafts/<company>.txt` | - | Same drafts, easy to read, with the review notes at the bottom. |
| `output/sent_log.csv` | sent_at, company, to, subject, message_id, mode, replied_at, bounced_at, followup_at | `mode` = `real` or `test`. **Keep this file between sessions**: it stops double-sending and drives follow-ups. |
| `/kaggle/working/server.log` | - | llama.cpp log, for debugging. |

---

## How we deal with spam

The goal: nobody we email could reasonably call it spam, and the society's Gmail never gets flagged.

**People in the loop**
- A person approves each prospect, then reads and approves each email. Nothing is sent unless
  `approved=yes`.
- `send_emails` previews by default; sending needs an explicit `--send`.

**Who we contact**
- Only **public business contacts** found on the company's own website (partnerships@, campus@,
  careers@, marketing@...). The scorer can only pick addresses that actually appear on the site, so it
  can't make one up.
- No scraped personal emails, no LinkedIn scraping, no LinkedIn bots. LinkedIn messages are sent by hand.
- Never re-contact anyone who said no.

**How much**
- Max 20 per run and 30 per 24 hours by default, with a random 30-90 s gap. Bursts of similar emails are
  what get Gmail accounts flagged.
- One email per company, plus at most one follow-up after 6+ days, and only if they haven't replied
  (checked automatically in Gmail). `sent_log.csv` blocks re-sending to the same company or address.
- Bounced addresses are flagged and never retried automatically. Repeated bounces hurt the account's
  reputation.
- Spread big campaigns over several days, or several team members' society accounts.

**What the email looks like**
- Personalized: the first sentence is a specific reason this company fits us. Low-confidence drafts get
  rewritten or dropped.
- One recipient per email: no CC/BCC lists.
- Plain text, no attachments. The brochure is a Drive link.
- Every email ends with "If this isn't relevant for you, just reply 'no' and we won't follow up." The
  sender adds it if a draft is missing it.
- Honest: prompts forbid made-up facts, but they are only as accurate as `config.json` and
  `brochure.md`.

**If something goes wrong**
- Gmail warns about unusual activity → stop sending for 24-48 hours, lower `--daily-cap`.
- Someone complains → apologize, mark them `declined`, never contact again.
- App password leaked → delete it in Google account settings immediately and make a new one.

---

## Tuning the prompts

All behaviour lives in `prompts/`. Edit, then rerun the society test (Step 0) to check.

| Want to change... | Edit |
|---|---|
| Tone, length, structure, signature of emails | `prompts/outreach_system.md` |
| What the editor checks / fixes in each draft | `prompts/review_system.md` |
| Follow-up wording | `FOLLOWUP` text at the top of `outreach/followups.py` |
| What kinds of companies we look for | `prompts/queries_system.md` |
| What counts as a company on a page | `prompts/extract_system.md` |
| How strict the fit score is, preferred contacts | `prompts/score_system.md` |

`{society_name}`, `{sender_name}` etc. in `outreach_system.md` are filled from `config.json`. Every
prompt must keep asking for **JSON only**: the scripts parse it.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ImportError: attempted relative import` | Pull the latest code (`git pull`). Both `python -m outreach.x` and `python Outreach/outreach/x.py` work now. |
| `No LLM server running` | Run `!python -m outreach.llm` first, on a Kaggle notebook with GPU T4 x2. |
| `llama-server died` | Check accelerator is **T4 x2** (not P100 / single GPU) and read the printed log. |
| `Nothing new to draft` | Those companies already have drafts. Use `--redo` to rewrite, or approve more prospects. |
| `Nothing to send` | Set `approved` to `yes` in `output/drafts.csv`. |
| `skip ... bad/missing address` | Put a real email in that row's `to` cell. |
| `SMTPAuthenticationError` | Wrong app password, 2-Step Verification off, or secrets not switched on for the notebook. |
| `Daily cap reached` | Working as intended. Wait, or (carefully) raise `--daily-cap`. |
| `followups`: `No real emails in sent_log.csv` | Upload the `sent_log.csv` you downloaded last session to `output/`. |
| IMAP login fails | Same app password as SMTP; check the secrets are switched on. If Gmail says IMAP is disabled, enable it in Gmail settings → Forwarding and POP/IMAP. |
| Many `CHECK:` issues in drafts | Usually missing info in `config.json` / `brochure.md` (dates, real link, tiers). |
| Search returns nothing / errors | DuckDuckGo rate-limits bursts. Wait a few minutes, use fewer `--queries`. |
| Few prospects found | Raise `--queries` / `--results-per-query`, improve `past_sponsors.csv` details. |
| Emails mention wrong facts | Fix `config.json` / `brochure.md`; tighten `outreach_system.md`. |

---

## Limitations

- Big companies rarely publish an email, so their `contact_emails` is often empty. Use `contact_role`
  and find the right person manually.
- Search quality depends on DuckDuckGo results and on how detailed `past_sponsors.csv` is.
- Some websites block automated fetching or need JavaScript; those companies are skipped.
- Kaggle deletes `/kaggle/working` after each session: download `output/` every time.
- The llama.cpp binary comes from a community build on Hugging Face (`afsafwtggg/llama-cpp-t4-cache`),
  not the official release.

**Using a different LLM:** any OpenAI-compatible server works (Ollama, LM Studio, Groq, Gemini's
OpenAI endpoint). Set `LLM_BASE_URL` (e.g. `http://localhost:11434/v1`) and `LLM_API_KEY`, and the Kaggle
setup is skipped.

---

## Who does what

| Area | Files | Owner |
|---|---|---|
| Sponsor search quality | `prompts/queries_system.md`, `prompts/extract_system.md`, `outreach/web.py` | |
| Fit scoring | `prompts/score_system.md` | |
| Email quality | `prompts/outreach_system.md`, `data/brochure.md` | Design Team (brochure), Media & Marketing (format) |
| Data | `data/past_sponsors.csv`, `data/society_test.csv` | |
| Sending + tracking | `outreach/send_emails.py`, `output/sent_log.csv` | |
| Docs | `README.md` | |

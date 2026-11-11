# Outreach: sponsor finder + email drafter

Finds companies likely to sponsor our events, starting from our past sponsors, and drafts a
personalized email for each one. Everything is free: Qwen 35B runs on Kaggle's free 2x T4 GPUs and
search goes through DuckDuckGo (`ddgs`).

**Nothing goes out without a person approving it.** Someone approves each prospect, then reads and
approves each email, and only then does `send_emails` send it through the society's Gmail, slowly and once
per company.

```
past_sponsors.csv ──► find_sponsors ──► output/prospects.csv ──► (human sets status=approved)
                         │  LLM writes search queries                       │
                         │  web search + read result pages                  ▼
                         │  LLM extracts companies, skips ones we know   draft_emails ──► output/drafts.csv
                         │  reads each company's own site                    (+ brochure)    output/drafts/*.txt
                         └  LLM scores fit 1-10, picks public contact email                       │
                                                                                                    ▼
                                                         human sets approved=yes ─► send_emails ─► Gmail
                                                                                    (max 30/day, once per company)
```

## Repo layout

| Path | What |
|---|---|
| `config.json` | Society details used in every prompt. **Fill this in first.** |
| `data/brochure.md` | Summary of the Design Team's brochure (tiers, reach, link) |
| `data/past_sponsors.csv` | Previous sponsors sheet (not committed, copy from `past_sponsors.example.csv`) |
| `data/society_test.csv` | Fake prospects made from our own members, for the end-to-end test |
| `prompts/` | All system prompts: `queries`, `extract`, `score`, `outreach` |
| `outreach/llm.py` | Starts Qwen on Kaggle with llama.cpp, `chat_json()` helper |
| `outreach/web.py` | Search, page fetch, public email extraction |
| `outreach/find_sponsors.py` | Step 1: past sponsors → new prospects |
| `outreach/draft_emails.py` | Step 2: approved prospects → email drafts |
| `outreach/send_emails.py` | Step 3: approved drafts → Gmail (rate-limited, logged, once per company) |

## Setup (Kaggle, free)

1. New Kaggle notebook → Settings: **Accelerator: GPU T4 x2**, **Internet: on**.
   (Internet needs a phone-verified Kaggle account.)
2. Run these cells:

```python
!git clone https://github.com/aryan1234-l/Outreach.git
%cd Outreach
!pip -q install -r requirements.txt
```

```python
# Upload the past sponsors sheet as a Kaggle dataset, or write it here.
# Needs a "company" column; any other columns (industry, what they gave, notes) help the model.
!cp /kaggle/input/<your-dataset>/past_sponsors.csv data/past_sponsors.csv
```

```python
!python -m outreach.llm          # first run downloads the 17 GB model (~1 min) and starts the server
```

3. Edit `config.json` and `data/brochure.md` (in the notebook file browser, or commit them).

## Run

**End-to-end test on our society first.** Put 2-3 of our members (with their OK) into
`data/society_test.csv` as fake companies, with **their own email** as the contact. Then:

```python
!python -m outreach.draft_emails --input data/society_test.csv --all
!cat output/drafts/*.txt
```

Check that the emails only use facts from the inputs, sound right, and that the low-information row gets
low confidence. Tweak `prompts/outreach_system.md` until they're good.

**Find real sponsors:**

```python
!python -m outreach.find_sponsors --max-candidates 30
```

This takes a while (it reads every page with the LLM). It writes rows to `output/prospects.csv` as it
goes, and skips companies already in the past sponsors sheet or in earlier runs, so you can stop and
rerun. Download the CSV, open it in Google Sheets, and set `status` to `approved` for the good ones
(and fix contacts by hand if needed). Put it back at `output/prospects.csv`, then:

```python
!python -m outreach.draft_emails --min-score 6
```

Rerunning only drafts new companies; existing drafts (and your edits) are kept. `--redo` rewrites all.

**Send through Gmail:**

One-time setup on the society Gmail account: Google Account → Security → turn on 2-Step Verification →
search "App passwords" → create one. In Kaggle: Add-ons → Secrets → add `GMAIL_ADDRESS` and
`GMAIL_APP_PASSWORD`, and switch both on for the notebook. **Never put the password in the repo.**

Open `output/drafts.csv`, read every draft, fix anything wrong, and set `approved` to `yes` for the
ones to send. Then:

```python
!python -m outreach.send_emails                                   # preview: shows what would go out
!python -m outreach.send_emails --test-to yourname@gmail.com --send   # send all to yourself first
!python -m outreach.send_emails --send                            # real send
```

The sender:
- only sends rows with `approved=yes` and a valid address
- never emails the same company or address twice (tracked in `output/sent_log.csv`; keep this file
  between sessions: download it, or save it to a Kaggle dataset)
- sends at most 20 per run and 30 per 24 hours (`--limit`, `--daily-cap`), with a random 30-90 s gap
- adds the opt-out line if a draft is missing it
- sends plain-text emails with no attachments (brochure is a link)

Replies come to the society inbox. Mark them in the prospects sheet (`replied` / `declined`).

## How we deal with spam

The goal is that no one we email could reasonably call it spam, and our society account never gets
flagged.

- **A human approves every email.** Nothing is sent unless someone read the draft and set
  `approved=yes`. One recipient per email, no bulk BCC.
- **Only public business contacts.** Emails come from the company's own website (partnerships@,
  campus@, careers@...). The scorer can only pick emails that actually appear on the site, never guessed
  ones. No scraped personal emails, no LinkedIn scraping or LinkedIn bots.
- **Every email is personal.** Each one opens with a specific reason this company fits us. Drafts with
  low `confidence` get rewritten or dropped.
- **Small batches:** max ~20-30 emails per day per sender account, spread across team members. A burst of
  near-identical emails is what gets Gmail accounts flagged.
- **One email, at most one follow-up** after about 7 days. Track it in the `status` column
  (`new` → `approved` → `sent` → `followed_up` → `replied` / `declined`) so nobody contacts the same
  company twice.
- **Easy opt-out.** Every email ends with "reply 'no' and we won't follow up". If they say no, mark them
  `declined` and never contact them again.
- **Send from an official society address**, and link the brochure (Drive/PDF link) instead of
  attaching big files, since attachments from unknown senders trip spam filters.
- **Fill `config.json` honestly.** The prompts forbid made-up numbers, but they can only be as accurate as
  what we give them.

## Who does what

| Area | Files |
|---|---|
| Sponsor search quality | `prompts/queries_system.md`, `prompts/extract_system.md`, `outreach/web.py` |
| Fit scoring | `prompts/score_system.md` |
| Email quality | `prompts/outreach_system.md`, `data/brochure.md` (Design Team), format from Media & Marketing |
| Data | `data/past_sponsors.csv`, `data/society_test.csv` |

Using a different LLM: any OpenAI-compatible server works. Set `LLM_BASE_URL` (and `LLM_API_KEY`) and
the Kaggle setup is skipped.

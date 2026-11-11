"""End-to-end run on Kaggle (GPU T4 x2, Internet on).

1. clone repo + install   2. start Qwen   3. society test drafts   4. send preview
5. small real sponsor search from a sample past-sponsors list

Results land in /kaggle/working/Outreach/output (download from the notebook's Output tab).
Real sending needs GMAIL_ADDRESS / GMAIL_APP_PASSWORD Kaggle Secrets; this run only previews.
"""
import csv
import os
import subprocess
import sys

REPO = "https://github.com/aryan1234-l/Outreach.git"
DIR = "/kaggle/working/Outreach"


def sh(cmd, check=True):
    print(f"\n$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, cwd=DIR if os.path.isdir(DIR) else "/kaggle/working")
    if check and r.returncode:
        sys.exit(f"Step failed ({r.returncode}): {cmd}")
    return r.returncode


def banner(title):
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70, flush=True)


banner("1. Get code")
if os.path.isdir(DIR):
    sh("git pull")
else:
    sh(f"git clone --depth 1 {REPO} {DIR}")
sh(f"{sys.executable} -m pip install -q -r requirements.txt")
sh("nvidia-smi --query-gpu=name,memory.total --format=csv", check=False)

banner("2. Start Qwen (llama.cpp)")
sh(f"{sys.executable} -m outreach.llm")

banner("3. Society test: draft emails")
sh(f"{sys.executable} -m outreach.draft_emails --input data/society_test.csv --all --redo")
sh("for f in output/drafts/*.txt; do echo \"----- $f\"; cat \"$f\"; echo; done", check=False)

banner("4. Send preview (nothing is sent)")
drafts = f"{DIR}/output/drafts.csv"
with open(drafts, newline="", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))
for r in rows:
    r["approved"] = "yes"  # test only: auto-approve so the preview has something to show
with open(drafts, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
sh(f"{sys.executable} -m outreach.send_emails --test-to preview@example.com", check=False)

banner("5. Find sponsors (small sample run)")
past = f"{DIR}/data/past_sponsors.csv"
if not os.path.exists(past):
    # Sample list until the real past-sponsors sheet is added (as a Kaggle dataset).
    with open(past, "w", newline="", encoding="utf-8") as fh:
        fh.write(
            "company,industry,what_they_gave,notes\n"
            "Devfolio,Hackathon platform,Platform + prizes,Hackathon partner\n"
            "Postman,Developer tools,Swag + API credits,Developer adoption\n"
            "Unstop,Student hiring platform,Cash,Student signups\n"
        )
sh(f"{sys.executable} -m outreach.find_sponsors --queries 4 --results-per-query 3 --max-candidates 6")
sh("cat output/prospects.csv", check=False)

banner("6. Status")
sh(f"{sys.executable} -m outreach.status", check=False)

banner("Done")
print("Outputs: output/drafts.csv, output/drafts/*.txt, output/prospects.csv")
sh("tail -n 20 /kaggle/working/server.log", check=False)

import glob
import json
import os
import pathlib
import subprocess
import tarfile
import time
import requests
from huggingface_hub import hf_hub_download

# ==========================================
# LinkedIn API Configuration
# ==========================================
LINKEDIN_USERNAME = "aryan-gupta-60a91a44a"
LI_AT_COOKIE = "AQEDAXD5Y-oFWeYeAAABoctHoRsAAAGh71QlG04AMSmvpdefxAFuiu-3Y2BglI6FdKm3PKEi5z3YMTBN7aXmyRVmh6KJ2sTEibS0-nlGH0HWROSdOfEmyv4xR8hTrmRhzROfJ58HINfWXFjulJIxD5m4"
JSESSIONID = "ajax:3508671631700845830"  # Must match the csrf-token value below

linkedin_headers = {
    "cookie": f'li_at={LI_AT_COOKIE}; JSESSIONID="{JSESSIONID}"',
    "csrf-token": JSESSIONID,
    "accept": "application/vnd.linkedin.normalized+json+2.1",
    "x-restli-protocol-version": "2.0.0",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

linkedin_params = {
    "q": "memberIdentity",
    "memberIdentity": LINKEDIN_USERNAME,
}

# ==========================================
# 1. Fetch LinkedIn Profile via Voyager API
# ==========================================
print(f"Fetching LinkedIn profile for: {LINKEDIN_USERNAME}...")
linkedin_res = requests.get(
    "https://www.linkedin.com/voyager/api/identity/dash/profiles",
    headers=linkedin_headers,
    params=linkedin_params,
)

linkedin_data = {}
if linkedin_res.status_code == 200:
    linkedin_data = linkedin_res.json()
    print("LinkedIn profile data fetched successfully!")
else:
    print(f"Failed to fetch LinkedIn data (Status {linkedin_res.status_code}): {linkedin_res.text}")

# Keep only fields useful for an outreach profile. Voyager may return other
# profile and account metadata; those fields are deliberately not persisted.
profile_entity = next(
    (
        item
        for item in linkedin_data.get("included", [])
        if isinstance(item, dict) and item.get("$type", "").endswith("Profile")
    ),
    {},
)

email_info = profile_entity.get("emailAddress")
if isinstance(email_info, dict):
    email_info = {
        key: email_info[key]
        for key in ("emailAddress", "confirmed", "primary")
        if email_info.get(key) is not None
    }
else:
    email_info = None

linkedin_profile = {
    "name": " ".join(
        part for part in (profile_entity.get("firstName"), profile_entity.get("lastName")) if part
    ),
    "username": profile_entity.get("publicIdentifier") or LINKEDIN_USERNAME,
    "email": email_info,
    "headline": profile_entity.get("headline"),
    "summary": profile_entity.get("summary"),
    "location": profile_entity.get("locationName"),
    "company": profile_entity.get("companyNameOnProfileTopCardShown"),
    "websites": profile_entity.get("websites"),
}
linkedin_profile = {
    key: value for key, value in linkedin_profile.items() if value not in (None, "", [])
}

with open("linkedin_profile.json", "w", encoding="utf-8") as profile_file:
    json.dump(linkedin_profile, profile_file, indent=2, ensure_ascii=False)
print("Filtered LinkedIn outreach profile saved to linkedin_profile.json")

# ==========================================
# 2. Setup llama.cpp Binary
# ==========================================
p = pathlib.Path(
    hf_hub_download(
        "afsafwtggg/llama-cpp-t4-cache", "llama-cpp-t4-539f245.tar.gz"
    )
)
with tarfile.open(p) as f:
    f.extractall("/kaggle/working/engine")

BIN = pathlib.Path("/kaggle/working/engine/llama-cpp-t4-539f245/bin")
for b in BIN.iterdir():
    if b.is_file():
        b.chmod(0o755)

# ==========================================
# 3. Download GGUF Model
# ==========================================
MODEL = hf_hub_download(
    "unsloth/Qwen3.6-35B-A3B-MTP-GGUF",
    "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf",
    local_dir="/kaggle/working/models",
)

# ==========================================
# 4. Launch llama-server
# ==========================================
nv = sorted(glob.glob("/usr/local/lib/python3.13/dist-packages/nvidia/*/lib"))
env = os.environ.copy()
env["LD_LIBRARY_PATH"] = ":".join([str(BIN), *nv, env.get("LD_LIBRARY_PATH", "")])

proc = subprocess.Popen(
    [
        str(BIN / "llama-server"),
        "-m",
        MODEL,
        "-ngl",
        "99",
        "-c",
        "262144",
        "--jinja",
        "-fa",
        "on",
        "-ctk",
        "q8_0",
        "-ctv",
        "q8_0",
        "-ts",
        "1,1",
        "--alias",
        "claude-sonnet-4-5",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--api-key",
        "CHANGE_ME",
    ],
    env=env,
    stdout=open("/kaggle/working/server.log", "w"),
    stderr=subprocess.STDOUT,
)

# Wait for server readiness
print("Waiting for server to start...")
for _ in range(300):
    if proc.poll() is not None:
        print("Server died, exit code", proc.returncode)
        if os.path.exists("/kaggle/working/server.log"):
            print(open("/kaggle/working/server.log").read()[-3000:])
        break
    try:
        if requests.get("http://127.0.0.1:8080/health").json().get("status") == "ok":
            print("Server is ready!")
            break
    except Exception:
        pass
    time.sleep(2)

# ==========================================
# 5. Send Prompt + Profile Data to llama-server
# ==========================================
local_headers = {
    "Authorization": "Bearer CHANGE_ME",
    "Content-Type": "application/json",
}

prompt_content = (
    f"Here is filtered LinkedIn profile data in JSON format:\n"
    f"{json.dumps(linkedin_profile, indent=2, ensure_ascii=False)}\n\n"
    "Prepare a concise outreach brief using only the information provided: summarize relevant background, "
    "identify a plausible professional point of connection, and draft a brief, respectful first message. "
    "Do not infer or invent experience, skills, contact details, or personal interests that are not present."
)

local_payload = {
    "model": "claude-sonnet-4-5",
    "messages": [{"role": "user", "content": prompt_content}],
}

llm_response = requests.post(
    "http://127.0.0.1:8080/v1/chat/completions",
    headers=local_headers,
    json=local_payload,
)

print("\nModel Response:")
print(llm_response.json()["choices"][0]["message"]["content"])

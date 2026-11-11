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
LINKEDIN_USERNAME = "<username>"
LI_AT_COOKIE = "<that_cookie>"
JSESSIONID = "ajax:1234567890"  # Must match the csrf-token value below

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
    f"Here is raw LinkedIn profile data in JSON format:\n"
    f"{json.dumps(linkedin_data, indent=2)}\n\n"
    "Please summarize this candidate's background, work history, and key skills."
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
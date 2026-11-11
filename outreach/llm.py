"""Start Qwen on Kaggle (2x T4) with llama.cpp and talk to it.

`python -m outreach.llm` starts the server and leaves it running in the background,
so the other scripts can reuse it. Set LLM_BASE_URL to use some other
OpenAI-compatible server instead (skips the Kaggle setup).
"""
import glob
import json
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = pathlib.Path("/kaggle/working") if pathlib.Path("/kaggle/working").exists() else ROOT
PORT = 8080
API_KEY = os.environ.get("LLM_API_KEY", "local-only")
BASE_URL = os.environ.get("LLM_BASE_URL", f"http://127.0.0.1:{PORT}/v1")
MODEL_ALIAS = "qwen"

ENGINE_REPO, ENGINE_FILE = "afsafwtggg/llama-cpp-t4-cache", "llama-cpp-t4-539f245.tar.gz"
MODEL_REPO, MODEL_FILE = "unsloth/Qwen3.6-35B-A3B-MTP-GGUF", "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf"


def load_prompt(name):
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def resolve(path):
    """Accept paths relative to the current folder or to the repo root."""
    p = pathlib.Path(path)
    if p.exists() or p.is_absolute():
        return p
    if (ROOT / p).exists():
        return ROOT / p
    sys.exit(f"File not found: {path} (also looked in {ROOT / p})")


def load_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def server_ok():
    try:
        r = requests.get(BASE_URL.removesuffix("/v1") + "/health", timeout=3)
        return r.ok and r.json().get("status") == "ok"
    except Exception:
        return False


def start_server():
    if server_ok():
        return
    if "LLM_BASE_URL" in os.environ:
        sys.exit(f"LLM_BASE_URL={BASE_URL} is not reachable.")
    if not pathlib.Path("/kaggle/working").exists():
        sys.exit("No LLM server running. Run this on Kaggle (GPU T4 x2), or set LLM_BASE_URL.")

    from huggingface_hub import hf_hub_download

    bin_dir = WORK / "engine/llama-cpp-t4-539f245/bin"
    if not (bin_dir / "llama-server").exists():
        with tarfile.open(hf_hub_download(ENGINE_REPO, ENGINE_FILE)) as tf:
            tf.extractall(WORK / "engine", filter="tar")
    for b in bin_dir.iterdir():
        if b.is_file():
            b.chmod(0o755)

    print("Downloading model (~17 GB, first run only)...")
    model = hf_hub_download(MODEL_REPO, MODEL_FILE, local_dir=str(WORK / "models"))

    env = os.environ.copy()
    nv = sorted(glob.glob("/usr/local/lib/python3*/dist-packages/nvidia/*/lib"))
    env["LD_LIBRARY_PATH"] = ":".join([str(bin_dir), *nv, env.get("LD_LIBRARY_PATH", "")])

    log_path = WORK / "server.log"
    proc = subprocess.Popen(
        [
            str(bin_dir / "llama-server"), "-m", model,
            "-ngl", "99", "-c", "32768", "--jinja", "-fa", "on",
            "-ctk", "q8_0", "-ctv", "q8_0", "-ts", "1,1",
            "--alias", MODEL_ALIAS, "--host", "127.0.0.1", "--port", str(PORT),
            "--api-key", API_KEY,
        ],
        env=env,
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,  # keep running after this script exits
    )
    print("Waiting for llama-server...")
    for _ in range(300):
        if proc.poll() is not None:
            sys.exit(f"llama-server died (exit {proc.returncode}):\n{log_path.read_text()[-3000:]}")
        if server_ok():
            print("LLM server ready.")
            return
        time.sleep(2)
    sys.exit(f"llama-server not ready after 10 min:\n{log_path.read_text()[-3000:]}")


def _parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise ValueError(f"No JSON in model output: {text[:300]}")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def chat_json(system, user, max_tokens=1500, retries=2):
    """Send one system+user turn, return parsed JSON from the reply."""
    payload = {
        "model": MODEL_ALIAS,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    last_err = None
    for _ in range(retries + 1):
        try:
            r = requests.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json=payload,
                timeout=600,
            )
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            return _parse_json(msg.get("content") or msg.get("reasoning_content") or "")
        except (requests.RequestException, ValueError, KeyError) as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"LLM call failed: {last_err}")


if __name__ == "__main__":
    start_server()
    print(chat_json('Reply with JSON {"ok": true}.', "ping", max_tokens=20))

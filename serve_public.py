#!/usr/bin/env python3
"""Start the server + a Cloudflare Quick Tunnel. Prints a public HTTPS URL."""
import os, re, subprocess, sys, threading, time, urllib.request

os.chdir("/home/user/test")

# ── 1. Start uvicorn ────────────────────────────────────────────────────────
def _server():
    subprocess.run(
        ["python3", "-m", "uvicorn", "app.server:app",
         "--host", "127.0.0.1", "--port", "8000"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

threading.Thread(target=_server, daemon=True).start()
print("Server starting…", flush=True)
time.sleep(7)
print("Server ready.", flush=True)

# ── 2. Download cloudflared ─────────────────────────────────────────────────
CF = "/tmp/cloudflared"
if not os.path.exists(CF):
    print("Downloading cloudflared…", flush=True)
    url = ("https://github.com/cloudflare/cloudflared/releases/latest"
           "/download/cloudflared-linux-amd64")
    try:
        urllib.request.urlretrieve(url, CF)
        os.chmod(CF, 0o755)
        print("cloudflared downloaded.", flush=True)
    except Exception as e:
        print(f"ERROR downloading cloudflared: {e}", flush=True)
        sys.exit(1)

# ── 3. Start tunnel ─────────────────────────────────────────────────────────
tunnel = subprocess.Popen(
    [CF, "tunnel", "--url", "localhost:8000"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)

# ── 4. Read URL from output ─────────────────────────────────────────────────
public_url = None
print("Waiting for tunnel URL…", flush=True)
for _ in range(120):
    raw = tunnel.stdout.readline()
    if not raw:
        break
    line = raw.decode("utf-8", "replace").rstrip()
    if line:
        print(line, flush=True)
    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
    if m:
        public_url = m.group()
        break
    time.sleep(0.3)

print(flush=True)
print("=" * 54, flush=True)
if public_url:
    print(f"  OPEN ON YOUR PHONE: {public_url}", flush=True)
else:
    print("  ERROR: could not get tunnel URL (check output above)", flush=True)
print("=" * 54, flush=True)
print(flush=True)

# ── 5. Keep alive — print status every minute ───────────────────────────────
while True:
    time.sleep(60)
    print(f"[alive {time.strftime('%H:%M')}]  {public_url}", flush=True)

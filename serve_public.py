#!/usr/bin/env python3
"""Start the server + a Cloudflare Quick Tunnel. Prints a public HTTPS URL."""
import os, re, subprocess, sys, threading, time

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

# ── 2. Ensure cloudflared is downloaded ────────────────────────────────────
CF = "/tmp/cloudflared"
if not os.path.exists(CF):
    import urllib.request
    print("Downloading cloudflared…", flush=True)
    url = ("https://github.com/cloudflare/cloudflared/releases/latest"
           "/download/cloudflared-linux-amd64")
    urllib.request.urlretrieve(url, CF)
    os.chmod(CF, 0o755)
    print("cloudflared downloaded.", flush=True)

# ── 3. Start tunnel — redirect output to FILE (avoids pipe-buffer deadlock) ─
CF_LOG = "/tmp/cf_tunnel.log"
with open(CF_LOG, "w") as logf:
    pass  # truncate
log_fd = open(CF_LOG, "a")
tunnel = subprocess.Popen(
    [CF, "tunnel", "--protocol", "http2", "--url", "localhost:8000"],
    stdout=log_fd, stderr=log_fd,
)

# ── 4. Poll log file for URL ────────────────────────────────────────────────
public_url = None
print("Waiting for tunnel URL…", flush=True)
for _ in range(80):
    time.sleep(1)
    try:
        content = open(CF_LOG).read()
    except OSError:
        continue
    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", content)
    if m:
        public_url = m.group()
        break

print(flush=True)
print("=" * 54, flush=True)
if public_url:
    print(f"  OPEN ON YOUR PHONE:", flush=True)
    print(f"  {public_url}", flush=True)
else:
    tail = open(CF_LOG).read()[-800:] if os.path.exists(CF_LOG) else ""
    print(f"  ERROR: no URL found. cloudflared log:\n{tail}", flush=True)
print("=" * 54, flush=True)
print(flush=True)

# ── 5. Keep alive — print heartbeat every 30 s ─────────────────────────────
while True:
    time.sleep(30)
    alive = tunnel.poll() is None
    print(f"[alive {time.strftime('%H:%M')} tunnel={'up' if alive else 'DOWN'}]  {public_url}", flush=True)
    if not alive:
        print("Tunnel died — restarting…", flush=True)
        log_fd2 = open(CF_LOG, "a")
        tunnel = subprocess.Popen(
            [CF, "tunnel", "--url", "localhost:8000"],
            stdout=log_fd2, stderr=log_fd2,
        )


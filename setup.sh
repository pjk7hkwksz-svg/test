#!/usr/bin/env bash
# One-time setup for ShortMagic — installs deps and downloads neural voices.
set -e

echo "==> Installing system packages (ffmpeg, espeak-ng)…"
if command -v apt-get >/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg espeak-ng
elif command -v brew >/dev/null; then
  brew install ffmpeg espeak
else
  echo "   Please install ffmpeg and espeak-ng manually."
fi

echo "==> Installing Python packages…"
pip install -q -r requirements.txt

echo "==> Downloading neural voice models (~240 MB)…"
mkdir -p voices
VOICES="en_US-ryan-medium en_US-hfc_male-medium en_US-hfc_female-medium en_US-lessac-medium"
for v in $VOICES; do
  if [ ! -f "voices/${v}.onnx" ]; then
    echo "   - $v"
    python3 -m piper.download_voices "$v" --data-dir voices
  else
    echo "   - $v (already present)"
  fi
done

echo ""
echo "Setup complete. Start the app with:"
echo "    python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8000"
echo "Then open http://localhost:8000 on your phone (same network) or browser."

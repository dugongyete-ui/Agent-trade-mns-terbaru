#!/bin/bash

REPO="dugongyete-ui/manus-trader"

if [ -z "$GITHUB_TOKEN" ]; then
  echo "ERROR: GITHUB_TOKEN belum diset."
  echo "Simpan sebagai Replit Secret bernama GITHUB_TOKEN."
  exit 1
fi

REMOTE_URL="https://x-token-auth:${GITHUB_TOKEN}@github.com/${REPO}.git"

echo "Pushing ke GitHub..."
PUSH_OUTPUT=$(git push "$REMOTE_URL" main 2>&1)
EXIT_CODE=$?

echo "$PUSH_OUTPUT"

if [ $EXIT_CODE -eq 0 ]; then
  echo ""
  echo "Push berhasil!"
  exit 0
fi

echo ""

# Ekstrak URL unblock dari Secret Scanning secara otomatis
UNBLOCK_URL=$(echo "$PUSH_OUTPUT" | grep -o 'https://github\.com/[^ )]*unblock-secret[^ )]*' | head -1)

if [ -n "$UNBLOCK_URL" ]; then
  echo "=========================================="
  echo "GitHub Secret Scanning memblokir push ini."
  echo ""
  echo "Klik link ini lalu pilih Allow:"
  echo ""
  echo "  👉  $UNBLOCK_URL"
  echo ""
  echo "Setelah Allow, jalankan push.sh lagi."
  echo "=========================================="
  exit 1
fi

echo "Push gagal. Kemungkinan penyebab:"
echo "  1. Token expired — buat token baru di https://github.com/settings/tokens"
echo "  2. Konflik — selesaikan merge conflict dulu"
exit 1

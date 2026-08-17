#!/usr/bin/env bash
# Download + extract the NGSO CR/C test fixtures (SRS / PFD-mask / EPFD-results
# MDBs for 15 NGSO systems) from the GitHub release. ~500 MB download, ~1.2 GB
# extracted into docs/test_data/NGSO_CRC_filings/ (gitignored).
#
# Usage:  bash docs/test_data/fetch_ngso_crc_fixtures.sh
set -euo pipefail

REPO="SIMULATOR-WG/SHARC-Orbit"
TAG="fixtures-ngso-crc-2026-1"
ASSET="NGSO_CRC_filings.tar.gz"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # docs/test_data/

if [ -d "$DEST/NGSO_CRC_filings" ]; then
  echo "Already present: $DEST/NGSO_CRC_filings (delete it to re-fetch). Aborting."
  exit 0
fi

cd "$DEST"
echo "Downloading $ASSET from $REPO@$TAG ..."
if command -v gh >/dev/null 2>&1; then
  gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$DEST"
else
  # Fallback for public repos without gh (uses the redirecting download URL).
  curl -fL -o "$ASSET" \
    "https://github.com/$REPO/releases/download/$TAG/$ASSET"
fi

echo "Extracting ..."
tar xzf "$ASSET" -C "$DEST"
rm -f "$ASSET"
echo "Done -> $DEST/NGSO_CRC_filings"

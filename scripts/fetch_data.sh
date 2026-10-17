#!/usr/bin/env bash
# Fetch the public Kubernetes records Inheritance is built on, pinned to the commits used in the demo.
# Only OWNERS files and KEP folders are checked out (sparse, blobless): about 110 MB.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/raw
K8S=98d0129cf21ce0871637ae36e67b5ff82d3db50d          # kubernetes/kubernetes, 2026-10-17
KEPS=6ebe0a94771723b3f27cb4a757f0cc22f29e38a2         # kubernetes/enhancements, 2026-10-14
COMMUNITY=440e496d3a172234d81728df1bfe43849b589adf    # kubernetes/community, 2026-10-17

sparse() {  # url dir commit patterns...
  local url=$1 dir=$2 rev=$3; shift 3
  if [ ! -d "$dir/.git" ]; then
    git clone --filter=blob:none --no-checkout --quiet "$url" "$dir"
  fi
  git -C "$dir" sparse-checkout set --no-cone "$@"
  git -C "$dir" fetch --quiet --depth 1 origin "$rev" 2>/dev/null || true
  git -C "$dir" checkout --quiet "$rev"
}
sparse https://github.com/kubernetes/kubernetes.git data/raw/k8s "$K8S" '**/OWNERS' '/OWNERS_ALIASES'
sparse https://github.com/kubernetes/enhancements.git data/raw/enhancements "$KEPS" '/keps/'
curl -fsSL "https://raw.githubusercontent.com/kubernetes/community/$COMMUNITY/sigs.yaml" -o data/raw/sigs.yaml
echo "fetched: $(find data/raw/k8s -name OWNERS | wc -l) OWNERS files, $(find data/raw/enhancements/keps -name kep.yaml | wc -l) KEPs"

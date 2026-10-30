#!/usr/bin/env bash
# CycloneDX SBOM (software bill of materials) of an installed environment
# (stage 13): every Python package with version, license, purl and hashes.
#
#   bash scripts/generate_sbom.sh [target-python] [output]
#
# target-python: the interpreter of the environment to describe, e.g. a venv
#   installed from requirements-embedding.lock.txt like the Docker image
#   (default: python3). The tool itself runs from any other environment:
#   pip install cyclonedx-bom
# output: default release/sbom.cdx.json (attach it to the release bundle).
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-python3}"
OUT="${2:-release/sbom.cdx.json}"
mkdir -p "$(dirname "$OUT")"
cyclonedx-py environment --output-reproducible --of JSON -o "$OUT" "$TARGET"
echo "SBOM written: $OUT ($(grep -c '"bom-ref"' "$OUT") entries)"

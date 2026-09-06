#!/bin/bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
manifest="$root/packaging/flatpak/io.github.GMagisk9527.PasteMDLinux.json"
build_root="$root/build/flatpak"
output="$root/dist/PasteMD-Linux-linux-v0.1.0-x86_64.flatpak"

mkdir -p "$build_root" "$root/dist"
flatpak run --filesystem="$root" org.flatpak.Builder \
  --force-clean --default-branch=stable --repo="$build_root/repo" \
  "$build_root/work" "$manifest"
flatpak build-bundle "$build_root/repo" "$output" \
  io.github.GMagisk9527.PasteMDLinux stable
(cd "$root/dist" && sha256sum "$(basename "$output")" > "$(basename "$output").sha256")
printf '%s\n' "$output"

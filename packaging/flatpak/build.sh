#!/bin/bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
# 版本单一来源：pastemd/__init__.py 的 __version__（去掉 -linux 后缀）
release_version="$(sed -n 's/^__version__ = "\(.*\)-linux"$/\1/p' "$root/pastemd/__init__.py")"
if [[ -z "$release_version" ]]; then
  echo "无法从 pastemd/__init__.py 读取版本号" >&2
  exit 1
fi
manifest="$root/packaging/flatpak/io.github.GMagisk9527.PasteMDLinux.json"
build_root="$root/build/flatpak"
output="$root/dist/PasteMD-Linux-linux-v$release_version-x86_64.flatpak"

mkdir -p "$build_root" "$root/dist"
flatpak run --filesystem="$root" org.flatpak.Builder \
  --force-clean --default-branch=stable --repo="$build_root/repo" \
  "$build_root/work" "$manifest"
flatpak build-bundle "$build_root/repo" "$output" \
  io.github.GMagisk9527.PasteMDLinux stable
(cd "$root/dist" && sha256sum "$(basename "$output")" > "$(basename "$output").sha256")
printf '%s\n' "$output"

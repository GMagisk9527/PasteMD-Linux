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
# 优先宿主 flatpak-builder（CI/容器环境），回退 org.flatpak.Builder 应用
# （本地开发环境；容器版 init 在沙箱内看不到宿主 user 作用域的 Sdk）
if command -v flatpak-builder >/dev/null 2>&1; then
  flatpak-builder --force-clean --default-branch=stable \
    --repo="$build_root/repo" \
    "$build_root/work" "$manifest"
else
  flatpak run --filesystem="$root" org.flatpak.Builder \
    --force-clean --default-branch=stable --repo="$build_root/repo" \
    "$build_root/work" "$manifest"
fi
flatpak build-bundle "$build_root/repo" "$output" \
  io.github.GMagisk9527.PasteMDLinux stable
(cd "$root/dist" && sha256sum "$(basename "$output")" > "$(basename "$output").sha256")
printf '%s\n' "$output"

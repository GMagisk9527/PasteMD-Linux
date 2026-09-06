#!/bin/bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
version="linux-v0.1.0"
appdir="$root/build/appimage/PasteMD-Linux.AppDir"
pyinstaller="$root/.build-venv/bin/pyinstaller"
appimagetool="$root/.build-tools/appimagetool-x86_64.AppImage"
runtime="$root/.build-tools/runtime-x86_64-20251108"
output="$root/dist/PasteMD-Linux-$version-x86_64.AppImage"
cache="$root/.cache/releases"
pandoc_archive="$cache/pandoc-3.7.0.2-linux-amd64.tar.gz"
wl_archive="$cache/wl-clipboard-v2.2.1.tar.gz"

test -x "$pyinstaller"
test -x "$appimagetool"
test -x /usr/bin/wl-paste

if [[ ! -f "$runtime" ]]; then
  curl -fL https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64 \
    -o "$runtime"
fi
printf '%s  %s\n' 2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d \
  "$runtime" | sha256sum -c -

mkdir -p "$cache"
test -f "$pandoc_archive" || curl -fL \
  https://github.com/jgm/pandoc/releases/download/3.7.0.2/pandoc-3.7.0.2-linux-amd64.tar.gz \
  -o "$pandoc_archive"
test -f "$wl_archive" || curl -fL \
  https://github.com/bugaevc/wl-clipboard/archive/refs/tags/v2.2.1.tar.gz \
  -o "$wl_archive"
test -f "$cache/pandoc-COPYING.md" || curl -fL \
  https://raw.githubusercontent.com/jgm/pandoc/3.7.0.2/COPYING.md \
  -o "$cache/pandoc-COPYING.md"
test -f "$cache/pandoc-COPYRIGHT" || curl -fL \
  https://raw.githubusercontent.com/jgm/pandoc/3.7.0.2/COPYRIGHT \
  -o "$cache/pandoc-COPYRIGHT"
printf '%s  %s\n' \
  8f8f67fdd540b6519326b0ac49d5c55c5d5d15e43920e80a086e02c8aff83268 "$pandoc_archive" \
  6eb8081207fb5581d1d82c4bcd9587205a31a3d47bea3ebeb7f41aa1143783eb "$wl_archive" \
  e7ea3adeab955103a837b692ca0017cb3abbed0d3dccbfa499d6b2b825d698c3 "$cache/pandoc-COPYING.md" \
  eb46b1cde09811dffc750b59672d42f3c74ce2e093e30a291023d570a91282e1 "$cache/pandoc-COPYRIGHT" | sha256sum -c -

rm -rf "$root/build/appimage"
mkdir -p "$appdir/usr/lib" "$appdir/usr/share/doc/pastemd-linux" "$root/dist"
mkdir -p "$root/build/appimage/vendor"
tar -xzf "$pandoc_archive" -C "$root/build/appimage/vendor"
tar -xzf "$wl_archive" -C "$root/build/appimage/vendor" wl-clipboard-2.2.1/COPYING
pandoc="$root/build/appimage/vendor/pandoc-3.7.0.2/bin/pandoc"

"$pyinstaller" --noconfirm --clean --onedir --name pastemd-linux \
  --paths "$root" --collect-submodules pastemd \
  --distpath "$root/build/appimage/pyinstaller-dist" \
  --workpath "$root/build/appimage/pyinstaller-work" \
  --specpath "$root/build/appimage" \
  --hidden-import=dbus.mainloop.glib --hidden-import=gi.repository.GLib \
  --collect-all dbus \
  --add-data "$root/assets/icons/logo.png:assets/icons" \
  --add-data "$root/LICENSE:." --add-data "$root/NOTICE.md:." \
  --add-data "$root/THIRD_PARTY_NOTICES.md:." \
  --add-data "$cache/pandoc-COPYING.md:licenses/pandoc" \
  --add-data "$cache/pandoc-COPYRIGHT:licenses/pandoc" \
  --add-data "$root/build/appimage/vendor/wl-clipboard-2.2.1/COPYING:licenses/wl-clipboard" \
  --add-binary "$pandoc:bin" --add-binary "/usr/bin/wl-paste:bin" \
  "$root/scripts/pastemd-linux.py"

mv "$root/build/appimage/pyinstaller-dist/pastemd-linux" "$appdir/usr/lib/pastemd"
install -Dm755 "$root/packaging/appimage/AppRun" "$appdir/AppRun"
install -Dm644 "$root/packaging/common/io.github.GMagisk9527.PasteMDLinux.desktop" \
  "$appdir/io.github.GMagisk9527.PasteMDLinux.desktop"
install -Dm644 "$root/packaging/common/io.github.GMagisk9527.PasteMDLinux.desktop" \
  "$appdir/usr/share/applications/io.github.GMagisk9527.PasteMDLinux.desktop"
install -Dm644 "$root/packaging/common/io.github.GMagisk9527.PasteMDLinux.png" "$appdir/pastemd-linux.png"
ln -s pastemd-linux.png "$appdir/.DirIcon"
install -Dm644 "$root/packaging/common/io.github.GMagisk9527.PasteMDLinux.metainfo.xml" \
  "$appdir/usr/share/metainfo/io.github.GMagisk9527.PasteMDLinux.appdata.xml"
install -Dm644 "$root/LICENSE" "$root/NOTICE.md" "$root/THIRD_PARTY_NOTICES.md" \
  -t "$appdir/usr/share/doc/pastemd-linux"
install -Dm644 "$cache/pandoc-COPYING.md" "$cache/pandoc-COPYRIGHT" \
  -t "$appdir/usr/share/doc/pastemd-linux/pandoc"
install -Dm644 "$root/build/appimage/vendor/wl-clipboard-2.2.1/COPYING" \
  -t "$appdir/usr/share/doc/pastemd-linux/wl-clipboard"

sed -i 's/^Icon=.*/Icon=pastemd-linux/' "$appdir/io.github.GMagisk9527.PasteMDLinux.desktop"
sed -i 's/^Icon=.*/Icon=pastemd-linux/' \
  "$appdir/usr/share/applications/io.github.GMagisk9527.PasteMDLinux.desktop"
(cd "$root" && LC_ALL=C.UTF-8 ARCH=x86_64 \
  ./.build-tools/appimagetool-x86_64.AppImage --appimage-extract-and-run \
  --no-appstream \
  --runtime-file ./.build-tools/runtime-x86_64-20251108 \
  build/appimage/PasteMD-Linux.AppDir dist/"$(basename "$output")")
chmod +x "$output"
(cd "$root/dist" && sha256sum "$(basename "$output")" > "$(basename "$output").sha256")
printf '%s\n' "$output"

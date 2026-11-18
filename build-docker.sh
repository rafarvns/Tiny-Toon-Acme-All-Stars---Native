#!/usr/bin/env bash
# Build with Docker: linux, windows, windows --release, or linux --appimage.
# Ordinary builds require a ROM path; the AppImage never includes a ROM.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"
ALVO=linux
RELEASE=0
APPIMAGE=0
VERSION=""
ROM=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        linux|windows) ALVO="$1" ;;
        --release) RELEASE=1 ;;
        --appimage) APPIMAGE=1 ;;
        --version) shift; [ "$#" -gt 0 ] || { echo 'Missing version' >&2; exit 2; }; VERSION="$1" ;;
        --help|-h) echo 'Usage: ./build-docker.sh [linux|windows] [--release] [ROM] | linux --appimage [--version VERSION]'; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) [ -z "$ROM" ] || { echo 'Only one ROM may be specified' >&2; exit 2; }; ROM="$1" ;;
    esac
    shift
done
command -v docker >/dev/null 2>&1 || { echo 'ERROR: Docker not found.' >&2; exit 1; }
# A linked worktree's .git file points outside /work. Make its common Git
# metadata available at the same absolute path so version generation works.
GIT_MOUNT=()
if command -v git >/dev/null 2>&1; then
    GIT_COMMON="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [ -n "$GIT_COMMON" ] && [ -d "$GIT_COMMON" ] && [ "$GIT_COMMON" != "$RAIZ/.git" ]; then
        GIT_MOUNT=(-v "$GIT_COMMON:$GIT_COMMON:ro")
    fi
fi
if [ "$APPIMAGE" = 1 ]; then
    [ "$ALVO" = linux ] && [ -z "$ROM" ] || { echo '--appimage requires Linux and no ROM' >&2; exit 2; }
    docker build --target appimage -t tinytoon-appimage-build .
    docker run --rm -v "$RAIZ:/work" "${GIT_MOUNT[@]}" --user "$(id -u):$(id -g)" -e HOME=/tmp \
        tinytoon-appimage-build bash -c '
            set -e
            cd /work
            python3 tools/gerar_baseline.py
            python3 tools/gerar_versao.py
            make -j"$(nproc)" RELEASE=1 RUNTIME_ONLY=1 BUILD=build/appimage-runtime
            if [ -n "$1" ]; then
                python3 tools/montar_appimage.py --version "$1"
            else
                python3 tools/montar_appimage.py
            fi
        ' _ "$VERSION"
    exit 0
fi
[ -z "$VERSION" ] || { echo '--version is only for --appimage' >&2; exit 2; }
ROM="${ROM:-rom.bin}"
[ -f "$ROM" ] || { echo "ERROR: ROM not found at '$ROM'. See LEGAL.md." >&2; exit 1; }
# Always ask Docker to build: its cache is cheap and notices Dockerfile changes.
docker build --target build -t tinytoon-build .
ROM_ABS="$(cd "$(dirname "$ROM")" && pwd)/$(basename "$ROM")"
docker run --rm -v "$RAIZ:/work" "${GIT_MOUNT[@]}" -v "$ROM_ABS:/rom.bin:ro" \
    --user "$(id -u):$(id -g)" -e HOME=/tmp tinytoon-build \
    bash -c '
        set -e
        cd /work
        if [ "$1" = windows ]; then
            export CC=x86_64-w64-mingw32-gcc WINDRES=x86_64-w64-mingw32-windres WINDOWS=1
            export SDL_CFLAGS="-I/opt/sdl3-win/include"
            export SDL_LIBS="-L/opt/sdl3-win/lib -lSDL3 -lkernel32 -luser32 -lgdi32 -lwinmm -limm32 -lole32 -loleaut32 -lversion -luuid -ladvapi32 -lsetupapi -lshell32 -ldinput8"
            export LDFLAGS="-static -static-libgcc" BUILD=build/win
        else
            export BUILD=build
        fi
        if [ "$2" = 1 ]; then ./build.sh /rom.bin --release; else ./build.sh /rom.bin; fi
    ' _ "$ALVO" "$RELEASE"
echo '== done'

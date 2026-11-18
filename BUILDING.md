# Building

Two paths. Docker needs nothing installed but Docker and works the same
everywhere. Native is faster to iterate on but you install the toolchain.

Ordinary builds produce a native executable from your ROM on your machine.
The separate AppImage mode packages only the runtime and tools, without a ROM;
the player supplies their own ROM at first launch.

---

## Your ROM

Playing the game, and building a monolithic executable, require your own 1 MB
raw `.bin` dump. See [LEGAL.md](LEGAL.md). Packaging the ROM-free AppImage does
not require a dump.

Put it next to the build script as `rom.bin`, or pass its path as the first
argument. The build verifies it before doing anything else and tells you exactly
what is wrong if something is:

| what it says | what happened |
|---|---|
| size is wrong | truncated download, or a `.zip`/`.7z` not yet extracted |
| SMD (interleaved) dump | you have a `.smd`; convert it to raw `.bin` |
| not a Mega Drive ROM | no `SEGA MEGA DRIVE` marker at `$100` |
| a different game | valid Mega Drive ROM, wrong title |
| checksum does not match | the file is damaged or was modified |
| different revision | development builds may run it with both SHA-1s logged; distributed builds require the exact European revision |

---

## Docker

```bash
./build-docker.sh                    # Linux ELF        -> build/tinytoon
./build-docker.sh windows            # Windows .exe     -> build/win/tinytoon.exe
./build-docker.sh windows --release  # single file, no console, with icon
./build-docker.sh linux /path/to/rom.bin
```

Docker checks the build definition on every invocation and reuses cached layers.
The first build compiles SDL3 from source for both targets and takes roughly ten
minutes; subsequent builds reuse that toolchain.

Your ROM is mounted **read-only** at a fixed path and never enters the image.
The container runs as your user, so the artifacts come out owned by you rather
than by root.

To rebuild the image from scratch: `docker rmi tinytoon-build`.

### Linux AppImage

```bash
./build-docker.sh linux --appimage
# Optional release filename:
./build-docker.sh linux --appimage --version v1.1.0
```

No ROM is mounted or required in this mode. It builds a runtime-only release
with `RUNTIME_ONLY=1`, then produces
`build/release/TinyToon-<version>-Linux-x64.AppImage`. The ordinary Linux and
Windows commands above still build monolithic executables from your ROM; those
contain ROM-derived game code and must not be published.

The AppImage bundles SDL3, Python 3.11, Capstone 5.0.7 and TinyCC, including the
headers needed to compile the game without a host toolchain. The packaging
image is separate (`tinytoon-appimage-build`). AppImage tools and their Type-2
runtime are pinned by checksum. Matching Debian source archives and third-party
license notices are included in `sources/` and `LICENSES/` inside the image;
packaging needs network access to retrieve the corresponding sources.

Bundled ELF files find their libraries through relative RPATHs, not a global
`LD_LIBRARY_PATH` that could break host helpers such as `/bin/sh`. TinyCC also
carries glibc's dynamic linker as a link input; generated modules still load the
host's glibc at runtime.
Bundled PatchELF 0.19.3 marks each generated module with a non-executable stack,
as required by modern glibc. Its source and license are included with the image.

The target is Linux x86_64 with glibc 2.36 or newer. Desktop graphics/audio
libraries and drivers come from the host. The first-run ROM picker uses the
desktop portal or Zenity. The image can run without FUSE:

```bash
./TinyToon-v1.1.0-Linux-x64.AppImage --appimage-extract-and-run --help
```

Normally, run the image with no arguments to select and remember a ROM, or pass
its path as the first argument. Data is written outside the image in
`${XDG_DATA_HOME:-$HOME/.local/share}/tinytoon`. `--data-dir DIR` selects another
writable directory for `tinytoon.toml`, `tinytoon.log`, `mods/` and `jogo.so`.
Changing the bundled builder invalidates the old local module; the next launch
rebuilds it from the player's ROM.

For unattended setup, use:

```bash
./TinyToon-v1.1.0-Linux-x64.AppImage --prepare --rom /path/to/rom.bin --saida /path/to/data
./TinyToon-v1.1.0-Linux-x64.AppImage --data-dir /path/to/data /path/to/rom.bin
```

The embedded `recomplauncher.json` uses those two modes with `{install}` as the
data directory. It works from both a mounted AppImage and an extracted AppDir,
including the RecompLauncher Flatpak's extraction path. Preparation keeps the
existing progress output and ROM error codes.

---

## Native

```bash
./build.sh                  # ./rom.bin, development build
./build.sh --release        # windowed: no console, log to file, icon
./build.sh --clean          # discard objects and generated C, start over
./build.sh -j8              # limit parallelism
```

### Prerequisites

| | |
|---|---|
| Python | 3.9 or newer |
| `capstone` | `pip install capstone` |
| C compiler | gcc or clang |
| make | any POSIX make |
| SDL3 | **3.2 or newer** — not SDL2 |

`build.sh` checks all of these before doing anything and stops with a specific
message naming what is missing.

### Debian / Ubuntu

```bash
sudo apt install build-essential make python3 python3-pip pkg-config
pip install capstone
```

SDL3 is new enough that many distributions still ship SDL2 only. If
`pkg-config --exists sdl3` fails, either use the Docker path or build SDL3:

```bash
git clone --depth 1 --branch release-3.4.14 https://github.com/libsdl-org/SDL
cmake -S SDL -B SDL/build -DCMAKE_BUILD_TYPE=Release
cmake --build SDL/build --parallel
sudo cmake --install SDL/build
sudo ldconfig
```

### Fedora / Arch

```bash
# Fedora
sudo dnf install gcc make python3 python3-pip pkgconf-pkg-config SDL3-devel
# Arch
sudo pacman -S base-devel python python-capstone sdl3
```

### macOS

**Untested.** The runtime is portable C and SDL3 supports macOS, so it should
work, but nobody has run it. If you try it, the result is worth reporting either
way.

```bash
brew install sdl3 python make
pip3 install capstone
./build.sh
```

### Windows

The straightforward path is `./build-docker.sh windows` from anywhere, which
cross-compiles and produces a single-file `.exe`.

Natively, use [MSYS2](https://www.msys2.org/) with the UCRT64 environment:

```bash
pacman -S mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-SDL3 \
          mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-pip make
pip install capstone
./build.sh
```

---

## What the build actually does

```
1. check the toolchain          stop early with a specific message
2. verify the ROM               tools/rom_info.py --check
3. translate 68000 to C         tools/recompile.py --all build/recomp
4. generate headers             ROM identity, build version
5. compile in parallel          make -j
```

Step 3 is the one that cannot ship pre-built, and the one that takes a minute or
two the first time. It is skipped on later builds unless the recompiler itself
changed — the check is a timestamp comparison against `tools/recompile.py`, so
editing the recompiler correctly forces a regeneration. That check exists
because without it a change to the recompiler would silently compile the old C
and the build would pass without containing the change.

The generated C is shared between targets; only objects and the executable live
under the per-target build directory.

---

## Build options

Everything is a normal make variable, so you can override any of it:

```bash
make CC=clang
make SDL_CFLAGS=-I/opt/sdl3/include SDL_LIBS="-L/opt/sdl3/lib -lSDL3"
make CFLAGS="-std=c99 -O0 -g"        # debuggable, but slow at run time
```

`-O2` is not decorative. The generated code is an enormous number of small
functions; at `-O0` the game does not reach full speed on modest hardware.

---

## If it goes wrong

**`multiple definition of main`** — something under `runtime/testes/` reached
the link. Those are standalone verification programs with their own `main()`,
and the Makefile's source list excludes them explicitly. If you added sources,
check that list.

**Link fails on SDL symbols** — you have SDL2, or SDL3 headers without the
library. `pkg-config --modversion sdl3` should print 3.2 or higher.

**The game runs but there is no window** — SDL3 built without a video backend.
On Linux that means the X11/Wayland development headers were missing when SDL3
was compiled. The Docker image installs them for this reason.

**It builds and immediately exits** — check `tinytoon.log` next to the
executable in a `--release` build, or the console output otherwise. The ROM
check reports there.

---

## Mods

```bash
cd mods
make                                          # for the host
make WINDOWS=1 CC=x86_64-w64-mingw32-gcc      # cross-compile to .dll
```

Any subdirectory with `.c` files in it is a mod and gets built; there is no list
to keep in sync. Copy the resulting library into `mods/` beside the executable.

A mod links against **nothing** from the game — every service arrives through a
struct of function pointers. That is what makes a mod work against the release
build, which is statically linked and stripped and exports no symbols at all.
See [docs/27-mods.md](docs/27-mods.md).

A headless run writes `tela-mod.png` alongside the clean `tela.png`, so a filter
can be verified without a screen: the difference between the two files is the
mod's effect.

## Verification

The gates that keep the project honest are described in
[docs/22-differential-testing.md](docs/22-differential-testing.md) and
[docs/25-codecs.md](docs/25-codecs.md). Most of them need reference
implementations that are not vendored here — Musashi, Nuked-OPN2, vasm — each
fetched and pinned by its own container in the original repository.

The one you can run immediately, with nothing extra:

```bash
python3 tools/rom_info.py --check    # your ROM against the recorded baseline
python3 tools/trace_code.py          # re-run the tracer; unresolved flow must be 0
python3 tools/rom_coverage.py        # how much of the ROM is classified
```

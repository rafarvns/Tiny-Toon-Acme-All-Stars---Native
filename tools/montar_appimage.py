"""Assemble a ROM-free, relocatable Linux AppImage inside the build image."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from gerar_icone import desenhar, png_rgba

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ("montar_jogo.py", "recompile.py", "trace_code.py", "romlib.py",
         "m68k.py", "m68k_ops.py", "ciclos.py")
ANALYSIS = ("data-regions.csv", "dispatchers.csv", "jump-tables.csv", "ram-names.csv",
            "stack-returns.csv", "symbol-names.csv", "rom-baseline.json")
RUNTIME = ("m68k.h", "memory.h", "jogo_api.h", "ponte_jogo.c")
HOST_LIBS = re.compile(r"^(?:ld-linux|libc\.so|libm\.so|libdl\.so|libpthread\.so|librt\.so|libresolv\.so|libnss_)")
FORBIDDEN = ("jogo.dll", "jogo.so", "*.bin", "*.smd", "*.gen", "*.md1", "rom.*",
             "tinytoon.toml", "*.log", "*.pyc")
GENERATED = re.compile(rb"MD_FUNCAO\(0x[0-9A-F]{6}\)")


def copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=True)


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True)


def deps(path: Path) -> list[tuple[str, Path]]:
    result = subprocess.run(["ldd", str(path)], text=True, capture_output=True)
    if "not found" in result.stdout:
        raise RuntimeError(f"Missing shared library for {path}: {result.stdout}")
    found = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\S+) => (/\S+)", line)
        if match and not HOST_LIBS.match(match.group(1)):
            found.append((match.group(1), Path(match.group(2))))
    return found


def copy_libraries(app: Path) -> set[Path]:
    lib = app / "usr/lib"
    queue = []
    for path in (app / "usr").rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) == b"\x7fELF":
                    queue.append(path)
    queue.extend(path for path in (app / "tcc/bin").iterdir() if path.is_file())
    seen: set[str] = set()
    sources: set[Path] = set()
    while queue:
        binary = queue.pop()
        for name, source in deps(binary):
            if name in seen:
                continue
            seen.add(name)
            sources.add(source.resolve())
            destination = lib / name
            copy(source, destination)
            queue.append(destination)
    return sources


def isolate_libraries(app: Path) -> None:
    # Host helpers such as /bin/sh must not load the bundled Python libraries.
    # Keep each packaged ELF's library search local instead of exporting it.
    library_dir = app / "usr/lib"
    binaries = list((app / "usr").rglob("*")) + list((app / "tcc/bin").iterdir())
    for path in binaries:
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        relative = os.path.relpath(library_dir, path.parent)
        rpath = "$ORIGIN" if relative == "." else f"$ORIGIN/{relative}"
        subprocess.run(["patchelf", "--force-rpath", "--set-rpath", rpath, str(path)],
                       check=True)


def libc_headers(app: Path) -> None:
    # Only the distribution's libc development headers, not the host's SDK.
    target = app / "tcc/sysinclude"
    for name in command("dpkg-query", "-L", "libc6-dev").splitlines():
        path = Path(name)
        if name.startswith("/usr/include/") and path.is_file():
            copy(path, target / path.relative_to("/usr/include"))


def tcc_toolchain(app: Path) -> None:
    tcc = app / "tcc"
    copy(Path("/usr/bin/tcc"), tcc / "bin/tcc")
    copy(Path("/usr/local/bin/patchelf"), tcc / "bin/patchelf")
    candidates = [Path(p) for p in command("dpkg-query", "-L", "tcc").splitlines()
                  if p.endswith("/libtcc1.a")]
    if len(candidates) != 1:
        raise RuntimeError("Could not find the TinyCC runtime directory")
    shutil.copytree(candidates[0].parent, tcc / "lib", symlinks=False)
    libc_headers(app)
    syslib = tcc / "syslib"
    for name in ("crti.o", "crtn.o", "crt1.o", "Scrt1.o", "libc.so.6", "libm.so.6", "libdl.so.2"):
        path = Path("/usr/lib/x86_64-linux-gnu") / name
        if path.exists():
            copy(path, syslib / name)
    # TinyCC reads libc's loader dependency even when linking a shared module.
    loader = Path("/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")
    copy(loader, syslib / loader.name)
    # Use the bundled link inputs, but generated modules still load host glibc.
    (syslib / "libc.so").write_text("INPUT ( libc.so.6 )\n")
    (syslib / "libm.so").write_text("INPUT ( libm.so.6 )\n")
    # Keep project headers ahead of libc's same-named memory.h.
    wrapper = tcc / "tcc"
    wrapper.write_text('''#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
unset CPATH C_INCLUDE_PATH LIBRARY_PATH
output=
previous=
shared=0
for argument do
    if [ "$previous" = "-o" ]; then output=$argument; fi
    if [ "$argument" = "-shared" ]; then shared=1; fi
    previous=$argument
done
"$root/bin/tcc" -B"$root/lib" -nostdinc -nostdlib -isystem "$root/lib/include" -isystem "$root/sysinclude" -isystem "$root/sysinclude/x86_64-linux-gnu" -L"$root/syslib" -L"$root/lib" "$@" -ltcc1 -lc
if [ "$shared" = 1 ] && [ -n "$output" ]; then
    "$root/bin/patchelf" --clear-execstack "$output"
fi
''')
    wrapper.chmod(0o755)
    shutil.copytree("/opt/tinytoon-sources/tcc", app / "sources/tcc")
    shutil.copytree("/opt/tinytoon-sources/patchelf", app / "sources/patchelf")


def notices(app: Path, libraries: set[Path]) -> None:
    target = app / "LICENSES"
    copy(ROOT / "LICENSE", target / "tinytoon.txt")
    copy(Path("/opt/tinytoon-licenses/SDL3.txt"), target / "SDL3.txt")
    copy(Path("/opt/tinytoon-runtime-license"), target / "appimage-runtime.txt")
    copy(Path("/opt/tinytoon-licenses/patchelf.txt"), target / "patchelf.txt")
    dist = next(Path("/usr/local/lib/python3.11/dist-packages").glob("capstone-*.dist-info"))
    licenses = list(dist.glob("LICENSE*")) + list((dist / "licenses").glob("LICENSE*"))
    if not licenses:
        raise RuntimeError("Capstone license is missing")
    copy(licenses[0], target / "capstone.txt")
    packages = {"python3.11", "libpython3.11-stdlib", "libc6", "libc6-dev", "tcc"}
    for path in libraries:
        # Debian's merged-/usr systems may record a library under /lib while
        # realpath() resolves it under /usr/lib (or vice versa).
        names = [str(path)]
        if str(path).startswith("/usr/lib/"):
            names.append(str(path)[4:])
        elif str(path).startswith("/lib/"):
            names.append("/usr" + str(path))
        for name in names:
            result = subprocess.run(["dpkg-query", "-S", name], capture_output=True, text=True)
            if result.returncode == 0:
                packages.update(result.stdout.split(": ", 1)[0].replace(":amd64", "").split(", "))
                break
        else:
            if not str(path).startswith("/usr/local/lib/"):
                raise RuntimeError(f"No Debian package owns bundled library {path}")
    for package in sorted(packages):
        source = Path("/usr/share/doc") / package / "copyright"
        if source.is_file():
            copy(source, target / f"{package}.txt")
        else:
            raise RuntimeError(f"Missing copyright notice for {package}")
    source_packages = {}
    for package in sorted(packages):
        info = command("dpkg-query", "-W", "-f=${source:Package}\\t${source:Version}", package)
        source, version = info.strip().split("\t")
        source_packages.setdefault((source, version), []).append(package)
    sources = app / "sources"
    for (source, version), binaries in sorted(source_packages.items()):
        if source == "tcc":
            continue  # Already downloaded in the packaging image.
        directory = sources / source
        directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(["apt-get", "source", "--download-only", f"{source}={version}"],
                       cwd=directory, check=True)
    (sources / "SOURCES.txt").write_text(
        "Exact Debian source packages corresponding to redistributed binaries and headers.\n"
        "Each directory contains the .dsc and accompanying source archives; unpack with "
        "`dpkg-source -x <file>.dsc`. Redistribute this directory with the AppImage.\n\n"
        "PatchELF 0.19.3 source is supplied separately in sources/patchelf.\n\n"
        + "".join(f"{name} {version}: {', '.join(binaries)}\n"
                  for (name, version), binaries in sorted(source_packages.items())),
        encoding="utf-8")
    (target / "platform.txt").write_text(
        "Linux x86_64, glibc 2.36 or newer. System graphics/audio drivers are required.\n"
        "TinyCC source, including Debian modifications, is supplied in sources/tcc.\n"
        "AppImage runtime source: https://github.com/AppImage/type2-runtime/tree/20251108\n")


def barrier(app: Path) -> None:
    for path in app.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(app)
        if any(fnmatch.fnmatch(path.name.lower(), pattern) for pattern in FORBIDDEN):
            raise RuntimeError(f"Forbidden file: {rel}")
        data = path.read_bytes()
        if len(data) == 1048576 or GENERATED.search(data):
            raise RuntimeError(f"ROM-sized or generated game content: {rel}")
        if data.startswith(b"\x7fELF"):
            symbols = subprocess.run(["readelf", "--dyn-syms", "--wide", str(path)],
                                     capture_output=True, text=True, check=True).stdout
            for line in symbols.splitlines():
                cols = line.split()
                if len(cols) >= 8 and cols[6] != "UND" and cols[7].split("@")[0] == "jogo_ligar":
                    raise RuntimeError(f"Game module export in {rel}")


def assemble(app: Path, version: str) -> None:
    copy(ROOT / "build/appimage-runtime/tinytoon", app / "usr/bin/tinytoon")
    copy(ROOT / "packaging/AppRun", app / "AppRun")
    (app / "AppRun").chmod(0o755)
    copy(ROOT / "packaging/tinytoon.desktop", app / "tinytoon.desktop")
    (app / "tinytoon.png").write_bytes(png_rgba(256, 256, desenhar(256)))
    copy(ROOT / "packaging/launch.py", app / "usr/share/tinytoon/launch.py")
    builder = app / "montador"
    for directory, names in (("tools", TOOLS), ("analysis", ANALYSIS), ("runtime", RUNTIME)):
        for name in names:
            copy(ROOT / directory / name, builder / directory / name)
    manifest = json.loads((ROOT / "recomplauncher.json").read_text())
    image = f"TinyToon-{version}-Linux-x64.AppImage"
    manifest["start"] = {"program": [image], "args": ["--data-dir", "{install}", "{game}"]}
    manifest["prepare"]["program"] = [image]
    manifest["prepare"]["args"] = ["--prepare", "--rom", "{game}", "--saida", "{install}"]
    (app / "recomplauncher.json").write_text(json.dumps(manifest, indent=2) + "\n")
    copy(Path("/usr/bin/python3.11"), app / "usr/bin/python3")
    shutil.copytree("/usr/lib/python3.11", app / "usr/lib/python3.11",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "test", "tests", "ensurepip"))
    site = app / "usr/lib/python3.11/site-packages"
    source = Path("/usr/local/lib/python3.11/dist-packages")
    shutil.copytree(source / "capstone", site / "capstone", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    tcc_toolchain(app)
    copy(Path("/usr/local/lib/libSDL3.so.0"), app / "usr/lib/libSDL3.so.0")
    libraries = copy_libraries(app)
    isolate_libraries(app)
    digest = hashlib.sha256()
    inputs = [p for p in builder.rglob("*") if p.is_file()]
    inputs.append(app / "usr/bin/python3")
    inputs += [p for p in (app / "tcc").rglob("*") if p.is_file()]
    inputs += [p for p in (site / "capstone").rglob("*") if p.is_file()]
    tcc_version = subprocess.run(["/usr/bin/tcc", "-v"], capture_output=True, check=True)
    digest.update(tcc_version.stdout + tcc_version.stderr + b"\0")
    for path in sorted(inputs):
        digest.update(path.relative_to(app).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    (builder / "builder.sha256").write_text(digest.hexdigest() + "\n", encoding="ascii")
    notices(app, libraries)
    barrier(app)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    args = parser.parse_args()
    if args.version:
        version = args.version
    else:
        header = (ROOT / "runtime/versao.h").read_text()
        version = re.search(r'^#define TINYTOON_VERSAO "([^"]+)"', header, re.M).group(1)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
        parser.error("version must contain only letters, digits, '.', '_', '+' or '-'")
    dest = ROOT / "build/release" / f"TinyToon-{version}-Linux-x64.AppImage"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tinytoon-appimage-") as tmp:
        app = Path(tmp) / "TinyToon.AppDir"
        app.mkdir()
        assemble(app, version)
        output = Path(tmp) / dest.name
        env = dict(os.environ, ARCH="x86_64")
        subprocess.run(["/opt/appimagetool/AppRun", "--runtime-file", "/opt/appimage-runtime",
                        str(app), str(output)], check=True, env=env)
        shutil.copyfile(output, dest)
        dest.chmod(0o755)
    print(dest)


if __name__ == "__main__":
    main()

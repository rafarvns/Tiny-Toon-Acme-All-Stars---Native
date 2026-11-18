"""Run the ROM-free AppImage from a writable directory, or prepare its game module."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


BUILD_FAILED = 20


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "tinytoon"


def builder_id(appdir: Path) -> str:
    return (appdir / "montador" / "builder.sha256").read_text(encoding="ascii").split()[0]


def prepare(appdir: Path, rom: Path, destination: Path) -> int:
    command = [
        sys.executable, "-B", str(appdir / "montador" / "tools" / "montar_jogo.py"),
        "--rom", str(rom), "--saida", str(destination),
        "--tcc", str(appdir / "tcc" / "tcc"),
    ]
    result = subprocess.run(command)
    if result.returncode != 0:
        return result.returncode if result.returncode > 0 else BUILD_FAILED

    # The ROM-free package cannot contain a hash of verified, ROM-derived C.
    # This stamp instead invalidates the local module when its builder changes.
    stamp = destination / ".builder-id"
    temporary = destination / ".builder-id.new"
    temporary.write_text(builder_id(appdir) + "\n", encoding="ascii")
    temporary.replace(stamp)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="build from your ROM without starting the game")
    parser.add_argument("--rom", help="ROM path for --prepare")
    parser.add_argument("--saida", type=Path, help="output directory for --prepare")
    parser.add_argument("--data-dir", type=Path, help="configuration, log, mods and generated game directory")
    parser.add_argument("game", nargs="?", help="your own European cartridge ROM")
    parser.add_argument("budget", nargs="?", help="optional diagnostic execution budget")
    parser.add_argument("frames", nargs="?", help="optional diagnostic frame limit")
    args = parser.parse_args(argv)

    if args.prepare and (not args.rom or args.game or args.budget or args.frames):
        parser.error("--prepare requires --rom PATH and no positional arguments")
    if not args.prepare and (args.rom or args.saida):
        parser.error("--rom and --saida are only used with --prepare")

    appdir = Path(os.environ["TINYTOON_APPDIR"]).resolve()
    directory = args.saida if args.prepare else args.data_dir
    destination = (directory if directory is not None else default_data_dir()).expanduser().resolve()
    rom = Path(args.rom or args.game).expanduser().resolve() if args.rom or args.game else None
    destination.mkdir(parents=True, exist_ok=True)

    if args.prepare:
        return prepare(appdir, rom, destination)

    # Generated code lives outside the read-only mount, even when the image was
    # unpacked by RecompLauncher's Flatpak. Never execute an old builder's module.
    stamp = destination / ".builder-id"
    previous = stamp.read_text(encoding="ascii").strip() if stamp.is_file() else ""
    if previous != builder_id(appdir):
        (destination / "jogo.so").unlink(missing_ok=True)
        stamp.unlink(missing_ok=True)

    os.chdir(destination)
    command = [str(appdir / "usr" / "bin" / "tinytoon")]
    if rom is not None:
        command.append(str(rom))
    if args.budget is not None:
        command.append(args.budget)
    if args.frames is not None:
        command.append(args.frames)
    os.execv(command[0], command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError) as exc:
        print(f"Could not start Tiny Toon: {exc}", file=sys.stderr)
        raise SystemExit(BUILD_FAILED)

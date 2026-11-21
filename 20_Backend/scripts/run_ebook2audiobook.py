#!/usr/bin/env python3
"""Run ebook2audiobook through Docker with repo-friendly defaults.

This wrapper keeps the upstream ebook2audiobook project as the source of
truth while giving WebTPDF a stable local entrypoint for audiobook
generation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
CACHE_ROOT = Path.home() / ".cache" / "webtpdf" / "ebook2audiobook"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "40_Ops" / "output" / "audiobooks"
DEFAULT_MODELS_DIR = CACHE_ROOT / "models"
DEFAULT_TMP_DIR = CACHE_ROOT / "tmp"
DEFAULT_IMAGE = "athomasson2/ebook2audiobook:cpu"
DOCKER_CANDIDATE_PATHS = (
    Path("/usr/local/bin/docker"),
    Path("/opt/homebrew/bin/docker"),
    Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
)
SUPPORTED_DOCKER_DEVICES = {"CPU", "CUDA", "ROCM", "XPU", "JETSON"}

CONTAINER_EBOOK_DIR = Path("/mnt/ebook")
CONTAINER_VOICE_DIR = Path("/mnt/voice")
CONTAINER_MODEL_DIR = Path("/mnt/custom_model")
CONTAINER_OUTPUT_DIR = Path("/app/audiobooks")
CONTAINER_MODELS_DIR = Path("/app/models")
CONTAINER_TMP_DIR = Path("/app/tmp")


def parse_device(raw_device: str) -> str:
    device = raw_device.upper()
    if device == "MPS":
        raise argparse.ArgumentTypeError("MPS is not exposed to Docker containers. Use CPU or run upstream locally.")
    if device not in SUPPORTED_DOCKER_DEVICES:
        supported = ", ".join(sorted(SUPPORTED_DOCKER_DEVICES))
        raise argparse.ArgumentTypeError(f"unsupported device: {raw_device}. Choose one of: {supported}")
    return device


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ebook2audiobook via Docker with WebTPDF defaults."
    )
    parser.add_argument("--ebook", required=True, help="Path to the input ebook file.")
    parser.add_argument(
        "--language",
        default="eng",
        help="Language code for the narration (default: eng).",
    )
    parser.add_argument(
        "--voice",
        help="Optional voice sample file for voice cloning.",
    )
    parser.add_argument(
        "--custom-model",
        help="Optional custom model zip file.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory for generated audiobooks (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--output-format",
        default="m4b",
        help="Audio output format passed to ebook2audiobook (default: m4b).",
    )
    parser.add_argument(
        "--tts-engine",
        help="Optional TTS engine override forwarded to ebook2audiobook.",
    )
    parser.add_argument(
        "--device",
        type=parse_device,
        help="Optional Docker device override forwarded to ebook2audiobook (CPU, CUDA, ROCM, XPU, JETSON; case-insensitive).",
    )
    parser.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help=f"Docker image to run (default: {DEFAULT_IMAGE}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the docker command without executing it.",
    )
    return parser.parse_args()


def resolve_existing_file(raw_path: str, label: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise SystemExit(f"{label} path is empty.")

    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")
    if not path.is_file():
        raise SystemExit(f"{label} must be a file: {path}")
    return path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_output_dir(raw_path: str, create: bool) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if create:
        return ensure_dir(path)
    return path


def resolve_docker_command(require: bool) -> str:
    found = shutil.which("docker")
    if found:
        return found

    for candidate in DOCKER_CANDIDATE_PATHS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    if require:
        searched = ", ".join(str(path) for path in DOCKER_CANDIDATE_PATHS)
        raise SystemExit(
            "docker was not found on PATH or at the standard Docker Desktop paths. "
            f"Searched: {searched}"
        )

    return "docker"


def mount_volume(host_path: Path, container_path: Path, read_only: bool = False) -> list[str]:
    suffix = ":ro" if read_only else ""
    return ["-v", f"{host_path}:{container_path}{suffix}"]


def docker_device_args(device: str | None) -> list[str]:
    if device == "CUDA":
        return ["--gpus", "all"]
    if device == "ROCM":
        return ["--device=/dev/kfd", "--device=/dev/dri"]
    if device == "XPU":
        return ["--device=/dev/dri"]
    if device == "JETSON":
        return ["--runtime", "nvidia"]
    return []


def validate_device_image(device: str | None, image: str) -> None:
    if device and device != "CPU" and image == DEFAULT_IMAGE:
        raise SystemExit(
            f"--device {device.lower()} requires a matching GPU/runtime image via --image. "
            "For example: --image athomasson2/ebook2audiobook:cu128"
        )


def build_command(args: argparse.Namespace) -> list[str]:
    validate_device_image(args.device, args.image)
    ebook_path = resolve_existing_file(args.ebook, "ebook")

    voice_path = None
    if args.voice:
        voice_path = resolve_existing_file(args.voice, "voice")

    custom_model_path = None
    if args.custom_model:
        custom_model_path = resolve_existing_file(args.custom_model, "custom model")

    docker_command = resolve_docker_command(require=not args.dry_run)
    output_dir = resolve_output_dir(args.output_dir, create=not args.dry_run)
    models_dir = resolve_output_dir(str(DEFAULT_MODELS_DIR), create=not args.dry_run)
    tmp_dir = resolve_output_dir(str(DEFAULT_TMP_DIR), create=not args.dry_run)

    command: list[str] = [docker_command, "run", "--rm"]
    if sys.stdin.isatty() and sys.stdout.isatty():
        command.append("-it")
    else:
        command.append("-i")

    command += ["-e", "USER=root"]
    command += docker_device_args(args.device)
    command += mount_volume(ebook_path.parent, CONTAINER_EBOOK_DIR, read_only=True)
    command += mount_volume(output_dir, CONTAINER_OUTPUT_DIR)
    command += mount_volume(models_dir, CONTAINER_MODELS_DIR)
    command += mount_volume(tmp_dir, CONTAINER_TMP_DIR)

    if args.voice:
        command += mount_volume(voice_path.parent, CONTAINER_VOICE_DIR, read_only=True)

    if args.custom_model:
        command += mount_volume(custom_model_path.parent, CONTAINER_MODEL_DIR, read_only=True)

    command.append(args.image)
    command += [
        "--script_mode",
        "full_docker",
        "--headless",
        "--ebook",
        str(CONTAINER_EBOOK_DIR / ebook_path.name),
        "--language",
        args.language,
        "--output_dir",
        str(CONTAINER_OUTPUT_DIR),
        "--output_format",
        args.output_format,
    ]

    if args.tts_engine:
        command += ["--tts_engine", args.tts_engine]

    if args.device:
        command += ["--device", args.device]

    if voice_path:
        command += ["--voice", str(CONTAINER_VOICE_DIR / voice_path.name)]

    if custom_model_path:
        command += ["--custom_model", str(CONTAINER_MODEL_DIR / custom_model_path.name)]

    return command


def main() -> int:
    args = parse_args()
    command = build_command(args)

    if args.dry_run:
        print(" ".join(shlex_quote(part) for part in command))
        return 0

    print("Running ebook2audiobook with:")
    print(" ".join(shlex_quote(part) for part in command))
    completed = subprocess.run(command, check=False)
    return completed.returncode


def shlex_quote(value: str) -> str:
    if not value:
        return "''"
    if all(ch.isalnum() or ch in "@%_-+=:,./" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())

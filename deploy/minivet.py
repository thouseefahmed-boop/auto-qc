"""Deploy the independent rtx-3 AutoQC app; no existing .env is required."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    if socket.gethostname().split(".")[0] != "rtx-3":
        raise SystemExit("This deployment targets rtx-3 only")
    if not (root / "_build/alfred/pyproject.toml").is_file():
        raise SystemExit("Stage pinned MiniVET alfred_src in _build/alfred before building")
    uuid = subprocess.check_output([
        "nvidia-smi", "--id=2", "--query-gpu=uuid", "--format=csv,noheader"
    ], text=True).strip()
    if not uuid.startswith("GPU-") or "\n" in uuid:
        raise SystemExit("Cannot identify physical GPU index 2")
    config = root / ".minivet-runtime.json"
    if config.exists():
        credentials = json.loads(config.read_text())
    else:
        credentials = {"POSTGRES_PASSWORD": secrets.token_hex(32),
                       "AUTOQC_WORKER_TOKEN": secrets.token_hex(32)}
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(credentials, handle)
    env = {**os.environ, **credentials, "AUTOQC_GPU_UUID": uuid}
    subprocess.run([
        "docker", "compose", "-f", "compose.minivet.yaml", "up", "-d",
        "--build", "--wait", "--wait-timeout", "180"
    ], cwd=root, env=env, check=True)
    print("AutoQC: http://rtx-3.dev.internal:8810")
    print(f"Cosmos worker exposes only GPU index 2 ({uuid})")
    print("Inference route: MiniVET Alfred -> prod.dev.internal/cosmos")


if __name__ == "__main__":
    main()

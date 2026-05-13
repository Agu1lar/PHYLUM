from __future__ import annotations

import argparse
import os

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the packaged Agente Desktop backend.")
    parser.add_argument("--host", default=os.getenv("AGENTE_BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AGENTE_BACKEND_PORT", "8000")))
    parser.add_argument("--log-level", default=os.getenv("AGENTE_BACKEND_LOG_LEVEL", "info"))
    parser.add_argument("--allow-lan", action="store_true", default=os.getenv("AGENTE_ALLOW_LAN", "").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--public-base-url", default=os.getenv("AGENTE_PUBLIC_BASE_URL", ""))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ["AGENTE_RUNTIME_HOST"] = args.host
    os.environ["AGENTE_RUNTIME_PORT"] = str(args.port)
    if args.allow_lan:
        os.environ["AGENTE_ALLOW_LAN"] = "true"
    if args.public_base_url:
        os.environ["AGENTE_PUBLIC_BASE_URL"] = args.public_base_url
    uvicorn.run(
        "app_main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()

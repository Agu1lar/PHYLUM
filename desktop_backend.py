from __future__ import annotations

import argparse
import os

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the packaged Agente Desktop backend.")
    parser.add_argument("--host", default=os.getenv("AGENTE_BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AGENTE_BACKEND_PORT", "8000")))
    parser.add_argument("--log-level", default=os.getenv("AGENTE_BACKEND_LOG_LEVEL", "info"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run(
        "app_main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()

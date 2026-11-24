"""Container entry point: python -m backend.serve (honors Render's PORT)."""
import os

import uvicorn


def main():
    try:
        port = int(os.getenv("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise SystemExit("PORT must be an integer between 1 and 65535.") from None
    # Locks and rate limits are process-local, even when PostgreSQL stores data.
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, workers=1)


if __name__ == "__main__":
    main()

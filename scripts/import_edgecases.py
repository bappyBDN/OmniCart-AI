"""Validate and import the multilingual edge-case Q&A into the RAG corpus."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import database
from backend.config import settings
from backend.edgecases import load_edgecases, import_edgecases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write to the configured database")
    args = parser.parse_args()
    entries = load_edgecases()
    if not args.apply:
        print(f"Validated {len(entries)} multilingual edge-case Q&A pairs. No database writes.")
        return
    database.initialize()
    with database.transaction() as db:
        count = import_edgecases(db, settings.shop_id, entries)
    print(f"Imported {count} edge-case pairs into the RAG corpus for shop {settings.shop_id}.")


if __name__ == "__main__":
    main()

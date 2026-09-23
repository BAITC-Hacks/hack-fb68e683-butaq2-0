"""Validate a complete starter kit; replace the active catalogue only with --replace."""
import argparse
import json
from pathlib import Path

from multi_agent.contracts import Catalog
from multi_agent.starter_kit import catalog_status


def load_catalog(directory: Path) -> Catalog:
    def read(name: str):
        return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    return Catalog.from_payload(read("scenarios"), knowledge=read("knowledge_base"), backend=read("mock_backend"), slots=read("slots"), actions=read("actions"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--replace", action="store_true", help="Explicitly replace the existing catalogue and reference data")
    args = parser.parse_args()
    catalog = load_catalog(args.directory)
    status = catalog_status(catalog)
    if not status["official_ids_complete"]:
        parser.error("Expected all forty original scenarios SC01–SC40")
    if args.replace:
        from app.db.repository import RouterDatabase
        RouterDatabase().replace_catalog(catalog)
    print(json.dumps({**status, "replaced": args.replace}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import process_species


def _read_species_file(path: Path) -> list[str]:
    """Read a species list file, ignoring comments and blank lines.

    Args:
        path (Path): Path to the species list file.

    Returns:
        list[str]: List of species names.
    """
    species = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            species.append(line)
    return species


def main() -> None:
    parser = argparse.ArgumentParser(prog="birdclock-songs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Download and preprocess bird songs")
    fetch.add_argument("species", nargs="*", help="Species common names, e.g. 'American Robin'")
    fetch.add_argument(
        "--from-file", type=Path, help="Path to a species list file (one name per line)"
    )
    fetch.add_argument(
        "--output-dir", type=Path, default=Path("birdsongs"), help="Where to write mp3s"
    )
    fetch.add_argument(
        "--force", action="store_true", help="Re-fetch even if the output file already exists"
    )
    fetch.add_argument(
        "--candidates",
        type=int,
        default=3,
        help="Number of candidate clips to build per species (default: 3), written as "
        "<output-dir>/<slug>_1.mp3, <slug>_2.mp3, ... -- each is an independent hourly play "
        "unit, played back to back when its species' hour comes up on the clock.",
    )

    args = parser.parse_args()

    if args.command == "fetch":
        species_list = list(args.species)
        if args.from_file:
            species_list.extend(_read_species_file(args.from_file))

        if not species_list:
            parser.error("no species given: pass names as arguments or --from-file")
        if args.candidates < 1:
            parser.error("--candidates must be at least 1")

        for species in species_list:
            try:
                process_species(
                    species,
                    output_dir=args.output_dir,
                    force=args.force,
                    num_candidates=args.candidates,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  error processing {species!r}: {exc}")


if __name__ == "__main__":
    main()

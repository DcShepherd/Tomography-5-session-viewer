from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tomography_session_browser.parsers.mrc_metadata_parser import format_mrc_metadata_report, parse_mrc_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect parsed MRC header and extended-header metadata.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--frame", type=int, default=None, help="Show one raw stack frame index.")
    parser.add_argument("--all-frames", action="store_true", help="Show compact metadata for all frames.")
    parser.add_argument("--tilts", action="store_true", help="Print parsed tilt angles in raw stack order.")
    parser.add_argument("--json", action="store_true", help="Emit parser output as JSON.")
    args = parser.parse_args()

    metadata = parse_mrc_metadata(args.path)
    if args.json:
        print(json.dumps(asdict(metadata), indent=2, default=str))
    elif args.tilts:
        for frame in metadata.frame_metadata:
            if frame.tilt_angle is not None:
                print(f"{frame.tilt_angle:7.2f}")
    else:
        print(format_mrc_metadata_report(metadata, frame_index=args.frame, all_frames=args.all_frames))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


EXPECTED_ENV = "tomoapp"


def main() -> int:
    prefix_name = Path(sys.prefix).name.lower()
    conda_env = (os.environ.get("CONDA_DEFAULT_ENV") or "").lower()
    if prefix_name != EXPECTED_ENV and conda_env != EXPECTED_ENV:
        print(
            "Refusing to run tests outside the tomoapp conda environment.\n"
            f"sys.prefix={sys.prefix}\n"
            f"CONDA_DEFAULT_ENV={os.environ.get('CONDA_DEFAULT_ENV')}",
            file=sys.stderr,
        )
        return 2

    return pytest.main(["-q", "-p", "no:cacheprovider"])


if __name__ == "__main__":
    raise SystemExit(main())

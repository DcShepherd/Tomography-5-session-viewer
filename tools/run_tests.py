from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Match on a prefix rather than an exact name. The documented environment is
# `tomoapp_session`, but working checkouts also use suffixed variants such as
# `tomoapp_claude`. An exact comparison against "tomoapp" matched neither and
# refused every real environment.
EXPECTED_ENV_PREFIX = "tomoapp"


def main() -> int:
    prefix_name = Path(sys.prefix).name.lower()
    conda_env = (os.environ.get("CONDA_DEFAULT_ENV") or "").lower()
    if not (
        prefix_name.startswith(EXPECTED_ENV_PREFIX)
        or conda_env.startswith(EXPECTED_ENV_PREFIX)
    ):
        print(
            f"Refusing to run tests outside a {EXPECTED_ENV_PREFIX}* conda environment.\n"
            f"sys.prefix={sys.prefix}\n"
            f"CONDA_DEFAULT_ENV={os.environ.get('CONDA_DEFAULT_ENV')}",
            file=sys.stderr,
        )
        return 2

    return pytest.main(["-q", "-p", "no:cacheprovider"])


if __name__ == "__main__":
    raise SystemExit(main())

import numpy as np

ABI_VERSION: int
MAX_TILE_ROWS: int
MAX_WORKERS: int
X86_64_V3: int
def sample(source: np.ndarray, matrix: np.ndarray, offset: np.ndarray,
           h: int, v: int, n: int, first: int, last: int, top: int, end: int,
           threads: int, output: np.ndarray, planes: tuple[np.ndarray, ...] | None,
           fresh: tuple[bool, ...] | None) -> None: ...

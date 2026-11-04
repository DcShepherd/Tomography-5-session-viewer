"""Known terminal reconstruction endings; no aliases or fuzzy name matching."""
from pathlib import PurePosixPath
import re
import unicodedata


def reconstruction_stem(value: str) -> str:
    name = PurePosixPath(value.replace('\\', '/')).name
    name = re.sub(r'\.(mrc|map|rec)$', '', name, flags=re.I)
    previous = ''
    while name != previous:
        previous = name
        name = re.sub(r'(?:_full_rec|_rec|_Vol|_\d+(?:\.\d+)?Apx)$', '', name, flags=re.I)
    return name


def reconstruction_name(value: str) -> str:
    return unicodedata.normalize('NFC', reconstruction_stem(value)).casefold()

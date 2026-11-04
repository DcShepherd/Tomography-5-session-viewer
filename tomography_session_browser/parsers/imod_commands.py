"""Literal IMOD StandardInput parameters; command files are never executed."""
from pathlib import Path, PureWindowsPath
import re


def standard_input(text: str, program: str) -> dict[str, tuple[str, ...]]:
    active, blocks = False, 0
    values: dict[str, tuple[str, ...]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('$'):
            if re.match(r'\$\s*cd(?:\s|$)', line, re.IGNORECASE):
                raise ValueError('Commands that change directory are not supported.')
            active = bool(re.fullmatch(r'\$' + re.escape(program) + r'\s+-StandardInput', line, re.IGNORECASE))
            blocks += int(active)
        elif active and line and not line.startswith('#'):
            parts = line.split(None, 1)
            key = parts[0].casefold()
            if key in ('endinput', 'done'):
                active = False
            elif key in ('reproject', 'projectmodel'):
                raise ValueError('Reprojection outputs are not reconstruction volumes.')
            else:
                values[key] = (*values.get(key, ()), parts[1].strip() if len(parts) == 2 else '')
    if blocks != 1:
        raise ValueError(f'Requires one {program} -StandardInput block.')
    return values


def parameter(values: dict[str, tuple[str, ...]], name: str) -> str:
    found = values.get(name.casefold(), ())
    if len(found) != 1 or not found[0]:
        raise ValueError(f'Requires one explicit {name}.')
    return found[0]


def literal_path(raw: str, root: Path) -> Path:
    if raw[:1] in ('"', "'") and raw[-1:] == raw[:1]:
        raw = raw[1:-1]
    foreign = PureWindowsPath(raw)
    if not raw or any(c in raw for c in '$`*?;|<>"\'') or foreign.drive or raw.startswith(('/', '\\')):
        raise ValueError('Requires a literal relative path inside this processing folder.')
    output = (root / raw.replace('\\', '/')).resolve()
    if not output.is_relative_to(root.resolve()):
        raise ValueError('Recorded path is outside this processing folder.')
    return output

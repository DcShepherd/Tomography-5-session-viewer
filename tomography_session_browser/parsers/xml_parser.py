from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def parse_xml_file(path: Path) -> dict[str, Any]:
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as exc:
        return {"_path": str(path), "_parse_error": str(exc)}
    except OSError as exc:
        return {"_path": str(path), "_read_error": str(exc)}

    data = element_to_data(root)
    if isinstance(data, dict):
        data.setdefault("_root", local_name(root.tag))
        data.setdefault("_path", str(path))
        return data
    return {"_root": local_name(root.tag), "_path": str(path), "value": data}


def element_to_data(element: ElementTree.Element) -> Any:
    children = list(element)
    text = (element.text or "").strip()
    attributes = {local_name(key): value for key, value in element.attrib.items()}

    if not children:
        if attributes:
            payload: dict[str, Any] = {"_attributes": attributes}
            if text:
                payload["value"] = coerce_scalar(text)
            return payload
        return coerce_scalar(text) if text else None

    grouped: dict[str, list[Any]] = {}
    for child in children:
        grouped.setdefault(local_name(child.tag), []).append(element_to_data(child))

    payload = {key: values[0] if len(values) == 1 else values for key, values in grouped.items()}
    if attributes:
        payload["_attributes"] = attributes
    if text:
        payload["_text"] = coerce_scalar(text)
    return payload


def coerce_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "nan":
        return None
    try:
        if any(marker in value for marker in (".", "E", "e")):
            parsed = float(value)
            return parsed if math.isfinite(parsed) else None
        return int(value)
    except ValueError:
        return value


def find_first(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = find_first(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_first(item, key)
            if found is not None:
                return found
    return None


def find_all(data: Any, key: str) -> list[tuple[tuple[str, ...], Any]]:
    """Return all values matching ``key`` with their parsed-dict key path.

    ``find_first`` is still useful for display-only best-effort lookups, but
    metadata parsing often needs to know whether a value came from a batch
    position, a search/acquisition branch, or a repeated candidate list. This
    helper keeps that context without changing the simple parsed XML shape.
    """

    return list(_iter_find_all(data, key, ()))


def _iter_find_all(data: Any, key: str, path: tuple[str, ...]) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(data, dict):
        for current_key, value in data.items():
            if not isinstance(current_key, str):
                continue
            current_path = (*path, current_key)
            if current_key == key:
                yield current_path, value
            yield from _iter_find_all(value, key, current_path)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_find_all(item, key, path)


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]

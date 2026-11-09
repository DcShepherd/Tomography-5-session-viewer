"""Strict tag project schema boundary. Unknown fields must never be lost on save."""
from dataclasses import asdict
from datetime import datetime
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any

from tomography_session_browser.domain.identity import path_identity_key
from tomography_session_browser.domain.tomogram_tags import (
    CaptureKey, CaptureLink, ChangeKind, JsonObject, JsonValue, SavedCapture, SourceAcceptance, Tag, TagAssignment,
    TagProject, TagSource, TomogramRef, TomogramTags, VolumeEvidence,
)
from tomography_session_browser.domain.tomograms import VolumeGrid
from tomography_session_browser.services.volume.review_repository import MAX_RECORD_BYTES, record_id

PROJECT_LIMIT = 32 * 1024 * 1024
MAX_COUNT = 999_999_999
MAX_NAME = 120
MAX_ROWS = 100_000
MAX_TAGS = 10_000
SCHEMA_PLAIN = 1
SCHEMA_ACCEPTANCES = 2
NEWEST_SCHEMA = SCHEMA_ACCEPTANCES
MAX_ACCEPTANCES = 10_000


def name(value: str) -> str:
    value = value.strip().lstrip("#").strip()
    if not value or len(value) > MAX_NAME or any(ord(c) < 32 for c in value):
        raise ValueError("Use a tag name of 1–120 characters without control characters.")
    return value


def count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise ValueError("Use a whole count from 0 to 999999999, or leave it blank.")
    return value


def text(value: object, limit: int = 4096) -> str:
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise ValueError("Invalid or oversized text in tag project.")
    return value


def identifier(value: object) -> str:
    result = text(value, 32)
    if re.fullmatch("[a-f0-9]{32}", result) is None:
        raise ValueError("Invalid project, source or tag ID.")
    return result


def timestamp(value: object) -> str:
    result = text(value, 40)
    parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("Tag timestamps must be UTC.")
    return result


def relative(value: object) -> str:
    result = text(value)
    path = PurePosixPath(result.replace("\\", "/"))
    if not result or path.is_absolute() or PureWindowsPath(result).drive or ".." in path.parts:
        raise ValueError("Tag paths must stay relative to their processing folder.")
    return result


def integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Invalid non-negative integer in tag project.")
    return value


def fields(value: object, required: str, optional: str = "") -> dict[str, Any]:
    keys, allowed = set(required.split()), set((required + " " + optional).split())
    if not isinstance(value, dict) or not keys <= value.keys() or not value.keys() <= allowed:
        raise ValueError("Invalid or unsupported fields in tag project.")
    return value


def array(value: object, limit: int = MAX_ROWS) -> list[Any]:
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        raise ValueError("Invalid or oversized collection in tag project.")
    return list(value)


def unique(values: list | tuple) -> None:
    if len(set(values)) != len(values):
        raise ValueError("Duplicate identity in tag project.")


def freeze(value: object, depth: int = 0) -> JsonValue:
    if depth > 16:
        raise ValueError("Capture metadata is nested too deeply.")
    if isinstance(value, dict):
        return JsonObject(tuple((text(k), freeze(v, depth + 1)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v, depth + 1) for v in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite capture metadata.")
        return value
    raise ValueError("Capture metadata must contain JSON values only.")


def thaw(value: JsonValue) -> Any:
    if isinstance(value, JsonObject):
        return {key: thaw(item) for key, item in value.items}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def capture_key(value: object) -> CaptureKey:
    row = fields(value, "created_ns history_id")
    identity = text(row["history_id"], 64)
    if re.fullmatch("[a-f0-9]{64}", identity) is None:
        raise ValueError("Invalid capture history identity.")
    return CaptureKey(integer(row["created_ns"]), identity)


def saved_capture(key: CaptureKey, value: object) -> SavedCapture:
    capture_key(asdict(key))
    record = fields(thaw(value) if isinstance(value, JsonObject) else value, "schema kind output", "state recipe format created_ns")
    if record["schema"] != 1 or record["kind"] != "capture":
        raise ValueError("Unsupported retained capture schema.")
    if "created_ns" in record and integer(record["created_ns"]) != key.created_ns:
        raise ValueError("Capture timestamp disagrees with its key.")
    if record_id(text(record["output"])) != key.history_id:
        raise ValueError("Capture output disagrees with its history identity.")
    if not isinstance(record.get("state"), dict) or not isinstance(record.get("recipe"), dict):
        raise ValueError("A retained capture needs its complete state and recipe.")
    if len(json.dumps(record, allow_nan=False).encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError("Capture metadata exceeds its size limit.")
    frozen = value if isinstance(value, JsonObject) else freeze(record)
    assert isinstance(frozen, JsonObject)
    return SavedCapture(key, frozen)


def grid(value: object) -> VolumeGrid:
    row = fields(value, "shape_zyx axes_crs spacing_xyz origin_xyz starts_crs")
    vectors = {key: array(v, 3) for key, v in row.items()}
    if any(len(v) != 3 for v in vectors.values()):
        raise ValueError("A volume grid needs complete three-axis vectors.")
    shape, axes = vectors["shape_zyx"], vectors["axes_crs"]
    if any(type(v) is not int or v <= 0 for v in shape) or sorted(axes) != [1, 2, 3] or any(type(v) is not int for v in axes):
        raise ValueError("Invalid volume shape or axes.")
    spacing, origin, starts = vectors["spacing_xyz"], vectors["origin_xyz"], vectors["starts_crs"]
    if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v <= 0) for v in spacing):
        raise ValueError("Invalid volume spacing.")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in origin) or any(type(v) is not int for v in starts):
        raise ValueError("Invalid volume origin or starts.")
    return VolumeGrid(tuple(shape), tuple(axes), tuple(spacing), tuple(origin), tuple(starts))


def decode_project(content: bytes) -> TagProject:
    if len(content) > PROJECT_LIMIT:
        raise ValueError("Tag project exceeds 32 MiB.")
    return project_from_data(json.loads(content, object_pairs_hook=unique_object))


def project_from_data(value: object) -> TagProject:
    data = fields(value, "schema kind project_id revision created sources catalogue tomograms")
    schema = data["schema"]
    if type(schema) is not int or schema not in (SCHEMA_PLAIN, SCHEMA_ACCEPTANCES) or data["kind"] != "tomogram-tags":
        raise ValueError("Unsupported tag project schema.")
    sources = []
    for value in array(data["sources"], 1000):
        row = fields(value, "id path name previous_paths")
        sources.append(TagSource(identifier(row["id"]), Path(text(row["path"])), text(row["name"]),
                                 tuple(Path(text(p)) for p in array(row["previous_paths"], 1000))))
        if not sources[-1].path.is_absolute() or any(not p.is_absolute() for p in sources[-1].previous_paths):
            raise ValueError("Source folders must be absolute paths.")
    unique(tuple(s.id for s in sources))
    unique(tuple(path_identity_key(s.path) for s in sources))
    tags = []
    for value in array(data["catalogue"], MAX_TAGS):
        row = fields(value, "id name created")
        label = text(row["name"], MAX_NAME)
        if name(label) != label:
            raise ValueError("Tag names must be stored without # or outer spaces.")
        tags.append(Tag(identifier(row["id"]), label, timestamp(row["created"])))
    unique(tuple(t.id for t in tags))
    unique(tuple(t.name.casefold() for t in tags))
    source_ids, tag_ids = {s.id for s in sources}, {t.id for t in tags}
    tomograms = []
    for value in array(data["tomograms"]):
        optional = "note acceptances" if schema == SCHEMA_ACCEPTANCES else "note"
        row = fields(value, "ref counting_complete tags captures links", optional)
        ref = fields(row["ref"], "source relative volumes")
        source = identifier(ref["source"])
        if source not in source_ids or type(row["counting_complete"]) is not bool:
            raise ValueError("Invalid source reference or Counting complete value.")
        volumes = []
        for raw in array(ref["volumes"], 1000):
            v = fields(raw, "version relative size mtime_ns grid dtype")
            volumes.append(VolumeEvidence(text(v["version"]), relative(v["relative"]), integer(v["size"]),
                                          integer(v["mtime_ns"]), grid(v["grid"]), text(v["dtype"], 100)))
        unique(tuple(v.relative for v in volumes))
        assignments = []
        for raw in array(row["tags"], MAX_TAGS):
            a = fields(raw, "tag_id count version_relative version_label recorded_at")
            if a["tag_id"] not in tag_ids:
                raise ValueError("Unknown assigned tag.")
            assignments.append(TagAssignment(a["tag_id"], count(a["count"]), relative(a["version_relative"]),
                                             text(a["version_label"]), timestamp(a["recorded_at"])))
        unique(tuple(a.tag_id for a in assignments))
        captures = []
        for raw in array(row["captures"], 10_000):
            c = fields(raw, "key record")
            captures.append(saved_capture(capture_key(c["key"]), c["record"]))
        unique(tuple(c.key for c in captures))
        keys, assigned = {c.key for c in captures}, {a.tag_id for a in assignments}
        links = []
        for raw in array(row["links"], 100_000):
            l = fields(raw, "tag_id capture_key linked_at")
            key = capture_key(l["capture_key"])
            if l["tag_id"] not in assigned or key not in keys:
                raise ValueError("A capture link requires an assigned tag and retained capture.")
            links.append(CaptureLink(l["tag_id"], key, timestamp(l["linked_at"])))
        unique(tuple((l.tag_id, l.capture_key) for l in links))
        if keys != {l.capture_key for l in links}:
            raise ValueError("Unreferenced retained capture.")
        acceptances = []
        for raw in array(row.get("acceptances", []), MAX_ACCEPTANCES):
            acceptance = fields(raw, "accepted_at kind")
            acceptances.append(SourceAcceptance(timestamp(acceptance["accepted_at"]), ChangeKind(acceptance["kind"])))
        tomograms.append(TomogramTags(TomogramRef(source, relative(ref["relative"]), tuple(volumes)),
                                      row["counting_complete"], text(row.get("note", ""), 4000),
                                      tuple(assignments), tuple(captures), tuple(links), tuple(acceptances)))
    unique(tuple((r.ref.source, r.ref.relative) for r in tomograms))
    return TagProject(identifier(data["project_id"]), timestamp(data["created"]), tuple(sources), tuple(tags),
                      tuple(tomograms), integer(data["revision"]))


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    unique(tuple(key for key, _ in pairs))
    return dict(pairs)


def encode_project(project: TagProject) -> bytes:
    schema = SCHEMA_ACCEPTANCES if any(row.acceptances for row in project.tomograms) else SCHEMA_PLAIN
    data = {
        "schema": schema, "kind": "tomogram-tags", "project_id": project.project_id,
        "revision": project.revision, "created": project.created,
        "sources": [{"id": s.id, "path": str(s.path), "name": s.name,
                     "previous_paths": [str(p) for p in s.previous_paths]} for s in project.sources],
        "catalogue": [{"id": t.id, "name": t.name, "created": t.created} for t in project.catalogue],
        "tomograms": [],
    }
    for row in project.tomograms:
        value = {
            "ref": {"source": row.ref.source, "relative": row.ref.relative,
                    "volumes": [{"version": v.version, "relative": v.relative, "size": v.size,
                                 "mtime_ns": v.mtime_ns, "grid": asdict(v.grid), "dtype": v.dtype}
                                for v in row.ref.volumes]},
            "counting_complete": row.counting_complete,
            "tags": [{"tag_id": t.tag_id, "count": t.count, "version_relative": t.version_relative,
                      "version_label": t.version_label, "recorded_at": t.recorded_at} for t in row.tags],
            "captures": [{"key": asdict(c.key), "record": c.record} for c in row.captures],
            "links": [{"tag_id": l.tag_id, "capture_key": asdict(l.capture_key), "linked_at": l.linked_at}
                      for l in row.links],
        }
        if row.note:
            value["note"] = row.note
        if row.acceptances:
            value["acceptances"] = [{"accepted_at": a.accepted_at, "kind": a.kind.value} for a in row.acceptances]
        data["tomograms"].append(value)
    project_from_data(data)
    content = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=thaw).encode("utf-8")
    if len(content) > PROJECT_LIMIT:
        raise ValueError("Tag project exceeds 32 MiB.")
    return content

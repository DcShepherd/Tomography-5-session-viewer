"""Bounded adapters for recorded Warp CTF and IMOD alignment/positioning fields.

Only exact series metadata and canonical processing paths are read. Presence of
alignment output does not establish that any reconstruction imported it.
"""
from __future__ import annotations

import math
from pathlib import Path, PureWindowsPath
import re
from xml.etree import ElementTree

from tomography_session_browser.domain.recorded_metrics import ProcessingEvidence, RecordedMetric
from tomography_session_browser.parsers.volume_header import fingerprint, is_cloud_placeholder, read_volume
from tomography_session_browser.parsers.imod_commands import literal_path, parameter, standard_input
from tomography_session_browser.parsers.tomopitch import parse_tomopitch, positive

METADATA_LIMIT = 8 * 1024 * 1024
THICKNESS_FIELD = "CTFSpecimenThicknessAngstrom"


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    if is_cloud_placeholder(path):
        raise ValueError("Metadata is an online-only placeholder; download it before reloading.")
    before = path.stat()
    if before.st_size > METADATA_LIMIT:
        raise ValueError("Metadata exceeds the size limit.")
    with path.open("rb") as stream:
        data = stream.read(METADATA_LIMIT + 1)
    after = path.stat()
    if len(data) > METADATA_LIMIT or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Metadata changed while reading or exceeds the size limit.")
    return data.decode("utf-8-sig")


def _number(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Recorded metric must be finite and non-negative.")
    return value


def _required(path: Path) -> str:
    value = _read(path)
    if value is None:
        raise ValueError(f'Missing calibration metadata: {path.name}.')
    return value


def _positioning_spacing(folder: Path, stem: str, scale: float) -> tuple[float, str]:
    paths = (folder / 'tilt.com', folder / f'{stem}.edf')
    stamps = {path: fingerprint(path) for path in paths if path.exists()}
    commands = standard_input(_required(paths[0]), 'tilt')
    if literal_path(parameter(commands, 'TILTFILE'), folder).stem != stem:
        raise ValueError('Tilt command identifies a different series.')
    binning = positive(parameter(commands, 'IMAGEBINNED'))
    if not binning.is_integer() or 'expandedbyfactor' in commands or 'supersamplefactor' in commands:
        raise ValueError('Unsupported reconstruction resampling; pixel frame is unverified.')
    edf: dict[str, str] = {}
    for line in _required(paths[1]).splitlines():
        key, separator, value = line.partition('=')
        if separator and not key.lstrip().startswith('#'):
            if key in edf:
                raise ValueError('Duplicate Etomo calibration field.')
            edf[key] = value.strip()
    if edf.get('Setup.DatasetName') != stem or edf.get('Setup.AxisType') != 'Single Axis':
        raise ValueError('Etomo dataset identity or single-axis frame is unverified.')
    if positive(edf.get('Setup.Binning', '')) != 1 or positive(edf.get('Setup.TomoPosBinningA', '')) != scale:
        raise ValueError('Etomo positioning binning disagrees with the tomopitch scale.')
    pixel_nm = positive(edf.get('Setup.PixelSize', ''))
    stack_path = literal_path(parameter(commands, 'InputProjections'), folder)
    stack = read_volume(stack_path, 'Calibration reference')  # Strict 1024-byte header only.
    stamps[stack_path] = stack.fingerprint
    grid = stack.grid
    if grid is None or grid.axes_crs != (1, 2, 3):
        raise ValueError('Aligned-stack header or XY frame is unavailable.')
    x, y, _ = grid.spacing_xyz
    if x is None or y is None or min(x, y) <= 0 or any(abs(value - 1) < 1e-6 for value in (x, y)):
        raise ValueError('Aligned-stack XY spacing is unresolved/default.')
    if not math.isclose(x, y, rel_tol=1e-5) or not math.isclose(x / binning / 10, pixel_nm, rel_tol=1e-5):
        raise ValueError('Etomo pixel size and aligned-stack header/binning disagree; nm conversion withheld.')
    if any(fingerprint(path) != stamp for path, stamp in stamps.items()):
        raise ValueError('Calibration metadata changed while reading.')
    note = (f'{paths[0].name}: IMAGEBINNED={binning:g}; {paths[1].name}: Setup.PixelSize={pixel_nm:g} nm, '
            f'TomoPosBinningA={scale:g}; {stack_path.relative_to(folder.resolve()).as_posix()} header XY={x:g} Å. '
            'The log recommendation is already scaled; ScaleFactor is not applied again.')
    return pixel_nm, note


def _tomopitch(folder: Path, stem: str, source_root: Path) -> ProcessingEvidence:
    folder, source_root = folder.absolute(), source_root.absolute()
    log = folder / 'tomopitch.log'
    source = log.relative_to(source_root).as_posix()
    try:
        stamp = fingerprint(log) if log.exists() else None
        text = _read(log)
        if text is None:
            return ProcessingEvidence()
        result = parse_tomopitch(text)
        qualification = ('Suggested by IMOD tomopitch from a boundary model; not applied by this application. '
            f'Final {result.field} recommendation: {result.pixels} pixels; unrounded {result.computed_pixels:g} pixels. '
            f'Includes ExtraThickness={result.border:g} pixels on each surface and FFT rounding. '
            f'Recorded ScaleFactor={result.scale:g}.')
        warnings = ()
        value, unit = float(result.pixels), 'pixels'
        try:
            spacing, note = _positioning_spacing(folder, stem, result.scale)
            value, unit = result.pixels * spacing, 'nm'
            if not math.isfinite(value):
                raise ValueError('Converted thickness is not finite.')
            qualification += ' ' + note
        except (OSError, ValueError) as exc:
            value, unit = float(result.pixels), 'pixels'
            warnings = (f'{source}: {exc}',)
            qualification += ' Physical conversion unavailable: ' + str(exc)
        if fingerprint(log) != stamp:
            raise ValueError('Tomopitch log changed while reading.')
        metric = RecordedMetric('imod.tomopitch.specimen_thickness', 'IMOD positioning thickness', value, unit,
                                source, f'Final aggregate {result.field}: set to', qualification)
        return ProcessingEvidence(metrics=(metric,), warnings=warnings)
    except (OSError, ValueError) as exc:
        return ProcessingEvidence(warnings=(f'{source}: {exc}',))


def read_imod_processing_evidence(root: Path, output: Path) -> ProcessingEvidence:
    """A root-level positioning log belongs only to its verified tilt output."""
    if not (root / 'tomopitch.log').exists():
        return ProcessingEvidence()
    try:
        command_stamp = fingerprint(root / 'tilt.com')
        commands = standard_input(_required(root / 'tilt.com'), 'tilt')
        if literal_path(parameter(commands, 'OutputFile'), root) != output.resolve():
            raise ValueError('Positioning log does not identify this reconstruction output.')
        tilt_file = literal_path(parameter(commands, 'TILTFILE'), root)
        if tilt_file.suffix.lower() != '.tlt':
            raise ValueError('Requires one explicit series tilt-angle file.')
        evidence = _tomopitch(root, tilt_file.stem, root)
        if fingerprint(root / 'tilt.com') != command_stamp:
            raise ValueError('Positioning output identity changed while reading.')
        return evidence
    except (OSError, ValueError) as exc:
        return ProcessingEvidence(warnings=(f'tomopitch.log: {exc}',))


def _warp(text: str, source: str) -> ProcessingEvidence:
    # DTDs/entities have no role in this scalar metadata adapter.
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations with DTDs or entities are unsupported.")
    root = ElementTree.fromstring(text)
    if root.tag != "TiltSeries":
        raise ValueError("Expected Warp TiltSeries metadata.")
    metrics, warnings = [], []
    fields = (
        ("warp.ctf_specimen_thickness", "Specimen thickness", "Å", THICKNESS_FIELD, "thickness"),
        ("warp.ctf_resolution", "CTF fit", "Å", "CTFResolutionEstimate", "resolution"),
        ("warp.ctf.defocus", "Defocus", "µm", "Defocus", "signed"),
        ("warp.ctf.defocus_delta", "Astigmatism delta defocus", "µm", "DefocusDelta", "signed"),
    )
    ctfs = root.findall("CTF")
    for definition, label, unit, field, rule in fields:
        if rule == "signed":
            if len(ctfs) > 1:
                warnings.append(f"{field}: multiple CTF blocks are ambiguous.")
                continue
            values = [node.get("Value") for ctf in ctfs for node in ctf.findall("Param") if node.get("Name") == field]
        else:
            values = [root.get(field)] if field in root.attrib else []
        if not values:
            continue
        try:
            if len(values) != 1 or values[0] is None:
                raise ValueError("Expected one explicit scalar value.")
            value = float(values[0])
            if not math.isfinite(value):
                raise ValueError("Recorded metric must be finite.")
            if rule == "thickness" and value < 0:
                raise ValueError("Recorded thickness must be non-negative.")
            if (rule == "thickness" and value == 0) or (rule == "resolution" and value <= 0):
                continue  # Warp's unavailable sentinels; signed CTF zero remains data.
            qualification = "Recorded Warp CTF estimate; not measured by this application."
            if field == "Defocus":
                qualification += " Positive means underfocus; the recorded sign is retained."
            metrics.append(RecordedMetric(definition, label, value, unit, source,
                f"CTF/Param[{field}]" if rule == "signed" else field, qualification))
        except (ValueError, TypeError) as exc:
            warnings.append(f"{field}: {exc}")
    return ProcessingEvidence(metrics=tuple(metrics), warnings=tuple(warnings))


def _imod(text: str, source: str, stem: str) -> tuple[str, tuple[RecordedMetric, ...]]:
    header = "*** Entries to program tiltalign ***"
    if text.count(header) != 1 or not text.rstrip().endswith("SUCCESSFULLY COMPLETED"):
        raise ValueError("Expected one completed IMOD tiltalign run.")
    body = text.split(header, 1)[1]
    if "*** End of entries ***" not in body:
        raise ValueError("Alignment log has an incomplete parameter block.")
    block = body.split("*** End of entries ***", 1)[0]
    models = re.findall(r"^\s*ModelFile\s*=\s*(.+?)\s*$", block, re.MULTILINE)
    if len(models) != 1 or PureWindowsPath(models[0]).stem != stem:
        raise ValueError("Alignment log does not identify this tomogram uniquely.")
    local = re.findall(r"^\s*LocalAlignments\s*=\s*(\S+)\s*$", block, re.MULTILINE)
    qualification = "Import into this reconstruction is unverified; values are not ranked across runs."
    alignment = f"IMOD tiltalign output found\nAlignment log: {source}\n{qualification}"
    if local != ["0"]:
        return alignment + " Residuals unavailable: global-only alignment is not established.", ()
    metrics: list[RecordedMetric] = []
    patterns = (
        ("mean", "Mean alignment residual", "Residual error mean and sd", r"\s*(\S+)\s+\S+\s+(\S+)"),
        ("weighted_mean", "Weighted mean alignment residual", "Residual error weighted mean", r"\s*(\S+)\s+(\S+)"),
    )
    for key, label, field, pattern in patterns:
        lines = [line.strip() for line in text.splitlines() if line.strip().startswith(field + ":")]
        if len(lines) > 1:
            raise ValueError("Multiple alignment residual summaries are ambiguous.")
        if not lines:
            continue
        match = re.fullmatch(re.escape(field) + ":" + pattern, lines[0])
        if match is None:
            raise ValueError("Alignment residual value or unit is missing.")
        raw, unit = match.groups()
        if unit not in ("nm", "pixels"):
            raise ValueError("Alignment residual unit is unsupported or missing.")
        value = _number(raw)
        detail = qualification
        if key == "weighted_mean":
            detail += " IMOD robust weights; weighting settings can differ between runs."
        metrics.append(RecordedMetric(f"imod.tiltalign.{key}", label, value, unit,
                                     source, field, detail))
    return alignment, tuple(metrics)


def read_processing_evidence(root: Path, stem: str) -> ProcessingEvidence:
    metrics: list[RecordedMetric] = []
    warnings: list[str] = []
    alignment = "Not recorded"
    for kind, relative in (
        ("warp", f"warp_tiltseries/{stem}.xml"),
        ("imod", f"warp_tiltseries/tiltstack/{stem}/align.log"),
    ):
        try:
            text = _read(root / relative)
            if text is None:
                continue
            if kind == "warp":
                evidence = _warp(text, relative)
                metrics.extend(evidence.metrics)
                warnings.extend(f"{relative}: {warning}" for warning in evidence.warnings)
            else:
                alignment, values = _imod(text, relative, stem)
                metrics.extend(values)
        except (OSError, ValueError, ElementTree.ParseError) as exc:
            warnings.append(f"{relative}: {exc}")
    positioning = _tomopitch(root / 'warp_tiltseries' / 'tiltstack' / stem, stem, root)
    return ProcessingEvidence(alignment, (*metrics, *positioning.metrics), (*warnings, *positioning.warnings))

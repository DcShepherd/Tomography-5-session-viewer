"""Capture selection is explicit and never broadens acquisition report scope."""
from dataclasses import dataclass

from tomography_session_browser.services.volume.snapshot import SnapshotRequest

MAX_CONTACT_CAPTURES = 24


@dataclass(frozen=True, slots=True)
class ContactSheetScope:
    captures: tuple[SnapshotRequest, ...]

    @property
    def page_count(self) -> int:
        return (len(self.captures) + 3) // 4


def normalise_contact_sheet(candidates: tuple[SnapshotRequest, ...], allowed_keys: frozenset[str],
                            selected: frozenset[int]) -> ContactSheetScope:
    if not selected or len(selected) > MAX_CONTACT_CAPTURES:
        raise ValueError(f"Select between 1 and {MAX_CONTACT_CAPTURES} captures.")
    if any(type(index) is not int or index < 0 or index >= len(candidates) for index in selected):
        raise ValueError("The capture selection is invalid.")
    captures = tuple(candidates[index] for index in sorted(selected))
    if any(capture.row_key not in allowed_keys for capture in captures):
        raise ValueError("A selected capture is outside the current filtered list.")
    if any(capture.recipe.interactive or capture.recipe.black is None or capture.recipe.white is None
           for capture in captures):
        raise ValueError("Contact sheets require refined captures with fixed intensity levels.")
    return ContactSheetScope(captures)

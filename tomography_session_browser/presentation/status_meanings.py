"""Explanatory copy beside the app's three distinct typed status vocabularies."""
from tomography_session_browser.domain.display_names import count_phrase
from tomography_session_browser.domain.status import AtlasMarkerStatus, RowStatus, ValidationStatus
from tomography_session_browser.services.tilt_series_validation import HARD_FAILURE_THRESHOLD

VALIDATION_MEANINGS: dict[ValidationStatus, str] = {
    ValidationStatus.COMPLETE: "At least the expected number of tilt images is present. A truncated or unreadable stack does not receive a Complete verdict.",
    ValidationStatus.INCOMPLETE: "Some expected tilt images are missing, or the available stack is truncated.",
    ValidationStatus.FAILED: f"No tilt images, or fewer than {count_phrase(HARD_FAILURE_THRESHOLD, 'tilt image')}, are present.",
    ValidationStatus.UNKNOWN: "An expected count cannot be established, or an unreadable stack leaves the verdict unknown. Read the series' validation explanation for its evidence.",
}

ROW_MEANINGS: dict[RowStatus, str] = {
    RowStatus.DONE: "Acquisition is complete according to this row's evidence.",
    RowStatus.PARTIAL: "Acquisition is partial in this row's context. Read its summary for the evidence.",
    RowStatus.INCOMPLETE: "The row has not completed the acquisition or evidence expected for that item.",
    RowStatus.FAILED: "The row has a failed acquisition outcome.",
    RowStatus.MISSING: "A file expected for this item is not on disk.",
    RowStatus.UNKNOWN: "The evidence does not establish an acquisition status.",
    RowStatus.PENDING: "Planned acquisition is not yet recorded as acquired.",
    RowStatus.AVAILABLE: "The atlas mosaic is on disk.",
    RowStatus.WITH_ACQUISITIONS: "The overview records downstream acquisitions.",
    RowStatus.NO_ACQUISITIONS: "The overview has no recorded downstream acquisitions.",
}

ATLAS_MARKER_MEANINGS: dict[AtlasMarkerStatus, str] = {
    AtlasMarkerStatus.COLLECTED: "The batch-position marker represents completed acquisition.",
    AtlasMarkerStatus.QUEUED: "A queued or not-yet-acquired batch position.",
    AtlasMarkerStatus.PARTIAL: "The batch position's acquisition is partial.",
    AtlasMarkerStatus.UNATTRIBUTED: "Failed tilt series with no attributable batch position. The acquisition relationship remains unresolved.",
    AtlasMarkerStatus.FAILED: "The batch position has failed acquisition.",
}

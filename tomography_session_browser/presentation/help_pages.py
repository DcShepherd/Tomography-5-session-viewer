"""Approved Help copy, projected from context-specific existing vocabulary."""
from tomography_session_browser.presentation.help_content import HelpPage, HelpSection
from tomography_session_browser.presentation.project_tree import PROJECT_BADGE_MEANINGS, SESSION_BADGE_MEANINGS
from tomography_session_browser.presentation.status_meanings import VALIDATION_MEANINGS, ROW_MEANINGS, ATLAS_MARKER_MEANINGS
from tomography_session_browser.services.item_status import display_status_label
from tomography_session_browser.services.status_taxonomy import acquisition_state

START_SECTIONS = (
    HelpSection("start.intro", HelpPage.START, "Review a collection, then follow the evidence.",
        ("Load a Tomography 5 session folder, or a Warp, IMOD or AreTomo3 processing folder. You can load several at once.",)),
    HelpSection("start.load", HelpPage.START, "1 · Load your folders",
        ("Choose folders in Load sessions, or drop them onto Project. A folder that cannot be loaded is explained; the other loaded data remains available.",)),
    HelpSection("start.scope", HelpPage.START, "2 · Choose what to review",
        ("Use Project to select a session, sample or processing folder. The active tab stays in place. Summary gives you the selected scope's findings; image tabs let you inspect the evidence.",)),
    HelpSection("start.links", HelpPage.START, "3 · Follow a verified relationship",
        ("Use Linked or Go to to open a related item. When no unique destination can be verified, the action explains why. Back returns to the view you left.",)),
    HelpSection("start.observations", HelpPage.START, "4 · Keep observations and export",
        ("In Tomograms, add tags, optional counts and notes to a separate project file. A snapshot stays on the current tomogram; use the navigation controls to move on. Reports export the acquisition scope you select.",)),
    HelpSection("start.help", HelpPage.START, "Help stays open while you work",
        ("F1 opens Keyboard and mouse at your active tab. Search covers all four Help pages.",)),
)

FILES_SECTIONS = (
    HelpSection("files.intro", HelpPage.FILES, "Your source folders stay unchanged.",
        ("Save reports, exported images, snapshots, contact sheets, movies, tag projects and CSV files outside every loaded acquisition and processing folder.",)),
    HelpSection("files.tags", HelpPage.FILES, "Tag projects and app preferences",
        ("Tags and notes live in a separate versioned JSON file. Choose an existing parent folder; backups and recovery copies stay beside the project. Folder bindings and preferences belong to the app's profile, never a source folder.",)),
    HelpSection("files.captures", HelpPage.FILES, "Snapshots and contact sheets",
        ("Choose an existing output folder. Snapshots reserve a unique filename. A contact sheet requires a new filename and does not replace an existing PDF. Both use the captured view and its recorded rendering recipe.",)),
    HelpSection("files.movies", HelpPage.FILES, "Tomogram movies",
        ("Export movie (Ctrl+E) saves the planes of the view as it was when the dialog opened. Save an H.264 MP4 file, PNG frames, or both. A movie never replaces a file; with PNG frames, its folder also holds a manifest of the rendering recipe.",
         "The dialog keeps your settings for each tomogram until you close the app, also if you click Cancel. All versions of a tomogram share them, so you can export again at another quality, or make a matched movie of another version. Plane numbers and holds apply only if the two versions have the same grid; if not, the dialog says so.",
         "MP4 export needs FFmpeg with the H.264 encoder (libx264), for example the conda-forge GPL build. On Linux, install it in the app's environment. On Windows and macOS, install it in its own environment and add its program folder to PATH. The README has the commands. Without FFmpeg, you can save PNG frames. The app never downloads FFmpeg.")),
    HelpSection("files.reports", HelpPage.FILES, "Acquisition reports and PNG images",
        ("The report selection controls what enters the PDF. Reports may create missing parent folders. PNG export asks before replacing an image. Manual tags and notes do not enter acquisition reports or contact sheets.",)),
    HelpSection("files.save-failure", HelpPage.FILES, "When saving fails",
        ("Keep the window open. Your edits stay available for Retry all or a project-specific Save As. Details and Copy preserve the diagnostic text. A saved project with a failed folder binding still needs attention.",)),
    HelpSection("files.moved", HelpPage.FILES, "When a folder has moved",
        ("Find moved folder reviews its recorded matches before reconnecting tag observations. Equal geometry does not prove identical voxels. This action does not move files or establish an acquisition link.",)),
    HelpSection("files.rules", HelpPage.FILES, "Different exports keep their own file rules",
        ("Source-folder protection applies to all of them; choosing a destination does not change scientific data, calibration or report scope.",)),
)

PROJECT_EXPLANATIONS = {
    "PR": "A loaded Warp, IMOD or AreTomo3 processing folder.",
    "LS": "A group with an atlas screening session and linked data collections.",
    "AT": "The group currently contains atlas screening without a linked data collection.",
    "DC": "A data collection without a linked atlas session in the current project.",
    "!": "The atlas relationship has not resolved to one destination. Review its explanation.",
}
SESSION_EXPLANATIONS = {
    "AT": "The session itself is atlas screening, including when it belongs to a linked group.",
    "DC": "The session contains acquisition data. Its group describes any atlas relationship.",
}


def help_sections() -> tuple[HelpSection, ...]:
    reading = [HelpSection("reading.intro", HelpPage.READING, "Read each mark in its context",
        ("A project group's badge describes its relationship. A session row's badge describes the session itself.",))]
    reading.extend(HelpSection(f"reading.project.{badge}", HelpPage.READING, f"{meaning} · project group",
        (PROJECT_EXPLANATIONS[badge],), badge) for badge, meaning in PROJECT_BADGE_MEANINGS.items())
    reading.extend(HelpSection(f"reading.session.{badge}", HelpPage.READING, f"{meaning} · session row",
        (SESSION_EXPLANATIONS[badge],), badge) for badge, meaning in SESSION_BADGE_MEANINGS.items())
    reading.append(HelpSection("reading.inferred", HelpPage.READING, "Inferred failed groups",
        ("Failed tilt series with no recorded batch link can form name-inferred groups within their grid. These groups are shown separately from the recorded Batch positions count. This grouping does not create a verified acquisition link.",)))
    reading.append(HelpSection("reading.context", HelpPage.READING, "Read a status in its context",
        ("Tilt-series validation, image-list row status and atlas-marker status keep their existing vocabulary and definitions. An unknown planned frame count and a missing image are different findings.",
         "Unresolved relationships keep their explanation. Help does not choose an atlas, batch or other destination on your behalf.")))
    for prefix, heading, meanings, label in (
        ("validation", "Tilt-series validation", VALIDATION_MEANINGS, display_status_label),
        ("row", "Image-list rows", ROW_MEANINGS, display_status_label),
        ("marker", "Atlas batch-position markers", ATLAS_MARKER_MEANINGS, acquisition_state),
    ):
        reading.extend(HelpSection(f"reading.{prefix}.{status.value}", HelpPage.READING,
            f"{heading} · {label(status)}", (meaning,)) for status, meaning in meanings.items())
    keyboard = HelpSection("keyboard.intro", HelpPage.KEYBOARD, "Controls follow their context",
        ("Choose a tab to see its controls. Editors and open menus keep their own keys. Common image controls apply to every eligible image tab.",))
    return (*START_SECTIONS, *reading, keyboard, *FILES_SECTIONS)

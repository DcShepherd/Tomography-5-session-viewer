# Change log

This file lists the fixes and new functions in each release.

## 1.0.1 (6 November 2026)

### Fixes

- **Tomogram import:** Reconstruction folders can now be recognised from MRC volume headers without an IMOD reconstruction label. A valid volume record uses space group 1, includes every section in one volume and has no tilt-stack extended header. Files with their own acquisition MDOC or known stack names remain protected from this fallback. In acquisition folders, an unaccompanied MRC volume is skipped with a warning instead of being listed as a tilt series.
- **Calibration and saved review state:** Pixel sizes supplied for a version survive a restart and remain separate from the value recorded in the file. Restoring a snapshot's calibration does not silently apply it to other views. Save failures and pending changes now keep accurate feedback, including CSV export results.
- **Navigation and controls:** Search-tile highlights follow the verified exposure, including its camera field. Keyboard focus is handed back to the visible page when a side panel closes. The main toolbar can no longer be hidden through Qt's default context menu.
- **Responsiveness:** Preview preparation avoids unnecessary image copies, and review-state writes remain bounded during repeated edits.

### Tomograms tab

- **One pixel size for several versions:** When two or more versions have no usable pixel size, **Apply to several…** lets you give them one value. You review the list first. The application uses your statement. It cannot check that the versions have the same voxel size. A file name that records a different value shows a warning. **Undo** removes the value from these versions. The application does not change the MRC files.
- **Pixel sizes after a restart:** The application keeps each pixel size that you give and shows it when you select that version again. If the file changes, the application asks again.
- **Pixel size warnings:** When you give a pixel size, the application stops the warning about the MRC header for that version, and **Needs attention** does not count it. **Undo**, **Use header** or a changed file shows the warning again. **Context** continues to show the value in the header.

## 1.0.0 (4 November 2026)

This release adds these functions to the public version of 20 October 2026.

### Tomograms tab

The **Tomograms** tab shows reconstructed volumes from processing folders. The application reads these folders. It does not change them.

- **Sources:** The application reads Warp, IMOD and AreTomo3 folders, and folders of reconstructions that have an IMOD header label.
- **Versions:** You can select the **Raw**, **Deconvolved** or **Denoised** version of a tomogram.
- **Views:** The Single view shows one plane. The Ortho view shows three planes. **Link** gives the Ortho panes the same scale and centre.
- **Focus:** **Focus** (Ctrl+M) makes the viewer larger. The panels show again when you stop Focus.
- **Playback:** **Play** moves through the planes at the rate that you select.
- **Slices:** **Slices** shows the average of adjacent planes.
- **Oblique views:** You can turn the view to an oblique angle. The lock button keeps the point that the view turns around.
- **Image settings:** The **Image** panel has filters, a histogram, three contrast modes, **Invert**, **Smooth pixels**, a calibration and a scale bar. These settings change only the display.
- **Memory:** All tomogram views and exports share one memory limit. You can change this limit.
- **Snapshots:** **Snapshot** (Ctrl+S) saves the area that you can see as a PNG, TIFF or JPEG file.
- **Captures:** **Captures** lists your snapshots and can show each saved view again. You can export 1 to 24 captures as a PDF contact sheet.
- **Saved view:** When you load the same source again, the application shows **Resume saved view**.
- **Tags:** You can add tags, counts and notes. The application keeps them in a project file that is not in the source folders. You can export the tags as a CSV file.
- **Moved folders:** **Find moved folder** connects the tags to a source folder that has a new location.
- **Tomogram list:** The list has the filters **All**, **Not opened** and **Needs attention**.
- **Recorded values:** The Context panel shows the thickness, residual, CTF fit, defocus, astigmatism and voxel size. It also shows the source of each value.
- **Links:** A tomogram can open its tilt series when the link is verified. You can make a decision about each link that has only the same name.
- **Speed:** The installation can build an optional CPU sampler for faster oblique planes.

### Movie export

**Export movie** (Ctrl+E) saves the planes of a tomogram as a movie.

- **Output:** The application saves an H.264 MP4 file, PNG frames, or the two. You can save PNG frames without FFmpeg.
- **Dialog:** The settings are in four cards that open and close: **Planes**, **Timing**, **Picture** and **File**.
- **Timing:** **Single pass** goes through the planes one time. **Bounce** goes through the planes and then goes to the first plane again. **Loop-ready** removes the last frame, so that a movie in a loop does not stop on its first plane.
- **Holds:** A movie can stop for a time at the start, at the turn and at the end. **Hold here** stops it at the frame that you select.
- **Size:** You can select **Native**, **HD**, **Full HD** or **4K**, and the picture shape.
- **Quality:** You can select **Higher quality**, **Balanced quality** or **Smaller file**.
- **Picture:** A movie can show a scale bar, plane names, plane numbers, intersection lines and a title.
- **Preview:** **Preview clip** plays the movie in the dialog before you export it.
- **Settings:** The dialog keeps the settings of each tomogram until you close the application. All versions of a tomogram share them. Thus you can make movies of two versions with the same settings.
- **Result:** A dialog shows the status of the export. Then it gives **Show in folder** and **Open movie**.

### Summary tab

- **Processing summary:** A new page shows the result of a tomogram selection. It has an outcome card, charts and tables of the recorded values, and a list of the volumes.
- **Charts:** The new charts show thickness, mean residual, CTF fit and defocus. A click on a tomogram highlights it in all charts. A double-click opens it.
- **Tag cards:** The processing summary shows a summary of the tags and counts.
- **Tomograms link:** Collection and sample summaries show the number of linked tomograms. A click opens these tomograms in the **Tomograms** tab.
- **Cards:** One card starts each summary page. Its entries open their rows in the applicable tab.

### Help and feedback

- **Help window:** **Help** (F1) opens four pages that you can search.
- **Load result:** The project panel shows the result of each load, and gives **Retry** for a failed folder.
- **Empty pages:** A page with no data shows an illustration and one sentence that tells what to do.

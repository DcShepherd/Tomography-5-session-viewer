# Change log

This file lists the fixes and new functions in each release.

## 1.1.0 (9 November 2026)

### Load sessions

- **Recent folders:** Below **Browse folders…**, **Recent:** shows a maximum of three folders that you loaded before and that are not in the project. Select a folder to add it to the list. The application does not load it until you select **Load**. When a folder is in the list, its button is not available. The window does not change the history of recent folders.
- **Keyboard:** The application adds a recent folder to the list when you release Enter or Space. Then the focus goes to **Load**, or to **Browse folders…** if the folder cannot load. If you hold Enter, the application does not open **Browse folders…** and does not load.
- **Layout:** When you add a folder, the recent folders do not move. The window always has sufficient height for one full row of the list.

### Project panel

- **Your own groups:** To mark a top-level row of the project tree, Ctrl+click it, Shift+click it or push Ctrl+Space. A marked row shows a ticked box. A mark does not change the dataset on show. To clear all marks, push Escape. Right-click a marked row and select **Group N marked rows…**, then type a name. The rows go below a heading with that name. Select the heading to see its rows in Context. Right-click the heading to rename or ungroup it, or right-click a row to remove it from the group. In the report dialog, tick a group to tick all its rows. The groups stay until you close the application. The application does not change the source files.

### Tomograms tab

- **Rotation sliders:** When the canvas toolbar has sufficient width for all its labels, X, Y and Z sliders replace **Angles**. When you move a slider, the view turns, and when you release the slider, the application shows the image at full quality. When you drag a slider, the angle changes by one rotation increment for each pixel. A click without a drag selects the slider and does not change the angle. When you select a slider, its letter and handle show in the accent colour. When a slider has the focus, the arrow keys and the mouse wheel turn the view by the rotation increment. A touchpad also turns the view by whole increments. To change the increment, right-click a slider. The button before the sliders opens the angle settings.
- **Slider values:** The sliders always show the angles of the view, also after a keypad rotation, **Back**, a snapshot or a different version. When you turn the view with the keyboard, the angles that change show in the accent colour. The colour stops 0.8 seconds after the last key.
- **Movie mark:** When you save a movie or PNG frames of a tomogram, a film mark shows in its row in the list, before the camera. The mark stays after you close the application. Put the pointer on the mark to see the date, the time and the file name. The application does not make sure that the file is still there. A cancelled or failed export does not add a mark.
- **Tomogram names:** In the list, the full name of a tomogram that you did not open shows in bold. When you open a tomogram, its full name shows in grey. Before, only the last part of each name showed clearly.
- **Canvas toolbar:** When **Angles** is the only control in **More**, the toolbar shows the zoom buttons and the labels again if it has sufficient width. The zoom buttons stay in the toolbar while **Angles** is in the toolbar. When all angles are zero, **Angles** shows "X 0.0°  Y 0.0°  Z 0.0°".

### Processing folders

- **Scipion jobs:** You can select a Scipion job folder or its `extra` folder. Both load the same source. The project tree shows the job, for example "IMOD reconstruction · 742" or "cryoCARE denoised · 1906", not "extra". A name that you give to the source stays.
- **cryoCARE:** The application loads the denoised tomograms of a Scipion cryoCARE job. Each tomogram has the name of its tilt series, for example `Umber_1_10`.
- **Half sets:** In a Scipion job, the even and odd half sets of a tomogram are part of that tomogram. Before, each half set gave a warning.
- **Scipion job protection:** The application does not save a report, image or other file in any part of a loaded Scipion job: the job folder, its `extra` folder or its `logs` folder. Before, only the `extra` folder had this protection. A report from a processing folder that you removed from the project also keeps this protection.

### Reports

- **Page size:** All PDF reports use A4 pages: the session report, the combined report, the folder report and the tomogram contact sheet. The contact sheet has equal margins on the left and right sides.
- **Linked tomograms:** The report dialog shows the loaded processing folders. If you select **Include linked tomograms**, each dataset gets a Tomograms section after its tilt-series table: the linked tomograms, their recorded values, warnings, tags and notes. The cover shows the counts. The report uses the values from the time when the dialog opened. If this option is off, the report is the same as before.
- **Central slices:** If you select **Include central slices**, the report shows one image for each linked tomogram: the middle XY plane of the most processed version, as the average of a slab that is approximately 10 nm thick. A window shows the progress and has a **Cancel** button. If you cancel, the application does not write a file. If the application cannot read a file, the image cell shows the reason.
- **Folder reports:** If you select only processing folders, the report shows all tomograms of these folders. This also works when no acquisition session is loaded. When a processing folder is the current scope, **Report** opens the dialog with only this folder selected.
- **Safe save:** The application writes each report to a temporary file and replaces the destination only when the report is complete. A failure keeps the earlier file.
- **Tags and notes in reports:** The report dialog tells you that a report with tomograms, and a folder report, include the review tags, counts and notes. The report uses them as they were when the dialog opened, also edits that you did not save.
- **Many tags:** The tag table shows the first four tags of each tomogram. The other tags show below the table, in a table for each tomogram with the tomogram name and the counts. Before, a report with many tags or long tag names could fail. Long names and large counts now wrap and do not go outside the page.
- **Small screens:** The report dialog stays on the screen, with **Generate** and **Cancel** visible. When the screen does not have sufficient height, the options scroll and the list of datasets keeps a usable height. A long scope name shows in a maximum of two lines next to **Select current review scope**, and the full name shows in its tooltip.

### Fixes

- **Recent sessions:** The project tree shows each recent folder one time. A damaged list of recent folders in the settings file does not stop the start of the application.
- **Load sessions:** A folder that is missing or that the application cannot read does not show "Not a session". A long hint adjacent to **Browse folders…** ends with "…".
- **Angles after a file change:** If the file of a tomogram changes after you turn its view, the application sets the angles to zero. **Angles** and the angle boxes show these zero angles, not the previous angles.
- **Animations on Linux:** A change of the window size during a panel animation, for example when you start or stop **Focus**, does not stop the application.
- **Context panel:** A long word without spaces, for example a file name, wraps in the panel.
- **Counts:** Messages about one capture say "1 capture", not "1 captures". When the total is not known, the capture list says "capture total unavailable".
- **Help and start page:** Help, the empty project panel and **Load sessions** name all the folders that you can load: Tomography 5 sessions and Warp, IMOD, AreTomo3, Scipion and reconstruction folders.

## 1.0.1 (6 November 2026)

### Fixes

- **Tomogram import:** Reconstruction folders can now be recognised from MRC volume headers without an IMOD reconstruction label. A valid volume record uses space group 1, includes every section in one volume and has no tilt-stack extended header. Files with their own acquisition MDOC or known stack names remain protected from this fallback. In acquisition folders, an unaccompanied MRC volume is skipped with a warning instead of being listed as a tilt series.
- **Calibration and saved review state:** Pixel sizes supplied for a version survive a restart and remain separate from the value recorded in the file. Restoring a snapshot's calibration does not silently apply it to other views. Save failures and pending changes now keep accurate feedback, including CSV export results.
- **Navigation and controls:** Search-tile highlights follow the verified exposure, including its camera field. Keyboard focus is handed back to the visible page when a side panel closes. The main toolbar can no longer be hidden through Qt's default context menu.
- **Responsiveness:** Preview preparation avoids unnecessary image copies, and review-state writes remain bounded during repeated edits.

### Tomograms tab

- **Review tag source changes:** The Tags panel and **Manage tags** distinguish changed dates, changed file facts, missing versions and versions the app could not check. **Accept current files** records the reviewed file facts and keeps the original counts. CSV exports include a **files accepted** column. Projects with an acceptance use schema 2. Version 1.0.0 cannot display those projects' tags, and leaves their files in place.
- **Rotation feedback:** Slow-render messages appear and clear without requesting a new page layout.
- **Canvas toolbar:** Fit stays inline when moving Angles into More frees enough room. When a focused control moves into More, keyboard focus follows it. If the focused More button disappears, focus returns to the canvas.
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
- **Snapshots linked to tags:** **Snapshot and link** saves the current view and links it to a tag on that tomogram. **Link captures…** attaches existing captures. The tag's camera opens its capture actions. Links are kept in the tag project and do not change observation counts.
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

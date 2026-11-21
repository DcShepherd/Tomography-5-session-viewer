# Change log

This file lists the fixes and new functions in each release.

## 1.2.1 (21 November 2026)

### Processing folders

- **Warp output folders:** You can select one of the output folders in a Warp processing folder, for example `warp_tiltseries/reconstruction/deconv`. The application then loads the full Warp processing folder.
- **Warp volumes without their project:** When the Warp processing folder is not available, for example in a copy of the `deconv` folder only, the application reads the volumes that have a Warp file name (`<name>_<pixel size>Apx`) as tomograms. The Context panel shows "Read as: tomograms, by Warp file names". Links to tilt series, CTF data and frame data are not available.
- **Warp folder refused:** The application does not load a Warp CTF, power-spectrum or frame folder, or a Warp folder that has no volumes, without its Warp processing folder. The message "This folder has Warp output, but its Warp project folder was not found." tells you why. The drop panel shows the full reason.
- **MRC files with no type:** Some MRC files have no MDOC file, no extended header and no Warp file name. In **Load sessions**, their folder shows **Select at load**. When you load the folders, the application shows one window for each of these folders. Select **Tomograms**, **Tilt series** or **Do not load**. The application keeps your selection for that folder in its settings, not in the folder. To change the selection, select **Change…** in the Context panel. The application then loads the folder again.
- **Two volumes with one version:** When a Warp folder has two or more volumes of one tomogram with the same version, the application shows a warning. The version list then shows the pixel size of each volume, for example "Raw · 5.00 Å/px". The Summary page and the report use only a version that has one volume. The report does not show a central slice for the other versions, and tells you why.

### Reports

- **Section order:** In each dataset, the tilt-series table comes first, then the exposure thumbnails, then the tomograms. Before, the tomograms came between the table and the thumbnails.
- **Exposure thumbnails:** The thumbnails do not show the stage position (Stage X, Y).
- **Two decimals:** In the report, the tilt range, the MRC pixel size and the stack pixel size show a maximum of two decimals, for example "-60.01 to 59.99" and "6.77". The mean residual and the weighted mean residual show two decimals on the screen and in the report, for example "0.95 nm". The recorded values do not change.
- **Fact cards:** The fact cards of the Tomograms section (Versions, Volume, Alignment and Parent link) have the same height, and their detail lines align.
- **Sources:** The **Sources** block has one short line for each column of the Recorded values table: what the value is, the file pattern and its caveat. A caveat that applies to all columns shows one time. A tomogram whose values differ from the others gets one short line. The details for each tomogram are in the PDF metadata. The "Not recorded" line below the table is removed, because the table does not show a column that has no value.
- **No dataset link:** The cover, the contents, the sections and the report dialog use the same words for tomograms that have no link to a dataset: "No dataset link". The cover gives the number of central slices, for example "Central slices 2 of 3". A slice that the report cannot show has the text "Not shown" and the reason.
- **Report dialog:** A pane on the right, **In this report**, shows the parts of the report in their sequence, with their counts. When you tick only processing folders, its first line tells you that this makes a folder report. The dialog has less text. The help is in tooltips, and a disabled option tells you why in its tooltip. The option names are shorter: **Exposure thumbnails**, **Linked tomograms** and **Central slices**. The Type column shows words, not codes. The dialog height follows the number of rows.
- **Ticks in the dialog:** The tree has no row selection. Click a row to tick or untick it. The tick boxes look the same as the other tick boxes, in the two themes. When you put the pointer on a row, the full row shows the highlight. When the dialog opens, the group and atlas rows show at the top.
- **One atlas control:** The tick of the atlas row (AT) is the only control for the full screening overview. **Also include supporting atlas-session overviews** is removed.
- **Warnings:** The report lists the warning "Failed batch … could not be associated with a search map", as the Summary page does. The cover totals include it.
- **Atlas sessions:** The header and the contents of an atlas session count all the grids that have an atlas. When the image of a grid is missing, a note gives the name of that grid.
- **Atlas markers:** When the application cannot calculate the atlas markers, the caption says "Atlas markers unavailable: they could not be computed." Before, it said that no marker could be placed.

### Tags

- **Tag project file names:** When you create a tag project (with the first tag, **Create project…** or **Save As**), you can type a name without `.json`. The application adds `.json`. When the extension has a typo, for example `test.jsn`, `testjson` or `test.jons`, the application asks you to use the corrected name `test.json` or to choose again. The application does not replace a file that exists. It tells you that a file with this name exists, and you choose again.

### Atlas and image tabs

- **Marker colours:** The markers on the Search map, Overview and Search tile images show the same batch result as the Atlas: collected, partial, failed or queued. Each exposure area shows the result of its own tilt series.
- **Search maps card:** On the Summary page, the tile of each search map shows the same status as its row in the list.
- **Overview links:** When a batch position records the name of its Overview image, the application links the position to the Overview of that name. When no Overview, or more than one, has that name, the application shows a warning and makes no link. Before, it could use the Overview of the search map.
- **Tilt-series names:** A tilt series joins a batch position by its name only when the name is the batch name, or the batch name followed by `_2`, `_3` and so on.
- **Failed batches on search maps:** The application puts an inferred failed batch on a search map only by its stage position, not by its name.
- **Search tiles for other exposures:** When all the search tiles of a position record a different exposure area, the application does not open them for this exposure, also when there is only one tile.
- **Enter:** Enter opens each marker that a double-click opens.
- **Camera fields:** When the exposure areas are hidden, the camera field of the selected exposure shows. Before, the first camera field with the same area name could show.
- **Zoom:** When a sharper level of the same image loads during a zoom or a flight, the motion continues.
- **Labels while you move:** While you pan or zoom, the marker labels follow the motion. The application does not draw all the markers again for each frame.

### Acquisition data

- **Aborted:** A recorded status of "Aborted" is a failure (an interruption), as "Failed" and "Error" are.
- **Names that look like numbers:** Names in the Tomography 5 XML files stay as they are recorded, for example `001` or `1.10`. Before, the application could read them as numbers, so the positions `1.1` and `1.10` could become one position.
- **Incomplete stacks:** When an MRC stack holds fewer frames than its header records, the application shows the frames on disk. A missing frame shows the reason, for example "MRC frame 41 is not on disk: the file holds 40 of 41 frames (incomplete data block)."
- **Atlases with JPG tiles:** When an atlas has only JPG tiles, the application reads the pixel size from all the tiles, not from the first 64. It reads only the position and size fields of each tile file, so the load stays fast.
- **Voxel size:** The voxel size of an MRC file is the cell size divided by the sampling (MX), as the MRC format defines it. The application uses the number of columns (NX) only when MX is 0.
- **Timestamps:** When a timestamp has more than six decimals of seconds, the application removes the other decimals. It does not round them, so a time does not move to the next second.
- **Cloud-only files:** The application does not open a cloud placeholder file, for example a file that is only online. It shows "Cloud-only file: make it available locally, then reload the folder." It skips a placeholder stack that has no MDOC file, with this warning.

### Tomograms tab

- **IMOD unsigned bytes:** The application reads an IMOD volume of mode 0 as unsigned bytes when its header has the IMOD stamp and does not set the signed-bytes flag. Before, it read all mode 0 volumes as signed bytes, so the grey values of these volumes were incorrect.
- **Slabs with missing values:** In a view along an axis, **Slices** calculates the average of adjacent planes from finite voxels only. A voxel shows no value only when no voxel of the slab is finite. Before, one missing value removed the average at that position. Integer data do not change.
- **Changed files:** When a volume file becomes shorter after the load, the application does not read it and tells you why.
- **Intersection:** A click without a drag does not move the intersection point. Double-click to move it. The tooltip of the lock button tells you this.
- **Home and End:** Home goes to the first plane, and End goes to the last plane.
- **Pointer readout:** The pointer readout and the intersection use the same voxel. Before, they could differ by one voxel.
- **Minimise:** When you minimise the window, the Tomograms tab only stops playback. **Focus**, a snapshot that waits and an open movie dialog stay. Before, a minimise stopped **Focus** and cancelled a snapshot that waited.
- **Failed reads:** When the application cannot read a volume into memory, it does not try again and again. Select a different volume, then select the volume again to try again.
- **Preview clip memory:** **Preview clip** uses the shared memory limit. When the limit is too small, it tells you why.

### Summary

- **Dose chart:** A time axis shows only the images that have a timestamp. A note gives the number of the other images, for example "3 dose images have no timestamp and are not shown." The statistics include all the values.
- **Timeline keys:** The arrow keys go through the timeline segments in time order. Before, they followed the sequence in which the application drew them.

### Window

- **Close during a movie export:** When you close the window during a movie export, the application asks about the movie first, before it checks the tags. When you select **Keep the app open**, nothing stops.
- **Close during a report:** When you close the window while the application makes a report, the application stops the report. It does not ask. When the application is writing the PDF file, it completes the file first.
- **Background work at close:** Preview clips and contact sheets stop when you close the application.
- **Current row:** After you group, rename or ungroup rows of the project tree, the row of the current scope stays the current row.
- **Start mark:** The window shows only after the start card has shown for its minimum time, also when a Windows timer starts early.

## 1.2.0 (16 November 2026)

### Atlas overlays

- **Batch positions with the same ID:** When one `BatchPositionsList.xml` has two different records with the same batch position name, the application shows a marker for each record, with the status of that record. Each marker shows "Unresolved link" and the tooltip "Batch ID is not unique in this folder. No tilt series is linked to it." Before, the Atlas tab showed one marker and the PDF report showed two markers with the status of the second record. The Atlas tab and the PDF report now show the same markers. These records do not highlight exposures when you point at them, they do not select a marker, and a search tile that names this ID shows no overlay. The counts do not change. On the Search tab, each of these records now has its own row with its own search-map tile. Before, when the records used the same search map, the first record showed on the tile of the second record, and the second record had no row.
- **Collections:** The application calculates the Atlas markers from all the data of the grid. When you hide a collection, its markers do not show, and the other markers do not change.
- **Presets:** The overlay panel of each image tab starts with **Show**: **Standard**, **Positions only** and **Clean image**. **Clean image** replaces **Remove all overlays**: it hides all overlays, the Positions card and the scale bar, and it keeps your collection selections. When your selection is not a preset, **Custom** shows how many rows differ from **Standard**. The **Overlays** button shows the name of a preset that is not **Standard**, for example "Overlays: Positions only".
- **Customise:** The rows are in the **Customise** section, under the headings **Layers**, **Labels** and **Canvas**. "Batch position numbers" is now "Position numbers", and "Batch and exposure names" is now "Names". "Shown once zoomed in" and "Merged while zoomed out" are now tooltips.
- **Layers without a row:** Tilt series and stage crosshairs show only when **Batch position** shows. Link lines show only when **Exposure area** shows. Condition areas show only when **Focus and tracking** shows the focus area. Before, these layers stayed on when you turned their row off.
- **One redraw:** When you select an item, change a preset or change a row, the canvas draws the markers one time. Before, it could draw them two or more times.
- **Positions card:** The **Positions card** replaces the marker legend on the Atlas tab. It shows only the marker types that are on the atlas, with their counts. In the overlay panel, "Marker legend" is now "Positions card". When it is closed, the card is only as wide as its header. When you open it, it becomes wider to show its rows, with the same motion as the opening.
- **From:** The **From** button on the Positions card replaces the **Data collections** section of the overlay panel. It opens a menu with a check box for each collection and **Show all**. You can use the arrow keys, Space and Enter in the menu. When you hide a collection and the Positions card is closed or hidden, the **From: n of m** button shows at the bottom left of the canvas. An image export does not include this button.
- **Colour by:** The **Colour by** button on the Positions card sets what the colour of each batch-position marker shows: **Status** (the default), **Tomogram**, **Tags**, **Specimen thickness**, **CTF fit** or **IMOD positioning thickness**. A mode shows only when the atlas has data for it, and a mode with no data shows grey in the menu, and its tooltip tells you why. In a value mode, a marker has a value only when all the tomograms of its tilt series record one value with one unit. When the tomograms do not agree, the marker shows **No value** and its tooltip names the processing folders. A failed position shows **No value** and keeps its cross. A position with an unresolved link keeps its dashed grey ring in all modes. The colours run from blue (low) to yellow (high), with the same colours in the two themes. The range comes from the markers that show. A cluster ring shows the mix of the values of its members. The tooltip and the cluster list show the value and the reason for **No value**. The **colour key** at the bottom centre of the canvas shows the name and range, a bar for each group of values, the colour strip and the number of markers with **No value**. **Clean image** hides the key, and an image export includes it. The PDF report keeps the status colours. The counts on the Positions card do not change with the mode. The mode stays when you change the scope or a collection, and goes back to **Status** when you load a different project.
- **Colour by tags:** In **Tags** mode, a batch position is **Tagged** when a placed tomogram of the position has a saved tag in an open tag project. The tag can have a count or no count. It is also **Tagged** when the reconstruction changed after you tagged it, and the tooltip tells you this. The tooltip gives the number of tagged tomograms ("Tagged tomograms: 1 of 2") and the tag names with their counts. A position is **Not tagged** when all its placed tomograms are in an open tag project and have no tag. A position shows **No value** when it has no placed tomogram, or when it has no tag and a tomogram is not in an open tag project; the tooltip names that tomogram. The colour key shows **Tagged**, **Not tagged** and **No value** with their counts. When you add a tag, remove a tag, or open or close a tag project, the colours change at once. **Tags** is grey in the menu when no open tag project contains a placed tomogram.
- **Controls stay in place:** The **Overlays** panel stops above the zoom buttons. When its rows do not fit, the panel scrolls. The zoom buttons move beside the panel only when the canvas is too short to show the presets. The panel keeps the same width when you open or close **Customise**. The colour key stays in the same place when you open or close the Positions card or change the colour mode.
- **Messages above the colour key:** On the **Atlas** tab, when the colour key shows, a short message (for example "Cleared marker selection and highlights.") shows just above the key, centred on it. It does not cover the key. On other tabs, messages show in the usual place.
- **Right-click to close:** When the Positions card or the overlay panel is open, right-click outside them to close all of them, and any open menu, with one click. The card and the **Overlays** button become narrow again. A right-click on the card, the panel, their buttons or a menu does not close them.
- **Colour key highlight:** Put the pointer on an entry of the colour key (a bar, a part of the colour strip, **No value**, or a Tomogram or Tags category) to highlight its markers. The other batch-position markers and their labels become faint, and a cluster with a member in the entry stays bright. The colours do not change. Move the pointer off the key to show all the markers in full again. The key is one Tab stop: **Left** and **Right** go to the previous or next entry, and **Esc** shows all the markers again. A screen reader reads the range and the number of positions of each entry. A new mode, a different set of markers, **Clean image** and an image export remove the highlight.
- **Unresolved link glyph:** A batch position that has no unique destination now shows the dashed grey ring of "Unresolved link" on the canvas and in the PDF report, as the legend shows it. Before, the canvas and the PDF report could show a solid grey ring.
- **Colour by menu:** A mode that has no data shows grey and does not highlight when you put the pointer on it, because you cannot select it. When it has the keyboard focus, it shows only an outline, so you can read its tooltip. The ring of each mode is easier to see in the two themes, and the selected mode has a dot in the accent colour.
- **Overlays button:** When you open the overlay panel, the **Overlays** button grows to the width of the panel with the same motion as the panel. When you close the panel, the button goes back to its width. The right edge of the button does not move. Before, the button became as wide as the panel immediately. With reduced motion, the button changes width immediately. When the canvas is too narrow for the label, the button shows only its icon and does not grow.

### Tomograms tab

- **Movie count:** The film mark in the list now shows the number of saved movies of the tomogram, as the camera shows the number of captures. Click the number, or right-click the row and select **Movies…**, to see the movies, newest first, 12 on each page. **Older movies** and **Newer movies** show the other pages. Each row shows the date and the size of the movie. For PNG frames, it shows "Frames folder". When the application cannot find the file, the row shows "Not found: moved or deleted". **Open** plays the movie, and the folder button opens its folder. The list does not change or delete a movie. When a tomogram has no movies, **Movies…** is not available and its tooltip tells you why.
- **Movie readout:** In **Export movie**, the line below the timeline shows only the plane, for example "Z 13 / 300", and the time. The plane always shows in full. **First**, **Middle** and **Last** are icon buttons.
- **Movie sections:** The first **Export movie** dialog opens with **Planes** open. Each subsequent dialog opens the section that was open when you closed the last dialog, until you close the application.
- **Equal values:** When a value is the same on all axes, the application shows it one time, for example "8 Å", not "8 × 8 × 8 Å". The lowpass unit shows "≈ 100 Å" in the same way.
- **Pixel size check:** When the three pixel sizes in a file header differ only by very small rounding errors, the application reads them as one pixel size. The application can then suggest this value for other versions. It does not offer to replace a valid pixel size.
- **Sliders:** The full handle of each slider in **Image settings** and of the movie timeline shows. Before, the top of the handle was cut.
- **Section arrows:** In **Image settings**, the arrow of an open section stays at the left of its title.
- **Film button:** The film button and the rate button ("10/s") have the same height and are aligned.
- **Shorter tooltips:** The tooltips and information notes of the Tomograms tab, **Image settings**, **Export movie** and the Summary page of a processing folder are shorter. They tell you what each control does in one short sentence.

### Scipion alignment

- **Alignment jobs:** You can load a Scipion Etomo job (`ProtImodEtomo`) or fiducial alignment job (`ProtImodFiducialAlignment`) with a reconstruction job (`ProtImodTomoReconstruction`). The application compares the tilt angles and transforms that each job recorded for a tilt series. When exactly one alignment job agrees with a reconstruction, the residuals of that job, and the positioning thickness of an Etomo job in nm, show on that tomogram. The text says "Matched to this reconstruction by its tilt angles and transforms" and gives the job name. Two programs print the same value with different digits, so the application compares each value within the precision that the two files print. When two jobs agree, or the values differ, or the reconstruction job has no readable `.tlt` or `.xf`, the application uses no job and shows the reason on the tomogram. A series name alone does not make a link unless you confirm it. The tooltip of an alignment job in **Project** shows how many of its series link to a loaded reconstruction.
- **Etomo jobs:** An Etomo job loads as an alignment job and adds no tomograms. Before, its `<ts>_full_rec.mrc` files loaded as tomograms. When a tag project has tags for these tomograms, the tag manager counts them as not in the loaded folders, and **Find moved folder…** does not show for them.
- **Positioning thickness:** When an Etomo job records the positioning thickness only in pixels, the application does not show it on the linked tomogram, because these pixels are not the pixels of that reconstruction. The warning tells you why.
- **Sources in the PDF report:** A value from another loaded job shows its source as the job name and the path in the job, for example "IMOD Etomo · 177, TS1/align.log". The report does not show paths from your computer.
- **Alignment card:** The **Alignment** card on the Summary page and in the PDF report shows the type of output, for example "IMOD tiltalign output found". Before, it showed all the text, with the file path. With many Warp tomograms, it showed "N output types". Each tomogram's detail shows where the value came from.
- **Confirm by name:** When a reconstruction job has no readable `.tlt` or `.xf` and exactly one loaded alignment job has the series, the warning card on the tomogram has **Confirm by name…**. **Review name-only links** also has an **Alignment job** section. A confirmed tomogram shows the residuals of that job with a **By name** tag, and the card has **Unlink**. The decision is saved in the app settings, not in the source folders. When a file of either job changes, the decision no longer applies. The status link now reads "N by name only · Review".
- **cryoCARE source:** A Scipion cryoCARE job records in `predict_config_<ts>.json` which even and odd half sets it denoised. When the reconstruction that has these files is loaded, the denoised tomogram shows the recorded values of that reconstruction (for example the alignment residual). The text names the reconstruction. **How each value was found** in the context panel starts with where the volume came from, and **Files** lists the JSON file and the two input files. A value in pixels shows only when the two volumes have the same dimensions. When the JSON file cannot be used, a warning on the tomogram tells you why.
- **Alignment job row:** When all the series of an alignment job link to loaded reconstructions, the job has no row of its own in **Project**. Its values show on the tomograms of the reconstruction, and the tooltip of the reconstruction names the job. When you reload the reconstruction, the job reloads too. When you remove it, the job is removed too, unless it also gives values to another loaded reconstruction. An alignment job that does not link fully keeps its row, so you can see the reason.
- **Help:** The **Reading** page of Help has the section "Alignment from a Scipion alignment job".

### Loading

- **Start-up:** The window opens approximately 0.7 seconds faster. The application makes each tab in its final position, and it loads the PDF library only when you make a report or a contact sheet.
- **Start mark:** While the application starts, a small card with the application mark shows in your theme, at the position where the window opens. It shows after approximately 0.4 seconds and closes when the window shows. Before, nothing showed for approximately 3 seconds. The card does not take the focus. When the application starts quickly (for example on Linux), the window shows only after the card has shown for 0.6 seconds, so the window does not open under the card.
- **Search tiles:** The application reads the tile files of a search map only when a batch position uses that search map. A collection with search maps that no batch position uses loads faster. Before, a damaged tile file in one of these search maps removed all the search tiles of the grid and showed the warning "Search tiles could not be parsed and was skipped". Now that file has no effect. A damaged tile file in a search map that a batch position uses shows the warning, as before.
- **Context after a linked processing folder:** When all the tomograms of a processing folder are linked to loaded tilt series, the Context panel shows the source folders and the acquisition settings of the project group after the load. Before, it showed internal program text until you selected a row.

### Load sessions

- **Check again:** When a folder in the list is missing or has no session, the line "To check a folder again, add it again." shows below the note at the bottom of the window. To check the folder again after you repair it, add it again with **Browse folders…**, a recent folder or a drop. Its row stays in the same position. Before, each new folder made the application check all the folders in the list again. Thus a repaired folder could change to ready when you added a different folder. Now a folder changes only when you add it again.
- **Speed:** The application checks each folder that you add one time. When you remove a folder or select **Clear list**, the application does not examine the disk. Before, a list of 12 folders made 78 checks, and each removal checked all the other folders again. When you drop folders, the application uses the checks that it made while you dragged them.
- **Focus:** When you remove a folder with the keyboard, the focus goes to the same button in the next row, or in the previous row if there is no next row. When the list becomes empty, the focus goes to **Browse folders…**. After **Use parent folder**, the focus goes to the remove button of the same row. Before, the focus could go to **Clear list** or **Cancel**, or be lost.
- **Tab order:** The Tab key goes from **Browse folders…** to the recent folders, **Clear list**, the buttons of each row in the list, **Cancel** and **Load**. The list is not a stop, and the list scrolls to show the row that gets the focus. Before, the row buttons came after **Load**.
- **Window size:** The smallest window is approximately 10 pixels taller when it shows recent folders, because it keeps space for the new line. The window does not grow when the line shows.
- **Folder count:** When you load more than one folder, the loading window shows the number of the folder, for example "Folder 2 of 4 · Grid_B". When you select **Retry**, it counts only the folders that it loads again.
- **Focus after the first load:** When you load sessions into an empty project, the focus goes to the project tree, also after a drop. Before, a drop put the focus back in the project filter. When you add more sessions, the focus goes back to where it was.
- **Read time:** In Context, **Source folders** shows when the application read each session folder, for example "Read into app: 2026-11-12 22:17:58". Processing folders show the time in the same format. Before, they also showed the time zone and "read-only". The application does not write to the folder.

### Tilt series

- **Tilt angles:** Each tilt angle on the screen shows the recorded value with a maximum of two decimals and no "+", for example "-40.01°" and "12°". Before, the frame readout showed one decimal and a "+", for example "+12.0°", and a single frame at -0.02° showed "0.0°" below the image. The PDF report does not change.

### Window

- **Back and Forward:** The tooltips of **Back** and **Forward** tell you where each step goes, for example "Back to Position_2 in Grid_A · Tilt series (Alt+Left)". Before, they showed only "Return to the previous navigation step.".
- **Keyboard start:** When the application opens, the focus is on **Load sessions…** in the page. Push Enter or Space to open **Load sessions**. Before, the focus was in the empty project filter, where Enter did nothing.
- **Ctrl+F:** Push Ctrl+F to put the cursor in the filter of the current tab. On **Summary** it is the project filter. The text in the filter is selected, so you can type a new filter. If the project panel, the list or the Tomograms list is hidden, Ctrl+F shows it first. The filter tooltips and Help show the key.
- **Focus ring:** A main button, for example **Load sessions…**, **Load** or **Retry**, shows a ring around it when it has the focus. Before, the button only became 1 pixel smaller, which was difficult to see. The button does not change size.
- **Copied:** Each copy control tells you that it copied. A button such as **Copy details** shows "Copied" for approximately one second. A copy icon shows the tooltip "Copied". A copy from an empty page does not also show a message at the bottom of the window.
- **Tab counts:** When you select another item in the project tree and the counts change, the tabs show the new counts approximately three times faster. The application measures the width of each tab name one time. Before, it measured all the tab names again at each change of a count.
- **Window title:** The window title shows the name of the dataset group, for example "Demo_collection — Tomography Session Browser". When you load more than one group, it shows the first group and the number of the other groups, for example "Grid_A + 1 more — Tomography Session Browser". Thus you can identify each window in the taskbar.

### Project panel

- **Filter with no match:** When the filter matches no row, the panel shows "No project items match this filter." and **Clear filter**. Before, the tree was empty with no explanation. **Clear filter** shows all the rows again and puts the cursor in the filter.

### Settings

- **Safe save:** The application writes its settings to a temporary file first. Then it replaces the settings file with the temporary file. If the application stops during a save, the settings file stays complete. Before, a stop during a save could damage the file, and the next start then used the default settings.
- **Compact setting:** The settings file no longer keeps the "compact" value. The **Compact** control was never available. An older settings file with this value opens as before, and the next save removes the value.

### Summary

- **Sample names:** The legends of the defocus and camera dose charts and the lanes of the timeline show the name of the sample as the Samples table shows it, for example "cryo_em". Before, the legends changed the name, for example to "Cryo Em", and in a project the lanes showed the name of the atlas grid, for example "SMP_olive". When two samples on one page have the same name, each one keeps its grid number, for example "1. cryo_em" and "3. cryo_em".
- **Chart colours:** Each sample in a chart keeps its colour when its name changes. Two samples with the same name are two series with two legend entries. Before, they were one series. The colours of some samples change one time with this release. The legend is in alphabetical order. Upper case and lower case letters have the same order.
- **Time axes:** The time axes of the timeline and of the defocus and camera dose charts show round times from the start, for example 00:00, 04:00, 08:00 and 12:00. The axis ends at the last acquisition, with a line and no time. Before, the times divided the axis into equal parts, for example 04:41 and 09:22.
- **Voxel spacing:** In the table of processing volumes, the column "Voxel X × Y × Z" has the new name "Voxel spacing". It gives the size of one voxel in Å, not the dimensions of the volume.

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

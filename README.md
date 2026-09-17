# Tomography Session Browser

Tomography Session Browser is a desktop application for Thermo Fisher Tomography 5 session folders. Use it to review session images and metadata. Use it to find incomplete or failed tilt series. Create scoped PDF reports.

The application is read-only. It does not edit, repair, rename, move, or normalise files in microscope-output folders.

## Supported platforms

| Operating system | Recommended terminal |
|---|---|
| Windows | PowerShell, Miniforge Prompt, or Anaconda Prompt |
| Linux | Bash |
| macOS | Terminal with Zsh or Bash |

The installation uses an isolated Conda environment. It does not change the system Python installation.

## Before you start

You need:

- A 64-bit Windows, Linux, or macOS computer with a graphical desktop.
- [Git](https://git-scm.com/downloads), unless you download a ZIP archive.
- A Conda-compatible installer. See the [Conda installation guide](https://docs.conda.io/projects/conda/en/stable/user-guide/install/).
- An original Thermo Fisher Tomography 5 atlas-screening or data-collection session folder.

If you are new to Conda, install it. Then close and reopen the terminal. Run this command to confirm that Conda is available:

~~~text
conda --version
~~~

## Install and start the application

### Windows PowerShell

Copy the HTTPS repository URL. Open PowerShell. Then run:

~~~powershell
git clone PASTE_REPOSITORY_URL_HERE
Set-Location Tomography-5-session-viewer
conda env create -f environment.yml
conda activate tomoapp_session
python -m pip install -e .
tomo_session_viewer
~~~

### Linux or macOS

Copy the HTTPS repository URL. Open a terminal. Then run:

~~~bash
git clone PASTE_REPOSITORY_URL_HERE
cd Tomography-5-session-viewer
conda env create -f environment.yml
conda activate tomoapp_session
python -m pip install -e .
tomo_session_viewer
~~~

If you downloaded a ZIP archive, extract it. Open a terminal in the extracted folder that contains environment.yml. Start with the conda env create command above.

Use this alternative start command when required:

~~~text
python -m tomography_session_browser.main
~~~

## Keep the session folder unchanged

> [!IMPORTANT]
> Select a complete session folder. Do not select a .dm, .mrc, .mdoc, XML, image, Atlas, Batch, SearchMap_*, or Sample1 item. Do not rename files or folders. Do not change letter case. Do not flatten the folder tree. Do not move files from the session folder. Copy a complete session folder for review only when its contents stay unchanged.

The application uses original Tomography 5 names and recorded paths. A changed name or path can cause missing images, empty tabs, or unresolved atlas links. Letter case is important on Linux and on case-sensitive macOS file systems.

### Atlas-screening session

Select the top folder that contains ScreeningSession.dm:

~~~text
Atlas_Screening_Session/          <- select this folder
├── ScreeningSession.dm
├── Sample1/                      <- Sample1 or Sample-1 names
│   ├── Sample.dm
│   └── Atlas/
│       ├── Atlas.dm
│       ├── Atlas_*.mrc or Atlas_*.jpg
│       └── Tile_* files
└── ...other original Tomography 5 files
~~~

### Multi-grid data-collection session

Select the top folder that contains Session.dm and the original sample folders:

~~~text
Data_Collection_Session/          <- select this folder
├── Session.dm
├── Sample1/
│   ├── Session.dm
│   ├── SearchMaps/
│   │   └── SearchMap_*/
│   │       ├── SearchMap.mrc or SearchMap.jpg
│   │       ├── Overview.mrc or Overview.jpg
│   │       └── Tile_* files
│   ├── Batch/
│   │   ├── BatchPositionsList.xml
│   │   └── *_Search.mrc, *_Tracking.mrc, and *_Exposure.mrc
│   └── matching tilt-series .mrc and .mdoc files
├── Sample2/
└── ...other original Tomography 5 files
~~~

Some single-collection exports put Session.dm, SearchMaps, Batch, and tilt-series files in the top session folder. Select that folder.

The application loads partial sessions. It reports recoverable problems as warnings.

## Review a session

The screenshots use synthetic data. They contain no microscope or research data.

### 1. Open session folders

Start the application. Select **Open folder**. Add an atlas-screening session, a data-collection session, or both. Select **Browse folder...**. Then select each complete session folder.

The application links two sessions only when their recorded metadata supports the link. Use **Import folder** to add another related session. Imported folders are also read-only.

![Select complete session folders](docs/images/readme/01-select-session-folders.png)

### 2. Check the project tree and dashboard

Wait for loading to finish. The project tree shows the available review scope. The Session tab shows atlases, overviews, search maps, batch positions, tilt series, acquisition time, and warnings.

![Linked-session dashboard with synthetic data](docs/images/readme/02-linked-session-dashboard.png)

| Badge | Meaning |
|---|---|
| LS | Linked atlas and data-collection session |
| AT | Atlas-only session |
| DC | Data-collection session |
| ! | Unresolved or ambiguous atlas link. Read the warning. |

### 3. Review images and metadata

Use the **Atlas**, **Overview**, **Search map**, **Search**, **Batch position**, and **Tilt series** tabs. Select an item to inspect its image, overlays, status, metadata, and available navigation. Tab counts match the selected project, session, or sample scope.

![Search-map review with synthetic data](docs/images/readme/03-search-map-review.png)

Warnings and failed or incomplete labels are valid findings when data or links are missing. The application shows unresolved information. It does not create a scientific link.

At a whole-grid Atlas view, nearby batch positions appear as counted clusters. Zoom in to show short batch IDs. Search-map footprints and tile grids appear only when they are readable. Select a batch marker to inspect it. Double-click a marker to open its one verified linked Overview. If several targets exist, the application does not navigate. It explains the reason.

Use the **Overlays** panel to control clusters, labels, tile grids, the marker legend, and the per-exposure detail layer. The detail layer is off by default. At a linked project node, use **Data collections** to control markers for each collection. Selecting one data-collection node limits Atlas batch markers to that collection.

In an Overview or Search-map image, middle-click or Shift-click an exposure area to highlight all exposure areas from the same batch position. Left-click an empty area, right-click the image, or press Escape to clear the highlight.

Use the export button above the zoom control, or press **Ctrl+S**, to save the current crop as a high-resolution PNG. The PNG includes visible markers, highlights, the scale bar, and the optional Atlas marker legend. It excludes viewer controls and the pixel-size badge.

### 4. Create a PDF report

Select **Report**. Select a linked group, a session, or data-collection samples. Then select **Generate report**. The report includes supporting Atlas context once when it is needed.

![Select report scope](docs/images/readme/04-report-scope.png)

## Update an installation

Open a terminal in the repository folder. Run:

~~~text
git pull
conda env update -n tomoapp_session -f environment.yml --prune
conda activate tomoapp_session
python -m pip install -e .
~~~

If you installed from a ZIP archive, download and extract the new version. Then run the last three commands in the new folder.

## Troubleshooting

### conda is not recognised

Close and reopen the terminal after you install Conda. On Windows, use the Miniforge Prompt or Anaconda Prompt. On Linux or macOS, initialise the shell once. Then reopen the terminal:

~~~text
conda init
~~~

### The environment already exists

Update the environment:

~~~text
conda env update -n tomoapp_session -f environment.yml --prune
conda activate tomoapp_session
python -m pip install -e .
~~~

### tomo_session_viewer is not recognised

Activate the environment. Reinstall the application from the repository root:

~~~text
conda activate tomoapp_session
python -m pip install -e .
python -m tomography_session_browser.main
~~~

### The folder is rejected or tabs are empty

- Confirm that you selected a complete session folder.
- Do not select Session.dm, ScreeningSession.dm, Atlas, Batch, SearchMaps, SearchMap_*, or one MRC file.
- Confirm that the original names, letter case, and folder nesting are unchanged.
- For atlas screening, confirm that the selected folder contains ScreeningSession.dm.
- For multi-grid collection, confirm that the selected folder contains Session.dm and sample folders.
- Read each application warning. A partial acquisition can include missing previews or unresolved links.

### Linux reports a Qt platform-plugin error

Run the application in a graphical desktop session. Do not use a headless SSH session. If the error names a missing library, use the Qt or PySide guidance for your Linux distribution.

### The application cannot save settings

The application logs a warning when the user configuration folder is read-only. Continue to review sessions and create reports. The application does not use source tomography folders to store settings.

## Developer use

The project uses Python 3.11, PySide6, Pillow, NumPy, ReportLab, and pytest. An editable installation keeps the start command connected to the current working copy:

~~~text
conda env create -f environment.yml
conda activate tomoapp_session
python -m pip install -e .
~~~

Run all tests:

~~~text
conda run -n tomoapp_session python -m pytest --basetemp .pytest_tmp
~~~

Run a compile check:

~~~text
conda run -n tomoapp_session python -m compileall tomography_session_browser tests tools
~~~

Create public README screenshots with synthetic data:

~~~text
conda run -n tomoapp_session python tools/generate_readme_screenshots.py
~~~

For a UI or report change, start the application. Open a non-sensitive session. Check the changed scopes and tabs. Create a scoped PDF when practical. Do not commit microscope-session data or screenshots that contain sensitive data.

## Scope and data safety

- Use this application with Thermo Fisher Tomography 5 session layouts.
- Use it to review acquisition metadata and images. Do not use it for reconstruction.
- The application keeps parsed models and project groups in memory.
- The application does not repair or write to source session folders.
- The application keeps ambiguous or missing scientific associations as warnings.

## License

This project uses the [MIT License](LICENSE).

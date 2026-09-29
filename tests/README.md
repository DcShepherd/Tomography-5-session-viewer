# Essential release tests

This public snapshot contains a small, self-contained release suite. Tests use synthetic data and temporary folders. They do not require microscope datasets. `conftest.py` isolates application settings from the user's configuration.

Run from the repository root after installing the documented dependencies:

```text
python -m pytest --basetemp .pytest_tmp
```

| Test module | Release safeguard |
| --- | --- |
| `test_release_smoke.py` | Window startup and rendering in both themes; current compiled loader shader and QML assets |
| `test_hardening_regression.py` | Recoverable parser errors, metadata precedence, and shared dashboard/report warning counts |
| `test_mrc_metadata_formats.py` | MRC header formats, units, preview scaling, and tilt-angle ordering |
| `test_atlas_reacquisition.py` | Current atlas selection and refusal to project markers onto an incompatible fallback image |
| `test_atlas_tile_count.py` | Atlas tile counts across reacquisitions |
| `test_atlas_lod_pdf_regression.py` | PDF atlas overlays, legend, and unavailable-overlay explanation |
| `test_project_model.py` | Conservative cross-session linking, project grouping, and report generation |
| `test_navigation_service.py` | Verified destinations and refusal of ambiguous jumps; uses `navigation_contract.py` |
| `test_logic_audit_regressions.py` | Failed/incomplete acquisition classification, scope, and viewer frame state |
| `test_session_intake.py` | Validation and deduplication of queued session folders |
| `test_loading_lifecycle.py` | Staged loading, overlay cleanup, and preparation for large sessions |
| `test_report_scope.py` | Selected report scope and deduplicated supporting atlas context |

The development checkout retains the full regression suite, including detailed UI layout, animation, accessibility, and performance tests. Passing this smaller release suite does not replace those checks or native display and PDF review before a release.

## Maintaining the snapshot

The root `.gitignore` explicitly allows only the test files listed above, `conftest.py`, `navigation_contract.py`, and this document. Keep that allowlist when synchronising application updates. Do not copy the whole development `tests` directory or force-add excluded tests. Keep the retained tests aligned with their development versions, except for public-safe fixture labels. `test_release_smoke.py` is specific to this snapshot.

Add a public test only when it protects a critical release behaviour not already covered here. Update this table and the allowlist together. Do not include private datasets, caches, generated PDFs, screenshots from real sessions, or benchmark output.

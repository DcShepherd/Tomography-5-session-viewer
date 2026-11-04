"""Qt-free view models: what the window, the dashboard and the PDF show.

Modules here shape domain and service output for display (project tree
groups, report scopes, context stacks, empty states, navigation history).
They import ``domain``, ``parsers`` and ``services``, never Qt, ``ui`` or
``reports``, so a test, a worker thread or the PDF can use them without a
display (``tests/test_layering.py``).
"""

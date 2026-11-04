"""The staged loader's parts: what is loaded, the background tasks, the steps
that build the UI one event-loop turn at a time, and the load's profiling.

The order a load goes through, and what the loading overlay waits for, is
``MainWindow``'s: these are the pieces it drives.
"""

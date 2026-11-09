"""A settled project stays out of CPython's full garbage collections.

A loaded project is a few hundred thousand objects that the cyclic garbage
collector tracks, and almost all of them live until the next load. A full
(generation-2) collection visits every one: about 90 ms on the GUI thread for
the real collection and atlas pair, which lands wherever the long-lived count
crosses its threshold (O5/B3 record, 8 November 2026). With the project frozen,
the same collection takes about 5 ms.

``settle`` runs when a load batch finishes. It moves every tracked object to the
permanent generation, which no collection visits, so later full collections
visit only what was made since. Reference counting still frees those objects;
only a reference cycle among them waits. ``thaw`` returns them to the oldest
generation when a load begins and when the window closes. Nothing else thaws:
a project edit, a link confirmation or a project installed at once leaves a
settled heap frozen.

A load that runs a full collection of its own frees every cycle that was
waiting, because the collection comes after the load's thaw; so does the close.
A small load or an edit runs none, so its cycles wait with the rest until such
a load. Measured on 8 November: about 800 objects (90 KiB) for an added atlas
and 400 (40 KiB) for an edit, none holding an array or an image.

No call here runs a collection: one would cost the ~90 ms the freeze saves, at
every small load and edit. Both calls are constant time. ``ui.motion.gc_pause``
is the other half of the policy: it holds every collection off for the length
of one animation.
"""
from __future__ import annotations

import gc


def settle() -> None:
    """A load is done: no collection visits what exists now."""
    gc.freeze()


def thaw() -> None:
    """A load begins, or the window closes: collections visit everything again."""
    gc.unfreeze()

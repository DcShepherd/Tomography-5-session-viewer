"""Window display recipe, source-specific levels and bounded recipe Undo."""
from collections import OrderedDict
from dataclasses import replace

from tomography_session_browser.services.volume.scale_bar import ScaleBarStyle

DISPLAY_FIELDS = ("black", "white", "lowpass", "highpass", "slices", "invert", "brightness", "contrast", "smooth", "show_bar", "scale_bar", "levels_mode", "levels_anchor", "spacing_override")
#: What Reset restores. A confirmed pixel size is a scientific choice, not an
#: image setting, so Reset keeps it.
DEFAULTS = {"lowpass": 0., "highpass": 0., "slices": 1, "invert": False, "brightness": 0., "contrast": 1.,
            "levels_mode": "Auto", "smooth": True, "show_bar": True, "scale_bar": ScaleBarStyle()}
#: The filter values G and the switches start from.
DEFAULT_FILTERS = (.1, 0.)


class ImageRecipes:
    def __init__(self, tab) -> None:
        self.tab = tab
        self.history = []
        self.levels = OrderedDict()
        self.last_filters = DEFAULT_FILTERS
        self.window_recipe = None
        self.levels_revision = 0

    def key(self, state):
        return (state.volume_id, state.volume_signature, state.slices, state.lowpass, state.highpass, state.levels_mode,
                state.rotation, state.pivot_xyz, state.pivot_grid_xyz)

    def remember_levels(self, state=None) -> None:
        state = state or self.tab.state
        if state.volume_id and state.black is not None and state.white is not None:
            key = (self.key(state), state.levels_anchor)
            self.levels[key] = (state.black, state.white, state.levels_anchor)
            self.levels.move_to_end(key)
            while len(self.levels) > 256:
                self.levels.popitem(last=False)

    def find_levels(self, state):
        return next((levels for (key, anchor), levels in reversed(self.levels.items()) if key == self.key(state)), None)

    def change(self, *, relevel=False, force=False, **values) -> None:
        self.tab.playback.stop()
        if force:
            self.levels_revision += 1
        old = self.tab.state
        self.remember_levels(old)
        state = replace(old, **values)
        if relevel:
            levels = None if force else self.find_levels(state)
            if levels is None and state.levels_mode == "Manual":
                state = replace(state, levels_mode="Auto")
                levels = self.find_levels(state)
            state = replace(state, black=levels[0] if levels else None, white=levels[1] if levels else None,
                            levels_anchor=levels[2] if levels else None)
        elif values.get("levels_mode") == "Manual":
            state = replace(state, levels_anchor=None)
        if state == old:
            return  # Nothing changed: no Undo step that would do nothing.
        self.history.append(old)
        del self.history[:-32]
        self.tab.state = state
        if state.lowpass or state.highpass:
            self.last_filters = state.lowpass, state.highpass
        self.window_recipe = state
        self.tab.image_settings.sync_state()
        self.tab.image_settings.sync_levels(state.black, state.white)
        self.tab.sync_slices()
        self.tab.request_render()
        if state.spacing_override != old.spacing_override:
            self.tab.metadata_changed.emit()

    @staticmethod
    def display(state) -> tuple:
        return tuple(getattr(state, name) for name in DISPLAY_FIELDS)

    def undo_index(self) -> int | None:
        """The latest step for this source version that changes what is shown."""
        current = self.tab.state
        return next((index for index in range(len(self.history)-1, -1, -1)
                     if (self.history[index].volume_id, self.history[index].volume_signature)
                     == (current.volume_id, current.volume_signature)
                     and self.display(self.history[index]) != self.display(current)), None)

    def can_undo(self) -> bool:
        return self.undo_index() is not None

    def is_default(self, state=None) -> bool:
        state = state or self.tab.state
        return all(getattr(state, name) == value for name, value in DEFAULTS.items())

    def undo(self) -> None:
        current = self.tab.state
        index = self.undo_index()
        if index is None:
            self.tab.status.message.setText("No earlier image change for this version.")
            return
        previous = self.history.pop(index)
        # Later steps of this version showed the image as it is now: they go too.
        version = (current.volume_id, current.volume_signature)
        self.history[index:] = [step for step in self.history[index:]
                                if (step.volume_id, step.volume_signature) != version]
        self.tab.playback.stop()
        self.tab.state = replace(current, **{name: getattr(previous, name) for name in DISPLAY_FIELDS})
        self.tab.image_settings.sync_state()
        self.tab.image_settings.sync_levels(previous.black, previous.white)
        self.tab.sync_slices()
        self.tab.request_render()

        if previous.spacing_override != current.spacing_override:
            self.tab.metadata_changed.emit()

    def reset(self) -> None:
        self.last_filters = DEFAULT_FILTERS
        self.change(relevel=True, force=True, **DEFAULTS)

    def toggle_filters(self) -> None:
        active = self.tab.state.lowpass or self.tab.state.highpass
        lowpass, highpass = (0., 0.) if active else self.last_filters
        self.change(relevel=True, lowpass=lowpass, highpass=highpass)

    def source_entry(self, state, explicit):
        self.remember_levels()
        self.window_recipe = self.tab.state if self.tab.state.volume_id else self.window_recipe
        if explicit or self.window_recipe is None:
            return state
        recipe = self.window_recipe
        if recipe.lowpass or recipe.highpass:
            self.last_filters = recipe.lowpass, recipe.highpass
        state = replace(state, lowpass=0., highpass=0., brightness=recipe.brightness, contrast=recipe.contrast,
                        invert=recipe.invert, smooth=recipe.smooth, slices=recipe.slices,
                        layout=recipe.layout, linked=recipe.linked, dividers=recipe.dividers,
                        show_bar=recipe.show_bar, scale_bar=recipe.scale_bar)
        levels = self.find_levels(state)
        if levels is None and state.levels_mode == "Manual":
            state = replace(state, levels_mode="Auto")
            levels = self.find_levels(state)
        return replace(state, black=levels[0] if levels else None, white=levels[1] if levels else None,
                       levels_anchor=levels[2] if levels else None)

    def version_entry(self, state, volume):
        old = self.tab.state
        manual = (state.black, state.white, state.levels_anchor) if state.levels_mode == "Manual" and state.black is not None and state.white is not None and (state.lowpass, state.highpass, state.slices) == (old.lowpass, old.highpass, old.slices) else None
        state = replace(state, **{key: getattr(old, key) for key in (
            "lowpass", "highpass", "slices", "invert", "brightness", "contrast", "smooth", "show_bar", "scale_bar")})
        if state.volume_signature is None:
            state = replace(state, volume_signature=(volume.fingerprint.size, volume.fingerprint.mtime_ns))
        levels = manual or self.find_levels(state)
        if levels is None:
            state = replace(state, levels_mode="Auto")
            levels = self.find_levels(state)
        return replace(state, black=levels[0] if levels else None, white=levels[1] if levels else None,
                       levels_anchor=levels[2] if levels else None)

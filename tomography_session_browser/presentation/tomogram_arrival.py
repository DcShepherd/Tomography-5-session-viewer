"""Request-owned intent for a verified reconstruction arrival."""
from dataclasses import dataclass, replace

from tomography_session_browser.presentation.tomogram_tabs import PreparedTomogramTab
from tomography_session_browser.presentation.tomogram_versions import review_versions
from tomography_session_browser.presentation.tomogram_state import TomogramViewState
from tomography_session_browser.presentation.tomogram_list_filter import ListFilter


@dataclass(frozen=True, slots=True)
class TomogramArrival:
    row_key: str
    parent_key: str | None
    filters: ListFilter
    working_set: frozenset[str] | None
    population: frozenset[str]
    view: TomogramViewState | None = None
    anchor: tuple[str, int] = ("", 0)
    history_index: int | None = None
    history_revision: int | None = None
    population_scope: str = ""
    requested_version: str | None = None

    def list_state(self, population: frozenset[str], scope: str) -> tuple[ListFilter, frozenset[str] | None]:
        filters = self.filters
        if filters.route_keys is not None:
            filters = replace(filters, route_keys=filters.route_keys & population)
        same_population = self.population_scope == scope and self.population == population
        return filters, self.working_set if same_population else None


def valid_arrival(arrival: TomogramArrival, payload: PreparedTomogramTab, history_revision: int) -> bool:
    if arrival.history_index is not None and arrival.history_revision != history_revision:
        return False
    if arrival.requested_version is not None and (
        payload.selection is None or payload.selection.version != arrival.requested_version
    ):
        return False
    target = next((row for row in payload.rows if row.tomogram.id == arrival.row_key), None)
    if target is None:
        return False
    if arrival.parent_key is not None:
        return target.link.status == "resolved" and target.link.target_key == arrival.parent_key
    if arrival.view is None or arrival.view.volume_id is None:
        return True
    volumes = review_versions(target.tomogram)
    selected_version = payload.selection.version if payload.selection else None
    scoped = next((volume for volume in volumes if volume.version == selected_version), None)
    return (scoped.id == arrival.view.volume_id if scoped is not None
            else any(volume.id == arrival.view.volume_id for volume in volumes))

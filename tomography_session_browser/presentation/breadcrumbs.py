"""Display crumbs derived from typed scope identity, without altering ancestry."""
from collections.abc import Sequence

from tomography_session_browser.presentation.context_stack import SCOPE_BADGES, describe_scope_value
from tomography_session_browser.presentation.project_tree import ProjectTreeGroup, named_after, scope_ancestors


def scope_crumbs(scope: object, groups: Sequence[ProjectTreeGroup], *,
                 titles_scope: bool, scope_name: str = "") -> tuple[str, ...]:
    chain = [*scope_ancestors(scope, groups), scope]
    crumbs: list[str] = []
    index = 0
    while index < len(chain):
        value = chain[index]
        if (isinstance(value, ProjectTreeGroup) and index + 1 < len(chain)
                and named_after(value) is chain[index + 1]):
            # Both identities remain visible, even when Summary titles the DC.
            crumbs.append(f"{value.display_name} (LS · DC)")
            index += 2
            continue
        if index == len(chain) - 1 and titles_scope:
            break
        name = scope_name if value is scope and scope_name else describe_scope_value(value)
        if value is scope:
            kind = getattr(value, "kind", "")
            badge = SCOPE_BADGES.get(kind, "")
            name = f"{name} ({badge})" if badge and name else name
        if name:
            crumbs.append(name)
        index += 1
    return tuple(crumbs)

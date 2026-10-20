"""The three visual tiers, dashboard row typography and link-style actions.

A fragment of the application's QSS template, filled in with the palette
and tokens by ``ui.stylesheet.build_stylesheet``. Fragments are joined in
the order listed in ``ui.stylesheets.__init__``: QSS precedence depends on
it, so a rule moves only together with the ones it overrides.
"""

TEMPLATE = """\
/* ---------------------------------------------------------------------
   Three visual tiers (V1).

   1. Page landmark  — where am I: the context header's scope, an empty
      state's heading.
   2. Primary scientific content — the thing under review: card values,
      row names, detail text.
   3. Supporting metadata — everything that qualifies it: card titles,
      counts, evidence, the context header's notes row.

   Expressed as type weight and colour only. The plan forbids gradients,
   oversized cards and added whitespace; density is the point of this app.

   These are **dynamic properties**, not object names. An earlier version used
   ``QLabel#tierLandmark``, which asked every widget to give up the semantic
   object name its own rule already needed — so no widget ever carried a tier
   and the whole block was unreachable. A property sits alongside the object
   name, so a widget can say both what it is and which tier it belongs to.

   A widget's own ``#objectName`` rule has higher specificity and may refine
   the size within its tier where a surface genuinely needs a different step
   (a 22pt card value and a 9.5pt crumb are both primary content). The tier is
   the declared classification and the default; the id rule is the exception.
   Use ``apply_tier()`` rather than setting the property by hand.
   --------------------------------------------------------------------- */

QLabel[tier="landmark"] {{
    color: {text_strong};
    font-size: {type_lead}pt;
    font-weight: 600;
}}

QLabel[tier="primary"] {{
    color: {text};
    font-size: {type_body}pt;
    font-weight: 500;
}}

QLabel[tier="supporting"] {{
    color: {text_muted};
    font-size: {type_caption}pt;
}}

/* Dashboard row typography. Previously set with inline style sheets, which
   put type decisions in a fourth place and baked the palette in at
   construction so a theme switch left them stale. */
QLabel#statusDot {{
    font-size: {type_lead}pt;
}}

QLabel#statusDot[compact="true"] {{
    font-size: {type_body}pt;
}}

QLabel#warningGroupLabel {{
    font-weight: 600;
}}

/* A link-style action, subordinate to the screen's one #primaryButton. The
   empty state's primary action used to have a recipe of its own on the plain
   accent, which read at 3.2:1 under its label in the light theme. */
QPushButton#secondaryAction {{
    background: transparent;
    color: {link_text};
    border: none;
    padding: 5px 8px;
    font-weight: 500;
}}

QPushButton#secondaryAction:hover {{
    color: {link_text_hover};
    text-decoration: underline;
}}

/* Dashboard warning actions are QToolButtons. Style both text and surface so
   a dark Linux desktop palette cannot supply a black native button. */
QToolButton#inlineLink {{
    background: transparent;
    border: 0;
    color: {link_text};
    padding: 4px 8px;
}}

QToolButton#inlineLink:hover {{
    color: {link_text_hover};
    text-decoration: underline;
}}

QToolButton#inlineLink:checked {{
    background: {selection};
    color: {selection_text};
    border-color: {accent};
}}

"""

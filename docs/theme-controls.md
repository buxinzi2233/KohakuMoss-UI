# Theme control families

The shared grid, density and reading scale stay in `web/app.css`. Theme-specific
component recipes belong to `web/themes/*.css`. The `.surface` material and its
single glass-opacity layer remain shared; fields inherit that layer in glass
mode, while select popups remain opaque for readability.

## Shape and inset contract

`--radius-sm` owns native fields, standalone actions and segmented controls;
`--radius-md` owns all panels, including `.surface` headers, parameter workspaces,
table wrappers and settings cards. `--radius-status` owns every status chip, and
`--radius-lg` owns media frames. Kohaku uses 6/8/6/8px, Soul 4/6/3/8px, and
Teletype keeps every role at zero. Tab bottoms stay square where they join a rule.

Panel padding comes from `--surface-pad-x/y` (12/10px relaxed, 8/7px compact).
Headers use `--view-top` equally above and below their content (16px relaxed,
10px compact), matching main's top inset. Actions sit at the vertical centre,
including headers that wrap on narrow screens.
Sticky headers share the header geometry and use opaque stock over scrolling
content; they no longer rely on negative margins. Material switches never alter
these dimensions. Standalone tables use a padded `.table-frame.surface` around
the focusable `.table-scroll`; the frame and its corner marks stay stationary
while only the columns scroll horizontally.
Teletype headers have one hairline frame. Independent framed sheets share the
top-left and bottom-right registration corners, including headers, padded cards,
filters, tables and the parameter workspace; nested controls and unframed notes
do not repeat the marks.
Toolbar fields use the action-label size and minimum height of toolbar buttons.
Native select captions use `selectedcontent` so long names truncate while the
native picker icon stays visible; option values and change handlers are unchanged.

| Family | Controls | Selection and focus | Data presentation |
| --- | --- | --- | --- |
| Kohaku | Warm recessed fields, neutral secondary actions, 6px controls and 8px groups/cards | Iolite action/selection, border-only field focus; explicit keyboard rings on buttons/tabs | Five exact gem triples, light/shadow inversion, aquamarine running/success, amber waiting, coral failure |
| Soul | Forge surface ladder, 4px controls, 6px cards and 8px media | Ember tonal selection, 3px field focus halo, static state chips | Subtle solid rules, zero tracking, semantic log/status colors |
| Teletype | Square stock, double-frame buttons, ruled fields | Ink-filled selections, printed focus; no frost or rounding | Receipt rules and stamps as specified in `teletype.md` |

Primary, secondary, danger, selected and disabled controls each have explicit
recipes. Native inputs, selects, textareas, segmented controls, color swatches,
mini actions, help buttons and tabs must not fall back to another family's look.
Disabled controls use their muted ink without a second whole-control opacity fade.
The renderer's legacy `runn`/`unkn` classes and studio `data-status` attributes
must agree on status colors.

## Project adaptations

- Keep the project's compact control dimensions, shared font-size tokens and
  scaling instead of importing upstream 34px buttons or full-size page layouts.
- Geist and Inter are not installed locally. Kohaku and Soul use the installed
  Noto Sans fallback; machine values, paths, identifiers and logs retain bundled
  IBM Plex Mono. No remote font or JavaScript animation dependency is added.
- Kohaku and Soul remove inherited print tracking; Teletype keeps its own type
  rules and the project's Chinese-language tracking correction.
- Touch fields use `max(16px, var(--fs-body))`, preserving larger reading scales.
- On narrow screens the shell hides duplicate masthead context while retaining
  the language/mode controls, and read-only paths wrap inside their field.
- No new keyframes. Soul uses only 120/160ms state transitions, covered by the
  existing reduced-motion rule. Surface opacity and wallpaper values are never
  rewritten by a theme recipe.

The regression gates are `tests/test_theme_controls.py` and
`tests/test_theme_teletype.py`; rendered QA must include both modes, native select
popups, focus/selected/disabled states, glass mode, and narrow viewports.

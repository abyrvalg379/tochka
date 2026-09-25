# -*- coding: utf-8 -*-
r"""TOCHKA - User Guide (EN). Generator on the family template (_docstyle.py).
Run:  python _gen_manual_en.py    Output:  docs\TOCHKA_Manual_EN.docx
"""

import json

import _docstyle as ds

OUT = r'D:\AI\ZCode\Project\TOCHKA\docs\TOCHKA_Manual_EN.docx'


def h1(doc, text):
    return ds.h1(doc, text)


def h2(doc, text):
    return ds.h2(doc, text)


def p(doc, text, bullet=False, italic=False, grey=False):
    return ds.p(doc, text, bullet=bullet, italic=italic, grey=grey)


def kv_note(doc, text):
    return ds.kv(doc, text)


def add_table(doc, rows, widths, sev_col=None):
    return ds.add_table(doc, rows, widths, sev_col=sev_col)


def _save(doc, out):
    ds.footer(doc.sections[1], 'TOCHKA')
    ds.strip_tail(doc)
    doc.save(out)
    h1s = [t for t in ds.H1_REGISTRY if t.lower() not in ('table of contents', 'contents')]
    json.dump(h1s, open(out.replace('.docx', '.h1.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    print('saved:', out)


doc = ds.new_doc('TOCHKA', 'User Guide', 'V1.1.0  -  BLENDER 4.2+')

p(doc, 'TOCHKA is a set of pivot tools for Blender: move the origin into the center of a '
       'selection in one press, drag it across surfaces with vertex snapping, rotate its '
       'orientation without moving geometry, align it to a face normal or an edge - and '
       'audit the pivots of a scene against your conventions before delivery. Instead of '
       'juggling the 3D cursor around the stock Set Origin - direct visual operators with '
       'live preview.')

kv_note(doc, 'github.com/abyrvalg379/tochka')

h1(doc, 'Contents')
ds.toc_field(doc, 'Table of contents: open the document in Word/LibreOffice and refresh '
                  'the field (F9) to fill in page numbers.')

h1(doc, '1. About TOCHKA')

h2(doc, '1.1 What TOCHKA does')
p(doc, 'Key features:', bullet=False)
for b in (
    'Origin to Selection - the origin moves into the selection center with the D key, '
    'Median / Bottom / Top anchors;',
    'Drag Pivot - drag the pivot with the mouse across any visible geometry, with vertex '
    'snapping and axis constraints;',
    'Rotate Pivot - rotate the pivot orientation without moving geometry, for animatable parts;',
    'Align Pivot to Normal - the pivot Z axis follows the normal of the face under the cursor;',
    'Align Pivot to Edge - the pivot X axis lies along an edge;',
    'Pivot Audit - check the pivots of selected meshes against pipeline conventions with '
    'batch fixing;',
    'honest undo: every tool commits as a single history step;',
    'keymap guard: on every enable the extension repairs a damaged system keymap by itself.',
):
    p(doc, b, bullet=True)

h2(doc, '1.2 Philosophy')
p(doc, 'Three principles. First - the pivot is separate from the geometry: rotation and '
       'orientation alignment change only the object matrix, vertices stay in place to '
       'machine epsilon. Second - live preview: every modal tool shows the result before '
       'confirmation, and the scene is untouched until commit - cancel (RMB or Esc) rolls '
       'back cleanly. Third - predictable undo: any operation commits as exactly one '
       'history step.')

h2(doc, '1.3 Tools and keys')
add_table(doc, [
    ('Tool', 'Key', 'Also on'),
    ('Origin to Selection', 'D · DD · DDD', 'panel, pie menu'),
    ('Drag Pivot', 'Ctrl+D', 'panel, pie menu'),
    ('Rotate Pivot', 'Ctrl+Alt+D (object mode)', 'panel, pie menu'),
    ('Align Pivot to Normal', '-', 'panel, pie menu'),
    ('Align Pivot to Edge', '-', 'panel, pie menu'),
    ('Pivot Audit', '-', 'panel'),
], [5.2, 5.6, 4.4])
p(doc, 'The pie menu with all operators opens from a button in the N-panel.')

h1(doc, '2. Installation')
for b in (
    'Download the zip of the latest release: github.com/abyrvalg379/tochka - Releases - '
    'Latest - the tochka_v*.zip asset.',
    'Blender - Edit - Preferences - Get Extensions - the corner menu - Install from Disk - '
    'pick the zip. Drag-and-drop installation works as well.',
    'The extension enables itself as TOCHKA; the tab appears in the 3D viewport N-panel '
    '(N key). The version is shown right in the panel header.',
    'Updating: install the new zip the same way, on top of the old version.',
):
    p(doc, b, bullet=True)
p(doc, 'Tested in Blender 5.2. On every enable TOCHKA checks the system keymap (Object Mode '
       'and Mesh): if transform bindings are damaged - by anyone - they are restored from '
       'defaults, and the console prints "TOCHKA: repaired damaged ... keymap". After such a '
       'repair press Preferences - Save Preferences to pin the clean keymap to disk.')

h1(doc, '3. Quick start')
p(doc, 'A typical pass - putting a wheel pivot on its rotation axis:')
for b in (
    'Enter edit mode on the wheel, select a face on the hub, press D - the origin lands in '
    'the selection center (Median). Need it lower - DD sets Bottom, another press - DDD, Top.',
    'Press Align Pivot to Edge on the panel and hover the rim - the pivot X axis lies along '
    'an edge; once it catches the wheel axis - LMB.',
    'Fine-tune the tilt if needed: Ctrl+Alt+D and rotate the orientation with the mouse, '
    'Ctrl for 5-degree steps, Shift for fine.',
    'Adjust the position with Ctrl+D: the pivot slides across surfaces, Ctrl snaps to the '
    'nearest vertex, X/Y/Z holds an axis.',
    'Before delivery run the Pivot Audit with the project convention - offenders appear as '
    'a list, Fix All Flagged fixes them in batch.',
):
    p(doc, b, bullet=True)

h1(doc, '4. Origin to Selection (D)')

h2(doc, '4.1 How it works')
p(doc, 'Select something and press D - the object origin moves into the center of the '
       'selection. In edit mode that is the center of the selected vertices/edges/faces; in '
       'object mode - the center of the selected objects, and every selected object gets its '
       'own origin from its own selection. Geometry is not distorted by the move: vertices '
       'are translated honestly, with the object transform compensated.')

h2(doc, '4.2 Anchors: D · DD · DDD')
add_table(doc, [
    ('Presses', 'Anchor', 'Where the pivot goes'),
    ('D', 'Median', 'center of the selection'),
    ('DD (fast, in a row)', 'Bottom', 'XY median of the selection, lowest point in Z'),
    ('DDD (fast, in a row)', 'Top', 'XY median of the selection, highest point in Z'),
], [4.4, 3.0, 9.6])
p(doc, 'A fourth press in a row (DDDD) returns to Median and the cycle starts over - the '
       'anchor walks in a circle as many times as you need.')

h2(doc, '4.3 The pause rule')
p(doc, 'The presses must land in a row, within 0.35 seconds of each other - that is what '
       '"DD" means. Pause longer and the next D is no longer the next anchor: it re-applies '
       'the last chosen one. An anchor picked with the panel buttons takes part in the cycle '
       'the same way - DD after it steps to the next one.')

h2(doc, '4.4 Undo')
p(doc, 'The operation commits as a single history step: one Ctrl+Z returns both the pivot '
       'and the vertices. After undo the scene displays correctly right away - no flying '
       'objects, no extra rolls back.')

h1(doc, '5. Drag Pivot (Ctrl+D)')

h2(doc, '5.1 Mechanics')
p(doc, 'Ctrl+D starts a drag: the pivot follows the cursor, sliding across any visible '
       'scene geometry (raycast). Viewport navigation keeps working during the drag. A line '
       'from the start position and a ring marker on the current point are drawn while '
       'dragging.')

h2(doc, '5.2 Vertex snapping')
p(doc, 'Holding Ctrl enables snapping: the pivot sticks to the nearest vertex within 24 '
       'screen pixels of the cursor. The radius is in pixels, not world units, so it works '
       'the same up close and far away. A caught vertex is marked with a yellow ring.')

h2(doc, '5.3 Axis constraints')
p(doc, 'The X / Y / Z keys during the drag constrain the motion to an axis: the first press '
       '- a world axis, the same key again - the object local axis, the third - off. Motion '
       'along the axis uses screen-space sliding, like a gizmo: an axis pointing almost at '
       'the camera simply does not move the marker. The ring marker takes the axis color '
       'while constrained. Snap and constraint combine: a caught vertex is projected onto '
       'the axis.')

h2(doc, '5.4 Confirm and cancel')
p(doc, 'LMB or Enter - apply (one undo step). RMB or Esc - cancel: the scene is not touched '
       'at all until the commit, cancellation returns the pivot. The panel-button click that '
       'started the operator is never counted as a commit - protection against an accidental '
       'instant apply.')

h1(doc, '6. Rotate Pivot (Ctrl+Alt+D)')

h2(doc, '6.1 Mechanics')
p(doc, 'An object-mode operator: rotates the orientation of the active mesh pivot without '
       'moving geometry. Why: for animatable parts - wheels, doors, lids - align the local '
       'axis with the actual rotation axis of the part, and the animator then turns it with '
       'a single channel. By default the mouse rotates the pivot around the view axis.')

h2(doc, '6.2 Axes and precision')
add_table(doc, [
    ('Key', 'Action'),
    ('X / Y / Z', 'world axis; press again - the object local axis; third press - back to the view axis'),
    ('Shift (hold)', 'fine rotation - one tenth of the speed'),
    ('Ctrl (hold)', '5-degree steps'),
    ('Ctrl + Shift', '1-degree steps'),
], [4.4, 12.6])

h2(doc, '6.3 Feedback')
p(doc, 'While rotating, a live axis triad and a ring marker are drawn, the current angle and '
       'axis are printed at the cursor ("23.4 deg [Z global]") and mirrored in the status '
       'bar. The numeric feedback is always visible, even if something covers the viewport '
       'overlays. LMB or Enter - apply, RMB or Esc - cancel: the object is untouched until '
       'the commit.')

h1(doc, '7. Align Pivot to Normal / to Edge')

h2(doc, '7.1 Align Pivot to Normal')
p(doc, 'Start it from the panel and hover the mesh: the pivot Z axis follows the normal of '
       'the face under the cursor, the Y axis leans toward world up. The classic case is a '
       'door: hover the cursor over the door plane - Z faces outward, LMB applies. A blue '
       'ring means the normal is caught, yellow - there is no face under the cursor.')

h2(doc, '7.2 Align Pivot to Edge')
p(doc, 'The same gesture, but the pivot X axis lies along the longest edge of the face under '
       'the cursor - deterministic, no cycling. Need another direction - move the cursor to '
       'a neighboring face with the right edge. A red ring means the edge is caught. For a '
       'wheel: a rim face gives an axis along the rim - that is the rotation axis.')

h2(doc, '7.3 Sticky target')
p(doc, 'The target in both modes is sticky: move the cursor off the mesh onto a panel or '
       'into space - the last caught orientation holds and waits for confirmation. LMB '
       'commits the caught orientation even if the cursor is not over the mesh at the moment '
       'of the click.')

h1(doc, '8. Pivot Audit')

h2(doc, '8.1 Why')
p(doc, 'Pivot conventions drift: assets arrive from different hands and packages, pivots end '
       'up at the bbox center, underground or wherever. The audit checks the pivots of '
       'selected meshes against a chosen anchor and lists the offenders - before the asset '
       'travels further down the pipeline.')

h2(doc, '8.2 Rules')
add_table(doc, [
    ('Rule', 'Anchor', 'Tolerance'),
    ('Bottom Center', 'bottom of the object bbox, XY center', 'percent of object size'),
    ('Bounds Center', 'center of the object bbox', 'percent of object size'),
    ('World Origin', 'world zero (0, 0, 0)', 'meters (0.1 by default)'),
], [4.6, 6.4, 6.0])
p(doc, 'World Origin is for assets that live with the pivot at world zero (buildings, '
       'terrain blocks): geometry extending underground is normal there, so the tolerance '
       'is measured in meters, not percent.')

h2(doc, '8.3 Run and fix')
for b in (
    'The suffix filter (default _geo) picks the objects to check - service objects stay out.',
    'Audit Selected runs the check on the selection; offenders appear as a list of select '
    'buttons: clicking a row selects the object and frames the camera.',
    'Fix All Flagged sets the pivot by the chosen anchor in batch; multi-user meshes (shared '
    'geometry on several objects) are skipped with a warning - fix their pivots manually '
    'after making them single-user.',
):
    p(doc, b, bullet=True)

h1(doc, '9. Panel and hints')
p(doc, 'All tools live in the TOCHKA tab of the N-panel: the Origin buttons (Median / '
       'Bottom / Top), Drag Pivot, Rotate Pivot, Align to Normal, Align to Edge, the pie '
       'menu button with every operator, and the Pivot Audit section. The extension version '
       'is printed in the panel header. Below - a collapsed Info sub-panel with the key '
       'cheatsheet: drag mode (LMB/Enter apply, RMB/Esc cancel, Ctrl snap, X/Y/Z constraint '
       'with the world-local-off cycle), rotation mode (Shift fine, Ctrl 5 degrees, '
       'Ctrl+Shift 1 degree).')

h1(doc, '10. How to use')

h2(doc, '10.1 A wheel: the pivot on the rotation axis')
p(doc, 'Align Pivot to Edge on a rim face - the X axis lies along the rim. If it needs a '
       'touch-up - Ctrl+Alt+D with Ctrl steps. The result: the wheel local axis matches the '
       'physical rotation axis, and the animation turns on a single X channel with no '
       'compensation empties.')

h2(doc, '10.2 A door: the pivot on the hinges')
p(doc, 'Align Pivot to Normal on the door plane - Z faces outward. Then Drag Pivot with an '
       'axis constraint - the pivot slides onto the hinge edge, Ctrl snaps to a corner '
       'vertex. The door now opens by rotating around its own edge.')

h2(doc, '10.3 Import: bringing an asset to convention')
p(doc, 'An asset arrives with the pivot in a random spot: D with the Bottom anchor puts the '
       'origin on the ground under the object center. For a batch of similar objects the '
       'Audit Selected run against the project rule is faster - and Fix All Flagged fixes '
       'everything at once.')

h2(doc, '10.4 The delivery check')
p(doc, 'Audit Selected with the suffix filter is the last step before publishing an asset: '
       'an empty offender list means the pivots are in convention; otherwise Fix All '
       'Flagged and re-check. Multi-user meshes, if flagged, are made single-user and fixed '
       'by hand.')

h1(doc, '11. Troubleshooting')
add_table(doc, [
    ('Symptom', 'Cause', 'What to do'),
    ('The D key does not run the operator', 'the keymap is damaged (an old TOCHKA version, a settings rollback, another addon)',
     'toggle the TOCHKA extension off and on - the guard restores the keymap; then Save Preferences'),
    ('G/R/S stopped working in the viewport', 'the system Object Mode and Mesh keymaps are damaged',
     'Preferences - Keymap - Restore for Object Mode and Mesh - Save Preferences; TOCHKA 1.0.4+ fixes this itself on enable'),
    ('Snapping does not catch a vertex', 'the vertex is beyond 24 px from the cursor on screen',
     'move the camera closer or aim more precisely - the capture radius is screen-based'),
    ('The pivot "flies away" after Ctrl+Z', 'a bug in versions up to 1.0.3 inclusive',
     'update TOCHKA to 1.0.4 or newer'),
    ('The operator closed right after starting from a button', 'an instant commit from the spawning click',
     'arming protection is built in: start moving the mouse and confirm with LMB'),
], [4.6, 5.4, 7.0])

_save(doc, OUT)

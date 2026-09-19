bl_info = {
    "name": "TOCHKA",
    "author": "Maksim Kovalev",
    "version": (1, 0, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > TOCHKA, hotkey D (pie: Alt+D, drag: Ctrl+D, rotate pivot: Ctrl+Alt+D, align: panel/pie)",
    "description": "Move object origin to the current selection",
    "category": "Object",
}

import bpy
import bmesh
import gpu
import math
import time
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector
from bpy.types import Operator, Panel, Menu, PropertyGroup
from bpy.props import EnumProperty, StringProperty, FloatProperty, IntProperty, CollectionProperty, PointerProperty

VERSION_STR = ".".join(str(v) for v in bl_info["version"])
TOCHKA_DEBUG = False


# ─── Anchor points ─────────────────────────────────────────────

def _selected_cos(bm, matrix_world):
    return [matrix_world @ v.co for v in bm.verts if v.select and not v.hide]


def anchor_point(sel_cos, anchor):
    """World-space pivot point from a list of selection coords, or None if empty."""
    if not sel_cos:
        return None
    med = sum(sel_cos, Vector()) / len(sel_cos)
    if anchor == "BOTTOM":
        med.z = min(c.z for c in sel_cos)
    elif anchor == "TOP":
        med.z = max(c.z for c in sel_cos)
    return med


def _bbox_cos(obj):
    return [obj.matrix_world @ Vector(c) for c in obj.bound_box]


def _bottom_center(obj):
    pts = _bbox_cos(obj)
    return Vector((sum(p.x for p in pts) / 8, sum(p.y for p in pts) / 8, min(p.z for p in pts)))


def _bounds_center(obj):
    pts = _bbox_cos(obj)
    return sum(pts, Vector()) / 8


def _pivot_offset(obj, target="BOTTOM"):
    """Offset of the pivot from the target anchor. BOTTOM/CENTER: relative to
    object size; ORIGIN: absolute meters from world zero."""
    pts = _bbox_cos(obj)
    size = max(max(p.x for p in pts) - min(p.x for p in pts),
               max(p.y for p in pts) - min(p.y for p in pts),
               max(p.z for p in pts) - min(p.z for p in pts)) or 1e-9
    pivot = obj.matrix_world.translation
    if target == "ORIGIN":
        return pivot.length
    ref = _bottom_center(obj) if target == "BOTTOM" else _bounds_center(obj)
    return (pivot - ref).length / size


def _audit_anchor(obj, target):
    if target == "ORIGIN":
        return Vector((0.0, 0.0, 0.0))
    return _bottom_center(obj) if target == "BOTTOM" else _bounds_center(obj)


class TOCHKA_props(PropertyGroup):
    suffix_filter: StringProperty(
        name="Suffix", description="Only audit objects whose name ends with this (empty = all selected)",
        default="_geo")
    audit_target: EnumProperty(
        name="Target",
        items=[
            ("BOTTOM", "Bottom Center", "Pivot at the bottom center of the bbox"),
            ("CENTER", "Bounds Center", "Pivot at the center of the bbox"),
            ("ORIGIN", "World Origin", "Pivot at the world zero point"),
        ],
        default="BOTTOM")
    tol_percent: FloatProperty(
        name="Tolerance %", description="Allowed pivot offset as % of object size",
        default=0.5, min=0.0, max=100.0)
    tol_origin_m: FloatProperty(
        name="Tolerance (m)", description="Allowed pivot distance from world zero, meters",
        default=0.1, min=0.0, soft_max=10.0)
    flagged: CollectionProperty(type=PropertyGroup, name="Flagged objects")


# ─── Origin move ───────────────────────────────────────────────

def _move_origin(obj, world_pt):
    """Shift mesh data so the origin lands on world_pt; geometry stays put."""
    inv = obj.matrix_world.inverted()
    local_pt = inv @ world_pt
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.translate(bm, verts=bm.verts, vec=-local_pt)
    bm.to_mesh(obj.data)
    bm.free()
    obj.location += obj.matrix_world.to_3x3() @ local_pt


def _move_origin_edit(obj, bm, world_pt):
    """Same as _move_origin but on a bmesh already in edit mode."""
    inv = obj.matrix_world.inverted()
    local_pt = inv @ world_pt
    bmesh.ops.translate(bm, verts=bm.verts, vec=-local_pt)
    obj.location += obj.matrix_world.to_3x3() @ local_pt


# ─── Drag Pivot (modal) ────────────────────────────────────────

class TOCHKA_OT_drag_pivot(Operator):
    bl_idname = "tochka.drag_pivot"
    bl_label = "Drag Pivot"
    bl_description = "Interactively drag the pivot over the surface (Ctrl: snap to vertex)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode in ("OBJECT", "EDIT_MESH")
            and context.active_object is not None
            and context.active_object.type == "MESH"
        )

    def invoke(self, context, event):
        self.obj = context.active_object
        self.start = self.obj.matrix_world.translation.copy()
        self.current = self.start.copy()
        # invoked from a panel/pie: context.region may be the UI sidebar and
        # region_data None — always take the 3D viewport's WINDOW region
        self._region = next((r for r in context.area.regions if r.type == "WINDOW"),
                            context.region) if context.area else context.region
        self._rv3d = (context.area.spaces.active.region_3d
                      if context.area and context.area.type == "VIEW_3D" else context.region_data)
        self._depsgraph = context.evaluated_depsgraph_get()
        self._last_coord = None
        self._kd = None
        self._snapped = False
        self._constrain = None  # None | ("X"|"Y"|"Z", "GLOBAL"|"LOCAL")
        self._axis_t = 0.0
        self._t0 = time.time()
        self._shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (), "WINDOW", "POST_VIEW"
        )
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_2d, (), "WINDOW", "POST_PIXEL"
        )
        self._draw_err = False
        context.area.tag_redraw()
        context.window.cursor_modal_set("HAND")
        context.workspace.status_text_set(self._status_text())
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    # -- axis constraint ------------------------------------------

    _AXIS_COLORS = {"X": (1.0, 0.25, 0.25, 1.0), "Y": (0.35, 1.0, 0.35, 1.0), "Z": (0.4, 0.5, 1.0, 1.0)}

    def _status_text(self):
        c = self._constrain
        mode = "  |  axis: %s %s" % (c[0], c[1].lower()) if c else ""
        return ("TOCHKA drag: LMB/Enter apply  |  RMB/Esc cancel  |  "
                "Ctrl snap to vertex  |  X/Y/Z constrain (press again: local, then off)%s" % mode)

    def _axis_vector(self, name):
        from mathutils import Vector
        if self._constrain and self._constrain[1] == "LOCAL":
            i = "XYZ".index(name)
            v = self.obj.matrix_world.to_3x3() @ Vector((i == 0, i == 1, i == 2))
            return v.normalized()
        return Vector((name == "X", name == "Y", name == "Z"))

    def _cycle_constraint(self, name):
        if self._constrain is None:
            self._constrain = (name, "GLOBAL")
        elif self._constrain == (name, "GLOBAL"):
            self._constrain = (name, "LOCAL")
        else:
            self._constrain = None
        if self._constrain:
            a = self._axis_vector(self._constrain[0])
            self._axis_t = (self.current - self.start).dot(a)

    def _axis_2d(self, a):
        """Screen-space direction of the axis, or None if it points at the camera."""
        from bpy_extras import view3d_utils
        from mathutils import Vector
        eps = self._rv3d.view_distance * 0.01
        p0 = view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, self.start)
        p1 = view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, self.start + a * eps)
        if p0 is None or p1 is None:
            return None
        d = Vector((p1.x - p0.x, p1.y - p0.y))
        if d.length < 1.0:
            return None
        return d.normalized()

    def _world_per_px(self):
        """World units per screen pixel at the pivot depth (perspective and ortho)."""
        from mathutils import Vector
        depth = max(abs((self._rv3d.view_matrix @ self.current.to_4d()).z), 1e-6)
        h = max(self._region.height, 1)
        p11 = max(self._rv3d.window_matrix[1][1], 1e-6)
        if self._rv3d.is_perspective:
            return 2.0 * depth / p11 / h  # 1/P[1][1] = tan(fov/2)
        return 2.0 / p11 / h

    def _constrain_point(self, cand):
        """Project candidate onto the constrained axis line through the start point."""
        name, _ = self._constrain
        a = self._axis_vector(name)
        d = cand - self.start
        return self.start + a * d.dot(a)

    # -- picking ------------------------------------------------

    def _in_region(self, event):
        r = self._region
        return 0 <= event.mouse_region_x < r.width and 0 <= event.mouse_region_y < r.height

    def _ray(self, event):
        from bpy_extras import view3d_utils
        if not self._in_region(event):
            return False, None
        coord = (event.mouse_region_x, event.mouse_region_y)
        direction = view3d_utils.region_2d_to_vector_3d(self._region, self._rv3d, coord)
        origin = view3d_utils.region_2d_to_origin_3d(self._region, self._rv3d, coord)
        hit, loc, normal, face_i, obj, matrix = self._depsgraph.scene.ray_cast(
            self._depsgraph, origin, direction
        )
        return hit, loc

    def _snap_vertex(self, event, mouse_coord):
        """Vertex closest to the mouse cursor on screen; anchored to the mouse
        ray (not to a mesh hit), so it works even off the mesh silhouette."""
        from bpy_extras import view3d_utils
        from mathutils.geometry import intersect_point_line
        kd = self._get_kdtree()
        if kd is None:
            return None
        origin = view3d_utils.region_2d_to_origin_3d(self._region, self._rv3d, mouse_coord)
        direction = view3d_utils.region_2d_to_vector_3d(self._region, self._rv3d, mouse_coord)
        anchor, _ = intersect_point_line(self.start, origin, origin + direction)
        radius = self._rv3d.view_distance * 0.3
        candidates = kd.find_range(anchor, radius)
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[2])
        best_co = None
        best_d2 = 24.0 ** 2  # accept radius: 24 px around the cursor
        for co, index, dist in candidates[:128]:
            co2d = view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, co)
            if co2d is None:
                continue
            d2 = (co2d.x - mouse_coord[0]) ** 2 + (co2d.y - mouse_coord[1]) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_co = co
        if best_co is None:
            return None
        self._snapped = True
        return best_co

    def _get_kdtree(self):
        if self._kd is not None:
            return self._kd
        from mathutils import kdtree
        cos = []
        if bpy.context.mode == "EDIT_MESH":
            bm = bmesh.from_edit_mesh(self.obj.data)
            mw = self.obj.matrix_world
            cos = [mw @ v.co for v in bm.verts if not v.hide]
        else:
            for o in bpy.context.selected_objects:
                if o.type != "MESH":
                    continue
                mw = o.matrix_world
                cos.extend(mw @ v.co for v in o.data.vertices)
        if not cos:
            return None
        kd = kdtree.KDTree(len(cos))
        for i, co in enumerate(cos):
            kd.insert(co, i)
        kd.balance()
        self._kd = kd
        return kd

    # -- drawing ------------------------------------------------

    def _draw(self):
        import traceback
        try:
            self._draw_3d()
        except Exception:
            if not self._draw_err:
                self._draw_err = True
                traceback.print_exc()

    def _draw_3d(self):
        gpu.state.depth_test_set("NONE")
        L = self._rv3d.view_distance * 0.03
        p = self.current
        co_axes = (
            (p.x - L, p.y, p.z), (p.x + L, p.y, p.z),
            (p.x, p.y - L, p.z), (p.x, p.y + L, p.z),
            (p.x, p.y, p.z - L), (p.x, p.y, p.z + L),
        )
        colors = ((1, 0.25, 0.25, 1), (1, 0.25, 0.25, 1),
                  (0.35, 1, 0.35, 1), (0.35, 1, 0.35, 1),
                  (0.4, 0.5, 1, 1), (0.4, 0.5, 1, 1))
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(2)
        self._shader.bind()
        self._shader.uniform_float("color", (0.7, 0.7, 0.7, 0.6))
        batch_for_shader(self._shader, "LINES", {"pos": (self.start, p)}).draw(self._shader)
        if self._constrain:
            D = self._rv3d.view_distance * 5.0
            a = self._axis_vector(self._constrain[0])
            a0 = self.start - a * D
            a1 = self.start + a * D
            gpu.state.line_width_set(1)
            self._shader.uniform_float("color", self._AXIS_COLORS[self._constrain[0]])
            batch_for_shader(self._shader, "LINES", {"pos": (a0, a1)}).draw(self._shader)
            gpu.state.line_width_set(2)
        self._shader.uniform_float("color", (1, 1, 1, 1))
        batch = batch_for_shader(self._shader, "LINES", {"pos": co_axes, "color": colors})
        batch.draw(self._shader)
        gpu.state.line_width_set(1)
        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    def _draw_2d(self):
        """Snap indicator in screen space: yellow circle at the projected vertex."""
        import traceback
        try:
            if not self._snapped:
                return
            from bpy_extras import view3d_utils
            co = view3d_utils.location_3d_to_region_2d(
                self._region, self._rv3d, self.current
            )
            if co is None:
                return
            import math
            r = 12.0 if self._constrain else 9.0
            color = self._AXIS_COLORS[self._constrain[0]] if self._constrain else (1.0, 0.85, 0.1, 1.0)
            n = 24
            verts = []
            for i in range(n + 1):
                a = 2.0 * math.pi * i / n
                verts.append((co.x + r * math.cos(a), co.y + r * math.sin(a)))
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(2)
            shader.bind()
            shader.uniform_float("color", color)
            batch_for_shader(shader, "LINE_STRIP", {"pos": verts}).draw(shader)
            gpu.state.line_width_set(1)
            gpu.state.blend_set("NONE")
        except Exception:
            if not self._draw_err:
                self._draw_err = True
                traceback.print_exc()

    # -- commit / cleanup ---------------------------------------

    def _apply(self, context):
        pt = self.current
        if context.mode == "EDIT_MESH":
            bm = bmesh.from_edit_mesh(self.obj.data)
            _move_origin_edit(self.obj, bm, pt)
            bmesh.update_edit_mesh(self.obj.data)
        else:
            _move_origin(self.obj, pt)

    def _cleanup(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
        bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, "WINDOW")
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)

    def modal(self, context, event):
        try:
            return self._modal_inner(context, event)
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self._cleanup(context)
            except Exception:
                pass
            return {"CANCELLED"}

    def _modal_inner(self, context, event):
        if event.type == "MOUSEMOVE":
            coord = (event.mouse_region_x, event.mouse_region_y)
            if coord != self._last_coord:
                prev = self._last_coord
                self._last_coord = coord
                hit, loc = self._ray(event)
                if self._constrain:
                    # screen-space slide: mouse delta projected onto the axis on screen
                    self._snapped = False
                    a = self._axis_vector(self._constrain[0])
                    a2d = self._axis_2d(a)
                    if a2d is not None and prev is not None:
                        d = (coord[0] - prev[0]) * a2d.x + (coord[1] - prev[1]) * a2d.y
                        self._axis_t += d * self._world_per_px()
                    cand = self.start + a * self._axis_t
                    if event.ctrl:
                        snap = self._snap_vertex(event, coord)
                        if snap is not None:
                            self._axis_t = (snap - self.start).dot(a)
                            cand = self.start + a * self._axis_t
                    self.current = cand
                else:
                    self._snapped = False
                    if event.ctrl:
                        snap = self._snap_vertex(event, coord)
                        if snap is not None:
                            self.current = snap
                    elif hit:
                        self.current = loc
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"X", "Y", "Z"} and event.value == "PRESS":
            self._cycle_constraint(event.type)
            context.workspace.status_text_set(self._status_text())
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if time.time() - self._t0 < 0.2:
                return {"RUNNING_MODAL"}  # swallow the launching button's own click
            self._cleanup(context)
            self._apply(context)
            self.report({"INFO"}, "Pivot moved")
            return {"FINISHED"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup(context)
            context.area.tag_redraw()
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


# ─── Operator ──────────────────────────────────────────────────

class TOCHKA_OT_origin_to_selection(Operator):
    bl_idname = "tochka.origin_to_selection"
    bl_label = "Origin to Selection"
    bl_description = "Move origin to the anchor point of the current selection"
    bl_options = {"REGISTER", "UNDO"}

    anchor: EnumProperty(
        name="Anchor",
        items=[
            ("MEDIAN", "Median", "Center of the selection"),
            ("BOTTOM", "Bottom", "XY median, Z at the lowest point"),
            ("TOP", "Top", "XY median, Z at the highest point"),
        ],
        default="MEDIAN",
    )

    @classmethod
    def poll(cls, context):
        return context.mode in ("OBJECT", "EDIT_MESH")

    def execute(self, context):
        if context.mode == "OBJECT":
            objs = [o for o in context.selected_objects if o.type == "MESH"]
            if not objs:
                self.report({"WARNING"}, "No mesh objects selected")
                return {"CANCELLED"}
            for obj in objs:
                pt = anchor_point(_bbox_cos(obj), self.anchor)
                if pt is not None:
                    _move_origin(obj, pt)
            return {"FINISHED"}

        # Edit mesh: every object whose mesh is in edit mode (multi-object edit)
        done = 0
        for obj in context.objects_in_mode_unique_data:
            bm = bmesh.from_edit_mesh(obj.data)
            pt = anchor_point(_selected_cos(bm, obj.matrix_world), self.anchor)
            if pt is not None:
                _move_origin_edit(obj, bm, pt)
                bmesh.update_edit_mesh(obj.data)
                done += 1
        if not done:
            self.report({"WARNING"}, "Nothing selected")
            return {"CANCELLED"}
        return {"FINISHED"}


class TOCHKA_MT_pie(Menu):
    bl_label = "TOCHKA"

    def draw(self, context):
        pie = self.layout.menu_pie()
        pie.operator("tochka.origin_to_selection", text="Median").anchor = "MEDIAN"
        pie.operator("tochka.origin_to_selection", text="Top").anchor = "TOP"
        pie.operator("tochka.origin_to_selection", text="Bottom").anchor = "BOTTOM"
        pie.operator("tochka.drag_pivot", text="Drag Pivot")
        pie.operator("tochka.rotate_pivot", text="Rotate Pivot")
        pie.operator("tochka.align_pivot", text="Align to Normal")
        pie.operator("tochka.align_pivot_edge", text="Align to Edge")


# ─── Panel ─────────────────────────────────────────────────────

class TOCHKA_PT_main(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TOCHKA"
    bl_label = "TOCHKA " + VERSION_STR

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.operator("tochka.origin_to_selection", text="Median").anchor = "MEDIAN"
        col.operator("tochka.origin_to_selection", text="Bottom").anchor = "BOTTOM"
        col.operator("tochka.origin_to_selection", text="Top").anchor = "TOP"
        col.separator()
        col.operator("tochka.drag_pivot", icon="VIEW_PAN")
        col.operator("tochka.rotate_pivot", icon="GESTURE_ROTATE")
        col.operator("tochka.align_pivot", icon="NORMALS_FACE")
        col.operator("tochka.align_pivot_edge", icon="EDGESEL")
        col.menu("TOCHKA_MT_pie", text="Pie Menu", icon="MENU_PANEL")

        box = layout.box()
        box.label(text="Pivot Audit")
        props = context.scene.tochka_props
        box.prop(props, "audit_target", text="")
        box.prop(props, "suffix_filter", text="Suffix")
        if props.audit_target == "ORIGIN":
            box.prop(props, "tol_origin_m")
        else:
            box.prop(props, "tol_percent")
        box.operator("tochka.audit")
        n_flag = len(props.flagged)
        if n_flag:
            box.label(text="Flagged: %d" % n_flag, icon="ERROR")
            for i, item in enumerate(props.flagged):
                box.operator("tochka.audit_select", text=item.name).index = i
            box.operator("tochka.audit_fix", icon="CHECKMARK")


class TOCHKA_PT_info(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TOCHKA"
    bl_label = "Info"
    bl_parent_id = "TOCHKA_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="D — Origin to Selection (Median)")
        col.label(text="Alt+D — Pie Menu")
        col.label(text="Ctrl+D — Drag Pivot")
        col.separator()
        col = layout.column(align=True)
        col.label(text="In drag:")
        col.label(text="    LMB / Enter — apply")
        col.label(text="    RMB / Esc — cancel")
        col.label(text="    Ctrl (hold) — snap to vertex")
        col.label(text="    X / Y / Z — axis constrain")
        col.label(text="    (press again: local axis, then off)")
        col.separator()
        col.label(text="Ctrl+Alt+D — Rotate Pivot (object mode)")
        col.label(text="In rotate: move mouse — rotate")
        col.label(text="    X / Y / Z — axis (again: local)")
        col.label(text="    Shift — fine, Ctrl — 5-deg, Ctrl+Shift — 1-deg")
        col.separator()
        col.label(text="Align Pivot to Normal (panel/pie):")
        col.label(text="    hover a face — pivot Z follows")
        col.label(text="    LMB apply, RMB cancel")
        col.separator()
        col.label(text="Align Pivot to Edge (panel/pie):")
        col.label(text="    hover a face — pivot X along")
        col.label(text="    its longest edge")



# ─── Rotate Pivot (modal) ──────────────────────────────────────

class TOCHKA_OT_rotate_pivot(Operator):
    bl_idname = "tochka.rotate_pivot"
    bl_label = "Rotate Pivot"
    bl_description = ("Rotate the pivot orientation without moving geometry "
                      "(X/Y/Z: axis, again: local, Ctrl: 5-deg steps, Shift: fine)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "OBJECT"
            and context.active_object is not None
            and context.active_object.type == "MESH"
        )

    def invoke(self, context, event):
        self.obj = context.active_object
        self.M0 = self.obj.matrix_world.copy()
        self.axis_key = None                        # None | "X"|"Y"|"Z"
        self.axis_space = "GLOBAL"
        self.angle = 0.0
        self._start_x = event.mouse_x
        # invoked from a panel/pie: context.region may be the UI sidebar and
        # region_data None — always take the 3D viewport's WINDOW region
        self._region = next((r for r in context.area.regions if r.type == "WINDOW"),
                            context.region) if context.area else context.region
        self._rv3d = (context.area.spaces.active.region_3d
                      if context.area and context.area.type == "VIEW_3D" else context.region_data)
        self._t0 = time.time()
        self._shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (), "WINDOW", "POST_VIEW"
        )
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_2d, (), "WINDOW", "POST_PIXEL"
        )
        context.area.tag_redraw()
        context.window.cursor_modal_set("CROSSHAIR")
        context.workspace.status_text_set(self._status_text())
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    _AXIS_COLORS = {"X": (1.0, 0.25, 0.25, 1.0), "Y": (0.35, 1.0, 0.35, 1.0), "Z": (0.4, 0.5, 1.0, 1.0)}

    def _status_text(self):
        ax = "view" if self.axis_key is None else "%s %s" % (self.axis_key, self.axis_space.lower())
        return ("TOCHKA rotate pivot: %.2f deg around %s  |  X/Y/Z axis (again: local)  |  "
                "Shift = fine, Ctrl = 5-deg, Ctrl+Shift = 1-deg  |  LMB/Enter apply  |  RMB/Esc cancel"
                % (math.degrees(self.angle), ax))

    def _axis_world(self):
        from mathutils import Vector, Matrix
        if self.axis_key is None:
            return (self._rv3d.view_rotation @ Vector((0, 0, 1)))
        v = Vector((self.axis_key == "X", self.axis_key == "Y", self.axis_key == "Z"))
        if self.axis_space == "LOCAL":
            return (self.M0.to_3x3() @ v).normalized()
        return v

    def _matrix(self):
        """Object matrix with the rotation part replaced by the accumulated
        turn; translation and scale (and thus the pivot point) stay put."""
        if abs(self.angle) < 1e-9:
            return self.M0.copy()
        from mathutils import Matrix
        loc, rot, scale = self.M0.decompose()
        a = self._axis_world()
        Rr = Matrix.Rotation(self.angle, 4, a)
        R = rot.to_matrix().to_4x4()
        if self.axis_key is not None and self.axis_space == "LOCAL":
            R = R @ Rr
        else:
            R = Rr @ R
        return Matrix.Translation(loc) @ R @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))

    def _draw(self):
        import traceback
        try:
            self._draw_3d()
        except Exception:
            traceback.print_exc()

    def _draw_3d(self):
        import traceback
        M = self._matrix()
        rot = M.to_3x3()
        p = M.translation
        L = self._rv3d.view_distance * 0.12
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(4)
        self._shader.bind()
        for name, local in (("X", Vector((1, 0, 0))), ("Y", Vector((0, 1, 0))), ("Z", Vector((0, 0, 1)))):
            d = (rot @ local).normalized() * L
            self._shader.uniform_float("color", self._AXIS_COLORS[name])
            batch_for_shader(self._shader, "LINES",
                             {"pos": (p - d, p + d)}).draw(self._shader)
        gpu.state.line_width_set(1)
        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("LESS_EQUAL")

    def _draw_2d(self):
        """Screen-space ring at the projected pivot + live angle text."""
        import traceback
        try:
            import blf
            from bpy_extras import view3d_utils
            import math as _m
            co = view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, self._matrix().translation)
            if co is not None:
                r = 34.0
                n = 32
                verts = [(co.x + r * _m.cos(2 * _m.pi * i / n), co.y + r * _m.sin(2 * _m.pi * i / n))
                         for i in range(n + 1)]
                shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                color = self._AXIS_COLORS[self.axis_key] if self.axis_key else (1.0, 0.85, 0.1, 1.0)
                gpu.state.blend_set("ALPHA")
                gpu.state.line_width_set(3)
                shader.bind()
                shader.uniform_float("color", color)
                batch_for_shader(shader, "LINE_STRIP", {"pos": verts}).draw(shader)
                gpu.state.line_width_set(1)
                gpu.state.blend_set("NONE")
            if getattr(self, "_mouse2d", None):
                font_id = 0
                try:
                    blf.size(font_id, 15)
                except TypeError:
                    blf.size(font_id, 15, 72)
                blf.position(font_id, self._mouse2d[0] + 18, self._mouse2d[1] + 12, 0)
                blf.color(font_id, 1.0, 0.85, 0.1, 1.0)
                blf.draw(font_id, "%.1f deg  [%s]" % (math.degrees(self.angle),
                                                     (self.axis_key or "view") + ("" if not self.axis_key else " " + self.axis_space.lower())))
        except Exception:
            traceback.print_exc()

    def _cleanup(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
        bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, "WINDOW")
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        try:
            return self._modal_inner(context, event)
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self._cleanup(context)
            except Exception:
                pass
            return {"CANCELLED"}

    def _modal_inner(self, context, event):
        if event.type == "MOUSEMOVE":
            self._mouse2d = (event.mouse_region_x, event.mouse_region_y)
            delta = event.mouse_x - self._start_x
            self.angle = delta * (0.001 if event.shift else 0.01)
            if event.ctrl:
                step = math.radians(1.0 if event.shift else 5.0)
                self.angle = round(self.angle / step) * step
            # preview is draw-only: mesh and matrix stay untouched until commit
            context.workspace.status_text_set(self._status_text())
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"X", "Y", "Z"} and event.value == "PRESS":
            if self.axis_key == event.type:
                if self.axis_space == "GLOBAL":
                    self.axis_space = "LOCAL"
                else:
                    self.axis_key = None
                    self.axis_space = "GLOBAL"
            else:
                self.axis_key = event.type
                self.axis_space = "GLOBAL"
            context.workspace.status_text_set(self._status_text())
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if time.time() - self._t0 < 0.2:
                return {"RUNNING_MODAL"}  # swallow the launching button's own click
            M_old = self.M0
            self._cleanup(context)
            if abs(self.angle) > 1e-9:
                M_new = self._matrix()
                self.obj.data.transform(M_new.inverted() @ M_old)  # keep world geometry
                self.obj.matrix_world = M_new
            self.report({"INFO"}, "Pivot rotated")
            return {"FINISHED"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}



# ─── Align Pivot to Normal (modal) ─────────────────────────────

class TOCHKA_OT_align_pivot(Operator):
    bl_idname = "tochka.align_pivot"
    bl_label = "Align Pivot to Normal"
    bl_description = ("Hover a face: the pivot Z axis follows its normal. "
                      "LMB/Enter apply, RMB/Esc cancel")
    bl_options = {"REGISTER", "UNDO"}

    _AXIS_COLORS = {"X": (1.0, 0.25, 0.25, 1.0), "Y": (0.35, 1.0, 0.35, 1.0), "Z": (0.4, 0.5, 1.0, 1.0)}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "OBJECT"
            and context.active_object is not None
            and context.active_object.type == "MESH"
        )

    def invoke(self, context, event):
        self.obj = context.active_object
        self.M0 = self.obj.matrix_world.copy()
        self._target = None          # world-space normal under cursor
        self._mode = "NORMAL"        # "NORMAL" | "EDGE"
        self._edges = []             # world dirs of the hovered face's edges
        self._edge_idx = -1          # -1 = NORMAL, else index into _edges
        self._depsgraph = context.evaluated_depsgraph_get()
        # invoked from a panel/pie: context.region may be the UI sidebar and
        # region_data None — always take the 3D viewport's WINDOW region
        self._region = next((r for r in context.area.regions if r.type == "WINDOW"),
                            context.region) if context.area else context.region
        self._rv3d = (context.area.spaces.active.region_3d
                      if context.area and context.area.type == "VIEW_3D" else context.region_data)
        self._t0 = time.time()
        self._shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (), "WINDOW", "POST_VIEW"
        )
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_2d, (), "WINDOW", "POST_PIXEL"
        )
        context.area.tag_redraw()
        context.window.cursor_modal_set("CROSSHAIR")
        context.workspace.status_text_set(
            "TOCHKA align pivot: hover a face (pivot Z follows the normal)  |  "
            "LMB/Enter apply  |  RMB/Esc cancel")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _in_region(self, event):
        r = self._region
        return 0 <= event.mouse_region_x < r.width and 0 <= event.mouse_region_y < r.height

    def _face_normal(self, event):
        from bpy_extras import view3d_utils
        if not self._in_region(event):
            return None
        coord = (event.mouse_region_x, event.mouse_region_y)
        direction = view3d_utils.region_2d_to_vector_3d(self._region, self._rv3d, coord)
        origin = view3d_utils.region_2d_to_origin_3d(self._region, self._rv3d, coord)
        depsgraph = self._depsgraph
        hit, loc, normal, face_i, obj, matrix = depsgraph.scene.ray_cast(depsgraph, origin, direction)
        if not hit:
            return None
        return Vector(normal).normalized()

    def _in_region(self, event):
        r = self._region
        return 0 <= event.mouse_region_x < r.width and 0 <= event.mouse_region_y < r.height

    def _edge_directions(self, event):
        """World-space directions of the hovered face's edges (deduplicated)."""
        from bpy_extras import view3d_utils
        if not self._in_region(event):
            return []
        coord = (event.mouse_region_x, event.mouse_region_y)
        direction = view3d_utils.region_2d_to_vector_3d(self._region, self._rv3d, coord)
        origin = view3d_utils.region_2d_to_origin_3d(self._region, self._rv3d, coord)
        hit, loc, normal, face_i, obj, matrix = self._depsgraph.scene.ray_cast(
            self._depsgraph, origin, direction)
        if not hit:
            return []
        me = obj.data
        try:
            poly = me.polygons[face_i]
        except IndexError:
            return []
        mw = obj.matrix_world
        cos = me.vertices
        vids = list(poly.vertices)
        dirs = []
        for i in range(len(vids)):
            d = (mw @ cos[vids[(i + 1) % len(vids)]].co) - (mw @ cos[vids[i]].co)
            if d.length < 1e-9:
                continue
            d.normalize()
            if all(abs(d.dot(prev)) < 0.9999 for prev in dirs):
                dirs.append(d)
        return dirs

    def _cycle_edge(self):
        if self._mode == "NORMAL":
            self._mode = "EDGE"
            self._edges = []
            self._edge_idx = 0
        else:
            self._edge_idx += 1
            if self._edge_idx >= len(self._edges):
                self._mode = "NORMAL"
                self._edge_idx = -1
                self._edges = []

    def _status(self):
        if self._mode == "EDGE" and 0 <= self._edge_idx < len(self._edges):
            return ("TOCHKA align pivot: edge %d/%d (pivot X -> edge)  |  "
                    "E next edge  |  LMB/Enter apply  |  RMB/Esc cancel"
                    % (self._edge_idx + 1, len(self._edges)))
        return ("TOCHKA align pivot: Z -> face normal  |  E: take edge direction  |  "
                "LMB/Enter apply  |  RMB/Esc cancel")

    def _matrix(self):
        """Orientation from the current target: Z on face normal or X on edge."""
        loc, rot, scale = self.M0.decompose()
        if self._mode == "EDGE" and 0 <= self._edge_idx < len(self._edges):
            q = self._edges[self._edge_idx].to_track_quat("X", "Z")
        elif self._target is not None:
            q = self._target.to_track_quat("Z", "Y")
        else:
            return self.M0.copy()
        return (Matrix.Translation(loc) @ q.to_matrix().to_4x4()
                @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0)))

    def _draw(self):
        import traceback
        try:
            M = self._matrix()
            rot = M.to_3x3()
            p = M.translation
            L = self._rv3d.view_distance * 0.12
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(4)
            self._shader.bind()
            for name, local in (("X", Vector((1, 0, 0))), ("Y", Vector((0, 1, 0))), ("Z", Vector((0, 0, 1)))):
                d = (rot @ local).normalized() * L
                self._shader.uniform_float("color", self._AXIS_COLORS[name])
                batch_for_shader(self._shader, "LINES", {"pos": (p - d, p + d)}).draw(self._shader)
            gpu.state.line_width_set(1)
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
        except Exception:
            traceback.print_exc()

    def _draw_2d(self):
        import traceback
        try:
            import blf
            from bpy_extras import view3d_utils
            import math as _m
            co = view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, self._matrix().translation)
            if co is not None:
                r = 34.0
                n = 32
                verts = [(co.x + r * _m.cos(2 * _m.pi * i / n), co.y + r * _m.sin(2 * _m.pi * i / n))
                         for i in range(n + 1)]
                shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                gpu.state.blend_set("ALPHA")
                gpu.state.line_width_set(3)
                shader.bind()
                shader.uniform_float("color", (0.4, 0.5, 1.0, 1.0) if self._target else (1.0, 0.85, 0.1, 1.0))
                batch_for_shader(shader, "LINE_STRIP", {"pos": verts}).draw(shader)
                gpu.state.line_width_set(1)
                gpu.state.blend_set("NONE")
            if getattr(self, "_mouse2d", None):
                font_id = 0
                try:
                    blf.size(font_id, 15)
                except TypeError:
                    blf.size(font_id, 15, 72)
                blf.position(font_id, self._mouse2d[0] + 18, self._mouse2d[1] + 12, 0)
                blf.color(font_id, 1.0, 0.85, 0.1, 1.0)
                if self._mode == "EDGE" and 0 <= self._edge_idx < len(self._edges):
                    txt = "X -> edge %d/%d" % (self._edge_idx + 1, len(self._edges))
                else:
                    txt = "Z -> normal" if self._target else "no face"
                blf.draw(font_id, txt)
        except Exception:
            traceback.print_exc()

    def _cleanup(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
        bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, "WINDOW")
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        try:
            return self._modal_inner(context, event)
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self._cleanup(context)
            except Exception:
                pass
            return {"CANCELLED"}

    def _modal_inner(self, context, event):
        if event.type == "MOUSEMOVE":
            self._mouse2d = (event.mouse_region_x, event.mouse_region_y)
            n = self._face_normal(event)
            if n is not None:
                self._target = n  # sticky: keep last valid target on a miss
            if self._mode == "EDGE":
                self._edges = self._edge_directions(event)
                if self._edges and self._edge_idx >= len(self._edges):
                    self._edge_idx = 0  # clamp, keep edge mode
            context.workspace.status_text_set(self._status())
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type == "E" and event.value == "PRESS":
            self._cycle_edge()
            if self._mode == "EDGE" and not self._edges:
                # no face under cursor yet: try picking right now
                self._edges = self._edge_directions(event)
            print("TOCHKA: E -> mode=%s edges=%d" % (self._mode, len(self._edges)))
            context.workspace.status_text_set(self._status())
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if time.time() - self._t0 < 0.2:
                return {"RUNNING_MODAL"}  # swallow the launching button's own click
            ok = self._target is not None or (self._mode == "EDGE" and 0 <= self._edge_idx < len(self._edges))
            M_old = self.M0
            self._cleanup(context)
            if ok:
                M_new = self._matrix()
                self.obj.data.transform(M_new.inverted() @ M_old)
                self.obj.matrix_world = M_new
                self.report({"INFO"}, "Pivot aligned to normal")
            return {"FINISHED" if ok else "CANCELLED"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}



# ─── Align Pivot to Edge (modal) ───────────────────────────────

class TOCHKA_OT_align_pivot_edge(Operator):
    bl_idname = "tochka.align_pivot_edge"
    bl_label = "Align Pivot to Edge"
    bl_description = ("Hover a face: pivot X follows its longest edge. "
                      "LMB/Enter apply, RMB/Esc cancel")
    bl_options = {"REGISTER", "UNDO"}

    _AXIS_COLORS = {"X": (1.0, 0.25, 0.25, 1.0), "Y": (0.35, 1.0, 0.35, 1.0), "Z": (0.4, 0.5, 1.0, 1.0)}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "OBJECT"
            and context.active_object is not None
            and context.active_object.type == "MESH"
        )

    def invoke(self, context, event):
        self.obj = context.active_object
        self.M0 = self.obj.matrix_world.copy()
        self._dir = None             # world-space edge direction under cursor
        self._depsgraph = context.evaluated_depsgraph_get()
        # invoked from a panel/pie: context.region may be the UI sidebar and
        # region_data None — always take the 3D viewport's WINDOW region
        self._region = next((r for r in context.area.regions if r.type == "WINDOW"),
                            context.region) if context.area else context.region
        self._rv3d = (context.area.spaces.active.region_3d
                      if context.area and context.area.type == "VIEW_3D" else context.region_data)
        self._t0 = time.time()
        self._shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        self._handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw, (), "WINDOW", "POST_VIEW"
        )
        self._handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_2d, (), "WINDOW", "POST_PIXEL"
        )
        self._mouse2d = None
        context.area.tag_redraw()
        context.window.cursor_modal_set("CROSSHAIR")
        context.workspace.status_text_set(
            "TOCHKA align pivot: hover a face (pivot X follows its longest edge)  |  "
            "LMB/Enter apply  |  RMB/Esc cancel")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _in_region(self, event):
        r = self._region
        return 0 <= event.mouse_region_x < r.width and 0 <= event.mouse_region_y < r.height

    def _edge_dir(self, event):
        """Direction of the longest edge of the face under the cursor."""
        from bpy_extras import view3d_utils
        if not self._in_region(event):
            return None
        coord = (event.mouse_region_x, event.mouse_region_y)
        direction = view3d_utils.region_2d_to_vector_3d(self._region, self._rv3d, coord)
        origin = view3d_utils.region_2d_to_origin_3d(self._region, self._rv3d, coord)
        hit, loc, normal, face_i, obj, matrix = self._depsgraph.scene.ray_cast(
            self._depsgraph, origin, direction)
        if not hit:
            return None
        me = obj.data
        try:
            poly = me.polygons[face_i]
        except IndexError:
            return None
        mw = obj.matrix_world
        cos = me.vertices
        vids = list(poly.vertices)
        best_dir, best_len = None, -1.0
        for i in range(len(vids)):
            d = (mw @ cos[vids[(i + 1) % len(vids)]].co) - (mw @ cos[vids[i]].co)
            ln = d.length
            if ln > best_len:
                best_len, best_dir = ln, d / ln
        return best_dir

    def _matrix(self):
        """Orientation with local X on the edge direction (translation/scale kept)."""
        loc, rot, scale = self.M0.decompose()
        if self._dir is None:
            return self.M0.copy()
        q = self._dir.to_track_quat("X", "Z")
        return (Matrix.Translation(loc) @ q.to_matrix().to_4x4()
                @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0)))

    def _draw(self):
        import traceback
        try:
            M = self._matrix()
            rot = M.to_3x3()
            p = M.translation
            L = self._rv3d.view_distance * 0.12
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(4)
            self._shader.bind()
            for name, local in (("X", Vector((1, 0, 0))), ("Y", Vector((0, 1, 0))), ("Z", Vector((0, 0, 1)))):
                d = (rot @ local).normalized() * L
                self._shader.uniform_float("color", self._AXIS_COLORS[name])
                batch_for_shader(self._shader, "LINES", {"pos": (p - d, p + d)}).draw(self._shader)
            gpu.state.line_width_set(1)
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
        except Exception:
            traceback.print_exc()

    def _draw_2d(self):
        import traceback
        try:
            import blf
            from bpy_extras import view3d_utils
            import math as _m
            co = view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, self._matrix().translation)
            if co is not None:
                r = 34.0
                n = 32
                verts = [(co.x + r * _m.cos(2 * _m.pi * i / n), co.y + r * _m.sin(2 * _m.pi * i / n))
                         for i in range(n + 1)]
                shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                gpu.state.blend_set("ALPHA")
                gpu.state.line_width_set(3)
                shader.bind()
                shader.uniform_float("color", (1.0, 0.25, 0.25, 1.0) if self._dir else (1.0, 0.85, 0.1, 1.0))
                batch_for_shader(shader, "LINE_STRIP", {"pos": verts}).draw(shader)
                gpu.state.line_width_set(1)
                gpu.state.blend_set("NONE")
            if getattr(self, "_mouse2d", None):
                font_id = 0
                try:
                    blf.size(font_id, 15)
                except TypeError:
                    blf.size(font_id, 15, 72)
                blf.position(font_id, self._mouse2d[0] + 18, self._mouse2d[1] + 12, 0)
                blf.color(font_id, 1.0, 0.85, 0.1, 1.0)
                blf.draw(font_id, "X -> edge" if self._dir else "no face")
        except Exception:
            traceback.print_exc()

    def _cleanup(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
        bpy.types.SpaceView3D.draw_handler_remove(self._handle_2d, "WINDOW")
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        try:
            return self._modal_inner(context, event)
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self._cleanup(context)
            except Exception:
                pass
            return {"CANCELLED"}

    def _modal_inner(self, context, event):
        if event.type == "MOUSEMOVE":
            self._mouse2d = (event.mouse_region_x, event.mouse_region_y)
            d = self._edge_dir(event)
            if d is not None:
                self._dir = d  # sticky: keep last valid direction on a miss
            context.workspace.status_text_set(
                "TOCHKA align pivot: %s  |  LMB/Enter apply  |  RMB/Esc cancel"
                % ("X -> longest edge" if self._dir else "no face under cursor"))
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if TOCHKA_DEBUG:
                print("DBG align_edge: LMB PRESS, dt=%.2f, dir=%s" % (time.time() - self._t0, self._dir is not None))
            if time.time() - self._t0 < 0.2:
                return {"RUNNING_MODAL"}  # swallow the launching button's own click
            ok = self._dir is not None
            M_old = self.M0
            self._cleanup(context)
            if ok:
                M_new = self._matrix()
                self.obj.data.transform(M_new.inverted() @ M_old)
                self.obj.matrix_world = M_new
                self.report({"INFO"}, "Pivot aligned to edge")
            return {"FINISHED" if ok else "CANCELLED"}

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}


# ─── Pivot Audit ───────────────────────────────────────────────

def _audit_scope(context, props):
    suf = props.suffix_filter.strip()
    objs = context.selected_objects if context.mode == "OBJECT" else []
    return [o for o in objs if o.type == "MESH" and (not suf or o.name.endswith(suf))]


class TOCHKA_OT_audit(Operator):
    bl_idname = "tochka.audit"
    bl_label = "Audit Selected"
    bl_description = "Check pivots against bottom-center of the bbox"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(context.selected_objects)

    def execute(self, context):
        props = context.scene.tochka_props
        target = props.audit_target
        tol = props.tol_origin_m if target == "ORIGIN" else props.tol_percent / 100.0
        props.flagged.clear()
        n = 0
        for o in _audit_scope(context, props):
            n += 1
            if _pivot_offset(o, target) > tol:
                props.flagged.add().name = o.name
        self.report({"INFO"}, "Audited %d, flagged %d" % (n, len(props.flagged)))
        return {"FINISHED"}


class TOCHKA_OT_audit_select(Operator):
    bl_idname = "tochka.audit_select"
    bl_label = "Select"
    bl_description = "Select this object"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        props = context.scene.tochka_props
        if self.index < len(props.flagged):
            name = props.flagged[self.index].name
            obj = bpy.data.objects.get(name)
            if obj:
                for o in context.selected_objects:
                    o.select_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj
        return {"FINISHED"}


class TOCHKA_OT_audit_fix(Operator):
    bl_idname = "tochka.audit_fix"
    bl_label = "Fix All Flagged"
    bl_description = "Move pivot to bottom-center of bbox on all flagged objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and len(context.scene.tochka_props.flagged) > 0

    def execute(self, context):
        props = context.scene.tochka_props
        done, skipped = 0, []
        for item in props.flagged:
            obj = bpy.data.objects.get(item.name)
            if obj is None or obj.type != "MESH":
                continue
            if obj.data.users > 1:
                skipped.append(obj.name)
                continue
            _move_origin(obj, _audit_anchor(obj, context.scene.tochka_props.audit_target))
            context.view_layer.update()  # bound_box is lazy
            done += 1
        props.flagged.clear()
        if skipped:
            self.report({"WARNING"}, "Fixed %d; skipped multi-user: %s" % (done, ", ".join(skipped)))
        else:
            self.report({"INFO"}, "Fixed %d" % done)
        return {"FINISHED"}


# ─── Keymap ────────────────────────────────────────────────────

addon_keymaps = []


def register_keymaps():
    wm = bpy.context.window_manager
    for km_name in ("Mesh", "Object Mode"):
        km = wm.keyconfigs.addon.keymaps.new(km_name, space_type="EMPTY")
        kmi = km.keymap_items.new("tochka.origin_to_selection", type="D", value="PRESS", ctrl=False)
        kmi.properties.anchor = "MEDIAN"
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new("wm.call_menu_pie", type="D", value="PRESS", alt=True)
        kmi.properties.name = "TOCHKA_MT_pie"
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new("tochka.drag_pivot", type="D", value="PRESS", ctrl=True)
        addon_keymaps.append((km, kmi))
        if km_name == "Object Mode":
            kmi = km.keymap_items.new("tochka.rotate_pivot", type="D", value="PRESS", ctrl=True, alt=True)
            addon_keymaps.append((km, kmi))


def unregister_keymaps():
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()


classes = (TOCHKA_props, TOCHKA_OT_origin_to_selection, TOCHKA_OT_drag_pivot,
           TOCHKA_OT_rotate_pivot, TOCHKA_OT_align_pivot, TOCHKA_OT_align_pivot_edge,
           TOCHKA_MT_pie, TOCHKA_OT_audit, TOCHKA_OT_audit_select, TOCHKA_OT_audit_fix,
           TOCHKA_PT_main, TOCHKA_PT_info)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.tochka_props = PointerProperty(type=TOCHKA_props)
    register_keymaps()
    print("TOCHKA %s REGISTERED" % VERSION_STR)


def unregister():
    unregister_keymaps()
    del bpy.types.Scene.tochka_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()

# -*- coding: utf-8 -*-
"""TOCHKA demo GIF builder: renders frames via GPU offscreen, assembles GIFs.
Run INSIDE Blender (via bridge): exec(open(R"gif_build.py").read())
"""
import bpy, bmesh, gpu, math, os, sys
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Vector, Matrix

OUT = r"D:\AI\ZCode\Project\TOCHKA\docs\img"
FRAMES = os.path.join(OUT, "frames")
os.makedirs(FRAMES, exist_ok=True)

W, H = 640, 400
FOV = math.radians(38.0)

# ── scene: clean default objects, build demo asset ─────────────
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)

# base plate 4 x 2 x 0.4
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.2))
plate = bpy.context.active_object
plate.scale = (4, 2, 0.4)
bpy.ops.object.transform_apply(scale=True)
# wheel-ish cylinder on top, axis along X
bpy.ops.mesh.primitive_cylinder_add(radius=0.6, depth=0.5, location=(0.6, 0, 0.6), rotation=(0, math.radians(90), 0))
wheel = bpy.context.active_object
# a corner-aligned small cube far side
bpy.ops.mesh.primitive_cube_add(size=0.8, location=(-1.4, 0.5, 0.4))

bpy.context.view_layer.update()

# ── camera view ────────────────────────────────────────────────
def look_at(eye, target):
    d = (Vector(target) - Vector(eye)).normalized()
    q = d.to_track_quat("-Z", "Y")
    return q.inverted().to_matrix().to_4x4() @ Matrix.Translation(-Vector(eye))

EYE = Vector((4.2, -4.6, 3.4))
TARGET = Vector((-0.2, 0, 0.2))
VIEW = look_at(EYE, TARGET)

def proj_mat(w, h):
    f = 1.0 / math.tan(FOV / 2)
    aspect = w / h
    near, far = 0.1, 100.0
    return Matrix((
        (f / aspect, 0, 0, 0),
        (0, f, 0, 0),
        (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
        (0, 0, -1, 0)))

def mesh_batches(ob, shader):
    me = ob.data
    me.calc_loop_triangles()
    mw = ob.matrix_world
    tris = []
    for t in me.loop_triangles:
        tris.extend([mw @ me.vertices[v].co for v in t.vertices])
    faces = batch_for_shader(shader, "TRIS", {"pos": tris})
    lines = []
    for e in me.edges:
        lines.extend([mw @ me.vertices[e.vertices[0]].co, mw @ me.vertices[e.vertices[1]].co])
    wire = batch_for_shader(shader, "LINES", {"pos": lines})
    return faces, wire

cube3 = bpy.data.objects.get("Cube.001")
OBJS = [(plate, (0.62, 0.64, 0.68, 1.0)), (wheel, (0.55, 0.58, 0.65, 1.0))] + (
    [(cube3, (0.60, 0.55, 0.50, 1.0))] if cube3 else [])

AXCOL = {"X": (1.0, 0.25, 0.25, 1.0), "Y": (0.35, 1.0, 0.35, 1.0), "Z": (0.4, 0.5, 1.0, 1.0)}

def draw_triad(shader, p, rot, L):
    for name, local in (("X", Vector((1, 0, 0))), ("Y", Vector((0, 1, 0))), ("Z", Vector((0, 0, 1)))):
        d = (rot @ local).normalized() * L
        shader.uniform_float("color", AXCOL[name])
        batch_for_shader(shader, "LINES", {"pos": (p - d, p + d)}).draw(shader)

def draw_ring(shader, p, r, color, n=40, axis_z=Vector((0, 0, 1))):
    q = axis_z.to_track_quat("Z", "Y").to_matrix()
    pts = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        pts.append(p + q @ (Vector((math.cos(a) * r, math.sin(a) * r, 0))))
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINE_STRIP", {"pos": pts}).draw(shader)

def draw_dot(shader, p, s):
    shader.uniform_float("color", (1.0, 0.6, 0.0, 1.0))
    batch_for_shader(shader, "POINTS", {"pos": (p,)}).draw(shader)

def render_frame(idx, markers_fn):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    off = gpu.types.GPUOffScreen(W, H)
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0.12, 0.125, 0.135, 1.0), depth=1.0)
        gpu.matrix.reset()
        gpu.matrix.load_projection_matrix(proj_mat(W, H))
        gpu.matrix.load_matrix(VIEW)
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.blend_set("ALPHA")
        gpu.state.line_width_set(1)
        for ob, col in OBJS:
            f, w = mesh_batches(ob, shader)
            gpu.state.depth_test_set("LESS_EQUAL")
            shader.uniform_float("color", col)
            f.draw(shader)
            gpu.state.depth_test_set("NONE")
            shader.uniform_float("color", (0.15, 0.16, 0.18, 1.0))
            w.draw(shader)
        gpu.state.depth_test_set("NONE")
        gpu.state.line_width_set(2)
        gpu.state.point_size_set(8)
        markers_fn(shader)
        gpu.state.blend_set("NONE")
        buf = fb.read_color(0, 0, W, H, 4, 0, "FLOAT")
        px = np.array(buf.to_list(), dtype=np.float32).reshape(H, W, 4)
    off.free()
    img = bpy.data.images.new("frame", W, H, alpha=True)
    img.pixels = (np.clip(px[:, :, :4], 0, 1)).ravel().tolist()
    img.filepath_raw = os.path.join(FRAMES, idx)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)

print("gif infra ready; frames dir:", FRAMES)

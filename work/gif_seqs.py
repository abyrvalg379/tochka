# -*- coding: utf-8 -*-
"""Render all demo frame sequences. Run in bridge AFTER gif_build exec (g)."""
import math
from mathutils import Vector, Matrix

FR = g["FRAMES"]
ease = lambda t: t * t * (3 - 2 * t)  # smoothstep

def lerp(a, b, t):
    return a + (b - a) * t

def seq_name(name, i):
    import os
    return os.path.join(FR, "%s_%03d.png" % (name, i))

# ── 1) DRAG: marker glides across the plate, snaps to the corner vertex ──
plate = g["plate"]
import sys
sys.path.insert(0, r"C:\Users\mkova\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\tochka")
import __init__ as tk
import importlib
importlib.reload(tk)
import bmesh

# remember original plate state
plate_mesh_snapshot = [v.co.copy() for v in plate.data.vertices]
plate_loc0 = plate.location.copy()
ORIGIN0 = Vector((0, 0, 0.0))
START = Vector((-0.4, 0.25, 0.0))
END = Vector((-2.0, 1.0, 0.0))  # plate corner vertex

N1 = 46
for i in range(N1):
    t = ease(min(i / float(N1 - 12), 1.0))
    p = START.lerp(END, t)
    # real origin move so the Blender-origin orange dot logic matches
    if i == 0:
        tk._move_origin(plate, ORIGIN0)
    tk._move_origin(plate, p)
    g["bpy"].context.view_layer.update()
    hold = i >= N1 - 12
    def m(shader, p=p, hold=hold):
        g["draw_triad"](shader, p, Matrix.Identity(3), 0.5)
        col = (0.35, 1.0, 0.35, 1.0) if hold else (1.0, 0.85, 0.1, 1.0)
        g["draw_ring"](shader, p, 0.34, col)
        g["draw_dot"](shader, plate.matrix_world.translation, 8)
    g["render_frame"](seq_name("drag", i), m)

# restore plate
for v, co in zip(plate.data.vertices, plate_mesh_snapshot):
    v.co = co
plate.location = plate_loc0
plate.data.update()
g["bpy"].context.view_layer.update()

# ── 2) ROTATE: triad turns 90 deg about world X (wheel axle), geometry still ──
wheel = g["wheel"]
pivot = wheel.matrix_world.translation.copy()
N2 = 40
for i in range(N2):
    t = ease(min(i / float(N2 - 10), 1.0))
    ang = math.radians(90.0) * t
    R = Matrix.Rotation(ang, 3, Vector((1, 0, 0)))
    ringcol = (1.0, 0.25, 0.25, 1.0) if i >= N2 - 10 else (1.0, 0.85, 0.1, 1.0)
    def m(shader, R=R, ringcol=ringcol):
        g["draw_triad"](shader, pivot, R, 0.7)
        g["draw_ring"](shader, pivot, 0.42, ringcol, axis_z=Vector((1, 0, 0)))
    g["render_frame"](seq_name("rotate", i), m)

# ── 3) ALIGN: phase A Z -> normal (+X, wheel side), phase B X -> edge dir (Y) ──
pivot2 = Vector((-1.4, 0.5, 0.4))
z0 = Vector((0, 0, 1))
z1 = Vector((1, 0, 0))
N3a, N3b = 24, 24
idx = 0
for i in range(N3a):
    t = ease(i / float(N3a - 1))
    z = z0.lerp(z1, t).normalized()
    y = z.cross(Vector((0, 0, 1)))
    if y.length < 1e-4:
        y = Vector((0, 1, 0))
    y.normalize()
    x = y.cross(z).normalized()
    R = Matrix((x, y, z)).transposed()
    def m(shader, R=R):
        g["draw_triad"](shader, pivot2, R, 0.6)
        g["draw_ring"](shader, pivot2, 0.4, (0.4, 0.5, 1.0, 1.0))
    g["render_frame"](seq_name("align", idx), m)
    idx += 1
x0 = Vector((1, 0, 0))
x1 = Vector((0, 1, 0))
for i in range(N3b):
    t = ease(i / float(N3b - 1))
    z = z1
    x = x0.lerp(x1, t).normalized()
    y = z.cross(x).normalized()
    R = Matrix((x, y, z)).transposed()
    def m(shader, R=R):
        g["draw_triad"](shader, pivot2, R, 0.6)
        g["draw_ring"](shader, pivot2, 0.4, (1.0, 0.25, 0.25, 1.0))
    g["render_frame"](seq_name("align", idx), m)
    idx += 1

print("sequences done: drag=%d rotate=%d align=%d" % (N1, N2, N3a + N3b))

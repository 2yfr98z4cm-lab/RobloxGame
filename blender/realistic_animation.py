"""
Realistic Blender animation — "Golden Hour Showcase"

A fully procedural, photoreal scene rendered with Cycles:
  * Physically based (PBR) materials: chrome, gold, glass, marble, red
    clear-coat car paint, and wet patchy concrete
  * Physical sky + sun at golden hour, plus a soft area fill light
  * A chrome ball that rolls with physically correct spin, a bouncing glass
    sphere, a floating gold torus, and a car-paint Suzanne on a marble turntable
  * An orbiting camera with depth of field and motion blur
  * AgX / Filmic color management, denoising, and MP4 output

No external textures or add-ons are needed. Tested against the Blender 3.6+
and 4.x Python APIs (input names that changed between versions are handled).

Usage
-----
Interactive: open Blender > Scripting tab > open this file > Run Script.
             Then press F12 (still) or Ctrl+F12 (animation).

Headless:
    blender -b -P realistic_animation.py -- --render            # full quality
    blender -b -P realistic_animation.py -- --render --preview  # fast draft
    blender -b -P realistic_animation.py -- --save scene.blend  # just save
"""

import math
import os
import sys

import bpy
from mathutils import Vector

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
FPS = 24
FRAME_START = 1
FRAME_END = 240              # 10 seconds
RES_X, RES_Y = 1920, 1080
SAMPLES = 256                # Cycles samples per pixel (final)
PREVIEW_SAMPLES = 48
SUN_ELEVATION_DEG = 12.0     # low sun = warm golden-hour light
SUN_AZIMUTH_DEG = 145.0
OUTPUT_PATH = "//render/golden_hour_"

# --------------------------------------------------------------------------
# Command-line arguments (everything after "--")
# --------------------------------------------------------------------------
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DO_RENDER = "--render" in argv
PREVIEW = "--preview" in argv
SAVE_PATH = argv[argv.index("--save") + 1] if "--save" in argv else None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def clear_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                 bpy.data.cameras, bpy.data.worlds, bpy.data.node_groups):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def set_input(node, names, value):
    """Set the first matching socket — Principled BSDF names changed in 4.0."""
    if isinstance(names, str):
        names = (names,)
    for name in names:
        if name in node.inputs:
            node.inputs[name].default_value = value
            return True
    return False


def new_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    return mat, mat.node_tree, bsdf


def assign(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def smooth(obj, subdiv=0):
    for poly in obj.data.polygons:
        poly.use_smooth = True
    if subdiv:
        mod = obj.modifiers.new("Subdivision", "SUBSURF")
        mod.levels = subdiv
        mod.render_levels = subdiv + 1


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def key_interpolation(kind):
    """New keyframes use this interpolation (avoids version-specific fcurve APIs)."""
    bpy.context.preferences.edit.keyframe_new_interpolation_type = kind


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------
def mat_chrome():
    mat, _, b = new_material("Chrome")
    set_input(b, "Base Color", (0.95, 0.93, 0.88, 1))
    set_input(b, "Metallic", 1.0)
    set_input(b, "Roughness", 0.04)
    return mat


def mat_gold():
    mat, _, b = new_material("Gold")
    set_input(b, "Base Color", (1.0, 0.766, 0.336, 1))
    set_input(b, "Metallic", 1.0)
    set_input(b, "Roughness", 0.18)
    set_input(b, ("Anisotropic",), 0.3)
    return mat


def mat_glass():
    mat, _, b = new_material("Glass")
    set_input(b, "Base Color", (0.86, 0.95, 1.0, 1))
    set_input(b, "Roughness", 0.0)
    set_input(b, "IOR", 1.45)
    set_input(b, ("Transmission Weight", "Transmission"), 1.0)
    return mat


def mat_car_paint():
    mat, nt, b = new_material("CarPaintRed")
    set_input(b, "Base Color", (0.55, 0.01, 0.015, 1))
    set_input(b, "Metallic", 0.6)
    set_input(b, "Roughness", 0.32)
    set_input(b, ("Coat Weight", "Clearcoat"), 1.0)
    set_input(b, ("Coat Roughness", "Clearcoat Roughness"), 0.02)
    # Metallic flakes: fine Voronoi noise bumping the base layer only
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    vor.inputs["Scale"].default_value = 900.0
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.08
    nt.links.new(vor.outputs["Distance"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    return mat


def mat_marble():
    mat, nt, b = new_material("Marble")
    n = nt.nodes
    coord = n.new("ShaderNodeTexCoord")
    noise = n.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.9
    noise.inputs["Detail"].default_value = 12.0
    noise.inputs["Distortion"].default_value = 2.5
    ramp = n.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.47, (0.92, 0.91, 0.88, 1)
    els[1].position, els[1].color = 0.50, (0.18, 0.17, 0.17, 1)
    e = els.new(0.53)
    e.color = (0.95, 0.94, 0.92, 1)
    nt.links.new(coord.outputs["Object"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], b.inputs["Base Color"])
    set_input(b, "Roughness", 0.12)
    set_input(b, ("Subsurface Weight", "Subsurface"), 0.15)
    set_input(b, "Subsurface Radius", Vector((0.4, 0.3, 0.25)))
    return mat


def mat_wet_concrete():
    mat, nt, b = new_material("WetConcrete")
    n = nt.nodes
    coord = n.new("ShaderNodeTexCoord")

    # Base grit / color variation
    grit = n.new("ShaderNodeTexNoise")
    grit.inputs["Scale"].default_value = 40.0
    grit.inputs["Detail"].default_value = 15.0
    col_ramp = n.new("ShaderNodeValToRGB")
    c = col_ramp.color_ramp.elements
    c[0].color = (0.10, 0.095, 0.09, 1)
    c[1].color = (0.32, 0.30, 0.28, 1)

    # Large puddle mask: low values = water
    puddle = n.new("ShaderNodeTexNoise")
    puddle.inputs["Scale"].default_value = 0.6
    puddle.inputs["Detail"].default_value = 6.0
    mask = n.new("ShaderNodeValToRGB")
    m = mask.color_ramp.elements
    m[0].position, m[0].color = 0.36, (0, 0, 0, 1)
    m[1].position, m[1].color = 0.42, (1, 1, 1, 1)

    # Roughness: water ~0.02, dry concrete ~0.85
    rough = n.new("ShaderNodeMapRange")
    rough.inputs["To Min"].default_value = 0.02
    rough.inputs["To Max"].default_value = 0.85

    # Wet areas are darker
    darken = n.new("ShaderNodeMix")
    darken.data_type = "RGBA"
    darken.blend_type = "MULTIPLY"
    darken.inputs[0].default_value = 1.0

    wet_tint = n.new("ShaderNodeMix")
    wet_tint.data_type = "RGBA"

    # Bump only on dry areas (water surface is flat)
    bump = n.new("ShaderNodeBump")
    bump.inputs["Distance"].default_value = 0.02
    bump_strength = n.new("ShaderNodeMath")
    bump_strength.operation = "MULTIPLY"
    bump_strength.inputs[1].default_value = 0.6

    L = nt.links.new
    L(coord.outputs["Object"], grit.inputs["Vector"])
    L(coord.outputs["Object"], puddle.inputs["Vector"])
    L(grit.outputs["Fac"], col_ramp.inputs["Fac"])
    L(puddle.outputs["Fac"], mask.inputs["Fac"])
    L(mask.outputs["Color"], rough.inputs["Value"])
    L(rough.outputs["Result"], b.inputs["Roughness"])

    # Color = mix(dark wet color, dry color, mask)
    darken.inputs[7].default_value = (0.45, 0.45, 0.45, 1)  # B (RGBA)
    L(col_ramp.outputs["Color"], darken.inputs[6])           # A (RGBA)
    L(mask.outputs["Color"], wet_tint.inputs[0])
    L(darken.outputs[2], wet_tint.inputs[6])
    L(col_ramp.outputs["Color"], wet_tint.inputs[7])
    L(wet_tint.outputs[2], b.inputs["Base Color"])

    L(mask.outputs["Color"], bump_strength.inputs[0])
    L(bump_strength.outputs["Value"], bump.inputs["Strength"])
    L(grit.outputs["Fac"], bump.inputs["Height"])
    L(bump.outputs["Normal"], b.inputs["Normal"])
    return mat


# --------------------------------------------------------------------------
# World & lighting
# --------------------------------------------------------------------------
def build_world():
    world = bpy.data.worlds.new("GoldenHourSky")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    bg.inputs["Strength"].default_value = 0.08

    sky = nt.nodes.new("ShaderNodeTexSky")
    for sky_type in ("NISHITA", "MULTIPLE_SCATTERING", "SINGLE_SCATTERING",
                     "HOSEK_WILKIE"):
        try:
            sky.sky_type = sky_type
            break
        except TypeError:
            continue
    if hasattr(sky, "sun_disc"):
        sky.sun_disc = False          # the sun lamp provides the direct light
    if hasattr(sky, "sun_elevation"):
        sky.sun_elevation = math.radians(SUN_ELEVATION_DEG)
        sky.sun_rotation = math.radians(SUN_AZIMUTH_DEG)
    # Extra haze for warmer scattering ("dust" became "aerosol" in 5.0)
    for attr, value in (("air_density", 1.2), ("dust_density", 2.5),
                        ("aerosol_density", 2.5)):
        if hasattr(sky, attr):
            setattr(sky, attr, value)
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])


def build_lights():
    # Sun — warm, low, slightly soft shadow edges
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 6.0
    sun_data.color = (1.0, 0.62, 0.32)
    sun_data.angle = math.radians(0.8)
    sun = bpy.data.objects.new("Sun", sun_data)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(90 - SUN_ELEVATION_DEG), 0,
                          math.radians(SUN_AZIMUTH_DEG + 90))

    # Cool sky-fill area light opposite the sun
    fill_data = bpy.data.lights.new("Fill", "AREA")
    fill_data.energy = 150
    fill_data.size = 6
    fill_data.color = (0.75, 0.85, 1.0)
    fill = bpy.data.objects.new("Fill", fill_data)
    bpy.context.collection.objects.link(fill)
    fill.location = (-6, 5, 5)
    track = fill.constraints.new("TRACK_TO")
    track.target = bpy.data.objects.get("Suzanne")
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
def build_ground():
    bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, 0))
    ground = bpy.context.object
    ground.name = "Ground"
    assign(ground, mat_wet_concrete())
    return ground


def build_pedestal_and_suzanne():
    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=1.0, depth=0.6,
                                        location=(0, 0, 0.3))
    ped = bpy.context.object
    ped.name = "Pedestal"
    bev = ped.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.03
    bev.segments = 4
    bev.limit_method = "ANGLE"
    smooth(ped)
    if hasattr(ped.data, "use_auto_smooth"):         # pre-4.1
        ped.data.use_auto_smooth = True
    else:
        try:
            bpy.ops.object.shade_auto_smooth()
        except Exception:
            pass
    assign(ped, mat_marble())

    bpy.ops.mesh.primitive_monkey_add(size=1.1, location=(0, 0, 1.2))
    suz = bpy.context.object
    suz.name = "Suzanne"
    suz.rotation_euler = (math.radians(-10), 0, 0)
    smooth(suz, subdiv=2)
    assign(suz, mat_car_paint())
    # Rest the head on the pedestal top (z = 0.6) instead of floating
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = suz.evaluated_get(depsgraph)
    lowest = min((evaluated.matrix_world @ v.co).z
                 for v in evaluated.data.vertices)
    suz.location.z += 0.6 - lowest

    # Turntable: one full constant-speed revolution of pedestal + Suzanne
    key_interpolation("LINEAR")
    for obj in (ped, suz):
        obj.rotation_euler.z = 0
        obj.keyframe_insert("rotation_euler", index=2, frame=FRAME_START)
        obj.rotation_euler.z = math.radians(360)
        obj.keyframe_insert("rotation_euler", index=2, frame=FRAME_END)
    return suz


def build_rolling_chrome_ball():
    r = 0.45
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=r,
                                         location=(-5, -2.2, r))
    ball = bpy.context.object
    ball.name = "ChromeBall"
    smooth(ball)
    assign(ball, mat_chrome())

    # Roll from x=-5 to x=4.5 with ease-in/out; spin = distance / radius
    key_interpolation("LINEAR")
    x0, x1 = -5.0, 4.5
    for f in range(FRAME_START, FRAME_END + 1):
        t = smoothstep((f - FRAME_START) / (FRAME_END - FRAME_START))
        x = x0 + (x1 - x0) * t
        ball.location.x = x
        ball.rotation_euler.y = (x - x0) / r
        ball.keyframe_insert("location", index=0, frame=f)
        ball.keyframe_insert("rotation_euler", index=1, frame=f)
    return ball


def build_bouncing_glass_sphere():
    r = 0.35
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=r,
                                         location=(2.4, 1.6, r))
    glass = bpy.context.object
    glass.name = "GlassSphere"
    smooth(glass)
    assign(glass, mat_glass())

    # Decaying bounces with squash & stretch on contact
    key_interpolation("LINEAR")
    h0, damping, period = 2.2, 0.62, 30.0
    for f in range(FRAME_START, FRAME_END + 1):
        t = f - FRAME_START
        bounce = int(t // period)
        phase = (t % period) / period
        height = h0 * (damping ** bounce) * 4 * phase * (1 - phase)
        glass.location.z = r + height
        contact = max(0.0, 1.0 - min(phase, 1 - phase) * 12) * (damping ** bounce)
        sq = 1.0 - 0.18 * contact
        glass.scale = (1 / math.sqrt(sq), 1 / math.sqrt(sq), sq)
        glass.location.z -= r * (1 - sq)
        glass.keyframe_insert("location", index=2, frame=f)
        glass.keyframe_insert("scale", frame=f)
    return glass


def build_floating_gold_torus():
    bpy.ops.mesh.primitive_torus_add(major_radius=0.55, minor_radius=0.14,
                                     major_segments=128, minor_segments=48,
                                     location=(-2.2, 1.8, 1.6))
    torus = bpy.context.object
    torus.name = "GoldTorus"
    smooth(torus)
    assign(torus, mat_gold())

    key_interpolation("LINEAR")
    for f in range(FRAME_START, FRAME_END + 1, 2):
        t = (f - FRAME_START) / FPS
        torus.location.z = 1.6 + 0.25 * math.sin(t * 1.4)
        torus.rotation_euler = (math.radians(65) + 0.25 * math.sin(t * 0.9),
                                0.2 * math.sin(t * 1.1),
                                t * 0.8)
        torus.keyframe_insert("location", index=2, frame=f)
        torus.keyframe_insert("rotation_euler", frame=f)
    return torus


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------
def build_camera(target_obj):
    target = bpy.data.objects.new("CamTarget", None)
    bpy.context.collection.objects.link(target)
    target.location = target_obj.location + Vector((0, 0, -0.1))

    rig = bpy.data.objects.new("CamRig", None)
    bpy.context.collection.objects.link(rig)

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 50
    cam_data.sensor_width = 36
    cam_data.dof.use_dof = True
    cam_data.dof.focus_object = target
    cam_data.dof.aperture_fstop = 2.0
    cam_data.dof.aperture_blades = 7
    cam = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.parent = rig
    cam.location = (0, -9.5, 2.2)
    bpy.context.scene.camera = cam

    track = cam.constraints.new("TRACK_TO")
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"

    # Slow eased orbit plus a gentle push-in
    key_interpolation("BEZIER")
    rig.rotation_euler.z = math.radians(-35)
    rig.keyframe_insert("rotation_euler", index=2, frame=FRAME_START)
    rig.rotation_euler.z = math.radians(40)
    rig.keyframe_insert("rotation_euler", index=2, frame=FRAME_END)

    cam.keyframe_insert("location", frame=FRAME_START)
    cam.location = (0, -7.0, 1.7)
    cam.keyframe_insert("location", frame=FRAME_END)
    return cam


# --------------------------------------------------------------------------
# Render settings
# --------------------------------------------------------------------------
def enable_gpu(scene):
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return
    for dev_type in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = dev_type
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type != "CPU"]
        if gpus:
            for d in prefs.devices:
                d.use = True
            scene.cycles.device = "GPU"
            print(f"[render] Using GPU via {dev_type}: "
                  + ", ".join(d.name for d in gpus))
            return
    print("[render] No GPU found, rendering on CPU")


def configure_render():
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = FRAME_START, FRAME_END
    scene.render.fps = FPS
    scene.render.engine = "CYCLES"
    enable_gpu(scene)

    c = scene.cycles
    c.samples = PREVIEW_SAMPLES if PREVIEW else SAMPLES
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.02 if PREVIEW else 0.008
    c.use_denoising = True
    c.max_bounces = 12
    c.glossy_bounces = 6
    c.transmission_bounces = 12
    c.caustics_reflective = False
    c.caustics_refractive = True
    c.blur_glossy = 0.5                  # filter glossy noise

    r = scene.render
    r.resolution_x, r.resolution_y = RES_X, RES_Y
    r.resolution_percentage = 50 if PREVIEW else 100
    r.use_motion_blur = True
    r.motion_blur_shutter = 0.5          # 180° shutter, like a film camera
    r.film_transparent = False

    vs = scene.view_settings
    for view in ("AgX", "Filmic"):
        try:
            vs.view_transform = view
            break
        except TypeError:
            continue
    for look in ("AgX - Medium High Contrast", "Medium High Contrast",
                 "Filmic - Medium High Contrast"):
        try:
            vs.look = look
            break
        except TypeError:
            continue
    vs.exposure = -0.3

    # MP4 / H.264 output
    img = r.image_settings
    if hasattr(img, "media_type"):       # Blender 5.0+
        img.media_type = "VIDEO"
    img.file_format = "FFMPEG"
    r.ffmpeg.format = "MPEG4"
    r.ffmpeg.codec = "H264"
    r.ffmpeg.constant_rate_factor = "HIGH"
    r.ffmpeg.ffmpeg_preset = "GOOD"
    r.filepath = OUTPUT_PATH


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    prev_interp = bpy.context.preferences.edit.keyframe_new_interpolation_type
    try:
        clear_scene()
        build_world()
        build_ground()
        suz = build_pedestal_and_suzanne()
        build_rolling_chrome_ball()
        build_bouncing_glass_sphere()
        build_floating_gold_torus()
        build_lights()
        build_camera(suz)
        configure_render()
    finally:
        bpy.context.preferences.edit.keyframe_new_interpolation_type = prev_interp

    bpy.context.scene.frame_set(FRAME_START)
    print("[scene] Golden Hour Showcase built.")

    if SAVE_PATH:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(SAVE_PATH))
        print(f"[scene] Saved to {SAVE_PATH}")
    if DO_RENDER:
        bpy.ops.render.render(animation=True)
        print(f"[render] Done -> {bpy.context.scene.render.filepath}")


if __name__ == "__main__":
    main()

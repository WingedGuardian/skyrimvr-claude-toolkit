"""Geometry split (PyFFI, LE-format NiTriShape only).

Splits ONE combined NiTriShape into two independent shapes along a Y threshold so
the two halves can carry different shaders (e.g. an emissive "glow" half + a normal
half) without the glow-map UV-overlap problem. The original shape block is rewritten
as the LOW half; a new NiTriShape (+ its own NiTriShapeData + a copied, brightened
BSLightingShaderProperty) is appended for the HIGH half.

  python3.10 tools/pyffi-geometry-split.py <input.nif> [threshold] [shape_block] [shader_block]

  threshold     Y value; verts with Y >= threshold go to the new (high) shape. Default 8.25
  shape_block   block index of the source NiTriShape.        Default 23
  shader_block  block index of the source BSLightingShader.   Default 25
                (The source NiTriShapeData is assumed to be shape_block + 1.)

Notes / gotchas (learned the hard way):
- deepcopy of PyFFI structs corrupts the header string table — fresh-construct instead.
- bit-struct flags must be copied per-bit, not by assignment.
- EVERY per-vertex array (vertices, normals, tangents, bitangents, uv_sets) must be
  resized with update_size() or the write corrupts.
- bound sphere (center + radius) is mandatory on each new shape.
- LE (user_version_2=83) NIFs only. PyFFI cannot read SSE BSTriShape — use PyNifly.
"""
import os, sys, time, math
time.clock = time.perf_counter
from pyffi.formats.nif import NifFormat

if len(sys.argv) < 2:
    print(__doc__); sys.exit(1)

PATH = sys.argv[1]
THRESHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 8.25
SHAPE_BLOCK = int(sys.argv[3]) if len(sys.argv) > 3 else 23
SHADER_BLOCK = int(sys.argv[4]) if len(sys.argv) > 4 else 25
GEOM_BLOCK = SHAPE_BLOCK + 1
NEW_SHAPE_NAME = b"GlowPart"
EM_R, EM_G, EM_B, EM_MULT = 0.8, 0.98, 1.0, 30.0   # emissive colour + multiple for the high half


def copy_struct(src, dst):
    """Field-by-field copy for PyFFI structs, classified by type name."""
    for attr in src._get_attribute_list():
        name = attr.name
        sval = getattr(src, name)
        dval = getattr(dst, name)
        tn = type(sval).__name__
        if tn.startswith("SkyrimShaderPropertyFlags"):
            for battr in sval._get_attribute_list():
                setattr(dval, battr.name, getattr(sval, battr.name))
        elif tn == "TexCoord":
            dval.u = sval.u; dval.v = sval.v
        elif tn == "Color3":
            dval.r = sval.r; dval.g = sval.g; dval.b = sval.b
        elif tn == "Color4":
            dval.r = sval.r; dval.g = sval.g; dval.b = sval.b; dval.a = sval.a
        elif tn == "Vector3":
            dval.x = sval.x; dval.y = sval.y; dval.z = sval.z
        else:
            try:
                setattr(dst, name, sval)
            except AttributeError:
                pass


data = NifFormat.Data()
with open(PATH, "rb") as f:
    data.read(f)
root = data.blocks[0]
oshape, ogeom, oshader = data.blocks[SHAPE_BLOCK], data.blocks[GEOM_BLOCK], data.blocks[SHADER_BLOCK]

# Snapshot original geometry (we overwrite the source data block in place for the low half)
overts = [(v.x, v.y, v.z) for v in ogeom.vertices]
onorms = [(n.x, n.y, n.z) for n in ogeom.normals]
otans  = [(t.x, t.y, t.z) for t in ogeom.tangents]
obitans= [(t.x, t.y, t.z) for t in ogeom.bitangents]
ouvs   = [(t.u, t.v) for t in ogeom.uv_sets[0]]
otris  = [(t.v_1, t.v_2, t.v_3) for t in ogeom.triangles]
o_num_uv_sets = ogeom.num_uv_sets
o_has_uv = ogeom.has_uv
o_consistency = ogeom.consistency_flags
o_keep_flags = ogeom.keep_flags
o_compress_flags = ogeom.compress_flags
o_extra_vectors_flags = ogeom.extra_vectors_flags
o_unknown_int_2 = ogeom.unknown_int_2
print(f"orig: {len(overts)} verts, {len(otris)} tris, "
      f"tangents={len(otans)}, bitangents={len(obitans)}")

is_high = [y >= THRESHOLD for (x, y, z) in overts]
print(f"high verts={sum(is_high)}  low verts={len(overts)-sum(is_high)}")

# Classify triangles by majority of their 3 verts, build re-indexed groups
low_vmap, low_vidx, low_tris = {}, [], []
high_vmap, high_vidx, high_tris = {}, [], []
for (a, b, c) in otris:
    hcount = is_high[a] + is_high[b] + is_high[c]
    if hcount >= 2:
        vmap, vidx, tlist = high_vmap, high_vidx, high_tris
    else:
        vmap, vidx, tlist = low_vmap, low_vidx, low_tris
    for x in (a, b, c):
        if x not in vmap:
            vmap[x] = len(vidx)
            vidx.append(x)
    tlist.append((vmap[a], vmap[b], vmap[c]))
print(f"low:  {len(low_vidx)} verts, {len(low_tris)} tris")
print(f"high: {len(high_vidx)} verts, {len(high_tris)} tris")


def bounds(vidx):
    xs = [overts[i][0] for i in vidx]
    ys = [overts[i][1] for i in vidx]
    zs = [overts[i][2] for i in vidx]
    cx, cy, cz = sum(xs)/len(xs), sum(ys)/len(ys), sum(zs)/len(zs)
    r = max(math.sqrt((overts[i][0]-cx)**2 + (overts[i][1]-cy)**2 + (overts[i][2]-cz)**2)
            for i in vidx)
    return cx, cy, cz, r


def fill_geom(g, vidx, tlist):
    n = len(vidx)
    g.num_vertices = n
    g.bs_max_vertices = n
    g.keep_flags = o_keep_flags
    g.compress_flags = o_compress_flags
    g.extra_vectors_flags = o_extra_vectors_flags
    g.unknown_int_2 = o_unknown_int_2
    g.has_vertices = True
    g.vertices.update_size()
    for i, ov in enumerate(vidx):
        g.vertices[i].x, g.vertices[i].y, g.vertices[i].z = overts[ov]
    g.has_normals = True
    g.normals.update_size()
    for i, ov in enumerate(vidx):
        g.normals[i].x, g.normals[i].y, g.normals[i].z = onorms[ov]
    g.tangents.update_size()
    for i, ov in enumerate(vidx):
        g.tangents[i].x, g.tangents[i].y, g.tangents[i].z = otans[ov]
    g.bitangents.update_size()
    for i, ov in enumerate(vidx):
        g.bitangents[i].x, g.bitangents[i].y, g.bitangents[i].z = obitans[ov]
    g.num_uv_sets = o_num_uv_sets
    g.has_uv = o_has_uv
    g.uv_sets.update_size()
    for i, ov in enumerate(vidx):
        g.uv_sets[0][i].u, g.uv_sets[0][i].v = ouvs[ov]
    g.consistency_flags = o_consistency
    g.num_triangles = len(tlist)
    g.num_triangle_points = len(tlist) * 3
    g.has_triangles = True
    g.triangles.update_size()
    for i, tr in enumerate(tlist):
        g.triangles[i].v_1, g.triangles[i].v_2, g.triangles[i].v_3 = tr
    cx, cy, cz, r = bounds(vidx)
    g.center.x, g.center.y, g.center.z = cx, cy, cz
    g.radius = r


# --- Rewrite the source data block as the LOW half ---
fill_geom(ogeom, low_vidx, low_tris)
print(f"source data block rewritten as low half ({ogeom.num_vertices} verts)")

# --- New high-half NiTriShapeData ---
high_geom = NifFormat.NiTriShapeData()
fill_geom(high_geom, high_vidx, high_tris)
print(f"high geom built ({high_geom.num_vertices} verts)")

# --- New high-half shader: copy of source + bright emissive ---
high_shader = NifFormat.BSLightingShaderProperty()
copy_struct(oshader, high_shader)
high_shader.emissive_color.r = EM_R
high_shader.emissive_color.g = EM_G
high_shader.emissive_color.b = EM_B
high_shader.emissive_multiple = EM_MULT
print(f"high shader built (type={high_shader.skyrim_shader_type}, emissive_mult={high_shader.emissive_multiple})")

# --- New high-half NiTriShape ---
bs = NifFormat.NiTriShape()
bs.name = NEW_SHAPE_NAME
bs.flags = oshape.flags
bs.translation.x, bs.translation.y, bs.translation.z = oshape.translation.x, oshape.translation.y, oshape.translation.z
# rotation matrix
for r in (1, 2, 3):
    for c in (1, 2, 3):
        m = f"m_{r}{c}"
        setattr(bs.rotation, m, getattr(oshape.rotation, m))
bs.scale = oshape.scale
bs.data = high_geom
bs.bs_properties.update_size()
bs.bs_properties[0] = high_shader
print("high NiTriShape built + wired")

# --- Append the new shape to root children ---
n = root.num_children
root.num_children = n + 1
root.children.update_size()
root.children[n] = bs
print(f"root children: {n} -> {root.num_children}")

# Overwrites the input, so the new bytes go to a temporary file first: PyFFI raising mid-write must not leave it truncated.
partial = PATH + ".partial"
with open(partial, "wb") as f:
    data.write(f)
os.replace(partial, PATH)
print("written.")

# Verify readback (note: cross-validate with PyNifly before any in-game test)
d2 = NifFormat.Data()
with open(PATH, "rb") as f:
    d2.read(f)
print(f"READBACK: {len(d2.blocks)} blocks, root.num_children={d2.blocks[0].num_children}")

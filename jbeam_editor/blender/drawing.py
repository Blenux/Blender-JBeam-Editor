# Copyright (c) 2023 BeamNG GmbH, Angelo Matteo
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import bpy
import blf
import bmesh

from blf import position as blfpos   #import the function can improve the performance
from blf import size as blfsize
from blf import draw as blfdraw
from blf import color as blfcolor

from bpy_extras.view3d_utils import location_3d_to_region_2d

from ..core import constants
from . import state

if not constants.UNIT_TESTING:
    import gpu
    from gpu_extras.batch import batch_for_shader


beam_render_width = 3.0
beam_render_shader = None
beam_render_batch = None
coords = []


# Draws a 3D text at each vertex position of their assigned node ID
def draw_callback_px(context: bpy.types.Context):
    scene = context.scene
    ui_props = scene.ui_properties
    if not hasattr(ui_props, 'toggle_node_ids_text'):
        return
    font_id = 0

    active_obj = context.active_object
    if active_obj is None:
        return
    active_obj_data = active_obj.data
    if active_obj_data.get(constants.MESH_JBEAM_PART) is None or not active_obj_data[constants.MESH_EDITING_ENABLED]:
        return

    collection = active_obj.users_collection[0]
    if collection is not None and collection.get(constants.COLLECTION_VEHICLE_MODEL) is not None:
        state.part_name_to_obj.clear()
        for obj in collection.all_objects:
            state.part_name_to_obj[obj.data[constants.MESH_JBEAM_PART]] = obj

        obj = scene.objects.get(collection[constants.COLLECTION_MAIN_PART])
        if obj is None:
            return

        obj_data = obj.data

        bm = None
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj_data)
        else:
            bm = bmesh.new()
            bm.from_mesh(obj_data)

        node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID]
        part_origin_layer = bm.verts.layers.string[constants.VL_NODE_PART_ORIGIN]
        is_fake_layer = bm.verts.layers.int[constants.VL_NODE_IS_FAKE]

        toggleNodeText = ui_props.toggle_node_ids_text
        ctxRegion = context.region
        ctxRegionData = context.region_data
        lblfPosition = blfpos
        lblfDraw = blfdraw
        blfsize(font_id, 12) # dpi value defaults to 72 when omitted, and no longer usable from 4.0+ (only 2 parameters allowed).
        blfcolor(font_id, 1, 1, 1, 1)

        for v in bm.verts:
            if v[is_fake_layer] == 1:
                continue

            coord = obj.matrix_world @ v.co
            node_id = v[node_id_layer].decode('utf-8')
            part_origin = v[part_origin_layer].decode('utf-8')

            if not state.part_name_to_obj[part_origin].visible_get():
                continue

            pos_text = location_3d_to_region_2d(ctxRegion, ctxRegionData, coord)
            if pos_text and toggleNodeText:
                lblfPosition(font_id, pos_text[0], pos_text[1], 0)

                #blf.draw(font_id, str(node_id) + " (" + str(v.index) + ")")
                lblfDraw(font_id, node_id)

        bm.free()

    else:
        if active_obj.visible_get():
            bm = None
            if active_obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(active_obj_data)
            else:
                bm = bmesh.new()
                bm.from_mesh(active_obj_data)

            node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID]
            is_fake_layer = bm.verts.layers.int[constants.VL_NODE_IS_FAKE]

            for v in bm.verts:
                if v[is_fake_layer] == 1:
                    continue
                coord = active_obj.matrix_world @ v.co
                node_id = v[node_id_layer].decode('utf-8')

                pos_text = location_3d_to_region_2d(context.region, context.region_data, coord)
                if pos_text and ui_props.toggle_node_ids_text:
                    blf.position(font_id, pos_text[0], pos_text[1], 0)
                    blf.size(font_id, 12) # dpi value defaults to 72 when omitted, and no longer usable from 4.0+ (only 2 parameters allowed).
                    blf.color(font_id, 1, 1, 1, 1)
                    #blf.draw(font_id, str(node_id) + " (" + str(v.index) + ")")
                    blf.draw(font_id, str(node_id))

            bm.free()


def draw_callback_view(context: bpy.types.Context):
    global beam_render_shader
    global beam_render_batch

    if beam_render_shader is None:
        beam_render_shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    if state.veh_render_dirty:
        coords.clear()

        scene = context.scene
        active_obj = context.active_object
        if active_obj is None:
            beam_render_batch = None
            state.veh_render_dirty = False
            return
        active_obj_data = active_obj.data
        if active_obj_data.get(constants.MESH_JBEAM_PART) is None or not active_obj_data[constants.MESH_EDITING_ENABLED]:
            beam_render_batch = None
            state.veh_render_dirty = False
            return

        collection = active_obj.users_collection[0]
        if collection is not None and collection.get(constants.COLLECTION_VEHICLE_MODEL) is not None:
            for obj in collection.all_objects:
                if obj.visible_get():
                    obj_data = obj.data
                    bm = None
                    if obj.mode == 'EDIT':
                        bm = bmesh.from_edit_mesh(obj_data)
                    else:
                        bm = bmesh.new()
                        bm.from_mesh(obj_data)

                    beam_indices_layer = bm.edges.layers.string[constants.EL_BEAM_INDICES]

                    e: bmesh.types.BMEdge
                    for e in bm.edges:
                        if e[beam_indices_layer].decode('utf-8') == '':
                            continue

                        v1, v2 = e.verts[0], e.verts[1]
                        coords.append(obj.matrix_world @ v1.co)
                        coords.append(obj.matrix_world @ v2.co)

                    bm.free()

            beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": coords})

        else:
            if active_obj.visible_get():
                bm = None
                if active_obj.mode == 'EDIT':
                    bm = bmesh.from_edit_mesh(active_obj_data)
                else:
                    bm = bmesh.new()
                    bm.from_mesh(active_obj_data)

                beam_indices_layer = bm.edges.layers.string[constants.EL_BEAM_INDICES]

                e: bmesh.types.BMEdge
                for e in bm.edges:
                    if e[beam_indices_layer].decode('utf-8') == '':
                        continue

                    v1, v2 = e.verts[0], e.verts[1]
                    coords.append(active_obj.matrix_world @ v1.co)
                    coords.append(active_obj.matrix_world @ v2.co)

                bm.free()

                beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": coords})
            else:
                beam_render_batch = None
                state.veh_render_dirty = False
        state.veh_render_dirty = False

    if beam_render_batch is not None:
        beam_render_shader.uniform_float("color", (0, 1, 0, 1))

        gpu.state.line_width_set(beam_render_width)
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        beam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False)
        gpu.state.line_width_set(1.0)

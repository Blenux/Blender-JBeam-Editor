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
import bmesh
import uuid
from bpy.app.handlers import persistent

from ..core import constants
from .. import text_editor
from .. import export_vehicle
from .. import export_jbeam
from . import state
from . import drawing


# https://blenderartists.org/t/make-latest-created-collection-active/1350762/5
def find_layer_collection_recursive(find, col):
    for c in col.children:
        if c.collection == find:
            return c
    return None


def _depsgraph_callback(context: bpy.types.Context, scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    return_early = False

    state.selected_nodes.clear()
    state.selected_beams.clear()
    state.selected_tris_quads.clear()

    reimporting_jbeam = False

    # Don't act on reimporting mesh
    if type(scene.get('jbeam_editor_reimporting_jbeam')) == int:
        scene['jbeam_editor_reimporting_jbeam'] -= 1

        if scene['jbeam_editor_reimporting_jbeam'] < 0:
            scene['jbeam_editor_reimporting_jbeam'] = 0
        else:
            reimporting_jbeam = True

        if constants.DEBUG:
            print('_depsgraph_callback: jbeam_editor_reimporting_jbeam')

    ui_props = scene.ui_properties

    active_obj = context.active_object
    if active_obj is None:
        return
    active_obj_data = active_obj.data
    if active_obj_data.get(constants.MESH_JBEAM_PART) is None or not active_obj_data[constants.MESH_EDITING_ENABLED]:
        return

    active_obj_eval: bpy.types.Object = active_obj.evaluated_get(depsgraph)

    # Show selected jbeam part's JBeam file in text editor
    jbeam_filepath = active_obj_data[constants.MESH_JBEAM_FILE_PATH]
    text_editor.show_int_file(jbeam_filepath)

    if not reimporting_jbeam:
        for update in depsgraph.updates:
            if update.id == active_obj_eval:
                #print('update.is_updated_geometry', update.is_updated_geometry, 'update.is_updated_shading', update.is_updated_shading, 'update.is_updated_transform', update.is_updated_transform)
                if update.id == active_obj_eval and (update.is_updated_geometry or update.is_updated_transform):
                    if constants.DEBUG:
                        print('_depsgraph_callback: updated_geometry')
                    state._do_export = True

    veh_model = active_obj_data.get(constants.MESH_VEHICLE_MODEL)
    if veh_model is not None:
        veh_collection = bpy.data.collections.get(veh_model)
        if veh_collection is not None:
            # Set vehicle collection as active collection
            layer = find_layer_collection_recursive(veh_collection, context.view_layer.layer_collection)
            if layer is not None:
                context.view_layer.active_layer_collection = layer
                scene['jbeam_editor_veh_collection_selected'] = veh_collection


    if active_obj.mode != 'EDIT':
        return

    bm = bmesh.from_edit_mesh(active_obj_data)

    # Check if new vertices are added
    init_node_id_layer = bm.verts.layers.string[constants.VL_INIT_NODE_ID]
    node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID]
    is_fake_layer = bm.verts.layers.int[constants.VL_NODE_IS_FAKE]

    # When new vertices are added, they seem to copy the data of the old vertices they were made from,
    # so rename their node ids to random ids (UUID)
    bm.verts.ensure_lookup_table()
    v: bmesh.types.BMVert
    for i,v in enumerate(bm.verts):
        if v[is_fake_layer]:
            continue
        if v.select:
            state.selected_nodes.append((v, v[init_node_id_layer].decode('utf-8')))

            # Do batch node renaming
            if state.batch_node_renaming_enabled:
                new_node_id: str = ui_props.batch_node_renaming_naming_scheme
                new_node_id = new_node_id.replace('#', f'{ui_props.batch_node_renaming_node_idx}')
                v[node_id_layer] = bytes(new_node_id, 'utf-8')
                ui_props.batch_node_renaming_node_idx += 1

                state._force_do_export = True

        if i >= active_obj_data[constants.MESH_VERTEX_COUNT]:
            new_node_id = str(uuid.uuid4())
            new_node_id_bytes = bytes(new_node_id, 'utf-8')
            v[init_node_id_layer] = new_node_id_bytes
            v[node_id_layer] = new_node_id_bytes
            active_obj_data[constants.MESH_VERTEX_COUNT] += 1

            if constants.DEBUG:
                print('new vertex added', new_node_id)

    # Check if new edges are added
    beam_indices_layer = bm.edges.layers.string[constants.EL_BEAM_INDICES]

    bm.edges.ensure_lookup_table()
    for i,e in enumerate(bm.edges):
        beam_indices = e[beam_indices_layer].decode('utf-8')
        if i >= active_obj_data[constants.MESH_EDGE_COUNT]:
            e[beam_indices_layer] = bytes('-1', 'utf-8')
            active_obj_data[constants.MESH_EDGE_COUNT] += 1

            if constants.DEBUG:
                print('new edge added', i)
        if beam_indices == '':
            continue
        if e.select:
            state.selected_beams.append((e, beam_indices))
            #print(e[beam_indices_layer].decode('utf-8'))

    # Check if new faces are added
    face_idx_layer = bm.faces.layers.int[constants.FL_FACE_IDX]

    bm.faces.ensure_lookup_table()
    for i,f in enumerate(bm.faces):
        if f.select:
            state.selected_tris_quads.append((f, f[face_idx_layer]))
        if i >= active_obj_data[constants.MESH_FACE_COUNT]:
            f[face_idx_layer] = -1
            active_obj_data[constants.MESH_FACE_COUNT] += 1

            if constants.DEBUG:
                print('new face added', i)

    # If one vertex is selected, set the UI input node_id field to the selected vertex's node_id attribute
    if len(state.selected_nodes) == 1:
        v = state.selected_nodes[0][0]
        node_id = v[node_id_layer].decode('utf-8')
        state.rename_enabled = False

        ui_props.input_node_id = node_id

    bm.free()


@persistent
def depsgraph_callback(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    context = bpy.context

    if constants.DEBUG:
        print('depsgraph_callback')

    _depsgraph_callback(context, scene, depsgraph)
    state.refresh_curr_vdata()


# If active file in text editor changed, reimport jbeam file/vehicle
@persistent
def check_files_for_changes():
    context = bpy.context

    changed = text_editor.check_open_int_file_for_changes(context)
    if changed:
        state.refresh_curr_vdata(True)

    return state.check_file_interval


op_no_export = {
    'OBJECT_OT_editmode_toggle',
    'jbeam_editor.batch_node_renaming',
}
_last_op = None

@persistent
def poll_active_operators():
    global _last_op
    context = bpy.context
    op = context.active_operator
    #print(op)
    active_obj = context.active_object
    if active_obj is not None:
        active_obj_data = active_obj.data
        if active_obj_data.get(constants.MESH_JBEAM_PART) is not None and active_obj_data[constants.MESH_EDITING_ENABLED]:
            # Trigger export JBeam/Vehicle on current operator finishing
            if state._force_do_export or (state._do_export and op is not None and op != _last_op and all(x != op.bl_idname for x in op_no_export)):
                veh_model = active_obj_data.get(constants.MESH_VEHICLE_MODEL)
                if veh_model is not None:
                    # Export
                    export_vehicle.auto_export(active_obj, veh_model)
                else:
                    # Export
                    export_jbeam.auto_export(active_obj)

                state.refresh_curr_vdata(True)

                state._do_export = False
                state._force_do_export = False

    _last_op = op

    return state.poll_active_ops_interval


@persistent
def on_post_register():
    # this will happen 0.1 seconds after addon registration completes.
    #print(bpy.context.view_layer)
    state.draw_handle = bpy.types.SpaceView3D.draw_handler_add(drawing.draw_callback_px, (bpy.context,), 'WINDOW', 'POST_PIXEL')

    if not constants.UNIT_TESTING:
        state.draw_handle2 = bpy.types.SpaceView3D.draw_handler_add(drawing.draw_callback_view, (bpy.context,), 'WINDOW', 'POST_VIEW')

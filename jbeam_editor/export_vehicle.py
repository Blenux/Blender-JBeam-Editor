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

import base64
import traceback
import pickle

from mathutils import Vector
import bpy

import bmesh

from . import constants
from . import text_editor
from . import export_utils
from . import globals as jb_globals # Import globals

import timeit


def export(veh_collection: bpy.types.Collection, active_obj: bpy.types.Object):
    try:
        t0 = timeit.default_timer()
        context = bpy.context
        scene = context.scene
        ui_props = scene.ui_properties
        affect_node_references = ui_props.affect_node_references

        # Check if the local rename toggle should override the global affect_node_references
        if jb_globals._use_local_rename_toggle_for_next_export:
            affect_node_references = ui_props.rename_selected_node_references
            # This flag will be reset in end_export_cycle()
            # jb_globals._use_local_rename_toggle_for_next_export = False # Reset after use

        veh_bundle = pickle.loads(base64.b64decode(veh_collection[constants.COLLECTION_VEHICLE_BUNDLE]))
        vdata = veh_bundle['vdata']
        init_nodes_data = vdata.get('nodes')

        active_obj_data = active_obj.data
        active_jbeam_part = active_obj_data[constants.MESH_JBEAM_PART]

        blender_nodes = {}
        parts_nodes_actions = {}

        # If in Edit Mode, we only care about the active object's changes (standard behavior)
        if active_obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(active_obj_data)
            blender_nodes, parts_nodes_actions = export_utils.get_nodes_add_delete_rename(active_obj, bm, active_jbeam_part, init_nodes_data, affect_node_references)
            # Note: bmesh from edit mesh should not be freed
        else:
            # In Object Mode, multiple objects might have been transformed.
            # Process all JBeam objects in the collection to ensure their positions are synced to the JBeam files.
            for obj_iter in veh_collection.all_objects:
                obj_iter_data = obj_iter.data
                if obj_iter_data and obj_iter_data.get(constants.MESH_JBEAM_PART) is not None:
                    part_name = obj_iter_data[constants.MESH_JBEAM_PART]
                    bm = bmesh.new()
                    bm.from_mesh(obj_iter_data)

                    part_blender_nodes, part_actions_map = export_utils.get_nodes_add_delete_rename(obj_iter, bm, part_name, init_nodes_data, affect_node_references)
                    blender_nodes.update(part_blender_nodes)

                    # Merge part_actions_map into parts_nodes_actions
                    for p_key, p_actions in part_actions_map.items():
                        if p_key not in parts_nodes_actions:
                            parts_nodes_actions[p_key] = p_actions
                        else:
                            dest = parts_nodes_actions[p_key]
                            dest.nodes_to_add.update(p_actions.nodes_to_add)
                            dest.nodes_to_delete.update(p_actions.nodes_to_delete)
                            dest.nodes_to_rename.update(p_actions.nodes_to_rename)
                            dest.nodes_to_move.update(p_actions.nodes_to_move)
                            dest.nodes_to_add_symmetrically.update(p_actions.nodes_to_add_symmetrically)

                    bm.free()

        parts_to_update = set(parts_nodes_actions.keys())

        jbeam_files_to_jbeam_part_objs = {}
        jbeam_files_to_jbeam_parts = {}
        obj: bpy.types.Object
        for obj in veh_collection.all_objects[:]:
            obj_data = obj.data
            jbeam_filepath = obj_data[constants.MESH_JBEAM_FILE_PATH]
            jbeam_part = obj_data[constants.MESH_JBEAM_PART]

            if jbeam_filepath not in jbeam_files_to_jbeam_part_objs:
                jbeam_files_to_jbeam_part_objs[jbeam_filepath] = []
                jbeam_files_to_jbeam_parts[jbeam_filepath] = set()
            jbeam_files_to_jbeam_part_objs[jbeam_filepath].append(obj)
            jbeam_files_to_jbeam_parts[jbeam_filepath].add(jbeam_part)

        filepaths = []
        reimport_needed = False

        for jbeam_filepath, objs in jbeam_files_to_jbeam_part_objs.items():
            jbeam_file_parts = jbeam_files_to_jbeam_parts[jbeam_filepath]

            if True in parts_to_update or any(x in parts_to_update for x in jbeam_file_parts):
                reimport_needed |= export_utils.export_file(jbeam_filepath, objs, vdata, blender_nodes, parts_nodes_actions, affect_node_references, parts_to_update)
                filepaths.append(jbeam_filepath)

        text_editor.check_int_files_for_changes(context, filepaths, regenerate_mesh=reimport_needed)

        # Make sure node positions are all synced if not reimporting
        if not reimport_needed:
            nodes_to_move = {}
            for jbeam_part, part_node_actions in parts_nodes_actions.items():
                nodes_to_move.update(part_node_actions.nodes_to_move)

            obj: bpy.types.Object
            for obj in veh_collection.all_objects[:]:
                obj_data = obj.data
                jbeam_filepath = obj.data[constants.MESH_JBEAM_FILE_PATH]
                jbeam_part = obj.data[constants.MESH_JBEAM_PART]

                if obj.mode == 'EDIT':
                    bm = bmesh.from_edit_mesh(obj_data)
                else:
                    bm = bmesh.new()
                    bm.from_mesh(obj_data)

                inv_matrix_world = obj.matrix_world.inverted()

                node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID]
                v: bmesh.types.BMVert
                for v in bm.verts:
                    node_id = v[node_id_layer].decode('utf-8')
                    if node_id in nodes_to_move:
                        v.co = nodes_to_move[node_id]
                        v.co = inv_matrix_world @ Vector(nodes_to_move[node_id])

                if obj.mode == 'EDIT':
                    bmesh.update_edit_mesh(obj_data)
                else:
                    bm.to_mesh(obj_data)
                
                if obj.mode != 'EDIT':
                    bm.free()

        bpy.ops.object.location_clear()

        t1 = timeit.default_timer()
        print('Exporting/reimporting Time', round(t1 - t0, 2), 's')

    except:
        traceback.print_exc()
    finally:
        export_utils.end_export_cycle() # Ensure cleanup happens


def auto_export(obj: bpy.types.Object, veh_model: str):
    collection = bpy.data.collections.get(veh_model)
    if collection is None:
        return
    export(collection, obj)


# class JBEAM_EDITOR_OT_export_vehicle(Operator):
#     bl_idname = 'jbeam_editor.export_vehicle'
#     bl_label = "Export Vehicle"
#     bl_description = 'Export BeamNG vehicle'

#     @classmethod
#     def poll(cls, context):
#         for obj in context.selected_objects:
#             obj_data = obj.data
#             if obj_data.get(constants.MESH_JBEAM_PART) is None:
#                 return False
#         return True

#     def execute(self, context):
#         jbeam_filepaths = set()

#         for obj in context.selectable_objects:
#             obj_data = obj.data
#             if obj_data.get(constants.MESH_JBEAM_PART) is None:
#                 continue
#             jbeam_filepaths.add(obj_data.get(constants.MESH_JBEAM_FILE_PATH))

#         for filepath in jbeam_filepaths:
#             export_utils.export_file_to_disk(filepath)

#         #export(veh_collection, context.selected_objects)

#         # import cProfile, pstats, io
#         # import pstats
#         # pr = cProfile.Profile()
#         # with cProfile.Profile() as pr:
#         #     manual_export(veh_collection, context.selected_objects)
#         #     stats = pstats.Stats(pr)
#         #     stats.strip_dirs().sort_stats('tottime').print_stats()

#         return {'FINISHED'}

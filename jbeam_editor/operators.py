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
import sys

# Import from local modules
from . import constants
from . import text_editor
from . import globals as jb_globals # Import globals
from .drawing import refresh_curr_vdata, find_node_line_number, find_beam_line_number, _scroll_editor_to_line # Import drawing functions

# Force JBeam Sync
class JBEAM_EDITOR_OT_force_jbeam_sync(bpy.types.Operator):
    bl_idname = "jbeam_editor.force_jbeam_sync"
    bl_label = "Force JBeam Sync"
    bl_description = "Manually syncs JBeam file with the mesh. Use it when the JBeam file doesn't get updated after a JBeam mesh operation (e.g. transforming a vertex with the input boxes above)"

    def invoke(self, context, event):
        print('Force JBeam Sync!')
        jb_globals._force_do_export = True
        return {'FINISHED'}

# Undo action
class JBEAM_EDITOR_OT_undo(bpy.types.Operator):
    bl_idname = "jbeam_editor.undo"
    bl_label = "Undo"

    def invoke(self, context, event):
        print('undoing!')
        text_editor.on_undo_redo(context, True)
        refresh_curr_vdata(True)
        return {'FINISHED'}

# Redo action
class JBEAM_EDITOR_OT_redo(bpy.types.Operator):
    bl_idname = "jbeam_editor.redo"
    bl_label = "Redo"

    def invoke(self, context, event):
        print('redoing!')
        text_editor.on_undo_redo(context, False)
        refresh_curr_vdata(True)
        return {'FINISHED'}

# Add JBeam beam/triangle/quad
class JBEAM_EDITOR_OT_add_beam_tri_quad(bpy.types.Operator):
    bl_idname = "jbeam_editor.add_beam_tri_quad"
    bl_label = "Add Beam/Triangle/Quad"

    @classmethod
    def poll(cls, context):
        # Check active object validity AND editing enabled
        obj = context.active_object
        if not obj or obj.data.get(constants.MESH_JBEAM_PART) is None or not obj.data.get(constants.MESH_EDITING_ENABLED, False):
            return False
        return len(jb_globals.selected_nodes) in (2,3,4)

    def invoke(self, context, event):
        obj = context.active_object
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        init_node_id_layer = bm.verts.layers.string[constants.VL_INIT_NODE_ID]
        is_fake_layer = bm.verts.layers.int[constants.VL_NODE_IS_FAKE]
        # Ensure lookup table for index access
        bm.verts.ensure_lookup_table()

        export = False

        len_selected_verts = len(jb_globals.selected_nodes)

        new_verts = []
        # Iterate through indices and node IDs
        for vert_index, node_id in jb_globals.selected_nodes:
            # Get the vertex from the current bmesh using the index
            v = bm.verts[vert_index]
            new_verts.append(v) # Use the original vertex

        if len_selected_verts == 2:
            beam_indices_layer = bm.edges.layers.string[constants.EL_BEAM_INDICES]
            beam_part_origin_layer = bm.edges.layers.string[constants.EL_BEAM_PART_ORIGIN] # Get origin layer
            # Check if edge already exists
            existing_edge = bm.edges.get(new_verts)
            if existing_edge is None:
                e = bm.edges.new(new_verts)
                e[beam_indices_layer] = bytes('-1', 'utf-8')
                # Assign part origin based on the active object's part
                e[beam_part_origin_layer] = bytes(obj_data[constants.MESH_JBEAM_PART], 'utf-8')
                export = True
            else:
                # If edge exists but isn't a JBeam beam yet, mark it as new
                if existing_edge[beam_indices_layer].decode('utf-8') == '':
                    existing_edge[beam_indices_layer] = bytes('-1', 'utf-8')
                    # Assign part origin based on the active object's part
                    existing_edge[beam_part_origin_layer] = bytes(obj_data[constants.MESH_JBEAM_PART], 'utf-8')
                    export = True
                else:
                    # Edge already exists and is a JBeam beam
                    self.report({'INFO'}, "Beam already exists between selected nodes.")


        elif len_selected_verts in (3,4):
            face_idx_layer = bm.faces.layers.int[constants.FL_FACE_IDX]
            face_part_origin_layer = bm.faces.layers.string[constants.FL_FACE_PART_ORIGIN] # Get origin layer
            try:
                f = bm.faces.new(new_verts)
                f[face_idx_layer] = -1
                # Assign part origin based on the active object's part
                f[face_part_origin_layer] = bytes(obj_data[constants.MESH_JBEAM_PART], 'utf-8')
                export = True
            except ValueError:
                # Face already exists or vertices are not suitable for a face
                self.report({'INFO'}, "Face already exists or cannot be created with selected nodes.")


        # Update the edit mesh if in edit mode
        if obj.mode == 'EDIT':
            bmesh.update_edit_mesh(obj_data)
        # No need to free bm from edit mesh

        if export:
            jb_globals._force_do_export = True

        return {'FINISHED'}

# Flip JBeam faces
class JBEAM_EDITOR_OT_flip_jbeam_faces(bpy.types.Operator):
    bl_idname = "jbeam_editor.flip_jbeam_faces"
    bl_label = "Flip Face(s)"

    @classmethod
    def poll(cls, context):
        # Check active object validity AND editing enabled
        obj = context.active_object
        if not obj or obj.data.get(constants.MESH_JBEAM_PART) is None or not obj.data.get(constants.MESH_EDITING_ENABLED, False):
            return False
        return len(jb_globals.selected_tris_quads) > 0

    def invoke(self, context, event):
        obj = context.active_object
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        face_flip_flag_layer = bm.faces.layers.int[constants.FL_FACE_FLIP_FLAG]

        face: bmesh.types.BMFace
        face_idx: int
        for (face, face_idx) in jb_globals.selected_tris_quads:
            # Toggle the flip flag instead of just setting to 1
            current_flag = face[face_flip_flag_layer]
            face[face_flip_flag_layer] = 1 - current_flag # Toggle 0 to 1 and 1 to 0
            face.normal_flip() # Also flip the Blender face normal for visual consistency

        # Update mesh after flipping normals
        bmesh.update_edit_mesh(obj_data)
        # No need to free bm from edit mesh

        jb_globals._force_do_export = True

        return {'FINISHED'}

# Batch node renaming
class JBEAM_EDITOR_OT_batch_node_renaming(bpy.types.Operator):
    bl_idname = "jbeam_editor.batch_node_renaming"
    bl_label = "Batch Node Renaming"
    bl_description = "After clicking \"Start\", clicking a node will rename it. Press \"Stop\" when done"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not obj:
            return False
        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return False
        # Check active object validity AND editing enabled
        if obj_data.get(constants.MESH_JBEAM_PART) is None or not obj_data.get(constants.MESH_EDITING_ENABLED, False):
            return False
        if obj.mode != 'EDIT':
            return False
        return True

    def invoke(self, context, event):
        scene = context.scene
        ui_props = scene.ui_properties

        jb_globals.batch_node_renaming_enabled = not jb_globals.batch_node_renaming_enabled
        if not jb_globals.batch_node_renaming_enabled:
            ui_props.batch_node_renaming_node_idx = 1
        return {'FINISHED'}

# Node Search Operator
class JBEAM_EDITOR_OT_find_node(bpy.types.Operator):
    bl_idname = "jbeam_editor.find_node"
    bl_label = "Find Node"
    bl_description = "Find and select the specified node ID in the active object (Vertex Mode only)"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        # Check if active object is valid JBeam, in Edit mode, editing is enabled, AND in Vertex select mode
        return (obj and obj.mode == 'EDIT' and
                obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and
                obj.data.get(constants.MESH_EDITING_ENABLED, False) and
                context.tool_settings.mesh_select_mode[0]) # Check vertex select mode (index 0)

    def execute(self, context):
        # Double-check vertex mode in execute for robustness
        if not context.tool_settings.mesh_select_mode[0]:
            self.report({'WARNING'}, "Node search requires Vertex selection mode.")
            return {'CANCELLED'}

        scene = context.scene
        ui_props = scene.ui_properties
        search_id = ui_props.search_node_id.strip() # Get search term from UI property

        if not search_id:
            self.report({'WARNING'}, "Please enter a Node ID to search for.")
            return {'CANCELLED'}

        obj = context.active_object
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)

        node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
        is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)

        if not node_id_layer or not is_fake_layer:
            self.report({'ERROR'}, "JBeam node layers not found on mesh.")
            # No need to free bm from edit mesh
            return {'CANCELLED'}

        bm.verts.ensure_lookup_table()
        found_vert = None

        for v in bm.verts:
            if v[is_fake_layer] == 0: # Only check real nodes
                node_id = v[node_id_layer].decode('utf-8')
                if node_id == search_id:
                    found_vert = v
                    break # Stop searching once found

        if found_vert:
            # Deselect all vertices first
            for v_deselect in bm.verts:
                v_deselect.select = False
            # Select the found vertex
            found_vert.select = True
            # Make the found vertex the active one (important for view_selected)
            bm.select_history.add(found_vert)
            bm.select_flush_mode() # Ensure selection updates

            # Update the mesh from the bmesh
            bmesh.update_edit_mesh(obj_data)

            # Center view on selection
            bpy.ops.view3d.view_selected(use_all_regions=False)

            self.report({'INFO'}, f"Node '{search_id}' found and selected.")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, f"Node ID '{search_id}' not found in this object.")
            # No need to free bm from edit mesh
            return {'CANCELLED'}

# Operator to scroll to definition
class JBEAM_EDITOR_OT_scroll_to_definition(bpy.types.Operator):
    bl_idname = "jbeam_editor.scroll_to_definition"
    bl_label = "Scroll to Definition"
    bl_description = "Scroll the Text Editor to the definition of the selected node or beam"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        # Check if active object is valid JBeam, in Edit mode, editing is enabled
        if not (obj and obj.mode == 'EDIT' and
                obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and
                obj.data.get(constants.MESH_EDITING_ENABLED, False)):
            return False
        # Check if exactly one node OR one beam is selected
        return len(jb_globals.selected_nodes) == 1 or len(jb_globals.selected_beams) == 1

    def execute(self, context):
        obj = context.active_object
        obj_data = obj.data
        jbeam_filepath = obj_data.get(constants.MESH_JBEAM_FILE_PATH)

        if not jbeam_filepath:
            self.report({'WARNING'}, "JBeam file path not found for this object.")
            return {'CANCELLED'}

        bm = None
        try:
            bm = bmesh.from_edit_mesh(obj_data)
            bm.edges.ensure_lookup_table()
        except Exception as e:
            self.report({'ERROR'}, f"Error accessing mesh data: {e}")
            return {'CANCELLED'}

        line_num = None
        target_element_type = None

        # Check Node Selection
        if len(jb_globals.selected_nodes) == 1:
            target_element_type = "Node"
            vert_index, node_id = jb_globals.selected_nodes[0]
            try:
                bm.verts.ensure_lookup_table()
                node_part_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN)
                if node_part_origin_layer:
                    target_part_origin = bm.verts[vert_index][node_part_origin_layer].decode('utf-8')
                    if target_part_origin:
                        line_num = find_node_line_number(jbeam_filepath, target_part_origin, node_id)
                else:
                     self.report({'WARNING'}, "Node part origin layer not found.")
            except (IndexError, KeyError) as e:
                self.report({'ERROR'}, f"Error accessing node data: {e}")
                return {'CANCELLED'}

        # Check Beam Selection
        elif len(jb_globals.selected_beams) == 1:
            target_element_type = "Beam"
            edge_index, beam_indices_str = jb_globals.selected_beams[0]
            try:
                e = bm.edges[edge_index] # Get the BMEdge using the index
            except IndexError:
                self.report({'ERROR'}, f"Selected beam edge index {edge_index} is invalid.")
                return {'CANCELLED'}

            beam_indices = beam_indices_str.split(',')
            if beam_indices:
                try:
                    # Get node IDs for line finding
                    v1 = e.verts[0]
                    v2 = e.verts[1]
                    init_node_id_layer = bm.verts.layers.string.get(constants.VL_INIT_NODE_ID)
                    if not init_node_id_layer:
                        self.report({'ERROR'}, "Initial node ID layer not found.")
                        return {'CANCELLED'}
                    node_id1 = v1[init_node_id_layer].decode('utf-8')
                    node_id2 = v2[init_node_id_layer].decode('utf-8')

                    beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
                    if beam_part_origin_layer:
                        target_part_origin = e[beam_part_origin_layer].decode('utf-8')
                        if target_part_origin:
                            line_num = find_beam_line_number(jbeam_filepath, target_part_origin, node_id1, node_id2)
                    else:
                        self.report({'WARNING'}, "Beam part origin layer not found.")
                except (ValueError, KeyError, ReferenceError) as e:
                    if isinstance(e, ReferenceError):
                         self.report({'ERROR'}, f"Error accessing beam data: BMesh data removed. Try re-selecting the beam.")
                    else:
                         self.report({'ERROR'}, f"Error accessing beam data: {e}")
                    return {'CANCELLED'}

        # Perform Scrolling
        if line_num is not None:
            scrolled = _scroll_editor_to_line(context, jbeam_filepath, line_num)
            if scrolled:
                self.report({'INFO'}, f"Scrolled to {target_element_type} definition (Line {line_num}).")
            else:
                self.report({'WARNING'}, f"Could not find an open Text Editor for {jbeam_filepath}.")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, f"{target_element_type} definition not found in JBeam file.")
            return {'CANCELLED'}

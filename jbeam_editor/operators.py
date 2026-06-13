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
import traceback # <<< ADDED: Import traceback

# Import from local modules
from . import constants
from . import text_editor
from . import globals as jb_globals # Import globals
from .drawing import refresh_curr_vdata, find_node_line_number, find_beam_line_number, _scroll_editor_to_line # Import drawing functions
from .text_editor import _to_short_filename # <<< ADDED: Import helper
# <<< ADDED: Import utils for show_message_box if needed, or use self.report >>>
from . import utils

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

        # <<< ADDED: Ensure lookup table for index access >>>
        bm.faces.ensure_lookup_table()

        # <<< CHANGE: Iterate through stored indices >>>
        for (face_index, face_idx_in_part) in jb_globals.selected_tris_quads:
            try:
                # <<< ADDED: Retrieve the BMFace using the index from the current bmesh >>>
                face = bm.faces[face_index]
            except IndexError:
                # Handle case where the index might be invalid (shouldn't happen often)
                print(f"Warning: Could not find face with index {face_index}. Skipping flip.", file=sys.stderr)
                continue
            except ReferenceError:
                 # Handle case where the face might *still* be invalid somehow
                 print(f"Warning: Face with index {face_index} became invalid before flipping. Skipping.", file=sys.stderr)
                 continue

            # Toggle the flip flag using the retrieved face
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

# <<< START REFACTORED NODE SEARCH LOGIC >>>
# Helper function for node finding logic
def _find_and_select_node_id_logic(context: bpy.types.Context, search_id: str, report_func=None):
    """
    Finds and selects a node by ID in the active object.
    Returns True on success, False on failure.
    Uses report_func (like operator.report) for feedback.
    """
    if report_func is None: # Default reporter if none provided
        report_func = lambda type, msg: print(f"{list(type)[0]}: {msg}") # Simple print fallback

    # --- Context Checks ---
    obj = context.active_object
    if not obj or obj.mode != 'EDIT':
        # Don't report if just called from property update without context
        # report_func({'WARNING'}, "Node search requires Edit Mode.")
        return False
    if not context.tool_settings.mesh_select_mode[0]:
        report_func({'WARNING'}, "Node search requires Vertex selection mode.")
        return False
    obj_data = obj.data
    if not obj_data or obj_data.get(constants.MESH_JBEAM_PART) is None or not obj_data.get(constants.MESH_EDITING_ENABLED, False):
        # report_func({'WARNING'}, "Active object is not a valid JBeam part or editing is disabled.")
        return False
    if not search_id:
        report_func({'WARNING'}, "Please enter a Node ID to search for.")
        return False
    # --- End Context Checks ---

    bm = None
    try:
        bm = bmesh.from_edit_mesh(obj_data)
    except Exception as e:
        report_func({'ERROR'}, f"Error accessing mesh data: {e}")
        return False

    node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
    is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)

    if not node_id_layer or not is_fake_layer:
        report_func({'ERROR'}, "JBeam node layers not found on mesh.")
        return False # No need to free bm from edit mesh here

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

        # --- MODIFIED: Center view on selection ---
        # Find a 3D View area to provide context for view_selected
        view3d_area = None
        if context.area and context.area.type == 'VIEW_3D':
            view3d_area = context.area
        else:
            # Fallback: search through all areas
            for window in context.window_manager.windows:
                screen = window.screen
                for area in screen.areas:
                    if area.type == 'VIEW_3D':
                        view3d_area = area
                        break
                if view3d_area:
                    break

        if view3d_area:
            try:
                # Use temp_override to ensure the operator runs in the 3D View context
                # Find a suitable region within the 3D View area (usually WINDOW)
                view3d_region = None
                for region in view3d_area.regions:
                    if region.type == 'WINDOW':
                        view3d_region = region
                        break

                if view3d_region:
                    with context.temp_override(area=view3d_area, region=view3d_region):
                        bpy.ops.view3d.view_selected(use_all_regions=False)
                else:
                    report_func({'WARNING'}, "Could not find a suitable region in the 3D Viewport to center view.")

            except RuntimeError as e:
                 # Report the error more visibly if centering fails
                 report_func({'WARNING'}, f"Could not center view: {e}")
        else:
            # Report if no 3D view found
            report_func({'WARNING'}, "Could not find a 3D Viewport to center the view.")
        # --- END MODIFIED ---

        report_func({'INFO'}, f"Node '{search_id}' found and selected.")
        return True
    else:
        report_func({'WARNING'}, f"Node ID '{search_id}' not found in this object.")
        return False
    # No need to free bm from edit mesh

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
        scene = context.scene
        ui_props = scene.ui_properties
        search_id = ui_props.search_node_id.strip() # Get search term from UI property

        # Call the helper function, passing self.report for feedback
        success = _find_and_select_node_id_logic(context, search_id, self.report)

        return {'FINISHED'} if success else {'CANCELLED'}
# <<< END REFACTORED NODE SEARCH LOGIC >>>

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

# Operator to split 3D View and open Text Editor
class JBEAM_EDITOR_OT_open_text_editor_split(bpy.types.Operator):
    """Splits the 3D Viewport and opens a Text Editor with the current JBeam file, or focuses an existing one."""
    bl_idname = "jbeam_editor.open_text_editor_split"
    bl_label = "Open Text Editor Split"
    bl_description = "Splits the 3D Viewport vertically and opens a Text Editor with the object's JBeam file, or focuses an existing editor if open" # <<< Updated description

    @classmethod
    def poll(cls, context):
        # Only run if the operator is invoked from a 3D View area
        # and there's an active JBeam object with a file path
        return (context.area is not None and
                context.area.type == 'VIEW_3D' and
                context.active_object is not None and
                context.active_object.data is not None and
                context.active_object.data.get(constants.MESH_JBEAM_PART) is not None and
                context.active_object.data.get(constants.MESH_JBEAM_FILE_PATH) is not None)

    def execute(self, context):
        current_area = context.area
        if current_area.type != 'VIEW_3D':
            self.report({'WARNING'}, "Operator must be run from a 3D Viewport area.")
            return {'CANCELLED'}

        active_obj = context.active_object
        jbeam_filepath = active_obj.data.get(constants.MESH_JBEAM_FILE_PATH)
        if not jbeam_filepath:
            # This case should be prevented by poll, but good to double-check
            self.report({'ERROR'}, "Active object has no JBeam file path.")
            return {'CANCELLED'}

        short_filename = _to_short_filename(jbeam_filepath)
        target_text_obj = bpy.data.texts.get(short_filename)

        if not target_text_obj:
            self.report({'WARNING'}, f"Could not find internal text '{short_filename}' for JBeam file. Cannot open editor.")
            return {'CANCELLED'}

        # --- Check for existing Text Editor showing the file ---
        existing_editor_area = None
        for area in context.screen.areas:
            if area.type == 'TEXT_EDITOR' and area.spaces.active and area.spaces.active.text == target_text_obj:
                existing_editor_area = area
                break

        if existing_editor_area:
            self.report({'INFO'}, f"Text Editor with '{short_filename}' is already open.")
            # Optional: You could try to make this area active, but it's complex.
            # For now, just reporting is sufficient.
            return {'FINISHED'}
        # --- End check ---

        # --- Proceed with splitting if no existing editor found ---
        try:
            # Split the current 3D View area vertically
            with context.temp_override(area=current_area):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)

            # Find the newly created area (heuristic remains the same)
            new_area = None
            for area in context.screen.areas:
                is_adjacent = (abs(area.x - current_area.x) < 2 or
                               abs(area.y - current_area.y) < 2 or
                               abs(area.x + area.width - (current_area.x + current_area.width)) < 2 or
                               abs(area.y + area.height - (current_area.y + current_area.height)) < 2)
                if area != current_area and area.type == 'VIEW_3D' and is_adjacent:
                    new_area = area
                    break

            if new_area is None:
                for area in reversed(context.screen.areas):
                     if area != current_area and area.type == 'VIEW_3D':
                         new_area = area
                         break

            if new_area is None:
                 self.report({'ERROR'}, "Could not identify the newly split area.")
                 return {'CANCELLED'}

            # Change the new area's type to Text Editor
            new_area.type = 'TEXT_EDITOR'

            # Load the JBeam file into the new Text Editor
            if new_area.spaces.active:
                new_area.spaces.active.text = target_text_obj
            else:
                 self.report({'WARNING'}, "Newly created Text Editor space is invalid.")

        except Exception as e:
            self.report({'ERROR'}, f"Failed to split area and open Text Editor: {e}")
            traceback.print_exc()
            return {'CANCELLED'}

        return {'FINISHED'}

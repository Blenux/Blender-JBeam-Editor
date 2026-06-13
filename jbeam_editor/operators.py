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
import json
from pathlib import Path # <<< ADDED: Import Path

# Import from local modules
from . import constants
from . import text_editor
from . import globals as jb_globals # Import globals
# Import drawing module and specific elements needed
from . import drawing, properties # <<< ADDED: Import drawing and properties modules
from .drawing import refresh_curr_vdata, find_node_line_number, find_beam_line_number, find_rail_line_number, _scroll_editor_to_line, _tag_redraw_3d_views # Import drawing functions
from .text_editor import _to_short_filename # <<< ADDED: Import helper
# <<< MODIFIED: Import utils and text_editor >>>
from . import utils
# <<< ADDED: Import import functions >>>
from . import import_jbeam, import_vehicle

from .jbeam import io as jbeam_io

# Force JBeam Sync
class JBEAM_EDITOR_OT_force_jbeam_sync(bpy.types.Operator):
    bl_idname = "jbeam_editor.force_jbeam_sync"
    bl_label = "Force JBeam Sync"
    bl_description = "Manually syncs JBeam file with the mesh. Use it when the JBeam file doesn't get updated after a JBeam mesh operation (e.g. transforming a vertex with the input boxes above)"

    @classmethod
    def poll(cls, context):
        # Only allow if editing an active, enabled JBeam object
        obj = context.active_object
        return (obj and obj.mode == 'EDIT' and
                obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and
                obj.data.get(constants.MESH_EDITING_ENABLED, False))


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

# Connect selected nodes with beams
class JBEAM_EDITOR_OT_connect_selected_nodes(bpy.types.Operator):
    bl_idname = "jbeam_editor.connect_selected_nodes"
    bl_label = "Connect Selected Nodes"
    bl_description = "Create new beams between all pairs of selected nodes"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not obj or obj.data.get(constants.MESH_JBEAM_PART) is None or \
           not obj.data.get(constants.MESH_EDITING_ENABLED, False):
            return False
        # Need at least 2 nodes to form a beam
        return len(jb_globals.selected_nodes) >= 2

    def invoke(self, context, event):
        obj = context.active_object
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        bm.verts.ensure_lookup_table()

        export_triggered = False
        beams_created_count = 0

        selected_node_indices = [node_info[0] for node_info in jb_globals.selected_nodes]
        selected_bm_verts = [bm.verts[idx] for idx in selected_node_indices if idx < len(bm.verts)]

        if len(selected_bm_verts) < 2:
            self.report({'INFO'}, "Not enough valid selected nodes to create beams.")
            return {'CANCELLED'}

        beam_indices_layer = bm.edges.layers.string[constants.EL_BEAM_INDICES]
        beam_part_origin_layer = bm.edges.layers.string[constants.EL_BEAM_PART_ORIGIN]

        # Iterate through unique pairs of selected vertices
        for i in range(len(selected_bm_verts)):
            for j in range(i + 1, len(selected_bm_verts)):
                v1 = selected_bm_verts[i]
                v2 = selected_bm_verts[j]

                existing_edge = bm.edges.get((v1, v2))

                if existing_edge is None or existing_edge[beam_indices_layer].decode('utf-8') == '':
                    # Create a new edge or mark existing Blender edge as new JBeam beam
                    e = existing_edge if existing_edge else bm.edges.new((v1, v2))
                    e[beam_indices_layer] = bytes('-1', 'utf-8') 
                    e[beam_part_origin_layer] = bytes(obj_data[constants.MESH_JBEAM_PART], 'utf-8')
                    export_triggered = True
                    beams_created_count += 1

        if obj.mode == 'EDIT':
            bmesh.update_edit_mesh(obj_data)

        if export_triggered:
            jb_globals._force_do_export = True
            self.report({'INFO'}, f"Created {beams_created_count} new beams.")
        else:
            self.report({'INFO'}, "No new beams created (all selected nodes might already be connected or beams exist).")
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

# <<< START MODIFIED FUNCTION >>>
# Helper function for node finding logic
# <<< RENAMED FUNCTION >>>
def _find_and_frame_element_logic(context: bpy.types.Context, search_input: str, report_func=None):
    """
    Finds a node or beam by ID/string in the active object and frames it in the view.
    Returns True on success, False on failure.
    Uses report_func (like operator.report) for feedback.
    """
    # <<< ADDED: Import Vector >>>
    from mathutils import Vector
    if report_func is None: # Default reporter if none provided
        report_func = lambda type, msg: print(f"{list(type)[0]}: {msg}") # Simple print fallback

    # --- Context Checks ---
    obj = context.active_object
    scene = context.scene # <<< Added scene access >>>
    if not obj or obj.mode != 'EDIT':
        # Don't report if just called from property update without context
        # report_func({'WARNING'}, "Node search requires Edit Mode.")
        return False

    obj_data = obj.data
    if not obj_data or obj_data.get(constants.MESH_JBEAM_PART) is None or not obj_data.get(constants.MESH_EDITING_ENABLED, False):
        # report_func({'WARNING'}, "Active object is not a valid JBeam part or editing is disabled.")
        return False
    if not search_input:
        report_func({'WARNING'}, "Please enter a Node or Beam ID to search for.")
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
        # No need to free bm from edit mesh here (as it wasn't assigned in this case)
        return False

    # --- Detect Element Type (Node or Beam) ---
    is_beam_search = '-' in search_input
    node_id_to_find = None
    beam_node_ids_to_find = None

    if is_beam_search:
        parts = search_input.split('-', 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            beam_node_ids_to_find = tuple(sorted(parts)) # Store sorted tuple
        else:
            report_func({'WARNING'}, f"Invalid beam format: '{search_input}'. Use 'node1-node2'.")
            return False
    else:
        node_id_to_find = search_input # Treat as node ID

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table() # <<< ADDED: Ensure edge table
    found_vert = None
    found_edge = None
    center_target_co = None # World coordinate to center view on

    # --- Find Node or Beam ---
    if node_id_to_find:
        for v in bm.verts:
            if v[is_fake_layer] == 0: # Only check real nodes
                node_id = v[node_id_layer].decode('utf-8')
                if node_id == node_id_to_find:
                    found_vert = v
                    center_target_co = obj.matrix_world @ found_vert.co # Get world coordinate
                    break # Stop searching once found
    elif beam_node_ids_to_find:
        for e in bm.edges:
            # Check if edge connects two valid, non-fake nodes
            v1, v2 = e.verts[0], e.verts[1]
            if v1[is_fake_layer] == 0 and v2[is_fake_layer] == 0:
                id1 = v1[node_id_layer].decode('utf-8')
                id2 = v2[node_id_layer].decode('utf-8')
                current_edge_node_ids = tuple(sorted((id1, id2)))
                if current_edge_node_ids == beam_node_ids_to_find:
                    found_edge = e
                    # Calculate midpoint in world space
                    midpoint_local = (v1.co + v2.co) / 2.0
                    center_target_co = obj.matrix_world @ midpoint_local
                    break # Stop searching once found

    # --- Frame Element ---
    if center_target_co:
        try:
            # Find a 3D View area to provide context
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
                # Find a suitable region within the 3D View area (usually WINDOW)
                view3d_region = None
                for region in view3d_area.regions:
                    if region.type == 'WINDOW':
                        view3d_region = region
                        break

                if view3d_region:
                    with context.temp_override(area=view3d_area, region=view3d_region):
                        # Center view on coordinate using 3D cursor
                        original_cursor_location = scene.cursor.location.copy()
                        scene.cursor.location = center_target_co # Move cursor to target
                        bpy.ops.view3d.view_center_cursor() # Center view on cursor
                        scene.cursor.location = original_cursor_location # Restore cursor
                else:
                    report_func({'WARNING'}, "Could not find a suitable region in the 3D Viewport to center view.")
            else:
                report_func({'WARNING'}, "Could not find a 3D Viewport to center the view.")

        except RuntimeError as e:
             report_func({'WARNING'}, f"Could not center view: {e}")

        # Report success
        if found_vert:
            report_func({'INFO'}, f"Node '{node_id_to_find}' found and framed.")
        elif found_edge:
            report_func({'INFO'}, f"Beam '{search_input}' found and framed.")
        return True

    else: # Element not found
        if node_id_to_find:
            report_func({'WARNING'}, f"Node ID '{node_id_to_find}' not found in this object.")
        else:
            report_func({'WARNING'}, f"Beam '{search_input}' not found in this object.")
        return False
    # No need to free bm from edit mesh

# Node Search Operator
class JBEAM_EDITOR_OT_find_node(bpy.types.Operator):
    bl_idname = "jbeam_editor.find_node"
    bl_label = "Find Element"
    # <<< MODIFIED: Updated description >>>
    bl_description = "Find and frame the specified Node or Beam (format: node1-node2) in the active object (Edit Mode only)"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj and obj.mode == 'EDIT' and
                obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and
                obj.data.get(constants.MESH_EDITING_ENABLED, False))
                # context.tool_settings.mesh_select_mode[0]) # Check vertex select mode (index 0)

    def execute(self, context):
        scene = context.scene
        ui_props = scene.ui_properties
        search_input = ui_props.search_node_id.strip() # Get search term from UI property

        # Call the helper function, passing self.report for feedback
        # <<< UPDATED: Call renamed helper >>>
        success = _find_and_frame_element_logic(context, search_input, self.report)

        return {'FINISHED'} if success else {'CANCELLED'}
# <<< END MODIFIED FUNCTION and OPERATOR >>>

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

        ui_props = context.scene.ui_properties
        len_selected_nodes = len(jb_globals.selected_nodes)

        # Check if exactly one node OR one beam is selected
        return len_selected_nodes == 1 or len(jb_globals.selected_beams) == 1 or (len_selected_nodes == 2 and ui_props.find_jump_to_rail_on_2_nodes)

    def execute(self, context):
        obj = context.active_object
        obj_data = obj.data
        ui_props = context.scene.ui_properties
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

        # Check Rail Selection (2 nodes) first to give it priority
        if len(jb_globals.selected_nodes) == 2 and ui_props.find_jump_to_rail_on_2_nodes:
            target_element_type = "Rail"
            node_id1 = jb_globals.selected_nodes[0][1]
            node_id2 = jb_globals.selected_nodes[1][1]

            # Since rails are defined by name, we need to find which rail uses these two nodes.
            # This requires iterating through the rail definitions in the current JBeam data.
            if jb_globals.curr_vdata and 'rails' in jb_globals.curr_vdata:
                rails_data = jb_globals.curr_vdata['rails']
                if isinstance(rails_data, dict):
                    for rail_name, rail_info in rails_data.items():
                        rail_nodes = rail_info if isinstance(rail_info, list) else rail_info.get('links:')
                        # Check for adjacent nodes in any rail list (simple or complex format)
                        if isinstance(rail_nodes, list) and len(rail_nodes) >= 2:
                            for i in range(len(rail_nodes) - 1):
                                if (rail_nodes[i] == node_id1 and rail_nodes[i+1] == node_id2) or \
                                   (rail_nodes[i] == node_id2 and rail_nodes[i+1] == node_id1):
                                    # Found the rail. Now find its line number.
                                    line_num = find_rail_line_number(jbeam_filepath, obj_data[constants.MESH_JBEAM_PART], rail_name)
                                    break # Stop searching once found

        elif len(jb_globals.selected_nodes) == 1:
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

        elif len(jb_globals.selected_beams) == 1: # Check Beam Selection
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

        internal_name = text_editor._get_internal_filename(jbeam_filepath) # <<< Use new helper
        target_text_obj = bpy.data.texts.get(internal_name)

        if not target_text_obj:
            self.report({'WARNING'}, f"Could not find internal text '{internal_name}' for JBeam file. Cannot open editor.")
            return {'CANCELLED'}

        # --- Check for existing Text Editor showing the file ---
        existing_editor_area = None
        for area in context.screen.areas:
            if area.type == 'TEXT_EDITOR' and area.spaces.active and area.spaces.active.text == target_text_obj:
                existing_editor_area = area # <<< Use internal_name here
                break

        if existing_editor_area:
            self.report({'INFO'}, f"Text Editor with '{internal_name}' is already open.")
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

# <<< ADDED: Operator to save a specific PC file from Text Editor to disk >>>
class JBEAM_EDITOR_OT_save_pc_file_to_disk(bpy.types.Operator):
    """Saves the internal text buffer corresponding to the PC file back to disk."""
    bl_idname = "jbeam_editor.save_pc_file_to_disk"
    bl_label = "Save PC File to Disk"
    bl_description = "Saves changes made in the Text Editor back to the original .pc file and reloads filters" # <<< MODIFIED
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(
        name="PC File Path",
        description="The full path to the .pc file to save",
    )

    @classmethod
    def poll(cls, context):
        # Always enabled if filepath is provided by the button
        return True

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "No PC file path specified.")
            return {'CANCELLED'}

        success = text_editor.write_from_int_to_ext_file(self.filepath)

        if success:
            # <<< MODIFIED: Trigger filter update after successful save >>>
            self.report({'INFO'}, f"Saved & Reloaded: {Path(self.filepath).name}")
            # properties.update_pc_filters(context.scene, context)
            properties.update_pc_filters(context.scene, context) # Call the update function
        else:
            self.report({'ERROR'}, f"Failed to save file: {self.filepath}")
            return {'CANCELLED'}

        return {'FINISHED'}
# <<< END ADDED >>>

# <<< ADDED: Operator to open any file path in Text Editor >>>
class JBEAM_EDITOR_OT_open_file_in_editor(bpy.types.Operator):
    """Opens the specified file in a Text Editor area, splitting if necessary."""
    bl_idname = "jbeam_editor.open_file_in_editor"
    bl_label = "Open File in Text Editor"
    bl_description = "Opens the specified file in a Text Editor, splitting the current area if it's a 3D View"
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(
        name="File Path",
        description="The full path to the file to open",
    )

    @classmethod
    def poll(cls, context):
        # The operator's ability to run depends on the filepath provided by the button,
        # so the button itself should always be enabled.
        return True

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "No file path specified.")
            return {'CANCELLED'}

        internal_name = text_editor._get_internal_filename(self.filepath)
        target_text_obj = bpy.data.texts.get(internal_name)

        if not target_text_obj:
            # Try to load from disk if not internal yet (shouldn't happen often for PC files after folder import)
            file_content = utils.read_file(self.filepath)
            if file_content is not None:
                text_editor.write_int_file(self.filepath, file_content)
                target_text_obj = bpy.data.texts.get(internal_name)
            else:
                self.report({'ERROR'}, f"Could not find or load file: {self.filepath}")
                return {'CANCELLED'}

        # Find an existing Text Editor or split the current view
        text_area = None
        for area in context.screen.areas:
            if area.type == 'TEXT_EDITOR':
                text_area = area
                break

        if text_area:
            text_area.spaces.active.text = target_text_obj
        else:
            # If no text editor exists, try splitting the current area (like the other operator)
            # We can reuse the logic by calling the other operator if needed, or replicate it.
            # For simplicity, let's just report if no editor is open.
            self.report({'INFO'}, "No Text Editor area open. Please open one manually.")
            # Alternatively, call the split operator:
            # bpy.ops.jbeam_editor.open_text_editor_split('INVOKE_DEFAULT') # This uses active object path, not ideal
            # Best to just focus/change existing editor or ask user to open one.

        return {'FINISHED'}
# <<< END ADDED >>>

# <<< Operator for Node Deletion Confirmation >>>
class JBEAM_EDITOR_OT_confirm_node_deletion(bpy.types.Operator):
    """Confirms deletion of newly created nodes that overlap existing ones."""
    bl_idname = "jbeam_editor.confirm_node_deletion"
    bl_label = "Confirm Overlapping Node Deletion"
    bl_options = {'REGISTER', 'INTERNAL'}

    nodes_data: bpy.props.StringProperty(options={'HIDDEN'})
    # Store list of tuples (node_id, display_name, position, existing_collided_id)
    nodes_to_delete_info: list = []

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.mode == 'EDIT' and obj.data

    # <<< MODIFIED: cancel method to clear flag AND trigger export >>>
    def cancel(self, context):
        """Called when the operator is cancelled. Keeps the new node and triggers export."""
        print("Node deletion confirmation cancelled. Keeping newly created node and triggering export...") # Debug message

        # --- ADDED: Reset 'is_fake' flag for kept nodes ---
        obj = context.active_object
        if obj and obj.mode == 'EDIT' and obj.data:
            obj_data = obj.data
            bm = None
            try:
                bm = bmesh.from_edit_mesh(obj_data)
                bm.verts.ensure_lookup_table()
                node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
                node_is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)

                if node_id_layer and node_is_fake_layer:
                    nodes_info = getattr(self, 'nodes_to_delete_info', None)
                    if nodes_info:
                        for node_id_to_keep, _, _, _ in nodes_info: # Iterate through stored info
                            for v in bm.verts:
                                if v[node_id_layer].decode('utf-8') == node_id_to_keep:
                                    v[node_is_fake_layer] = 0 # Reset flag
                                    print(f"Cancel: Reset 'is_fake' flag for kept node {node_id_to_keep}")
                                    break # Found the vertex, move to next node_id
                    bmesh.update_edit_mesh(obj_data) # Update mesh after resetting flags
            except Exception as e:
                print(f"Error resetting 'is_fake' flag in cancel: {e}", file=sys.stderr)
            # No need to free bm from edit mesh
        # --- END ADDED ---

        # --- Trigger Export ---
        # The newly created node (with its temporary or final ID) exists in the mesh.
        # Triggering an export will process the current mesh state, including the new node.
        # The export logic (export_utils.get_nodes_add_delete_rename) should handle
        # the final naming and addition of this node to the JBeam file.
        jb_globals._force_do_export = True
        # --- End Trigger Export ---

        # --- Ensure flag is cleared ---
        # This must happen regardless of export success/failure
        jb_globals.confirm_delete_pending = False
        print("Cancel: confirm_delete_pending flag cleared.")
        # <<< ADDED: Remove cancelled nodes from remap dict >>>
        nodes_info = getattr(self, 'nodes_to_delete_info', None)
        if nodes_info:
            for node_id_to_cancel, _, _, _ in nodes_info: # Iterate through stored info
                if node_id_to_cancel in jb_globals.node_overlap_remap:
                    del jb_globals.node_overlap_remap[node_id_to_cancel]
                    print(f"Cancel: Removed {node_id_to_cancel} from node_overlap_remap.")
        # <<< END ADDED >>>
        # --- End Ensure flag is cleared ---

        # Trigger viewport redraw might be good practice after potentially changing state
        drawing.veh_render_dirty = True
        drawing._tag_redraw_3d_views(context)
        # <<< END MODIFICATION >>>

    def invoke(self, context, event):
        # Flag is set *before* invoke in export_utils.py
        try:
            # Store parsed data in nodes_to_delete_info
            # Use getattr to safely access nodes_data, default to empty string
            nodes_data_str = getattr(self, 'nodes_data', '')
            if not nodes_data_str:
                 raise ValueError("Node data string is empty")
            self.nodes_to_delete_info = json.loads(nodes_data_str)
            if not isinstance(self.nodes_to_delete_info, list):
                raise ValueError("Invalid data format")
        except (json.JSONDecodeError, ValueError) as e:
            self.report({'ERROR'}, f"Failed to parse node deletion data: {e}")
            jb_globals.confirm_delete_pending = False # CLEAR FLAG on invoke error
            return {'CANCELLED'}

        if not self.nodes_to_delete_info:
            self.report({'WARNING'}, "No nodes specified for deletion confirmation.")
            jb_globals.confirm_delete_pending = False # CLEAR FLAG if no nodes
            return {'CANCELLED'}

        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        layout.label(text="The following newly created nodes overlap existing nodes:")
        layout.label(text="Confirm deletion to proceed?")
        box = layout.box()
        # Use getattr for safer access
        nodes_info = getattr(self, 'nodes_to_delete_info', [])
        if not nodes_info:
             box.label(text="Error: Node information not available.")
        else:
            for node_id, display_name, pos, collided_id in nodes_info: # Unpack collided_id too
                pos_str = f"({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f})"
                # Use display_name which might be the mirrored name or the UUID
                box.label(text=f"- Node '{display_name}' at {pos_str} (overlaps '{collided_id}')")

    def execute(self, context):
        obj = context.active_object
        obj_data = obj.data

        # Use getattr for safer access
        nodes_info = getattr(self, 'nodes_to_delete_info', None)
        if not nodes_info:
             self.report({'WARNING'}, "No node info available for deletion execution.")
             jb_globals.confirm_delete_pending = False # Should not happen, but clear flag just in case
             return {'CANCELLED'}

        bm = None
        verts_geom = []
        # edges_to_delete_geom = [] # <<< REMOVED: No longer needed
        try:
            bm = bmesh.from_edit_mesh(obj_data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table() # <<< ADDED: Ensure edge table
            node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID) # Get node ID layer
            node_part_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN) # <<< ADDED: Get node origin layer

            if not node_id_layer:
                self.report({'ERROR'}, "Node ID layer not found on mesh.")
                jb_globals.confirm_delete_pending = False
                return {'CANCELLED'}

            # Find vertices by Node ID
            # <<< MODIFIED: Build map of node_id -> vertex and find existing nodes >>>
            node_id_to_vertex_map = {}
            existing_node_vertices = {} # {existing_node_id: BMVert}
            vertices_to_delete_map = {} # {overlapping_node_id: BMVert}
            overlapping_to_existing_map = {info[0]: info[3] for info in nodes_info} # {overlapping_id: existing_id}

            # First pass: map all node IDs to vertices
            for v in bm.verts:
                try:
                    if not v.is_valid: continue
                    current_node_id = v[node_id_layer].decode('utf-8')
                    node_id_to_vertex_map[current_node_id] = v
                    if current_node_id in overlapping_to_existing_map:
                        vertices_to_delete_map[current_node_id] = v
                except ReferenceError:
                    print(f"Execute: Vertex {v.index} became invalid during mapping.", file=sys.stderr)
                except Exception as e:
                    print(f"Warning: Error processing vertex {v.index} during mapping: {e}", file=sys.stderr)

            # Find the BMVerts for the existing nodes that were overlapped
            for overlapping_id, existing_id in overlapping_to_existing_map.items():
                if existing_id not in existing_node_vertices: # Avoid redundant lookups
                    v_existing = node_id_to_vertex_map.get(existing_id)
                    if v_existing:
                        existing_node_vertices[existing_id] = v_existing
                    else:
                        self.report({'WARNING'}, f"Could not find existing node '{existing_id}' in mesh for remapping.")
                        # If the existing node isn't found, we can't remap, so skip deletion? Or just delete?
                        # For now, let's skip the deletion of the overlapping node if its target doesn't exist.
                        if overlapping_id in vertices_to_delete_map:
                            del vertices_to_delete_map[overlapping_id]
                            print(f"Skipping deletion of '{overlapping_id}' as target '{existing_id}' not found.")

            # <<< ADDED: Reconnect edges BEFORE deleting vertices >>>
            beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
            beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)

            if not beam_indices_layer or not beam_part_origin_layer:
                self.report({'ERROR'}, "Beam layers not found. Cannot reconnect edges.")
                jb_globals.confirm_delete_pending = False
                return {'CANCELLED'}

            for overlapping_node_id, v_to_delete in vertices_to_delete_map.items():
                existing_node_id = overlapping_to_existing_map[overlapping_node_id]
                v_existing = existing_node_vertices.get(existing_node_id)
                if not v_existing: continue # Skip if existing node wasn't found

                v_existing_part_origin_bytes = v_existing[node_part_origin_layer] # Get origin bytes

                try:
                    # Iterate over edges connected to the vertex being deleted
                    for edge in list(v_to_delete.link_edges): # Use list() to copy as we modify edges
                        v_other = edge.other_vert(v_to_delete)
                        if v_other is None or not v_other.is_valid: continue # Skip invalid edges/verts

                        # Avoid creating self-loops or duplicate edges
                        if v_other != v_existing and bm.edges.get((v_other, v_existing)) is None:
                            # Create the new edge
                            new_edge = bm.edges.new((v_other, v_existing))
                            # Mark as new beam for export
                            new_edge[beam_indices_layer] = b'-1'
                            # Set part origin based on the existing node
                            new_edge[beam_part_origin_layer] = v_existing_part_origin_bytes
                            print(f"Remapped edge from {overlapping_node_id} to {existing_node_id} (connected to {v_other[node_id_layer].decode('utf-8')})")

                        # Add the original edge to the deletion list
                        # if edge not in edges_to_delete_geom: # <<< REMOVED
                        #     edges_to_delete_geom.append(edge) # <<< REMOVED

                    # Add the vertex itself to the deletion list
                    verts_geom.append(v_to_delete)

                except ReferenceError:
                     # Handle cases where vertex becomes invalid during iteration
                     print(f"Execute: Vertex {v.index} became invalid during search.", file=sys.stderr)
                except Exception as e:
                    # Handle potential decoding errors or layer access issues for a specific vertex
                    print(f"Warning: Error processing vertex {v.index} during deletion search: {e}", file=sys.stderr)

            # Perform Deletions
            if not verts_geom:
                 self.report({'WARNING'}, "No valid vertices found for deletion.")
                 jb_globals.confirm_delete_pending = False # CLEAR FLAG
                 return {'CANCELLED'}

            # Delete original edges first
            # if edges_to_delete_geom: # <<< REMOVED
            #     try: # <<< REMOVED
            #         bmesh.ops.delete(bm, geom=edges_to_delete_geom, context='EDGES') # <<< REMOVED
            #     except Exception as edge_del_err: # <<< REMOVED
            #         print(f"Warning: Error deleting original edges: {edge_del_err}", file=sys.stderr) # <<< REMOVED

            # Delete the vertices (this will also remove connected edges)
            bmesh.ops.delete(bm, geom=verts_geom, context='VERTS')
            bmesh.update_edit_mesh(obj_data)
            self.report({'INFO'}, f"Deleted {len(verts_geom)} overlapping nodes and remapped edges.")

            # Trigger viewport redraw to show the deletion
            drawing.veh_render_dirty = True
            drawing._tag_redraw_3d_views(context)

            # Trigger a new export cycle to write the changes correctly after deletion
            jb_globals._force_do_export = True

        except Exception as e:
            self.report({'ERROR'}, f"Failed to delete nodes: {e}")
            traceback.print_exc()
            jb_globals.confirm_delete_pending = False # CLEAR FLAG on error
            return {'CANCELLED'}
        finally:
             # ENSURE FLAG IS CLEARED on successful execution
             jb_globals.confirm_delete_pending = False
             # No need to free bm from edit mesh

        return {'FINISHED'}

# <<< START: Modified Native Undo/Redo Warning Operators >>>

def _check_jbeam_edit_context(context: bpy.types.Context):
    """Helper function to check if we are editing a valid JBeam object."""
    active_obj = context.active_object
    return (active_obj is not None and
            active_obj.mode == 'EDIT' and
            active_obj.data is not None and
            active_obj.data.get(constants.MESH_JBEAM_PART) is not None and
            active_obj.data.get(constants.MESH_EDITING_ENABLED, False))

class JBEAM_EDITOR_OT_warn_native_undo(bpy.types.Operator):
    """Intercepts native Undo (Ctrl+Z) to warn and confirm if editing a JBeam object."""
    bl_idname = "jbeam_editor.warn_native_undo"
    bl_label = "JBeam Native Undo Warning"
    # bl_description is used in the draw method now
    bl_description = "Native Undo ( Ctrl+Z ) is NOT recommended for JBeam editing as it can cause issues.\nPlease use the addon's Undo ( Ctrl+[ ) instead.\n\nProceed anyway?"
    bl_options = {'REGISTER', 'INTERNAL'}

    def invoke(self, context, event):
        if _check_jbeam_edit_context(context):
            # Show confirmation dialog if editing JBeam
            # <<< MODIFIED: Added width argument >>>
            return context.window_manager.invoke_props_dialog(self, width=500)
        else:
            # Otherwise, execute the default Blender undo immediately
            try:
                bpy.ops.ed.undo('INVOKE_DEFAULT')
            except RuntimeError as e:
                # Handle cases where undo might not be available
                self.report({'WARNING'}, f"Native undo failed: {e}")
                return {'CANCELLED'}
            return {'FINISHED'}

    # <<< MODIFIED: draw method for centering >>>
    def draw(self, context):
        layout = self.layout
        # Split the description into lines for better formatting
        lines = self.bl_description.split('\n')
        for line in lines:
            # Create a row for each line and center it
            row = layout.row()
            row.alignment = 'CENTER'
            row.label(text=line)

    def execute(self, context):
        # This method is only called if the user confirms the dialog
        try:
            bpy.ops.ed.undo('INVOKE_DEFAULT')
            self.report({'WARNING'}, "Executed native Undo despite JBeam editing context.") # Optional warning after execution
        except RuntimeError as e:
            # Handle cases where undo might not be available even after confirmation
            self.report({'WARNING'}, f"Native undo failed after confirmation: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}

class JBEAM_EDITOR_OT_warn_native_redo(bpy.types.Operator):
    """Intercepts native Redo (Ctrl+Shift+Z) to warn and confirm if editing a JBeam object."""
    bl_idname = "jbeam_editor.warn_native_redo"
    bl_label = "JBeam Native Redo Warning"
    # bl_description is used in the draw method now
    bl_description = "Native Redo ( Ctrl+Shift+Z ) is NOT recommended for JBeam editing as it can cause issues.\nPlease use the addon's Redo ( Ctrl+] ) instead.\n\nProceed anyway?"
    bl_options = {'REGISTER', 'INTERNAL'}

    def invoke(self, context, event):
        if _check_jbeam_edit_context(context):
            # Show confirmation dialog if editing JBeam
            # <<< MODIFIED: Added width argument >>>
            return context.window_manager.invoke_props_dialog(self, width=500) # Adjusted width to match undo
        else:
            # Otherwise, execute the default Blender redo immediately
            try:
                bpy.ops.ed.redo('INVOKE_DEFAULT')
            except RuntimeError as e:
                # Handle cases where redo might not be available
                self.report({'WARNING'}, f"Native redo failed: {e}")
                return {'CANCELLED'}
            return {'FINISHED'}

    # <<< MODIFIED: draw method for centering >>>
    def draw(self, context):
        layout = self.layout
        # Split the description into lines for better formatting
        lines = self.bl_description.split('\n')
        for line in lines:
            # Create a row for each line and center it
            row = layout.row()
            row.alignment = 'CENTER'
            row.label(text=line)

    def execute(self, context):
        # This method is only called if the user confirms the dialog
        try:
            bpy.ops.ed.redo('INVOKE_DEFAULT')
            self.report({'WARNING'}, "Executed native Redo despite JBeam editing context.") # Optional warning after execution
        except RuntimeError as e:
            # Handle cases where redo might not be available even after confirmation
            self.report({'WARNING'}, f"Native redo failed after confirmation: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}

# <<< END: Modified Native Undo/Redo Warning Operators >>>

# <<< START: Added Reload JBeam from Disk Operator >>>
class JBEAM_EDITOR_OT_reload_jbeam_from_disk(bpy.types.Operator):
    """Reloads the JBeam file from disk, updates the internal text editor, and refreshes the mesh/visualizations."""
    bl_idname = "jbeam_editor.reload_jbeam_from_disk"
    bl_label = "Reload JBeam from Disk"
    bl_description = "Reloads the associated JBeam file from disk, discarding any unsaved changes in the internal text editor, and refreshes the 3D representation"
    bl_options = {'REGISTER'} # Keep REGISTER if invoked by button

    @classmethod
    def poll(cls, context):
        # Check if active object is a valid JBeam object with a file path
        obj = context.active_object
        return (obj and
                obj.data and
                obj.data.get(constants.MESH_JBEAM_PART) is not None and
                obj.data.get(constants.MESH_JBEAM_FILE_PATH) is not None)

    # <<< ADDED: Draw method for confirmation >>>
    def draw(self, context):
        layout = self.layout
        # <<< MODIFIED: Center align text using rows >>>
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text="This will discard unsaved changes in the internal text editor.")
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text="Are you sure you want to reload from disk?")

    # <<< ADDED: Invoke method to show dialog >>>
    def invoke(self, context, event):
        # <<< MODIFIED: Added width parameter >>>
        return context.window_manager.invoke_props_dialog(self, width=450)

    # <<< MODIFIED: Original execute logic moved here >>>
    def execute(self, context):
        # This now runs only *after* the user confirms the dialog

        obj = context.active_object
        obj_data = obj.data
        jbeam_filepath = obj_data.get(constants.MESH_JBEAM_FILE_PATH)
        jbeam_part_name = obj_data.get(constants.MESH_JBEAM_PART)
        veh_model = obj_data.get(constants.MESH_VEHICLE_MODEL) # Check if it's part of a vehicle

        if not jbeam_filepath:
            self.report({'ERROR'}, "No JBeam file path associated with this object.")
            return {'CANCELLED'}

        # 1. Read from disk
        file_content = utils.read_file(jbeam_filepath)
        if file_content is None:
            self.report({'ERROR'}, f"Could not read file from disk: {jbeam_filepath}")
            return {'CANCELLED'}

        # 2. Update internal text
        text_editor.write_int_file(jbeam_filepath, file_content)

        # 3. Trigger reimport/refresh
        # We force a check on the specific file, triggering reimport and mesh regeneration.
        # Pass undoing_redoing=True to prevent this action from being added to the custom history.
        try:
            text_editor.check_int_files_for_changes(context, [jbeam_filepath], undoing_redoing=True, reimport=True, regenerate_mesh=True)
            self.report({'INFO'}, f"Reloaded '{jbeam_part_name}' from {jbeam_filepath}")
        except Exception as e:
            self.report({'ERROR'}, f"Error during refresh after reload: {e}")
            traceback.print_exc()
            return {'CANCELLED'}

        return {'FINISHED'}
# <<< END: Added Reload JBeam from Disk Operator >>>

# <<< ADDED: Operator for Text Deletion Confirmation >>>
class JBEAM_EDITOR_OT_confirm_text_deletion(bpy.types.Operator):
    """Confirms deletion of an unused internal JBeam text document."""
    bl_idname = "jbeam_editor.confirm_text_deletion"
    bl_label = "Confirm Unused Text Deletion"
    bl_options = {'REGISTER', 'INTERNAL'} # INTERNAL prevents it showing in search

    short_filename: bpy.props.StringProperty(options={'HIDDEN'})
    filepath: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        # Operator is invoked internally, no UI poll needed
        return True

    def invoke(self, context, event):
        # Check if text still exists before showing dialog
        if not bpy.data.texts.get(self.short_filename):
            self.report({'INFO'}, f"Text document '{Path(self.filepath).name}' already removed.")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text=f"Text document for '{Path(self.filepath).name}'") # Show filename only
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text="is no longer used by any JBeam object.")
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text="Delete this internal text document?")

    def execute(self, context):
        scene = context.scene
        text_to_delete = bpy.data.texts.get(self.short_filename)

        if text_to_delete:
            try:
                # Clean up scene properties related to this file
                if text_editor.SCENE_PREV_TEXTS in scene and self.short_filename in scene[text_editor.SCENE_PREV_TEXTS]: # Key is internal_name (passed as short_filename prop)
                    del scene[text_editor.SCENE_PREV_TEXTS][self.short_filename] # <<< Use internal_name

                if text_editor.SCENE_SHORT_TO_FULL_FILENAME in scene and self.short_filename in scene[text_editor.SCENE_SHORT_TO_FULL_FILENAME]: # <<< Use internal_name
                    del scene[text_editor.SCENE_SHORT_TO_FULL_FILENAME][self.short_filename]

                # Remove the text document
                bpy.data.texts.remove(text_to_delete, do_unlink=True)
                self.report({'INFO'}, f"Deleted unused text document: {Path(self.filepath).name}")

            except Exception as e:
                self.report({'ERROR'}, f"Failed to delete text document: {e}")
                traceback.print_exc()
                return {'CANCELLED'}
        else:
            # Report as INFO instead of WARNING if text was already removed by another instance
            self.report({'INFO'}, f"Text document '{Path(self.filepath).name}' was already removed.")

        return {'FINISHED'}

# <<< ADDED: Operator for Deleting All Unused Texts >>>
class JBEAM_EDITOR_OT_delete_all_unused_texts(bpy.types.Operator):
    """Finds and deletes all internal JBeam text documents not currently used by any JBeam object."""
    bl_idname = "jbeam_editor.delete_all_unused_texts"
    bl_label = "Delete All Unused JBeam Texts"
    bl_description = "Deletes internal JBeam text documents (.jbeam) that are not linked to any current JBeam object in the scene" # <<< MODIFIED description
    bl_options = {'REGISTER'} # Keep REGISTER if invoked by button

    # Store list of internal names to delete
    unused_texts_to_delete: list = []

    @classmethod
    def poll(cls, context):
        # Always enabled, but checks for texts internally
        return True

    def invoke(self, context, event):
        scene = context.scene
        self.unused_texts_to_delete.clear() # Clear previous list

        internal_name_to_full_map = scene.get(text_editor.SCENE_SHORT_TO_FULL_FILENAME, {}) # Map is internal_name -> full_path
        if not internal_name_to_full_map:
            self.report({'INFO'}, "No internal JBeam text documents found to check.")
            return {'CANCELLED'}

        # Build set of filepaths currently used by JBeam objects
        used_filepaths = set()
        for obj in scene.objects:
            if obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None:
                fp = obj.data.get(constants.MESH_JBEAM_FILE_PATH)
                if fp:
                    used_filepaths.add(fp)

        # Find unused texts
        for internal_name, full_filepath in internal_name_to_full_map.items(): # Iterate using internal_name
            # <<< ADDED: Skip .pc files >>>
            if full_filepath.lower().endswith('.pc'):
                continue
            # <<< END ADDED >>>
            if full_filepath not in used_filepaths:
                # Check if the text object actually exists
                if bpy.data.texts.get(internal_name):
                    self.unused_texts_to_delete.append(internal_name) # Store internal_name

        if not self.unused_texts_to_delete:
            self.report({'INFO'}, "No unused internal JBeam text documents found.")
            return {'CANCELLED'}

        # Show confirmation dialog
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        layout = self.layout
        # <<< MODIFIED: Center align the count label >>>
        row = layout.row()
        row.alignment = 'CENTER' # <<< MODIFIED: Clarify only JBeam texts >>>
        row.label(text=f"Found {len(self.unused_texts_to_delete)} unused internal JBeam text documents.")
        layout.separator()
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text="Are you sure you want to delete them?")
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text="(This cannot be undone.)")

    def execute(self, context):
        scene = context.scene
        deleted_count = 0
        for internal_name in self.unused_texts_to_delete: # Iterate using internal_name
            # Use the single text deletion operator's logic for cleanup
            # We pass the properties directly to its execute method
            # Pass internal_name as the 'short_filename' property
            bpy.ops.jbeam_editor.confirm_text_deletion('EXEC_DEFAULT', short_filename=internal_name, filepath="<unknown>") # Filepath isn't strictly needed here
            deleted_count += 1

        self.report({'INFO'}, f"Deleted {deleted_count} unused JBeam text documents.")
        return {'FINISHED'}
# <<< END ADDED >>>

# <<< ADDED: Operator for Importing Folder >>>
class JBEAM_EDITOR_OT_import_folder(bpy.types.Operator, import_jbeam.ImportHelper):
    """Imports all .jbeam and .pc files from a selected folder"""
    bl_idname = "jbeam_editor.import_vehicle_folder" # Renamed idname for clarity
    bl_label = "Import BeamNG Vehicle Folder" # Renamed label
    bl_description = "Recursively imports .jbeam (all parts), .pc, and optionally .dae files from the selected folder" # Updated description

    # Use directory property
    directory: bpy.props.StringProperty(
        name="Directory",
        description="Select the folder containing JBeam and DAE files",
        subtype='DIR_PATH',
    )

    # filter_glob is not used for directory selection but required by ImportHelper
    filter_glob: bpy.props.StringProperty(default="", options={'HIDDEN'})

    # <<< ADDED: Toggle for DAE import >>>
    import_dae_files: bpy.props.BoolProperty(
        name="Import DAE Files",
        description="Import Collada (.dae) mesh files found in the folder",
        default=True,
    )

    def execute(self, context):
        folder_path = Path(self.directory)
        if not folder_path.is_dir():
            self.report({'ERROR'}, f"Selected path is not a valid directory: {self.directory}")
            return {'CANCELLED'}

        jbeam_files_found = list(folder_path.rglob('*.jbeam'))
        pc_files_found = list(folder_path.rglob('*.pc'))
        dae_files_found = list(folder_path.rglob('*.dae')) # <<< ADDED: Find DAE files

        if not jbeam_files_found and not pc_files_found and (not self.import_dae_files or not dae_files_found):
            self.report({'WARNING'}, f"No .jbeam, .pc, or requested .dae files found in {folder_path} or its subdirectories.")
            return {'CANCELLED'}

        dae_imported_count = 0
        dae_skipped_count = 0

        # <<< ADDED: Import DAE files first if requested >>>
        if self.import_dae_files:
            print(f"Importing {len(dae_files_found)} DAE files...")
            for dae_file in dae_files_found:
                dae_filepath_str = dae_file.as_posix()
                try:
                    # Use Blender's built-in Collada importer
                    bpy.ops.wm.collada_import(filepath=dae_filepath_str)
                    dae_imported_count += 1
                except RuntimeError as e:
                    print(f"Error importing DAE file {dae_filepath_str}: {e}", file=sys.stderr)
                    dae_skipped_count += 1
                except Exception as e: # Catch other potential exceptions
                    print(f"Unexpected error importing DAE file {dae_filepath_str}: {e}", file=sys.stderr)
                    dae_skipped_count += 1
            print("DAE import finished.")
        # <<< END ADDED >>>

        jbeam_imported_count = 0
        jbeam_skipped_count = 0
        pc_imported_count = 0 # <<< ADDED: Count for PC files

        # <<< ADDED: Get JBeam collection to check for existing objects >>>
        jbeam_collection: bpy.types.Collection | None = bpy.data.collections.get('JBeam Objects')

        # Import JBeam files (all parts)
        for jbeam_file in jbeam_files_found:
            jbeam_filepath_str = jbeam_file.as_posix()
            try:
                # <<< ADDED: Invalidate cache for the specific file being processed >>>
                # This ensures we get fresh data if the file changed since last import
                import_jbeam.jbeam_io.invalidate_cache_for_file(jbeam_filepath_str)
                jbeam_file_data, _ = import_jbeam.jbeam_io.get_jbeam(jbeam_filepath_str, False, True)
                if jbeam_file_data:
                    for part_name in jbeam_file_data.keys():
                        # <<< ADDED: Check if part object already exists >>>
                        if jbeam_collection and jbeam_collection.objects.get(part_name):
                            # print(f"Skipping import: Part '{part_name}' already exists in scene.") # Optional debug print
                            jbeam_skipped_count += 1
                            continue # Skip to the next part
                        # <<< END ADDED >>>

                        # Reuse import_jbeam_part logic, handle existing parts
                        res = import_jbeam.import_jbeam_part(context, jbeam_filepath_str, jbeam_file_data, part_name)
                        if res:
                            jbeam_imported_count += 1
                        else:
                            # import_jbeam_part handles reporting errors, but we count skips
                            jbeam_skipped_count += 1
            except Exception as e:
                print(f"Error processing JBeam file {jbeam_filepath_str}: {e}", file=sys.stderr)
                jbeam_skipped_count += 1 # Count as skipped on error

        # <<< MODIFIED: Moved PC file finding earlier >>>
        scene = context.scene
        # Ensure the collection property exists
        if not hasattr(scene, 'jbeam_editor_imported_pc_files'):
             # This might happen if the addon was just enabled or properties weren't registered yet.
             # <<< MODIFIED: Check scene dictionary as fallback >>>
             # For robustness, try accessing via scene dictionary if direct attribute fails.
             if 'jbeam_editor_imported_pc_files' not in scene:
                 self.report({'ERROR'}, "PC file storage property not found on scene.")
                 return {'CANCELLED'} # Cannot store PC files

        imported_pc_collection = scene.jbeam_editor_imported_pc_files
        existing_pc_paths = {item.path for item in imported_pc_collection}

        for pc_file in pc_files_found:
            pc_path_str = pc_file.as_posix()
            if pc_path_str not in existing_pc_paths:
                # --- ADDED: Read content and write internal text ---
                pc_content = utils.read_file(pc_path_str)
                if pc_content is not None:
                    text_editor.write_int_file(pc_path_str, pc_content) # This will use the base name now
                # --- END ADDED ---
                item = imported_pc_collection.add()
                item.path = pc_path_str
                pc_imported_count += 1
        # <<< END MODIFIED >>>

        # <<< ADDED: Final Summary Report >>>
        summary_message = (
            f"Folder Import Summary:\n"
            f"- JBeam Parts Imported: {jbeam_imported_count} (Skipped/Errors: {jbeam_skipped_count})\n"
            f"- PC Files Added: {pc_imported_count}\n"
        )
        if self.import_dae_files:
            summary_message += f"- DAE Files Imported: {dae_imported_count} (Skipped/Errors: {dae_skipped_count})"

        self.report({'INFO'}, summary_message)
        print(summary_message) # Also print to console
        # <<< END ADDED >>>

        return {'FINISHED'}
# <<< END ADDED >>>

# <<< ADDED: Operator to toggle a PC filter >>>
class JBEAM_EDITOR_OT_toggle_pc_filter(bpy.types.Operator):
    """Adds or removes a PC file path from the active filters."""
    bl_idname = "jbeam_editor.toggle_pc_filter"
    bl_label = "Toggle PC Filter"
    bl_options = {'REGISTER', 'INTERNAL'}

    pc_file_path: bpy.props.StringProperty()
    index: bpy.props.IntProperty() # Optional index, might not be needed

    def execute(self, context):
        scene = context.scene
        active_filters = scene.jbeam_editor_active_pc_filters
        found_idx = -1
        for i, item in enumerate(active_filters):
            if item.path == self.pc_file_path:
                found_idx = i
                break

        if found_idx != -1:
            # Remove existing filter
            active_filters.remove(found_idx)
        else:
            # Add new filter
            item = active_filters.add()
            item.path = self.pc_file_path

        # Manually call the update function since CollectionProperty doesn't have 'update'
        properties.update_pc_filters(scene, context) # Pass scene as 'self' for the update function context
        return {'FINISHED'}
# <<< END ADDED >>>

# <<< ADDED: Operator to clear all PC filters >>>
class JBEAM_EDITOR_OT_clear_pc_filters(bpy.types.Operator):
    """Clears all active PC file filters."""
    bl_idname = "jbeam_editor.clear_pc_filters"
    bl_label = "Clear All PC Filters"
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        active_filters = scene.jbeam_editor_active_pc_filters
        if active_filters:
            active_filters.clear()
            # Manually call the update function
            properties.update_pc_filters(scene, context) # Pass scene as 'self' for the update function context
            self.report({'INFO'}, "Cleared all PC filters.")
        else:
            self.report({'INFO'}, "No active PC filters to clear.")
        return {'FINISHED'}
# <<< END ADDED >>>

# <<< ADDED: Operator to reload a specific PC file >>>
class JBEAM_EDITOR_OT_reload_pc_file(bpy.types.Operator):
    """Reloads a specific Part Config (.pc) file from disk."""
    bl_idname = "jbeam_editor.reload_pc_file"
    bl_label = "Reload PC File"
    bl_description = "Reloads the selected .pc file from disk, updating the internal text and potentially filters"
    bl_options = {'REGISTER'} # Keep REGISTER if invoked by button

    pc_file_path: bpy.props.StringProperty(
        name="PC File Path",
        description="The full path to the .pc file to reload",
    )

    def execute(self, context):
        if not self.pc_file_path:
            self.report({'ERROR'}, "No PC file path specified.")
            return {'CANCELLED'}

        # 1. Read from disk
        file_content = utils.read_file(self.pc_file_path)
        if file_content is None:
            self.report({'ERROR'}, f"Could not read file from disk: {self.pc_file_path}")
            return {'CANCELLED'}

        # 2. Update internal text
        text_editor.write_int_file(self.pc_file_path, file_content)

        # 3. Trigger filter update
        properties.update_pc_filters(context.scene, context)
        self.report({'INFO'}, f"Reloaded PC file: {Path(self.pc_file_path).name}")
        return {'FINISHED'}
# <<< END ADDED >>>

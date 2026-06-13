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
import sys
import traceback
import time # Keep for potential future debugging
import json # <<< ADDED: Import json
from pathlib import Path # <<< ADDED: Import Path

from bpy.app.handlers import persistent
from mathutils import Vector

# Import from local modules
from . import constants
from . import globals as jb_globals # Import globals
# <<< MODIFIED: Import text_editor module itself >>>
from . import text_editor
# <<< END MODIFIED >>>
from . import export_vehicle
from . import export_jbeam
# Import drawing module and specific elements needed
from . import drawing
from .drawing import (
    refresh_curr_vdata, find_node_line_number, find_beam_line_number,
    _scroll_editor_to_line, all_nodes_cache, part_name_to_obj,
    # Import highlight coord lists
    highlight_coords, highlight_torsionbar_outer_coords, highlight_torsionbar_mid_coords,
    # <<< Import the moved highlight function >>>
    find_and_highlight_element_for_line, _tag_redraw_3d_views,
    # Import coordinate lists and batch variables for load_post_handler
    beam_coords, anisotropic_beam_coords, support_beam_coords, hydro_beam_coords, # <<< Keep existing imports
    bounded_beam_coords, lbeam_coords, pressured_beam_coords, torsionbar_coords,
    torsionbar_red_coords, rail_coords, cross_part_beam_coords,
    # <<< MODIFIED: Import single dynamic list >>>
    dynamic_beam_coords_colors,
    # Batches
    beam_render_batch, anisotropic_beam_render_batch, support_beam_render_batch,
    hydro_beam_render_batch, bounded_beam_render_batch, lbeam_render_batch,
    pressured_beam_render_batch, torsionbar_render_batch, torsionbar_red_render_batch,
    rail_render_batch, cross_part_beam_render_batch,
    # <<< MODIFIED: Import single dynamic batch >>>
    dynamic_beam_batch,
    # Highlight batches
    highlight_render_batch, highlight_torsionbar_outer_batch, highlight_torsionbar_mid_batch,
    # <<< ADDED: Import highlight dirty flag >>>
    _highlight_dirty,
)
# <<< MODIFIED: Remove JBEAM_EDITOR_OT_confirm_text_deletion import >>>
from .operators import JBEAM_EDITOR_OT_batch_node_renaming # Import operators
from .text_editor import SCENE_SHORT_TO_FULL_FILENAME
# from .sjsonast import parse as sjsonast_parse, calculate_char_positions as sjsonast_calculate_char_positions, ASTNode # <<< Remove ASTNode import here
from . import utils # <<< ADDED: Import utils for show_message_box

# Timer intervals
check_file_interval = 0.1
poll_active_ops_interval = 0.1 # Keep this relatively fast for responsiveness

# Operators that should NOT trigger an export
op_no_export = {
    'OBJECT_OT_editmode_toggle',
    JBEAM_EDITOR_OT_batch_node_renaming.bl_idname,
    'VIEW3D_OT_rotate',
    'VIEW3D_OT_move',
    'VIEW3D_OT_zoom',
    'VIEW3D_OT_dolly',
    'SCREEN_OT_screen_full_area',
    'SCREEN_OT_back_to_previous',
    'OBJECT_OT_select',
    'MESH_OT_select_all',
    'MESH_OT_select_linked',
    'MESH_OT_select_more',
    'MESH_OT_select_less',
    'MESH_OT_select_random',
    'MESH_OT_select_mirror',
    'MESH_OT_select_similar',
    'MESH_OT_select_mode',
    'jbeam_editor.find_node',
    'jbeam_editor.scroll_to_definition',
    'TEXT_OT_cursor', # Add text cursor movement ops
    'TEXT_OT_move_select',
    'TEXT_OT_move',
    'TEXT_OT_scroll_bar',
    'TEXT_OT_scroll',
    # <<< ADDED: Prevent export trigger from native undo/redo itself >>>
    'ed.undo',
    'ed.redo',
    # <<< END ADDED >>>
}

_last_op = None # Used in poll_active_operators

# <<< ADDED: Globals for deletion tracking >>>
previous_known_jbeam_objects = set() # Store {(name, filepath)}
texts_pending_deletion_check = set() # Store {(short_filename, filepath)}

# --- Highlight on Click Logic ---

# <<< DRAW HANDLER FUNCTION >>>
def draw_callback_text_editor(context: bpy.types.Context):
    """Draw handler for Text Editor space to handle highlighting."""
    scene = context.scene
    ui_props = scene.ui_properties
    area = context.area
    space = area.spaces.active

    if not space or space.type != 'TEXT_EDITOR':
        return

    text_obj = space.text
    highlight_needs_clearing = False
    process_highlight = False

    if text_obj:
        if ui_props.highlight_element_on_click:
            current_line_index = text_obj.current_line_index
            # Check if the text or line has changed since the last successful highlight check
            # This handles cursor movement and switching files. Content changes are handled by check_open_int_file_for_changes.
            if (text_obj.name != jb_globals.last_text_area_info['name'] or
                    current_line_index != jb_globals.last_text_area_info['line_index']):
                process_highlight = True
        else:
            # Toggle is off, mark for clearing if highlight exists
            if jb_globals.highlighted_element_type is not None:
                highlight_needs_clearing = True
                jb_globals.last_text_area_info['name'] = None # Reset last info
                jb_globals.last_text_area_info['line_index'] = -1
    else:
        # No text object, mark for clearing if highlight exists
        if jb_globals.highlighted_element_type is not None:
            highlight_needs_clearing = True
            jb_globals.last_text_area_info['name'] = None # Reset last info
            jb_globals.last_text_area_info['line_index'] = -1

    # Process highlighting if needed (due to cursor move or file switch)
    if process_highlight:
        # Call the highlight function for the current line
        # This function now clears previous coords internally first
        try:
            success = find_and_highlight_element_for_line(context, text_obj, current_line_index)
            # find_and_highlight_element_for_line now clears node IDs internally
        except Exception as e:
            print(f"Error during highlight update on cursor move: {e}", file=sys.stderr)
            traceback.print_exc()
            # Ensure highlight is cleared on error (including node IDs)
            highlight_coords.clear()
            highlight_torsionbar_outer_coords.clear()
            highlight_torsionbar_mid_coords.clear()
            jb_globals.highlighted_node_ids.clear() # <<< ADDED: Clear node IDs on error
            jb_globals.highlighted_element_type = None
            _tag_redraw_3d_views(context) # Always tag redraw for highlight update
            # <<< ADDED: Mark highlight dirty if it was previously active >>>
            drawing._highlight_dirty = True # Use drawing module's flag
            if text_obj: jb_globals.last_text_area_info['name'] = text_obj.name
            jb_globals.last_text_area_info['line_index'] = current_line_index

    # Clear highlight if marked (e.g., toggle turned off)
    elif highlight_needs_clearing:
        was_highlighted = jb_globals.highlighted_element_type is not None # Check before clearing
        highlight_coords.clear()
        highlight_torsionbar_outer_coords.clear()
        highlight_torsionbar_mid_coords.clear()
        jb_globals.highlighted_node_ids.clear() # <<< ADDED: Clear node IDs when toggled off
        jb_globals.highlighted_element_ordered_node_ids.clear() # Clear ordered list too
        jb_globals.highlighted_element_type = None
        _tag_redraw_3d_views(context) # Always tag redraw for highlight update
        # <<< ADDED: Mark highlight dirty if it was previously active >>>
        if was_highlighted:
            drawing._highlight_dirty = True # Use drawing module's flag
# <<< END DRAW HANDLER FUNCTION >>>

# --- End Highlight on Click Logic ---


# Depsgraph Update Handler
def _depsgraph_callback(context: bpy.types.Context, scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    # <<< ADDED: Access globals >>>
    global previous_known_jbeam_objects, texts_pending_deletion_check
    # (Keep existing _depsgraph_callback content)
    # ... (rest of the function remains the same) ...
    reimporting_jbeam = False
    if isinstance(scene.get('jbeam_editor_reimporting_jbeam'), int):
        scene['jbeam_editor_reimporting_jbeam'] -= 1
        if scene['jbeam_editor_reimporting_jbeam'] < 0:
            scene['jbeam_editor_reimporting_jbeam'] = 0
        else:
            reimporting_jbeam = True

    ui_props = scene.ui_properties
    active_obj = context.active_object

    if active_obj is None or active_obj.data is None:
        jb_globals._selected_beam_line_info = None; jb_globals._selected_beam_params_info = None
        jb_globals._selected_node_params_info = None; jb_globals._selected_node_line_info = None
        return
    active_obj_data = active_obj.data

    is_jbeam_obj = active_obj_data.get(constants.MESH_JBEAM_PART) is not None
    if not is_jbeam_obj:
        jb_globals._selected_beam_line_info = None; jb_globals._selected_beam_params_info = None
        jb_globals._selected_node_params_info = None; jb_globals._selected_node_line_info = None
        refresh_curr_vdata()
        return

    refresh_curr_vdata()

    jbeam_filepath = active_obj_data.get(constants.MESH_JBEAM_FILE_PATH)
    if jbeam_filepath:
        text_editor.show_int_file(jbeam_filepath)

    mesh_editing_enabled = active_obj_data.get(constants.MESH_EDITING_ENABLED, False)
    if active_obj.mode != 'EDIT' or not mesh_editing_enabled:
        jb_globals._selected_beam_line_info = None; jb_globals._selected_beam_params_info = None
        jb_globals._selected_node_params_info = None; jb_globals._selected_node_line_info = None
        return

    active_obj_eval: bpy.types.Object = active_obj.evaluated_get(depsgraph)

    if not reimporting_jbeam:
        for update in depsgraph.updates:
            if update.id.original == active_obj:
                if update.is_updated_geometry or update.is_updated_transform:
                    jb_globals._do_export = True
                    drawing.veh_render_dirty = True # Use drawing module's global

    veh_model = active_obj_data.get(constants.MESH_VEHICLE_MODEL)
    if veh_model is not None:
        veh_collection = bpy.data.collections.get(veh_model)
        if veh_collection is not None:
            # Import find_layer_collection_recursive from registration or move it to utils
            from .registration import find_layer_collection_recursive
            current_active_layer_col = context.view_layer.active_layer_collection
            if current_active_layer_col is None or current_active_layer_col.collection != veh_collection:
                layer = find_layer_collection_recursive(veh_collection, context.view_layer.layer_collection)
                if layer is not None:
                    context.view_layer.active_layer_collection = layer

    bm = None
    try:
        bm = bmesh.from_edit_mesh(active_obj_data)
    except Exception as e:
        print(f"Error getting bmesh in depsgraph callback: {e}", file=sys.stderr)
        return

    init_node_id_layer = bm.verts.layers.string.get(constants.VL_INIT_NODE_ID)
    node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
    is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
    beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
    face_idx_layer = bm.faces.layers.int.get(constants.FL_FACE_IDX)
    beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
    face_part_origin_layer = bm.faces.layers.string.get(constants.FL_FACE_PART_ORIGIN)
    node_part_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN)

    if not all([init_node_id_layer, node_id_layer, is_fake_layer, beam_indices_layer, face_idx_layer, beam_part_origin_layer, face_part_origin_layer, node_part_origin_layer]):
        print("Warning: One or more JBeam layers missing from mesh.", file=sys.stderr)
        return

    bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()

    current_vert_count = active_obj_data.get(constants.MESH_VERTEX_COUNT, 0)
    current_edge_count = active_obj_data.get(constants.MESH_EDGE_COUNT, 0)
    current_face_count = active_obj_data.get(constants.MESH_FACE_COUNT, 0)
    new_vert_count = len(bm.verts); new_edge_count = len(bm.edges); new_face_count = len(bm.faces)

    current_selected_indices = set()
    newly_selected_vert_index = -1
    num_currently_selected = 0

    for v in bm.verts:
        if v[is_fake_layer]: continue
        if v.select:
            current_selected_indices.add(v.index)
            num_currently_selected += 1
            if v.index not in jb_globals.previous_selected_indices:
                if newly_selected_vert_index == -1: newly_selected_vert_index = v.index
                else: newly_selected_vert_index = -2

    if jb_globals.batch_node_renaming_enabled and newly_selected_vert_index >= 0:
        try:
            vert_to_rename = bm.verts[newly_selected_vert_index]
            new_node_id: str = ui_props.batch_node_renaming_naming_scheme
            if '#' in new_node_id:
                new_node_id = new_node_id.replace('#', f'{ui_props.batch_node_renaming_node_idx}')
                vert_to_rename[node_id_layer] = bytes(new_node_id, 'utf-8')
                ui_props.batch_node_renaming_node_idx += 1
                jb_globals._force_do_export = True
            else:
                 print(f"Warning: Batch rename scheme '{ui_props.batch_node_renaming_naming_scheme}' does not contain '#'. No rename performed.")
        except IndexError: print(f"Error: Could not find vertex with index {newly_selected_vert_index} for renaming.")
        except Exception as rename_err: print(f"Error during batch renaming: {rename_err}")

    # --- Update Node Selection ---
    node_selection_changed = False
    if len(current_selected_indices) != len(jb_globals.previous_selected_indices) or \
       current_selected_indices != jb_globals.previous_selected_indices:
        node_selection_changed = True
        jb_globals.selected_nodes.clear()
        for idx in current_selected_indices:
            try:
                v = bm.verts[idx]
                jb_globals.selected_nodes.append((idx, v[init_node_id_layer].decode('utf-8')))
            except IndexError: pass
        # <<< ADDED: Set veh_render_dirty if node selection changed >>>
        drawing.veh_render_dirty = True
        jb_globals.previous_selected_indices = current_selected_indices
    # --- End Update Node Selection ---

    # --- MODIFICATION START ---
    for i, v in enumerate(bm.verts):
        if i >= current_vert_count: # If this is a newly added vertex
            # Assign a temporary ID. The final L/R/M prefix will be added during export.
            temp_node_id = f"TEMP_{uuid.uuid4()}" # Use TEMP_ prefix for easy identification

            temp_node_id_bytes = bytes(temp_node_id, 'utf-8')
            v[init_node_id_layer] = temp_node_id_bytes # Assign as initial ID
            v[node_id_layer] = temp_node_id_bytes      # Assign as current ID
            v[node_part_origin_layer] = bytes(active_obj_data[constants.MESH_JBEAM_PART], 'utf-8') # Assign part origin
            # v[is_fake_layer] should default to 0 (or be set if necessary)
    # --- MODIFICATION END ---

    # --- Update Beam Selection ---
    beam_selection_changed = False
    current_selected_beam_indices = set()
    for i, e in enumerate(bm.edges):
        beam_indices = e[beam_indices_layer].decode('utf-8')
        if i >= current_edge_count:
            if beam_indices == '':
                e[beam_indices_layer] = bytes('-1', 'utf-8')
                e[beam_part_origin_layer] = bytes(active_obj_data[constants.MESH_JBEAM_PART], 'utf-8')
        if beam_indices != '' and e.select:
            current_selected_beam_indices.add(e.index) # Add index to current set

    # Compare current beam selection with previous state
    if current_selected_beam_indices != jb_globals.selected_beam_edge_indices:
        beam_selection_changed = True
        jb_globals.selected_beam_edge_indices = current_selected_beam_indices
        # Update the list used for properties/tooltips
        jb_globals.selected_beams.clear()
        for edge_index in current_selected_beam_indices:
            try:
                e = bm.edges[edge_index]
                beam_indices_str = e[beam_indices_layer].decode('utf-8')
                jb_globals.selected_beams.append((edge_index, beam_indices_str))
            except (IndexError, ReferenceError): pass # Ignore if edge becomes invalid
        drawing.veh_render_dirty = True # Trigger redraw if beam selection changed
    # --- End Update Beam Selection ---

    jb_globals.selected_tris_quads.clear()
    for i, f in enumerate(bm.faces):
        face_idx = f[face_idx_layer]
        if i >= current_face_count:
            if face_idx == 0:
                f[face_idx_layer] = -1
                f[face_part_origin_layer] = bytes(active_obj_data[constants.MESH_JBEAM_PART], 'utf-8')
        if face_idx != 0 and f.select:
            # <<< CHANGE: Store face index (f.index) instead of the BMFace object (f) >>>
            jb_globals.selected_tris_quads.append((f.index, face_idx))

    if new_vert_count != current_vert_count: active_obj_data[constants.MESH_VERTEX_COUNT] = new_vert_count
    if new_edge_count != current_edge_count: active_obj_data[constants.MESH_EDGE_COUNT] = new_edge_count
    if new_face_count != current_face_count: active_obj_data[constants.MESH_FACE_COUNT] = new_face_count

    if len(jb_globals.selected_nodes) == 1:
        vert_index, init_node_id = jb_globals.selected_nodes[0]
        try:
            v = bm.verts[vert_index]
            current_node_id = v[node_id_layer].decode('utf-8')
            if ui_props.input_node_id != current_node_id:
                ui_props.input_node_id = current_node_id
        except IndexError:
             if ui_props.input_node_id != "": ui_props.input_node_id = ""

    # --- Tooltip Logic ---
    jb_globals._selected_beam_line_info = None; jb_globals._selected_beam_params_info = None
    jb_globals._selected_node_params_info = None; jb_globals._selected_node_line_info = None

    if len(jb_globals.selected_nodes) == 1:
        vert_index, node_id = jb_globals.selected_nodes[0]
        node_world_pos = active_obj.matrix_world @ bm.verts[vert_index].co

        if jb_globals.curr_vdata and 'nodes' in jb_globals.curr_vdata and node_id in jb_globals.curr_vdata['nodes']:
            node_data = jb_globals.curr_vdata['nodes'][node_id]; params_list = []
            from .utils import Metadata # Local import for Metadata check
            for k in sorted(node_data.keys(), key=lambda x: str(x)):
                if k == Metadata or k == 'pos' or k == 'posNoOffset': continue
                params_list.append((k, repr(node_data[k])))
            if params_list: jb_globals._selected_node_params_info = {'params_list': params_list, 'pos': node_world_pos}
            else: jb_globals._selected_node_params_info = {'params_list': [("(No properties)", "")], 'pos': node_world_pos}

        try:
            target_part_origin = bm.verts[vert_index][node_part_origin_layer].decode('utf-8')
            if target_part_origin and jbeam_filepath:
                line_num = find_node_line_number(jbeam_filepath, target_part_origin, node_id)
                if line_num is not None: jb_globals._selected_node_line_info = {'line': line_num, 'pos': node_world_pos}
        except Exception as find_err: print(f"Error processing node line tooltip: {find_err}", file=sys.stderr); traceback.print_exc()

    elif len(jb_globals.selected_beams) == 1:
        edge_index, beam_indices_str = jb_globals.selected_beams[0]
        try:
            e = bm.edges[edge_index]
        except (IndexError, ReferenceError) as get_edge_err:
            print(f"Warning: Could not access selected beam edge in depsgraph: {get_edge_err}", file=sys.stderr)
            return

        beam_indices = beam_indices_str.split(',')
        if beam_indices:
            try:
                target_part_origin = e[beam_part_origin_layer].decode('utf-8')
                midpoint = active_obj.matrix_world @ ((e.verts[0].co + e.verts[1].co) / 2)

                if target_part_origin and jbeam_filepath:
                    v1 = e.verts[0]; v2 = e.verts[1]
                    node_id1 = v1[init_node_id_layer].decode('utf-8'); node_id2 = v2[init_node_id_layer].decode('utf-8')
                    line_num = find_beam_line_number(jbeam_filepath, target_part_origin, node_id1, node_id2)
                    if line_num is not None: jb_globals._selected_beam_line_info = {'line': line_num, 'midpoint': midpoint}

                target_beam_idx_in_part = int(beam_indices[0])
                if jb_globals.curr_vdata and 'beams' in jb_globals.curr_vdata and target_beam_idx_in_part > 0:
                    global_beam_idx = -1; current_part_beam_count = 0
                    for i, b in enumerate(jb_globals.curr_vdata['beams']):
                        if b.get('partOrigin') == target_part_origin:
                            current_part_beam_count += 1
                            if current_part_beam_count == target_beam_idx_in_part: global_beam_idx = i; break
                    if global_beam_idx != -1 and global_beam_idx < len(jb_globals.curr_vdata['beams']):
                        beam_data = jb_globals.curr_vdata['beams'][global_beam_idx]; params_list = []
                        from .utils import Metadata # Local import for Metadata check
                        for k in sorted(beam_data.keys(), key=lambda x: str(x)):
                            if k in ('id1:', 'id2:', 'partOrigin') or k == Metadata: continue
                            params_list.append((k, repr(beam_data[k])))
                        if params_list: jb_globals._selected_beam_params_info = {'params_list': params_list, 'midpoint': midpoint}
                        else: jb_globals._selected_beam_params_info = {'params_list': [("(No properties)", "")], 'midpoint': midpoint}
                    else: print(f"  Warning: Global beam index {global_beam_idx} not found or invalid for part '{target_part_origin}' for param lookup.")
            except ValueError: print(f"Warning: Could not parse beam index: {beam_indices_str}", file=sys.stderr)
            except Exception as find_err: print(f"Error processing beam tooltips: {find_err}", file=sys.stderr); traceback.print_exc()

@persistent
def depsgraph_update_post_handler(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    context = bpy.context
    try:
        _depsgraph_callback(context, scene, depsgraph)
    except Exception as e:
        print(f"Error in depsgraph callback: {e}", file=sys.stderr)
        traceback.print_exc()

    # --- Detect Deleted JBeam Objects ---
    try:
        # <<< ADDED: Explicitly declare globals within this scope >>>
        global previous_known_jbeam_objects, texts_pending_deletion_check
        current_jbeam_objects_info = set()
        # Use scene.objects which is generally available here
        for obj in scene.objects:
             obj_data = obj.data
             if obj_data and obj_data.get(constants.MESH_JBEAM_PART) is not None:
                 filepath = obj_data.get(constants.MESH_JBEAM_FILE_PATH)
                 if filepath:
                     current_jbeam_objects_info.add((obj.name, filepath))

        deleted_objects_info = previous_known_jbeam_objects - current_jbeam_objects_info

        if deleted_objects_info:
            # Map filepath to remaining users
            filepath_users = {}
            for _, fp in current_jbeam_objects_info:
                filepath_users[fp] = filepath_users.get(fp, 0) + 1

            for deleted_name, deleted_filepath in deleted_objects_info:
                # Check if the filepath of the deleted object has any remaining users
                if filepath_users.get(deleted_filepath, 0) == 0:
                    short_filename = text_editor._to_short_filename(deleted_filepath)
                    # Check if text exists and add to pending check set
                    if bpy.data.texts.get(short_filename):
                        texts_pending_deletion_check.add((short_filename, deleted_filepath))

        # Update known objects for the next cycle
        previous_known_jbeam_objects = current_jbeam_objects_info

        # --- Process Pending Deletion Checks ---
        # Process immediately after the loop.
        if texts_pending_deletion_check:
            # Use list() to avoid modifying set during iteration
            for short_filename, filepath in list(texts_pending_deletion_check):
                # Double-check usage *now*
                is_still_unused = True
                for obj in scene.objects: # Check current objects again
                    obj_data = obj.data
                    if obj_data and obj_data.get(constants.MESH_JBEAM_PART) is not None:
                        fp = obj_data.get(constants.MESH_JBEAM_FILE_PATH)
                        if fp == filepath:
                            is_still_unused = False
                            break

                if is_still_unused:
                    # <<< MODIFICATION: Remove item BEFORE invoking operator >>>
                    # Use discard() instead of remove() to prevent KeyError if already removed by another handler instance
                    texts_pending_deletion_check.discard((short_filename, filepath))

                    # Check if text still exists before invoking
                    if bpy.data.texts.get(short_filename):
                        try:
                            # Pass properties to the operator
                            # <<< MODIFIED: Use string literal for bl_idname >>>
                            bpy.ops.jbeam_editor.confirm_text_deletion('INVOKE_DEFAULT', # Use string literal
                                                                        short_filename=short_filename,
                                                                        filepath=filepath)
                        except Exception as e:
                             print(f"Error invoking text deletion operator: {e}", file=sys.stderr)

                else: # <<< ADDED: Else block for the is_still_unused check >>>
                    # If it's not unused anymore, just remove it from the pending set
                    texts_pending_deletion_check.discard((short_filename, filepath)) # Use discard for safety

    except Exception as e:
        print(f"Error in JBeam object deletion detection: {e}", file=sys.stderr)
        traceback.print_exc()
        texts_pending_deletion_check.clear() # Clear pending checks on error

# Timer to check for text editor changes
@persistent
def check_files_for_changes_timer():
    context = bpy.context
    try:
        changed = text_editor.check_open_int_file_for_changes(context)
        if changed:
            refresh_curr_vdata(True)
    except Exception as e:
        print(f"Error checking files for changes: {e}", file=sys.stderr)
    return check_file_interval

# Timer to poll active operators and trigger export
@persistent
def poll_active_operators_timer():
    global _last_op # Use the global _last_op defined in this file
    context = bpy.context
    scene = context.scene
    ui_props = scene.ui_properties
    op = context.active_operator
    wm = context.window_manager

    try:
        # --- Auto Export Logic ---
        active_obj = context.active_object # Get active object here if needed for export
        if active_obj is not None and active_obj.data is not None:
            active_obj_data = active_obj.data
            if active_obj_data.get(constants.MESH_JBEAM_PART) is not None and active_obj_data.get(constants.MESH_EDITING_ENABLED, False):
                op_changed = op != _last_op
                is_export_trigger_op = op is not None and op.bl_idname not in op_no_export
                should_export = jb_globals._force_do_export or (jb_globals._do_export and op_changed and is_export_trigger_op)

                if should_export:
                    if jb_globals.confirm_delete_pending:
                        print("Export skipped: Node deletion confirmation is pending.")
                        jb_globals._do_export = False
                        jb_globals._force_do_export = False
                    else:
                        veh_model = active_obj_data.get(constants.MESH_VEHICLE_MODEL)
                        if veh_model is not None: export_vehicle.auto_export(active_obj, veh_model)
                        else: export_jbeam.auto_export(active_obj)
                        refresh_curr_vdata(True)
                        jb_globals._do_export = False; jb_globals._force_do_export = False
            else:
                jb_globals._do_export = False; jb_globals._force_do_export = False
        else:
            jb_globals._do_export = False; jb_globals._force_do_export = False
        # --- End Auto Export Logic ---

    except Exception as e:
        print(f"Error polling active operators: {e}", file=sys.stderr) # Modified error message
        traceback.print_exc()
        jb_globals._do_export = False; jb_globals._force_do_export = False
        # Clear highlight on error
        if jb_globals.highlighted_element_type is not None:
            highlight_coords.clear()
            highlight_torsionbar_outer_coords.clear()
            highlight_torsionbar_mid_coords.clear()
            jb_globals.highlighted_node_ids.clear() # <<< ADDED: Clear node IDs on error
            jb_globals.highlighted_element_type = None
            # Clear batches directly in drawing module
            drawing.highlight_render_batch = None
            drawing.highlight_torsionbar_outer_batch = None
            drawing.highlight_torsionbar_mid_batch = None
            drawing.veh_render_dirty = True
    finally:
         _last_op = op # Update _last_op regardless of errors

    return poll_active_ops_interval

# Handler to run after registration is complete
@persistent
def on_post_register_handler():
    # Import draw handles from drawing.py
    from .drawing import draw_callback_px, draw_callback_view
    # Import draw_handle, draw_handle2 from registration.py where they are managed
    from .registration import draw_handle, draw_handle2, text_draw_handle # <<< Import text_draw_handle

    try:
        if bpy.context.window_manager and bpy.context.window:
            # Need to assign the handles back to the registration module's scope
            from . import registration
            # Register 3D View handlers
            registration.draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_px, (bpy.context,), 'WINDOW', 'POST_PIXEL')
            if not constants.UNIT_TESTING:
                registration.draw_handle2 = bpy.types.SpaceView3D.draw_handler_add(draw_callback_view, (bpy.context,), 'WINDOW', 'POST_VIEW')
            # <<< Register Text Editor handler >>>
            registration.text_draw_handle = bpy.types.SpaceTextEditor.draw_handler_add(draw_callback_text_editor, (bpy.context,), 'WINDOW', 'POST_PIXEL')
            print("Registered draw handlers (3D View + Text Editor)") # DEBUG
        else:
             print("Warning: Could not add draw handlers, context invalid during registration.", file=sys.stderr)
    except Exception as e:
        print(f"Error adding draw handlers: {e}", file=sys.stderr)
        traceback.print_exc() # Print traceback for handler registration errors

# Handler to reset state on file load/revert >>>
@persistent
def load_post_handler(dummy):
    print("JBeam Editor: Resetting state after file load/revert...")
    # Reset selection tracking
    jb_globals.prev_obj_selected = None
    jb_globals.selected_nodes.clear()
    jb_globals.selected_beams.clear()
    jb_globals.selected_beam_edge_indices.clear() # <<< ADDED: Clear new set
    jb_globals.selected_tris_quads.clear()
    jb_globals.previous_selected_indices.clear()

    # Reset data cache
    jb_globals.curr_vdata = None

    # Reset tooltip data
    jb_globals._selected_beam_line_info = None
    jb_globals._selected_beam_params_info = None
    jb_globals._selected_node_params_info = None
    jb_globals._selected_node_line_info = None

    # Reset highlight state
    jb_globals.highlighted_element_type = None
    jb_globals.highlighted_node_ids.clear() # <<< ADDED: Clear highlighted node IDs
    jb_globals.last_text_area_info = {'name': None, 'line_index': -1}

    # <<< ADDED: Explicitly reset text editor history >>>
    text_editor.history_stack.clear()
    text_editor.history_stack_idx = -1
    print("JBeam Editor: Cleared text editor undo/redo history.")
    # <<< END ADDED >>>

    # Reset drawing state
    drawing.veh_render_dirty = True # Force redraw on next cycle
    drawing.all_nodes_cache_dirty = True # Force node cache rebuild
    drawing.all_nodes_cache.clear()
    drawing.part_name_to_obj.clear()
    # <<< ADDED: Reset variable cache state >>>
    jb_globals.jbeam_variables_cache.clear()
    jb_globals.jbeam_variables_cache_dirty = True
    # <<< ADDED: Reset deletion tracking state >>>
    global previous_known_jbeam_objects, texts_pending_deletion_check
    previous_known_jbeam_objects.clear()
    texts_pending_deletion_check.clear()
    # <<< ADDED: Reset PC filter state >>>
    if hasattr(bpy.context.scene, 'jbeam_editor_imported_pc_files'): bpy.context.scene.jbeam_editor_imported_pc_files.clear()
    if hasattr(bpy.context.scene, 'jbeam_editor_active_pc_filters'): bpy.context.scene.jbeam_editor_active_pc_filters.clear()
    # <<< END ADDED >>>

    # Clear coordinate lists
    drawing.beam_coords.clear()
    # <<< MODIFIED: Clear single dynamic list >>>
    drawing.dynamic_beam_coords_colors.clear()
    # <<< END MODIFIED >>>
    drawing.anisotropic_beam_coords.clear()
    drawing.support_beam_coords.clear()
    drawing.hydro_beam_coords.clear()
    drawing.bounded_beam_coords.clear()
    drawing.lbeam_coords.clear()
    drawing.pressured_beam_coords.clear()
    drawing.torsionbar_coords.clear()
    drawing.torsionbar_red_coords.clear()
    drawing.rail_coords.clear()
    drawing.cross_part_beam_coords.clear()
    drawing.highlight_coords.clear()
    drawing.highlight_torsionbar_outer_coords.clear()
    drawing.highlight_torsionbar_mid_coords.clear()

    # Clear batch variables (set to None)
    # Need to use 'global' keyword if modifying module-level variables directly
    # Or better, access them via the module name 'drawing.'
    drawing.beam_render_batch = None
    # <<< MODIFIED: Clear single dynamic batch >>>
    drawing.dynamic_beam_batch = None
    # <<< END MODIFIED >>>
    drawing.anisotropic_beam_render_batch = None
    drawing.support_beam_render_batch = None
    drawing.hydro_beam_render_batch = None
    drawing.bounded_beam_render_batch = None
    drawing.lbeam_render_batch = None
    drawing.pressured_beam_render_batch = None
    drawing.torsionbar_render_batch = None
    drawing.torsionbar_red_render_batch = None
    drawing.rail_render_batch = None
    drawing.cross_part_beam_render_batch = None
    drawing.highlight_render_batch = None
    drawing.highlight_torsionbar_outer_batch = None
    drawing.highlight_torsionbar_mid_batch = None

    # --- ADDED: Explicitly try to refresh data after reset ---
    try:
        # Need context for refresh_curr_vdata
        context = bpy.context
        if context and context.window_manager: # Check context validity
            refresh_curr_vdata(True) # Force refresh after state reset
            print("JBeam Editor: Triggered initial data refresh after load.")
        else:
            # This might happen if the handler runs too early
            print("JBeam Editor: Context not fully available for initial refresh after load.")
    except Exception as e:
        print(f"JBeam Editor: Error during initial data refresh after load: {e}", file=sys.stderr)
        traceback.print_exc()
    # --- END ADDED ---

    # Optional: Trigger a redraw of UI areas if needed
    # _tag_redraw_3d_views(bpy.context) # Might be useful

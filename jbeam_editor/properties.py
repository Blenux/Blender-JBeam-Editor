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
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from pathlib import Path
import sys # Added import
import json # <<< ADDED: Import json

import bpy
import bmesh
import traceback

# Import from local modules
from . import constants
from . import globals as jb_globals # Import globals
# Import drawing module to access its state/functions if needed later
# <<< ADDED: Import drawing module directly for setting dirty flag >>>
from . import drawing
# Import the update function from drawing.py after it's defined there
# <<< ADDED: Import utils, text_editor, bng_sjson >>>
from . import utils, import_vehicle
# This avoids circular import if drawing needs properties
# <<< MODIFIED: Removed _update_dynamic_beam_coloring from this import >>>
# <<< MODIFIED: Added _tag_redraw_3d_views >>>

# <<< ADDED: Update function for native faces visibility >>>
def _update_native_faces_vis(self, context):
    """
    Handles changes to the native faces visibility toggle.
    If turned ON, unhides all faces in Edit Mode for the active JBeam object.
    If turned OFF, the depsgraph handler will hide them.
    Also tags 3D views for redraw.
    """
    active_obj = context.active_object

    # Only proceed if we have an active mesh object in Edit Mode
    if not (active_obj and active_obj.type == 'MESH' and active_obj.mode == 'EDIT'):
        drawing._tag_redraw_3d_views(context) # Still redraw for UI update
        return

    # Ensure it's a JBeam part and editing is enabled for it
    obj_data = active_obj.data
    if not (obj_data.get(constants.MESH_JBEAM_PART) is not None and \
            obj_data.get(constants.MESH_EDITING_ENABLED, False)):
        drawing._tag_redraw_3d_views(context) # Still redraw for UI update
        return

    # At this point, we are in Edit Mode with a valid, enabled JBeam object.
    bm = None
    try:
        bm = bmesh.from_edit_mesh(obj_data)
        bm.faces.ensure_lookup_table()

        if self.toggle_native_faces_vis: # If toggle is now ON
            needs_update = False
            # Unhide all faces
            for f in bm.faces:
                if f.hide: # If face is currently hidden
                    f.hide = False # Unhide it
                    needs_update = True
            # Unhide all vertices
            bm.verts.ensure_lookup_table()
            for v_iter in bm.verts: # Use v_iter
                if v_iter.hide:
                    v_iter.hide = False
                    needs_update = True
            # Unhide all edges
            bm.edges.ensure_lookup_table()
            for e_iter in bm.edges: # Use e_iter
                if e_iter.hide:
                    e_iter.hide = False
                    needs_update = True
            if needs_update:
                bmesh.update_edit_mesh(obj_data)
        # If toggle is OFF, the depsgraph_update_post_handler will take care of hiding.
    except Exception as e:
        print(f"Error in _update_native_faces_vis: {e}", file=sys.stderr)

    drawing._tag_redraw_3d_views(context)

# <<< ADDED: Update function for show_console_warnings_missing_nodes toggle >>>
def _update_show_console_warnings_missing_nodes(self, context):
    """
    Update function for the 'Show Node Not Found Warnings' toggle.
    If turned on, marks the all_nodes_cache as dirty to force a rebuild,
    which will re-evaluate which nodes are missing.
    Also marks the main visualization as dirty.
    """
    if self.show_console_warnings_missing_nodes:
        # If the toggle is turned ON, we want to re-check for missing nodes.
        # Marking the cache dirty will trigger a rebuild on the next drawing cycle.
        setattr(drawing, 'all_nodes_cache_dirty', True)
        # Also clear the set of already warned nodes for this rebuild cycle,
        # so warnings are re-issued if nodes are still missing after a cache update.
        if hasattr(drawing, 'warned_missing_nodes_this_rebuild'):
            drawing.warned_missing_nodes_this_rebuild.clear()

    # Always mark the main visualization dirty to ensure drawing updates.
    setattr(drawing, 'veh_render_dirty', True)
    drawing._tag_redraw_3d_views(context) # Tag for UI updates
# <<< END ADDED >>>

from .drawing import (
    _update_toggle_cross_part_beams_vis,
    veh_render_dirty,
    _tag_redraw_3d_views,
    resolve_jbeam_variable_value # <<< ADDED: Import resolve function
)

# <<< ADDED: Import the helper function >>>
# <<< MODIFIED: Import renamed helper >>>
from .operators import _find_and_frame_element_logic
# <<< ADDED: Import globals >>>
# <<< ADDED: Import TYPE_CHECKING for type hinting UIProperties >>>
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    class UIProperties(bpy.types.PropertyGroup): # Forward declaration for type hinting
        node_weight_variables: bpy.props.CollectionProperty
        # Add other UIProperties fields here if needed for type hinting in this file
else:
    class UIProperties(bpy.types.PropertyGroup): # Actual definition at the end
        pass
# <<< MODIFIED: Import text_editor and bng_sjson >>>
from . import globals as jb_globals, text_editor, bng_sjson
# from . import globals as jb_globals # This line is redundant

# Refresh property input field UI
# Simplified rename logic
def on_input_node_id_field_updated(self, context: bpy.types.Context):
    scene = context.scene
    ui_props = scene.ui_properties
    obj = context.active_object

    # Basic checks: Ensure we have a valid JBeam object, editing is enabled, and exactly one node is selected.
    if (obj is None or
            obj.data.get(constants.MESH_JBEAM_PART) is None or
            not obj.data.get(constants.MESH_EDITING_ENABLED, False) or
            len(jb_globals.selected_nodes) != 1):
        return

    try:
        # Get the index of the selected vertex
        selected_vert_index = jb_globals.selected_nodes[0][0]
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        bm.verts.ensure_lookup_table() # Ensure lookup table is available

        node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID]
        vert = bm.verts[selected_vert_index]
        current_node_id = vert[node_id_layer].decode('utf-8')
        new_node_id = ui_props.input_node_id.strip() # Get the value from the UI

        # Only perform rename if the UI value is different from the current node ID and not empty
        if new_node_id and new_node_id != current_node_id:
            print(f"Renaming node {current_node_id} (index {selected_vert_index}) to {new_node_id}")
            vert[node_id_layer] = bytes(new_node_id, 'utf-8')
            jb_globals._force_do_export = True
            # Signal that the next export should consider the local rename toggle
            jb_globals._use_local_rename_toggle_for_next_export = True

            # Symmetrical renaming logic
            if ui_props.rename_symmetrical_counterpart:
                from .export_utils import get_symmetrical_node_id # Helper function

                # 1. Get original counterpart ID (based on ID before primary rename)
                original_counterpart_id = get_symmetrical_node_id(current_node_id, ui_props)

                if original_counterpart_id:
                    # 2. Get new counterpart ID (based on the new ID of the primary node)
                    new_counterpart_id = get_symmetrical_node_id(new_node_id, ui_props)

                    if new_counterpart_id:
                        # 3. Find the BMVert for the original_counterpart_id
                        found_counterpart_vert = None
                        for v_counter in bm.verts: # Iterate through all verts in the bmesh
                            if v_counter[node_id_layer].decode('utf-8') == original_counterpart_id:
                                found_counterpart_vert = v_counter
                                break

                        if found_counterpart_vert:
                            # 4. Rename the counterpart vertex in bmesh
                            print(f"Symmetrically renaming node {original_counterpart_id} to {new_counterpart_id}")
                            found_counterpart_vert[node_id_layer] = bytes(new_counterpart_id, 'utf-8')
                            # _force_do_export is already True. Export will pick this up.
                        else:
                            print(f"Symmetrical counterpart '{original_counterpart_id}' not found in mesh for renaming.")
                    else:
                        print(f"Could not determine a valid new symmetrical counterpart name for '{new_node_id}'. Symmetrical rename skipped.")
            # Update mesh visually after all potential changes
            bmesh.update_edit_mesh(obj_data)

        # No need to free bm from edit mesh

    except IndexError:
        print(f"Error: Could not access selected vertex with index {jb_globals.selected_nodes[0][0]} during rename attempt.")
    except Exception as e:
        print(f"Error during node rename: {e}")
        traceback.print_exc()

    # Trigger UI redraw for potentially other panels/areas
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in ['VIEW_3D', 'PROPERTIES']:
                area.tag_redraw()

# Update function for the master visualization toggle
def _update_master_toggle_vis(self, context):
    """Sets all individual beam visualization toggles based on the master toggle."""
    scene = context.scene
    ui_props = scene.ui_properties
    master_state = ui_props.toggle_master_vis

    # List of individual toggle property names
    # <<< MODIFIED: Added 'toggle_cross_part_beams_vis' >>>
    toggle_props = [
        'toggle_beams_vis',
        'toggle_anisotropic_beams_vis',
        'toggle_support_beams_vis',
        'toggle_hydro_beams_vis',
        'toggle_bounded_beams_vis',
        'toggle_lbeam_beams_vis',
        'toggle_pressured_beams_vis',
        'toggle_torsionbars_vis',
        'toggle_rails_vis',
        'toggle_cross_part_beams_vis', # <<< ADDED >>>
    ]

    # Update each individual toggle
    for prop_name in toggle_props:
        # Use setattr to dynamically set the property value
        setattr(ui_props, prop_name, master_state)

    # Trigger a redraw/rebuild of the visualization
    # Use the scene property which is checked in the drawing handler
    scene.jbeam_editor_veh_render_dirty = True
    # Also directly set the drawing module's flag for good measure
    # (though the scene property should be sufficient)
    setattr(drawing, 'veh_render_dirty', True)

# <<< START MODIFIED FUNCTION _update_search_node_id >>>
# <<< RENAMED FUNCTION >>>
def _update_search_element_id(self, context):
    """
    Called when the search_node_id property changes.
    Attempts to find and select the node, unless the update was triggered
    by the text editor highlight function.
    """
    # Check the global flag
    if jb_globals._populating_search_id_from_highlight:
        # If the flag is set, it means the highlight function updated the value.
        # Reset the flag and do nothing else (don't trigger the search).
        jb_globals._populating_search_id_from_highlight = False
        return # Exit the callback

    # If the flag was not set, proceed with the normal search logic
    # (likely triggered by user pressing Enter in the UI field).
    # <<< MODIFIED: Use renamed property >>>
    search_input = self.search_node_id.strip()
    if search_input: # Only attempt search if the field is not empty
        # Call the helper logic. Feedback is handled by the helper.
        _find_and_frame_element_logic(context, search_input) # <<< MODIFIED: Call renamed helper
    # No return needed for update callbacks
# <<< END MODIFIED FUNCTION _update_search_node_id >>>

# <<< Update function for dynamic coloring properties (defined here) >>>
def _update_dynamic_beam_coloring(self, context):
    """
    Sets the render dirty flag when dynamic beam coloring settings change.
    Also, if a beam is currently highlighted, it re-calculates and updates its color.
    """
    context.scene.jbeam_editor_veh_render_dirty = True
    setattr(drawing, 'veh_render_dirty', True)

    # If a beam is highlighted, update its color based on the new setting
    if jb_globals.highlighted_element_type == 'beam':
        # Re-calculate the color for the highlighted beam
        ui_props = context.scene.ui_properties
        new_color = None
        
        # Find the beam data for the highlighted beam
        found_beam_data = None
        if jb_globals.curr_vdata and 'beams' in jb_globals.curr_vdata and jb_globals.highlighted_element_ordered_node_ids:
            target_id1, target_id2 = jb_globals.highlighted_element_ordered_node_ids[0], jb_globals.highlighted_element_ordered_node_ids[1]
            active_obj = context.active_object
            target_part_origin = active_obj.data.get(constants.MESH_JBEAM_PART) if active_obj and active_obj.data else None

            if target_part_origin:
                for beam_data in jb_globals.curr_vdata['beams']:
                    if isinstance(beam_data, dict) and beam_data.get('partOrigin') == target_part_origin:
                        b_id1, b_id2 = beam_data.get('id1:'), beam_data.get('id2:')
                        if (b_id1 == target_id1 and b_id2 == target_id2) or (b_id1 == target_id2 and b_id2 == target_id1):
                            found_beam_data = beam_data
                            break

        if self.use_dynamic_beam_coloring and found_beam_data:
            # Calculate dynamic color
            param_name = self.dynamic_coloring_parameter
            param_value_raw = found_beam_data.get(param_name)
            if param_value_raw is not None:
                low_thresh = drawing.auto_beam_min_val if self.use_auto_thresholds and drawing.auto_beam_thresholds_valid else self.dynamic_color_threshold_low
                high_thresh = drawing.auto_beam_max_val if self.use_auto_thresholds and drawing.auto_beam_thresholds_valid else self.dynamic_color_threshold_high
                new_color = drawing._calculate_dynamic_color(param_value_raw, low_thresh, high_thresh, 'beam')
        elif found_beam_data:
            # Calculate static color
            beam_type = found_beam_data.get('beamType', '|NORMAL')
            if beam_type == '|ANISOTROPIC': new_color = ui_props.anisotropic_beam_color
            elif beam_type == '|SUPPORT': new_color = ui_props.support_beam_color
            # ... add other static beam types if needed ...
            else: new_color = ui_props.beam_color # Default to normal beam color

        if new_color:
            jb_globals.highlighted_element_color = new_color
            setattr(drawing, '_highlight_dirty', True) # Mark highlight for redraw

# <<< MODIFIED: Update function for dynamic node coloring >>>
def _update_dynamic_node_coloring(self, context):
    """Sets the render dirty flag when dynamic node coloring settings change."""
    # Node ID colors are handled in draw_callback_px, which doesn't use the
    # main veh_render_dirty flag directly for batch rebuilding.
    # Instead, we need to trigger a redraw of the 3D viewports.
    drawing._tag_redraw_3d_views(context)
    # <<< ADDED: Also trigger the main visualization rebuild >>>
    # This ensures auto thresholds are recalculated when settings change.
    context.scene.jbeam_editor_veh_render_dirty = True
    setattr(drawing, 'veh_render_dirty', True)
    # <<< END ADDED >>>
# <<< END MODIFIED >>>

# <<< START ADDED: Update function for width properties >>>
def _update_width_property(self, context):
    """
    Update function for width properties.
    Marks both main visualization and highlight as dirty.
    """
    # Mark main visualization dirty
    context.scene.jbeam_editor_veh_render_dirty = True
    setattr(drawing, 'veh_render_dirty', True)

    # Mark highlight dirty if a highlight is active
    if jb_globals.highlighted_element_type is not None:
        setattr(drawing, '_highlight_dirty', True)
# <<< END ADDED >>>

# <<< ADDED: Update function for cross-part node ID visibility >>>
def _update_cross_part_node_ids_vis(self, context):
    """Tags 3D views for redraw when cross-part node ID visibility changes."""
    drawing._tag_redraw_3d_views(context)
# <<< END ADDED >>>

# <<< ADDED: Update function for node group filter changes >>>
def _update_node_group_filter(self, context):
    """Updates drawing when node group filter settings change."""
    # Redraw 3D views for text elements (like Node IDs)
    if hasattr(drawing, '_tag_redraw_3d_views') and callable(drawing._tag_redraw_3d_views):
        drawing._tag_redraw_3d_views(context)
    # Trigger a full visualization rebuild for elements like node dots
    context.scene.jbeam_editor_veh_render_dirty = True
    setattr(drawing, 'veh_render_dirty', True)
# <<< END ADDED >>>

# <<< ADDED: Update function for PC Filters >>>
def update_pc_filters(self, context):
    """Applies visibility filters based on selected .pc files."""
    scene = context.scene
    active_filter_paths = {item.path for item in scene.jbeam_editor_active_pc_filters}
    allowed_parts = set()
    allowed_flexbody_meshes = set() # <<< ADDED: Set to store allowed flexbody mesh names
    show_all = not active_filter_paths

    # --- Build Part -> Filepath Map ---
    part_to_filepath_map = {}
    for obj in bpy.data.objects:
        if obj.data and obj.data.get(constants.MESH_JBEAM_PART):
            part_name = obj.data.get(constants.MESH_JBEAM_PART)
            filepath = obj.data.get(constants.MESH_JBEAM_FILE_PATH)
            if part_name and filepath and part_name not in part_to_filepath_map:
                part_to_filepath_map[part_name] = filepath

    # --- Process Active Filters ---
    if not show_all: # Only parse if filters are active
        for pc_path in active_filter_paths:
            try:
                # --- Get Allowed Parts (Existing Logic) ---
                # Use build_config which reads from disk if not internal
                # Note: build_config might write to internal text editor if importing first time.
                # For filtering, we might want a read-only version later.
                pc_config = import_vehicle.build_config(pc_path)
                parts_in_config = pc_config.get('parts', {})
                if isinstance(parts_in_config, dict):
                    for part_name in parts_in_config.values():
                        if isinstance(part_name, str) and part_name:
                            allowed_parts.add(part_name)
                # Add mainPartName if specified in format 2
                main_part = pc_config.get('mainPartName')
                if main_part:
                    allowed_parts.add(main_part)

                # --- Get Allowed Flexbodies (New Logic) ---
                # Iterate through the parts allowed by *this specific* PC file
                current_pc_parts = set(parts_in_config.values()) if isinstance(parts_in_config, dict) else set()
                if main_part: current_pc_parts.add(main_part)

                for part_name in current_pc_parts:
                    if not isinstance(part_name, str) or not part_name: continue # Skip invalid part names

                    jbeam_filepath = part_to_filepath_map.get(part_name)
                    if not jbeam_filepath: continue # Skip if we don't know the file for this part

                    # Read JBeam content from internal text editor
                    file_content = text_editor.read_int_file(jbeam_filepath)
                    if not file_content: continue

                    # Parse JBeam content
                    try:
                        # Use bng_sjson for parsing robustness
                        padded_content = file_content + chr(127) * 2
                        c, i = bng_sjson._skip_white_space(padded_content, 0, jbeam_filepath)
                        parsed_data = None
                        if c == 123:
                            parsed_data, _ = bng_sjson._read_object(padded_content, i, jbeam_filepath)
                        if not parsed_data: continue

                        part_jbeam_data = parsed_data.get(part_name)
                        if isinstance(part_jbeam_data, dict):
                            flexbodies_section = part_jbeam_data.get("flexbodies", [])
                            if isinstance(flexbodies_section, list):
                                for flex_entry in flexbodies_section:
                                    # Standard format: ["meshName", ["group1", ...], {}]
                                    if isinstance(flex_entry, list) and len(flex_entry) > 0 and isinstance(flex_entry[0], str):
                                        mesh_name = flex_entry[0]
                                        allowed_flexbody_meshes.add(mesh_name)
                    except SyntaxError as se:
                        print(f"Syntax error parsing {jbeam_filepath} for flexbodies: {se}", file=sys.stderr)
                    except Exception as parse_err:
                        print(f"Error parsing {jbeam_filepath} for flexbodies: {parse_err}", file=sys.stderr)

            except Exception as e:
                print(f"Error parsing PC filter file {pc_path}: {e}", file=sys.stderr)
                # Optionally report to user if parsing fails?
                # utils.show_message_box('ERROR', f"Filter Error", f"Could not parse {Path(pc_path).name}: {e}")

    # --- Apply Filter to Scene Objects ---
    for obj in bpy.data.objects:
        is_jbeam_part_obj = obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None
        is_potential_flexbody_obj = not is_jbeam_part_obj # Assume non-JBeam objects could be flexbodies

        is_visible = True # Default to visible
        if not show_all: # Only apply filtering logic if filters are active
            if is_jbeam_part_obj:
                part_name = obj.data.get(constants.MESH_JBEAM_PART)
                is_visible = part_name in allowed_parts
            elif is_potential_flexbody_obj:
                # Check if the object's name matches an allowed flexbody mesh
                # Use obj.name directly for matching
                is_visible = obj.name in allowed_flexbody_meshes
            # Else: Object is neither JBeam nor potential flexbody (lights, cameras, etc.) - keep visible

        # Apply visibility change if needed
        if obj.hide_viewport == is_visible:
            obj.hide_viewport = not is_visible
        # obj.hide_render = not is_visible # Optional: Apply to render visibility too

    _tag_redraw_3d_views(context) # Trigger redraw after processing all objects
# <<< END ADDED >>>

# <<< ADDED: Property Group for storing imported PC file paths >>>
class ImportedPCFileItem(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty(name="PC File Path")

# <<< ADDED: Property Group for storing active PC filter paths >>>
class ActivePCFilterItem(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty(name="Active PC Filter Path")
# <<< END ADDED >>>

# <<< ADDED: Callback function to get available node groups >>>
def get_available_node_groups(self, context):
    """
    Dynamically generates a list of available node groups for the EnumProperty.
    """
    items = [
        ("__NODES_WITHOUT_GROUPS__", "Nodes Without Groups", "Show only nodes that are not assigned to any group")
    ]

    unique_groups = set()
    if jb_globals.curr_vdata and 'nodes' in jb_globals.curr_vdata:
        for node_data in jb_globals.curr_vdata['nodes'].values():
            if isinstance(node_data, dict):
                group_attr = node_data.get('group')
                if isinstance(group_attr, str) and group_attr.strip(): # Ensure group name is not empty
                    unique_groups.add(group_attr)
                elif isinstance(group_attr, list):
                    for g_item in group_attr:
                        if isinstance(g_item, str) and g_item.strip(): # Ensure group name is not empty
                            unique_groups.add(g_item)

    sorted_groups = sorted(list(unique_groups), key=str.lower)
    if sorted_groups: # Add a separator if there are specific groups
        items.append(("_SEPARATOR_", "--- Specific Groups ---", "", 'SEPARATOR', 0)) # Using item_icon = 0 for separator

    for group_name in sorted_groups:
        items.append((group_name, group_name, f"Show nodes in group '{group_name}'"))

    if not unique_groups and len(items) == 1: # Only "__NODES_WITHOUT_GROUPS__" is present
        # Add a placeholder if no specific groups are found and only the default option is there
        items.append(("_NO_SPECIFIC_GROUPS_", "No Specific Groups Found", "No specific groups defined in the current JBeam data"))
    elif not unique_groups and len(items) > 1 and items[1][0] == "_SEPARATOR_":
        # If separator was added but no groups followed, remove separator and add placeholder
        items.pop(1) # Remove separator
        items.append(("_NO_SPECIFIC_GROUPS_", "No Specific Groups Found", "No specific groups defined in the current JBeam data"))


    return items
# <<< END ADDED >>>

# <<< ADDED: Update function for batch edit parameter list >>>
def _update_batch_edit_param_list(self, context):
    """Updates the manual parameter name field when a selection is made from the dropdown."""
    if self.batch_edit_param_list != 'CUSTOM':
        self.batch_edit_param_name = self.batch_edit_param_list

# <<< ADDED: Callback and update for filtered batch edit params >>>
def get_batch_edit_params(self, context):
    """Dynamically generates the parameter list based on the target element type."""
    items = [('CUSTOM', "Custom...", "Enter a custom parameter name in the box below")]
    
    if self.batch_edit_apply_to == 'NODES':
        items.extend([
            ('nodeWeight', "nodeWeight", "Node weight (kg)"),
            ('group', "group", "Group assignment"),
            ('collision', "collision", "Collision toggle (true/false)"),
            ('selfCollision', "selfCollision", "Self-collision toggle (true/false)"),
            ('frictionCoef', "frictionCoef", "Friction coefficient"),
            ('nodeMaterial', "nodeMaterial", "Node material type"),
            ('staticCollision', "staticCollision", "Static collision toggle"),
        ])
    elif self.batch_edit_apply_to == 'BEAMS':
        items.extend([
            ('beamSpring', "beamSpring", "Beam stiffness"),
            ('beamDamp', "beamDamp", "Beam damping"),
            ('beamDeform', "beamDeform", "Deformation threshold"),
            ('beamStrength', "beamStrength", "Break strength"),
            ('beamType', "beamType", "Beam behavior type"),
            ('beamLimitSpring', "beamLimitSpring", "Limit stiffness"),
            ('beamLimitDamp', "beamLimitDamp", "Limit damping"),
            ('beamPrecompression', "beamPrecompression", "Initial compression/tension"),
            ('dampCutoffHz', "dampCutoffHz", "Damping cutoff frequency"),
            ('deformationTriggerRatio', "deformationTriggerRatio", "Ratio to trigger deformation events"),
            ('breakGroup', "breakGroup", "Group of beams that break together"),
            ('deformGroup', "deformGroup", "Group of beams that deform together"),
        ])
        
    return items

def _update_batch_edit_apply_to(self, context):
    """Resets the parameter selection when the 'Apply To' category changes."""
    if self.batch_edit_apply_to == 'NODES':
        self.batch_edit_param_list = 'nodeWeight'
    elif self.batch_edit_apply_to == 'BEAMS':
        self.batch_edit_param_list = 'beamSpring'

# <<< ADDED: PropertyGroup for Node Weight Variable Selection >>>
class NodeWeightVariableItem(bpy.types.PropertyGroup):
    # The actual JBeam variable name, e.g., "$varX"
    name: bpy.props.StringProperty(name="Variable Name")

    # Whether this variable (any instance of it) is considered for nodeWeight
    selected: bpy.props.BoolProperty(
        name="Selected",
        description="Include this variable (the active instance) when calculating nodeWeight for dynamic coloring",
        default=True,
        update=lambda self, context: setattr(drawing, 'veh_render_dirty', True) # Trigger full redraw
    )

    # Stores the unique_id of the instance chosen by the user from the dropdown
    # This ID will be like "filepath::partname::line_or_idx"
    active_instance_unique_id: bpy.props.StringProperty(
        name="Active Instance ID",
        description="Internal ID of the chosen instance for this variable name",
        update=lambda self, context: setattr(drawing, 'veh_render_dirty', True) # Redraw on change
    )

    # Callback for the dropdown
    def get_instance_choices(self, context):
        items = []
        # Access UIProperties to get the toggle state
        ui_props = context.scene.ui_properties

        if self.name in jb_globals.jbeam_variables_cache:
            instances = jb_globals.jbeam_variables_cache[self.name]
            for i, inst_data in enumerate(instances):
                # Construct a display name for the dropdown
                # Conditionally include the filename based on the toggle
                if ui_props.show_filename_in_node_weight_instance:
                    display_name = f"{Path(inst_data['source_file']).name} ({inst_data['source_part']})"
                else:
                    display_name = f"({inst_data['source_part']})"

                if inst_data.get('line_number') is not None:
                    display_name += f" L:{inst_data['line_number']}"
                elif inst_data.get('source_type') == 'default':
                    display_name += " (default)"

                # The 'identifier' for the EnumProperty item will be the unique_id
                unique_id = inst_data.get('unique_id', f"error_no_id_{i}") # Fallback ID
                items.append((unique_id, display_name, f"Source: {inst_data['source_file']}, Part: {inst_data['source_part']}"))
        if not items:
            items.append(("NONE", "No instances found", "This variable name has no defined instances"))
        return items

    # The EnumProperty for the dropdown itself
    instance_choice_dropdown: bpy.props.EnumProperty(
        name="Instance",
        description="Choose which instance of this variable to use",
        items=get_instance_choices,
        # The update function for this dropdown will set 'active_instance_unique_id'
        update=lambda self, context: setattr(self, 'active_instance_unique_id', self.instance_choice_dropdown)
    )
# <<< END ADDED >>>

class UIProperties(bpy.types.PropertyGroup):
    input_node_id: bpy.props.StringProperty(
        name="Input Node ID",
        description="",
        default="",
        update=on_input_node_id_field_updated
    )
    rename_selected_node_references: bpy.props.BoolProperty(
        name="Rename All References",
        description="When renaming the selected node ID above, also update all its references throughout the JBeam document",
        default=True,
        # No update function needed here, its value is read during export
    )
    rename_symmetrical_counterpart: bpy.props.BoolProperty(
        name="Rename Symmetrical Counterpart",
        description="When renaming the selected node, also rename its symmetrical counterpart (e.g., 'L' to 'R') based on Symmetrical Pairs settings. Works with 'Rename All References'.",
        default=False,
        # No update function needed here, its value is read during the primary rename's update function
    )
    find_jump_to_rail_on_2_nodes: bpy.props.BoolProperty(
        name="Find Rail on 2 Nodes",
        description="When 'Find and Jump to' is used with 2 selected nodes, search for a rail definition instead of a beam",
        default=False,
        # No update function needed here, its value is read by the operator
    )
    find_jump_to_face_on_3_4_nodes: bpy.props.BoolProperty(
        name="Find Face on 3/4 Nodes",
        description="When 'Find and Jump to' is used with 3 or 4 selected nodes, search for a triangle/quad definition instead of adding one",
        default=False,
        # No update function needed here, its value is read by the operator
    )


    # Node Search Property
    search_node_id: bpy.props.StringProperty(
        name="Search Element ID", # <<< MODIFIED NAME
        # <<< MODIFIED DESCRIPTION >>>
        description="Enter Node ID or Beam ID (node1-node2) to find and frame (Press Enter)",
        default="",
        update=_update_search_element_id # <<< MODIFIED: Assign renamed update callback
    )

    batch_node_renaming_naming_scheme: bpy.props.StringProperty(
        name="Naming Scheme",
        description="'#' characters will be replaced with \"Node Index\" (e.g. '#rr' results in '1rr', '2rr', '3rr', etc)",
        default="",
    )

    batch_node_renaming_node_idx: bpy.props.IntProperty(
        name="Node Index",
        description="Node index that will replace '#' characters in naming scheme",
        default=1,
        min=1
    )

    # <<< RENAMED/ENSURED: Panel Toggle for Node Visualization >>>
    # <<< ADDED: Native Faces Visibility Toggle >>>
    toggle_native_faces_vis: bpy.props.BoolProperty(
        name="Show Native Faces",
        description="Toggles the visibility of the standard Blender mesh faces (triangles and quads) in Edit Mode",
        default=True,
        update=_update_native_faces_vis
    )
    # <<< END ADDED >>>

    # <<< RENAMED/ENSURED: Panel Toggle for Node Visualization >>>
    show_node_visualization_settings: bpy.props.BoolProperty(
        name="Node Visualization Settings",
        description="Expand to see node visualization options",
        default=False, # Start collapsed
    )
    # <<< END RENAMED/ENSURED >>>

    # --- Node Visualization Properties (Existing and New) ---
    toggle_node_ids_text: bpy.props.BoolProperty(
        name="Toggle NodeIDs Text",
        description="Toggles the text of NodeIDs",
        default=True,
        # <<< ADDED: Update function >>>
        update=_update_dynamic_node_coloring
    )

    node_id_font_size: bpy.props.IntProperty(
        name="Node ID Font Size",
        description="Adjust the font size for the Node ID text in the viewport",
        default=12,
        min=6,
        max=36,
        # <<< ADDED: Update function >>>
        update=_update_dynamic_node_coloring
    )

    node_id_outline_size: bpy.props.IntProperty(
        name="Node ID Outline Size",
        description="Adjust the pixel thickness of the Node ID text outline (0 for no outline)",
        default=2,
        min=0,
        max=5,
        # <<< ADDED: Update function >>>
        update=_update_dynamic_node_coloring
    )

    node_id_text_offset: bpy.props.IntProperty(
        name="Node ID Text Offset",
        description="Adjust the distance (in pixels) between the node and its ID text",
        default=5,
        min=0,
        max=50,
        # <<< ADDED: Update function >>>
        update=_update_dynamic_node_coloring
    )

    toggle_node_group_text: bpy.props.BoolProperty(
        name="Show Node Group(s)",
        description="Toggles displaying the node's assigned group(s) next to its ID",
        default=False,
        # Update function to redraw 3D views when toggled
        update=lambda self, context: drawing._tag_redraw_3d_views(context) if hasattr(drawing, '_tag_redraw_3d_views') and callable(drawing._tag_redraw_3d_views) else None
    )

    toggle_node_group_filter: bpy.props.BoolProperty(
        name="Filter by Node Group",
        description="Enable filtering of Node ID visibility by a selected node group", # MODIFIED description
        default=False,
        update=_update_node_group_filter # <<< MODIFIED: Use new update function
    )

    # <<< REPLACED node_group_filter_text with EnumProperty >>>
    node_group_to_show: bpy.props.EnumProperty(
        name="Group to Show",
        description="Select a specific node group to display, or show nodes without groups. The list is populated from the current JBeam data.",
        items=get_available_node_groups,
        default=0, # Default to the first item ("Nodes Without Groups") when filter is active
        update=_update_node_group_filter # <<< MODIFIED: Use new update function
    )
    # <<< END REPLACED >>>


    # <<< ADDED: Dynamic Node Coloring Properties >>>
    use_dynamic_node_coloring: bpy.props.BoolProperty(
        name="Use Dynamic Coloring (Node Weight)",
        description="Color Node IDs based on the 'nodeWeight' parameter using a blue-cyan-green-yellow-red gradient",
        default=False,
        update=_update_dynamic_node_coloring # Ensure this update function is assigned
    )
    # <<< MODIFIED: Separate properties for min and max node thresholds >>>
    auto_node_threshold_min_display: bpy.props.StringProperty(
        name="Auto Node Min Threshold",
        description="Automatically calculated min nodeWeight value for dynamic coloring",
        default="N/A", # Default value when no data is available
    )
    auto_node_threshold_max_display: bpy.props.StringProperty(
        name="Auto Node Max Threshold",
        description="Automatically calculated max nodeWeight value for dynamic coloring",
        default="N/A", # Default value when no data is available
    )
    # <<< END MODIFIED >>>

    # Note: dynamic_node_coloring_parameter is omitted as it's fixed to 'nodeWeight' for now.
    # Note: dynamic_node_color_threshold_low and dynamic_node_color_threshold_high remain
    # for manual input when use_auto_node_thresholds is off.

    use_auto_node_thresholds: bpy.props.BoolProperty(
        name="Use Auto Thresholds",
        description="Automatically determine Low/High thresholds based on the actual min/max nodeWeight values in the active part(s)",
        default=True,
        update=_update_dynamic_node_coloring # Ensure this update function is assigned
    )
    # Note: dynamic_node_coloring_parameter is omitted as it's fixed to 'nodeWeight' for now.
    # Note: dynamic_node_color_threshold_low and dynamic_node_color_threshold_high remain for manual input when use_auto_node_thresholds is off.
    dynamic_node_color_threshold_low: bpy.props.FloatProperty(
        name="Low Threshold",
        description="Node weight below or equal to this threshold are blue (used when Auto Thresholds is off)",
        default=1.0, # Sensible default for nodeWeight
        min=0.0,
        # Allow higher max for node weights
        max=10000.0,
        update=_update_dynamic_node_coloring # Ensure this update function is assigned
    )
    dynamic_node_color_threshold_high: bpy.props.FloatProperty(
        name="High Threshold",
        description="Node weight above or equal to this threshold are red. Values between Low and High transition through the gradient (used when Auto Thresholds is off)",
        default=10.0, # Sensible default for nodeWeight
        min=0.0,
        # Allow higher max for node weights
        max=10000.0,
        update=_update_dynamic_node_coloring # Ensure this update function is assigned
    )
    dynamic_node_color_distribution_bias: bpy.props.FloatProperty(
        name="Node Color Distribution Bias",
        description="Controls node color spread. < 0.5 skews to Blue/Low, > 0.5 skews to Red/High. 0.5 is linear.",
        default=0.5,
        min=0.0,
        max=1.0,
        update=_update_dynamic_node_coloring # Use the same update function as other node dynamic color props
    )

    toggle_node_weight_text: bpy.props.BoolProperty(
        name="Show Node Weight",
        description="Toggles displaying the node's 'nodeWeight' value next to its ID",
        default=False,
        update=lambda self, context: drawing._tag_redraw_3d_views(context) if hasattr(drawing, '_tag_redraw_3d_views') and callable(drawing._tag_redraw_3d_views) else None
    )

    summed_visible_node_weight_display: bpy.props.StringProperty(
        name="Summed Visible Node Weight",
        description="Sum of 'nodeWeight' for all currently visible nodes in the active JBeam part(s) that pass the current group filter",
        default="N/A",
        # This property is for display only, calculated in drawing.py
    )

    # <<< END ADDED >>>

    # <<< ADDED: Cross-Part Node ID Visibility Toggle >>>
    toggle_cross_part_node_ids_vis: bpy.props.BoolProperty(
        name="Show Cross-Part Node IDs",
        description="Toggles the visibility of node IDs defined in other parts but referenced by the active part",
        default=True,
        update=_update_cross_part_node_ids_vis # Use the new update function
    )
    # <<< END ADDED >>>

    # --- Node Dot Visualization Properties ---
    toggle_node_dots_vis: bpy.props.BoolProperty(
        name="Show Node Dots",
        description="Toggles the visibility of 3D dots for nodes",
        default=True,
        update=lambda self, context: setattr(drawing, 'veh_render_dirty', True)
    )
    node_dot_size: bpy.props.FloatProperty(
        name="Node Dot Size",
        description="Size of the 3D dots representing nodes",
        default=10.0,
        min=1.0, max=20.0,
        update=lambda self, context: setattr(drawing, 'veh_render_dirty', True) # Redraw needed if size changes
    )

    # --- Tooltip Panel Toggle ---
    show_tooltips_panel: bpy.props.BoolProperty(
        name="Tooltips",
        description="Expand to see tooltip options",
        default=False,
    )

    # --- Tooltip Placement ---
    tooltip_placement: bpy.props.EnumProperty(
        name="Tooltip Placement",
        description="Horizontal placement of the parameter tooltips in the viewport",
        items=[
            ('BOTTOM_LEFT', "Bottom Left", "Place tooltips at the bottom left"),
            ('BOTTOM_CENTER', "Bottom Center", "Place tooltips at the bottom center"),
            ('BOTTOM_RIGHT', "Bottom Right", "Place tooltips at the bottom right"),
        ],
        default='BOTTOM_LEFT',
    )

    # <<< NEW PROPERTY >>>
    tooltip_padding_x: bpy.props.IntProperty(
        name="Horizontal Padding",
        description="Horizontal distance (in pixels) from the viewport edge (for Left/Right placement)",
        default=60,
        min=0,
        max=200, # Set a reasonable maximum
    )
    # <<< END NEW PROPERTY >>>

    # --- Shared Tooltip Settings --- <<< MODIFIED >>>
    toggle_line_tooltip: bpy.props.BoolProperty(
        name="Show Line # Tooltip",
        description="Shows the JBeam file line number for a selected node or beam",
        default=True
    )
    line_tooltip_color: bpy.props.FloatVectorProperty(
        name="Line Tooltip Color",
        description="Color of the line number tooltip text",
        subtype='COLOR',
        default=(1.0, 1.0, 0.0, 1.0),
        min=0.0, max=1.0,
        size=4
    )
    toggle_params_tooltip: bpy.props.BoolProperty(
        name="Show Parameters Tooltip",
        description="Shows the parameters for a selected node or beam (mirrors Properties panel)",
        default=True
    )
    params_tooltip_color: bpy.props.FloatVectorProperty(
        name="Params Name Color",
        description="Color of the parameter name tooltip text",
        subtype='COLOR',
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0, max=1.0,
        size=4
    )
    params_value_tooltip_color: bpy.props.FloatVectorProperty(
        name="Params Value Color",
        description="Color of the parameter value tooltip text",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0),
        min=0.0, max=1.0,
        size=4
    )
    tooltip_show_resolved_values: bpy.props.BoolProperty(
        name="Show Resolved Values in Tooltip",
        description="Display final calculated/resolved values in tooltips (like Properties panel), otherwise show raw JBeam values",
        default=True,
        # No update function needed, value is read when tooltips are generated
    )

    # --- End Shared Tooltip Settings ---

    affect_node_references: bpy.props.BoolProperty(
        name="Affect Node References",
        description="Toggles updating JBeam entries who references nodes. E.g. deleting a beam who references a node being deleted",
        default=False
    )

    # <<< RENAMED/ENSURED: Panel Toggle for Line Visualization >>>
    show_line_visualization_settings: bpy.props.BoolProperty(
        name="Line Visualization Settings",
        description="Expand to see visualization options for beams, rails, torsionbars, etc.",
        default=False, # Start collapsed
    )
    # <<< END RENAMED/ENSURED >>>

    # --- Master Visualization Toggle --- <<< MOVED HERE (Inside UIProperties, but will be drawn in the new panel) >>>
    toggle_master_vis: bpy.props.BoolProperty(
        name="Show All Line Visualizations",
        description="Toggles the visibility of all beam/rail/torsionbar lines (excluding highlights)",
        default=True,
        update=_update_master_toggle_vis # Use the new update function
    )

    # --- Beam Visualization Panel Toggle --- <<< MOVED HERE (Inside UIProperties, but will be drawn in the new panel) >>>
    show_beam_visualization_panel: bpy.props.BoolProperty(
        name="Beam Visualization",
        description="Expand to see beam visualization options",
        default=False,
    )

    # Beam visualization properties (NORMAL)
    toggle_beams_vis: bpy.props.BoolProperty(
        name="Show Normal Beams",
        description="Toggles the visibility of normal beams (Green Lines or Dynamic Color)",
        default=True,
        update=_update_dynamic_beam_coloring
    )
    beam_color: bpy.props.FloatVectorProperty(
        name="Normal Beam Color",
        description="Color of the normal beam visualization lines (used when dynamic coloring is off)",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0),
        min=0.0, max=1.0,
        size=4,
        update=_update_dynamic_beam_coloring
    )
    beam_width: bpy.props.FloatProperty(
        name="Normal Beam Width",
        description="Line width for normal beam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # --- Dynamic Beam Coloring Properties --- <<< MODIFIED SECTION >>>
    use_dynamic_beam_coloring: bpy.props.BoolProperty(
        name="Use Dynamic Coloring",
        description="Color normal beams based on a selected parameter and thresholds using a blue-cyan-green-yellow-red gradient",
        default=False,
        update=_update_dynamic_beam_coloring
    )
    # <<< MODIFIED: Separate properties for min and max beam thresholds >>>
    auto_beam_threshold_min_display: bpy.props.StringProperty(
        name="Auto Beam Min Threshold",
        description="Automatically calculated min value for the selected parameter for dynamic beam coloring",
        default="N/A", # Default value when no data is available
    )
    auto_beam_threshold_max_display: bpy.props.StringProperty(
        name="Auto Beam Max Threshold",
        description="Automatically calculated max value for the selected parameter for dynamic beam coloring",
        default="N/A", # Default value when no data is available
    )
    # <<< ADDED: Auto Threshold Toggle >>>
    use_auto_thresholds: bpy.props.BoolProperty(
        name="Use Auto Thresholds",
        description="Automatically determine Low/High thresholds based on the actual min/max values in the active part",
        default=True,
        update=_update_dynamic_beam_coloring # Use the same update function
    )
    # <<< END ADDED >>>
    dynamic_coloring_parameter: bpy.props.EnumProperty(
        name="Parameter",
        description="JBeam parameter to use for dynamic coloring",
        items=[
            ('beamSpring', 'beamSpring', 'Color based on beamSpring value'),
            ('beamDamp', 'beamDamp', 'Color based on beamDamp value'),
            ('beamDeform', 'beamDeform', 'Color based on beamDeform value'),
            ('beamStrength', 'beamStrength', 'Color based on beamStrength value'),
            # Add other relevant parameters if needed
        ],
        default='beamSpring',
        update=_update_dynamic_beam_coloring
    )
    dynamic_color_threshold_low: bpy.props.FloatProperty(
        name="Low Threshold",
        description="Values below or equal to this threshold are blue (used when Auto Thresholds is off)", # <<< Updated description
        default=0.0,
        min=-0.0, # Allow negative for some params
        max=50000000.0,
        update=_update_dynamic_beam_coloring
    )
    dynamic_color_threshold_high: bpy.props.FloatProperty(
        name="High Threshold",
        description="Values above or equal to this threshold are red. Values between Low and High transition through the gradient (used when Auto Thresholds is off)", # <<< Updated description
        default=5000000.0,
        min=-0.0, # Allow negative for some params
        max=50000000.0,
        update=_update_dynamic_beam_coloring
    )
    dynamic_color_distribution_bias: bpy.props.FloatProperty(
        name="Color Distribution Bias",
        description="Controls color spread. < 0.5 skews to Blue/Low, > 0.5 skews to Red/High. 0.5 is linear.",
        default=0.5,
        min=0.0,
        max=1.0,
        update=_update_dynamic_beam_coloring
    )
    # --- End Dynamic Beam Coloring Properties ---

    # Anisotropic Beam Visualization Properties
    toggle_anisotropic_beams_vis: bpy.props.BoolProperty(
        name="Show Anisotropic Beams",
        description="Toggles the visibility of anisotropic beams (White Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    anisotropic_beam_color: bpy.props.FloatVectorProperty(
        name="Anisotropic Beam Color",
        description="Color of the anisotropic beam visualization lines",
        subtype='COLOR',
        default=(1.0, 0.0, 1.0, 1.0), # Magenta
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    anisotropic_beam_width: bpy.props.FloatProperty(
        name="Anisotropic Beam Width",
        description="Line width for anisotropic beam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # Support Beam Visualization Properties
    toggle_support_beams_vis: bpy.props.BoolProperty(
        name="Show Support Beams",
        description="Toggles the visibility of support beams (Magenta Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    support_beam_color: bpy.props.FloatVectorProperty(
        name="Support Beam Color",
        description="Color of the support beam visualization lines",
        subtype='COLOR',
        default=(1.0, 0.0, 1.0, 1.0), # Magenta
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    support_beam_width: bpy.props.FloatProperty(
        name="Support Beam Width",
        description="Line width for support beam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # Hydro Beam Visualization Properties
    toggle_hydro_beams_vis: bpy.props.BoolProperty(
        name="Show Hydro Beams",
        description="Toggles the visibility of hydro beams (Magenta Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    hydro_beam_color: bpy.props.FloatVectorProperty(
        name="Hydro Beam Color",
        description="Color of the hydro beam visualization lines",
        subtype='COLOR',
        default=(1.0, 0.0, 1.0, 1.0), # Magenta
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    hydro_beam_width: bpy.props.FloatProperty(
        name="Hydro Beam Width",
        description="Line width for hydro beam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # Bounded Beam Visualization Properties
    toggle_bounded_beams_vis: bpy.props.BoolProperty(
        name="Show Bounded Beams",
        description="Toggles the visibility of bounded beams (Magenta Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    bounded_beam_color: bpy.props.FloatVectorProperty(
        name="Bounded Beam Color",
        description="Color of the bounded beam visualization lines",
        subtype='COLOR',
        default=(1.0, 0.0, 1.0, 1.0), # Magenta
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    bounded_beam_width: bpy.props.FloatProperty(
        name="Bounded Beam Width",
        description="Line width for bounded beam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # LBeam Visualization Properties
    toggle_lbeam_beams_vis: bpy.props.BoolProperty(
        name="Show LBeams",
        description="Toggles the visibility of LBeams (Magenta Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    lbeam_beam_color: bpy.props.FloatVectorProperty(
        name="LBeam Color",
        description="Color of the LBeam visualization lines",
        subtype='COLOR',
        default=(1.0, 0.0, 1.0, 1.0), # Magenta
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    lbeam_beam_width: bpy.props.FloatProperty(
        name="LBeam Width",
        description="Line width for LBeam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # Pressured Beam Visualization Properties
    toggle_pressured_beams_vis: bpy.props.BoolProperty(
        name="Show Pressured Beams",
        description="Toggles the visibility of pressured beams (Magenta Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    pressured_beam_color: bpy.props.FloatVectorProperty(
        name="Pressured Beam Color",
        description="Color of the pressured beam visualization lines",
        subtype='COLOR',
        default=(1.0, 0.0, 1.0, 1.0), # Magenta
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    pressured_beam_width: bpy.props.FloatProperty(
        name="Pressured Beam Width",
        description="Line width for pressured beam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # Torsionbar visualization properties
    toggle_torsionbars_vis: bpy.props.BoolProperty(
        name="Show Torsionbars",
        description="Toggles the visibility of torsionbars (Orange/Red Lines)", # <<< Updated description
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    torsionbar_color: bpy.props.FloatVectorProperty(
        name="Torsionbar Color",
        description="Color of the outer torsionbar visualization segments",
        subtype='COLOR',
        default=(0.0, 0.0, 1.0, 1.0),
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    torsionbar_mid_color: bpy.props.FloatVectorProperty(
        name="Torsionbar Mid Color",
        description="Color of the middle torsionbar visualization segment",
        subtype='COLOR',
        default=(1.0, 0.0, 0.0, 1.0),
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    torsionbar_width: bpy.props.FloatProperty(
        name="Torsionbar Width",
        description="Line width for torsionbar visualization",
        default=2.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # Rail visualization properties
    toggle_rails_vis: bpy.props.BoolProperty(
        name="Show Rails",
        description="Toggles the visibility of rails (Yellow Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    rail_color: bpy.props.FloatVectorProperty(
        name="Rail Color",
        description="Color of the rail visualization lines",
        subtype='COLOR',
        default=(1.0, 1.0, 0.0, 1.0),
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    rail_width: bpy.props.FloatProperty(
        name="Rail Width",
        description="Line width for rail visualization",
        default=2.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )

    # Cross-Part Beam Visualization
    toggle_cross_part_beams_vis: bpy.props.BoolProperty(
        name="Show Cross-Part Beams",
        description="Toggles the visibility of beams connecting to nodes defined in other parts (Purple Lines)",
        default=True,
        update=_update_toggle_cross_part_beams_vis
    )
    cross_part_beam_color: bpy.props.FloatVectorProperty(
        name="Cross-Part Beam Color",
        description="Color of the cross-part beam visualization lines",
        subtype='COLOR',
        default=(0.5, 0.7, 1.0, 1.0),
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    cross_part_beam_width: bpy.props.FloatProperty(
        name="Cross-Part Beam Width",
        description="Line width for cross-part beam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=_update_width_property # <<< MODIFIED
    )
    use_generic_cross_part_beam_color: bpy.props.BoolProperty(
        name="Use Generic Cross-Part Color",
        description="If enabled, all cross-part beams will use the 'Cross-Part Beam Color'. If disabled, they will use their respective beam type's color (e.g., Normal, Support). Dynamic coloring still applies if enabled.",
        default=True, # Default to current behavior (using the specific cross-part color)
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )


    # Highlight on Click Property
    highlight_element_on_click: bpy.props.BoolProperty(
        name="Highlight Element on Click",
        description="Highlight the JBeam element (beam, rail, etc.) in the 3D view corresponding to the clicked line in the Text Editor",
        default=True,
        # Trigger redraw when changed to clear/show highlight immediately
        update=lambda self, context: setattr(drawing, '_highlight_dirty', True) # <<< MODIFIED: Use highlight dirty flag
    )

    # Highlight Thickness Multiplier Property
    highlight_thickness_multiplier: bpy.props.FloatProperty(
        name="Highlight Thickness Multiplier",
        description="Multiplier for the line width of the highlighted element from text editor click",
        default=4.0,
        min=1.0, max=10.0,
        update=lambda self, context: setattr(drawing, '_highlight_dirty', True) # <<< MODIFIED: Use highlight dirty flag
    )

    # Toggle for Selected Beam Outline
    show_selected_beam_outline: bpy.props.BoolProperty(
        name="Show Selected Beam Outline",
        description="Draw a white outline for beams selected in the 3D viewport",
        default=True,
        update=lambda self, context: setattr(drawing, 'veh_render_dirty', True)
    )

    # Selected Beam Thickness Multiplier Property
    selected_beam_thickness_multiplier: bpy.props.FloatProperty(
        name="Selected Beam Thickness Multiplier",
        description="Multiplier for the line width of beams selected in the 3D viewport, based on their original width", # Updated description
        default=2.0,
        min=1.0, max=10.0,
        update=lambda self, context: setattr(drawing, 'veh_render_dirty', True)
    )

    show_console_warnings_missing_nodes: bpy.props.BoolProperty(
        name="Show Console Messages (debug)",
        description="Show console messages for debugging, such as when a JBeam element's referenced node cannot be found for drawing, a JBeam variable is unresolved, or an expression contains an unsupported operation.",
        default=False,
        update=_update_show_console_warnings_missing_nodes # <<< MODIFIED: Use new update function
    )

    monitor_external_changes: bpy.props.BoolProperty(
        name="Monitor External Changes",
        description="Automatically check if JBeam files are modified externally and ask to reload",
        default=True
    )
    # <<< ADDED: Collection for Node Weight Variables >>>
    node_weight_variables: bpy.props.CollectionProperty(
        type=NodeWeightVariableItem,
        name="Node Weight Variables",
        description="Select variables to include in nodeWeight calculation for dynamic coloring"
        # No direct update function here; individual item.selected has one.
    )

    # <<< ADDED: Toggle for Node Weight Variable Selection visibility >>>
    show_node_weight_variable_selection: bpy.props.BoolProperty(
        name="Show Node Weight Variable Selection",
        description="Expand to see options for selecting variables used in node weight calculation",
        default=False, # Start collapsed
    )

    # <<< ADDED: Toggle for filename visibility in node weight instance column >>>
    show_filename_in_node_weight_instance: bpy.props.BoolProperty(
        name="Show Filename in Instance",
        description="Show the source filename in the 'Instance (File & Part)' column for node weight variables",
        default=False,
        # Update function to redraw UI when toggled
        update=lambda self, context: _tag_redraw_3d_views(context) if hasattr(drawing, '_tag_redraw_3d_views') and callable(drawing._tag_redraw_3d_views) else None
    )
    # <<< END ADDED >>>

    # --- Node Creation Prefixes ---
    show_new_node_naming_panel: bpy.props.BoolProperty(
        name="New Node Naming",
        description="Expand to see new node naming options",
        default=False, # Start collapsed by default
    )

    # <<< ADDED: Toggle for prefix/suffix feature >>>
    use_node_naming_prefixes: bpy.props.BoolProperty(
        name="Use Prefix/Suffix and Auto-Symmetry",
        description="Automatically add a prefix or suffix to newly created nodes based on their X position + apply automatic symmetry if possible",
        default=True,
    )
    # <<< END ADDED >>>

    # <<< MODIFIED: Replace left/right with JSON pairs >>>
    new_node_symmetrical_pairs: bpy.props.StringProperty(
        name="Symmetrical Pairs (JSON)",
        description='Define symmetrical identifier pairs as a JSON list of lists, e.g., [["l", "r"], ["ll", "rr"]]. The first item in each pair is treated as "left" (positive X), the second as "right" (negative X). Longer pairs are checked first.',
        default='[["l", "r"], ["ll", "rr"]]',
        # Add validation later if needed
    )
    # new_node_prefix_left: bpy.props.StringProperty(
    #     name="Left Prefix/Suffix",
    #     description="Prefix/Suffix for newly created nodes with positive X coordinate + reference for automatic symmetry target",
    #     default="l",
    # )
    new_node_prefix_middle: bpy.props.StringProperty(
        name="Middle Prefix/Suffix",
        description="Prefix/Suffix for newly created nodes with near-zero X coordinate",
        default="",
    )
    # new_node_prefix_right: bpy.props.StringProperty(
    #     name="Right Prefix/Suffix",
    #     description="Prefix/Suffix for newly created nodes with negative X coordinate + reference for automatic symmetry target",
    #     default="r",
    # )
    # <<< END MODIFIED >>>

    new_node_prefix_position: bpy.props.EnumProperty(
        name="Position",
        description="Place the identifier at the front or back of the node name",
        items=[
            ('FRONT', "Front", "Add identifier as a prefix (e.g., L_node)"),
            ('BACK', "Back", "Add identifier as a suffix (e.g., node_L)"),
        ],
        default='BACK',
    )
    # --- End Node Creation Prefixes ---

    # --- Batch Property Edit ---
    batch_edit_apply_to: bpy.props.EnumProperty(
        name="Apply To",
        description="Target elements for batch editing",
        items=[
            ('NODES', "Nodes", "Apply to selected nodes"),
            ('BEAMS', "Beams", "Apply to selected beams"),
        ],
        default='NODES',
        update=_update_batch_edit_apply_to
    )
    batch_edit_param_list: bpy.props.EnumProperty(
        name="Common Parameters",
        description="Select a commonly used JBeam parameter to edit",
        items=get_batch_edit_params,
        default=1,
        update=_update_batch_edit_param_list
    )
    batch_edit_param_name: bpy.props.StringProperty(
        name="Parameter",
        description="JBeam parameter name (e.g., nodeWeight, beamSpring, beamDamp)",
        default="nodeWeight"
    )
    batch_edit_param_value: bpy.props.StringProperty(
        name="Value",
        description="Value to set (can be a number, string, or expression)",
        default="1.0"
    )

# Copyright (c) 2023 BeamNG GmbH, Angelo Matteo
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
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
from pathlib import Path

import bpy
import bmesh

# Import from local modules
from . import constants
from . import globals as jb_globals # Import globals
from .utils import Metadata # Import Metadata for filtering
from .operators import ( # Import operators used in panels
    JBEAM_EDITOR_OT_force_jbeam_sync,
    JBEAM_EDITOR_OT_add_beam_tri_quad,
    JBEAM_EDITOR_OT_flip_jbeam_faces,
    JBEAM_EDITOR_OT_scroll_to_definition,
    JBEAM_EDITOR_OT_find_node,
    JBEAM_EDITOR_OT_batch_node_renaming,
    JBEAM_EDITOR_OT_open_text_editor_split,
    JBEAM_EDITOR_OT_reload_jbeam_from_disk, # <<< ADDED: Import new operator
    JBEAM_EDITOR_OT_delete_all_unused_texts, # <<< ADDED: Import new operator
    JBEAM_EDITOR_OT_toggle_pc_filter, # <<< ADDED: Import filter operators
    JBEAM_EDITOR_OT_open_file_in_editor, # <<< ADDED: Import new operator
    JBEAM_EDITOR_OT_reload_pc_file, # <<< ADDED: Import PC reload operator
    JBEAM_EDITOR_OT_save_pc_file_to_disk, # <<< ADDED: Import PC save operator
    JBEAM_EDITOR_OT_clear_pc_filters, # <<< ADDED: Import filter operators
)
from .drawing import resolve_jbeam_variable_value

class JBEAM_EDITOR_PT_transform_panel_ext(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_label = 'JBeam'

    # Poll method checks if editing is enabled
    @classmethod
    def poll(cls, context):
        # Always show the panel in the Item tab.
        # Buttons inside will handle their own context sensitivity via their poll methods.
        return True
        # obj = context.active_object
        # return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and obj.data.get(constants.MESH_EDITING_ENABLED, False)

    def draw(self, context):
        layout = self.layout
        # <<< MOVED: Reload button moved here >>>
        layout.operator(JBEAM_EDITOR_OT_reload_jbeam_from_disk.bl_idname, text=" Reload JBeam from Disk", icon='FILE_REFRESH')
        # <<< ADDED: Delete Unused Texts button >>>
        layout.operator(JBEAM_EDITOR_OT_delete_all_unused_texts.bl_idname, text=" Delete Unused Texts", icon='TRASH')
        layout.operator(JBEAM_EDITOR_OT_force_jbeam_sync.bl_idname, text='Force JBeam Sync')
        layout.separator() # Add separator after buttons


class JBEAM_EDITOR_PT_jbeam_panel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'JBeam'

    # Poll method checks if editing is enabled
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        # Allow panel to show even if editing is disabled, but content might be restricted
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        obj = context.active_object
        if not obj:
            return

        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return

        # Check if editing is enabled for enabling/disabling controls
        editing_enabled = obj_data.get(constants.MESH_EDITING_ENABLED, False)

        bm = None
        # Only get bmesh if in edit mode and editing is enabled
        if obj.mode == 'EDIT' and editing_enabled:
            try:
                bm = bmesh.from_edit_mesh(obj_data)
            except Exception as e:
                print(f"Error getting bmesh for JBeam panel: {e}")
                self.layout.label(text="Error accessing mesh data.")
                return

        scene = context.scene
        ui_props = scene.ui_properties

        jbeam_part_name = obj_data.get(constants.MESH_JBEAM_PART)

        layout = self.layout
        if jbeam_part_name:
            layout.label(text=f'{jbeam_part_name}')

            # --- ADDED: Button to open Text Editor ---
            row = layout.row()
            row.scale_y = 1.2 # Make button slightly bigger
            row.operator(JBEAM_EDITOR_OT_open_text_editor_split.bl_idname, text=" Open JBeam File (Split View)", icon='TEXT')
            layout.separator() # Add separator after the button
            # --- END ADDED ---

            # --- Existing Functionality Box ---
            action_box = layout.box()
            col = action_box.column()
            # Disable action box content if not in edit mode or editing disabled
            col.enabled = obj.mode == 'EDIT' and editing_enabled

            len_selected_verts = len(jb_globals.selected_nodes)
            len_selected_faces = len(jb_globals.selected_tris_quads)
            len_selected_beams = len(jb_globals.selected_beams) # Get beam selection count

            # Scroll to Definition Button
            # Only enable if exactly one node or one beam is selected
            row = col.row()
            row.enabled = len_selected_verts == 1 or len_selected_beams == 1
            row.operator(JBEAM_EDITOR_OT_scroll_to_definition.bl_idname, text=" Find and Jump to (Text Editor)", icon='FOLDER_REDIRECT')
            col.separator() # Add separator after the button

            if len_selected_verts == 1:
                col.row().label(text='Selected Node ID')
                col.row().prop(ui_props, 'input_node_id', text = "")

            elif len_selected_verts in (2,3,4):
                label = None
                if len_selected_verts == 2:
                    label = 'Add Beam'
                elif len_selected_verts == 3:
                    label = 'Add Triangle'
                else:
                    label = 'Add Quad'
                col.row().operator(JBEAM_EDITOR_OT_add_beam_tri_quad.bl_idname, text=label)

            if len_selected_faces > 0:
                col.row().operator(JBEAM_EDITOR_OT_flip_jbeam_faces.bl_idname)

            # --- ADDED: Documentation Button ---
            layout.separator() # Add separator before the button
            row = layout.row()
            op = row.operator("wm.url_open", text="BeamNG Documentation", icon='URL')
            op.url = "https://documentation.beamng.com/modding/vehicle/sections/"
            # --- END ADDED ---

        # No need to free bm from edit mesh

class JBEAM_EDITOR_PT_find_node(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Find Element by ID (3D Viewport)' # <<< MODIFIED LABEL
    bl_icon = 'VIEWZOOM'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        ui_props = scene.ui_properties
        obj = context.active_object

        if not obj or not obj.data:
            layout.label(text="No active object.")
            return

        editing_enabled = obj.data.get(constants.MESH_EDITING_ENABLED, False)

        box = layout.box()
        col = box.column(align=True)
        col.enabled = obj.mode == 'EDIT' and editing_enabled

        # Display the label first
        col.label(text="Element ID:")
        # Put the text box and button on the next row
        row = col.row(align=True)
        row.prop(ui_props, 'search_node_id', text="") # Remove text label from prop itself
        row.operator(JBEAM_EDITOR_OT_find_node.bl_idname, text="", icon='VIEWZOOM')


class JBEAM_EDITOR_PT_jbeam_properties_panel(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Properties'
    bl_icon = 'PROPERTIES'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        col = box.column()

        # ... (object checks, edit mode check, bmesh access remain the same) ...
        obj = context.active_object
        if not obj: col.label(text="No active object."); return
        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh) or obj_data.get(constants.MESH_JBEAM_PART) is None:
            col.label(text="Active object is not a JBeam mesh."); return
        editing_enabled = obj_data.get(constants.MESH_EDITING_ENABLED, False)
        if not editing_enabled: col.label(text="JBeam editing disabled for this object."); return
        if obj.mode != 'EDIT': col.label(text="Enter Edit Mode to see properties."); return
        bm = None
        try:
            bm = bmesh.from_edit_mesh(obj_data)
            bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()
        except Exception as e: print(f"Error getting bmesh for properties panel: {e}"); col.label(text="Error accessing mesh data."); return
        if jb_globals.curr_vdata is None: col.label(text="JBeam data not loaded."); return
        # --- End checks ---

        if len(jb_globals.selected_nodes) == 1:
            if 'nodes' in jb_globals.curr_vdata:
                vert_index, node_id = jb_globals.selected_nodes[0]
                if node_id in jb_globals.curr_vdata['nodes']:
                    node = jb_globals.curr_vdata['nodes'][node_id]
                    col.label(text=f"Node: {node_id}")
                    for k in sorted(node.keys(), key=lambda x: str(x)):
                        if k == 'pos' or k == Metadata or k == 'posNoOffset': continue
                        val = node[k]
                        # Pass the global cache explicitly
                        resolved_val = resolve_jbeam_variable_value(val, jb_globals.jbeam_variables_cache)
                        display_val = repr(resolved_val)
                        if isinstance(val, str) and val.startswith('=$'):
                            # Add indicator if original was an expression/variable reference
                            if resolved_val != val: # Check if resolution/evaluation changed the value
                                display_val += f" (from {val})" # Show original expression
                            else:
                                display_val += " (unresolved/failed)" # Indicate if evaluation failed
                        col.row().label(text=f'- {k}: {display_val}')
                else: col.label(text=f"Node '{node_id}' not found in JBeam data.")
            else: col.label(text="'nodes' section not found.")

        elif len(jb_globals.selected_beams) == 1:
            if 'beams' in jb_globals.curr_vdata:
                # ... (beam index finding logic remains the same) ...
                edge_index, beam_indices_str = jb_globals.selected_beams[0]
                try: e = bm.edges[edge_index]
                except (IndexError, ReferenceError) as get_edge_err: col.label(text=f"Error accessing selected beam: {get_edge_err}"); return
                part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
                beam_indices = beam_indices_str.split(',')
                if not beam_indices or not part_origin_layer: col.label(text="Beam data missing."); return
                part_origin = e[part_origin_layer].decode('utf-8')
                try: beam_idx_in_part = int(beam_indices[0])
                except ValueError: col.label(text="Invalid beam index."); return
                global_beam_idx = -1; current_part_beam_count = 0
                for i, b in enumerate(jb_globals.curr_vdata['beams']):
                    if b.get('partOrigin') == part_origin:
                        current_part_beam_count += 1
                        if current_part_beam_count == beam_idx_in_part: global_beam_idx = i; break
                # --- End beam index finding ---

                if global_beam_idx != -1 and global_beam_idx < len(jb_globals.curr_vdata['beams']):
                    beam = jb_globals.curr_vdata['beams'][global_beam_idx]
                    col.label(text=f"Beam: {beam.get('id1:', '?')}-{beam.get('id2:', '?')} (Index {beam_idx_in_part} in {part_origin})")
                    for k in sorted(beam.keys(), key=lambda x: str(x)):
                        if k in ('id1:', 'id2:', 'partOrigin') or k == Metadata: continue
                        val = beam[k]
                        # Pass the global cache explicitly
                        resolved_val = resolve_jbeam_variable_value(val, jb_globals.jbeam_variables_cache)
                        display_val = repr(resolved_val)
                        if isinstance(val, str) and val.startswith('=$'):
                            if resolved_val != val:
                                display_val += f" (from {val})"
                            else:
                                display_val += " (unresolved/failed)"
                        col.row().label(text=f'- {k}: {display_val}')
                else: col.label(text=f"Beam index {beam_idx_in_part} not found in part '{part_origin}'.")
            else: col.label(text="'beams' section not found.")

        elif len(jb_globals.selected_tris_quads) == 1:
            # ... (face index finding logic remains the same) ...
            face_data = jb_globals.selected_tris_quads[0]; face_index = face_data[0]; face_idx_in_part = face_data[1]
            try: f = bm.faces[face_index]
            except (IndexError, ReferenceError) as get_face_err: col.label(text=f"Error accessing selected face: {get_face_err}"); return
            num_verts = len(f.verts); face_type = None
            if num_verts == 3: face_type = 'triangles'
            elif num_verts == 4: face_type = 'quads'
            if face_type and face_type in jb_globals.curr_vdata:
                face_idx_layer = bm.faces.layers.int.get(constants.FL_FACE_IDX); part_origin_layer = bm.faces.layers.string.get(constants.FL_FACE_PART_ORIGIN)
                if not face_idx_layer or not part_origin_layer: col.label(text="Face data missing."); return
                part_origin = f[part_origin_layer].decode('utf-8'); global_face_idx = -1; current_part_face_count = 0
                for i, face_entry in enumerate(jb_globals.curr_vdata[face_type]):
                    if face_entry.get('partOrigin') == part_origin:
                        current_part_face_count += 1
                        if current_part_face_count == face_idx_in_part: global_face_idx = i; break
                # --- End face index finding ---
                if global_face_idx != -1 and global_face_idx < len(jb_globals.curr_vdata[face_type]):
                    face = jb_globals.curr_vdata[face_type][global_face_idx]
                    ids = [face.get(f'id{x+1}:', '?') for x in range(num_verts)]
                    col.label(text=f"{face_type.capitalize()[:-1]}: {'-'.join(ids)} (Index {face_idx_in_part} in {part_origin})")
                    for k in sorted(face.keys(), key=lambda x: str(x)):
                        if k.startswith('id') and k.endswith(':'): continue
                        if k == 'partOrigin': continue
                        val = face[k]
                        resolved_val = resolve_jbeam_variable_value(val, jb_globals.jbeam_variables_cache)
                        display_val = repr(resolved_val)
                        if isinstance(val, str) and val.startswith('=$'):
                            if resolved_val != val: display_val += f" (from {val})"
                            else: display_val += " (unresolved/failed)"
                        col.row().label(text=f'- {k}: {display_val}')
                else: col.label(text=f"{face_type.capitalize()[:-1]} index {face_idx_in_part} not found in part '{part_origin}'.")
            elif face_type: col.label(text=f"'{face_type}' section not found.")
            else: col.label(text="Selected face is not a triangle or quad.")
        else:
            col.label(text="Select a single node, beam, or face to see properties.")


class JBEAM_EDITOR_PT_batch_node_renaming(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Batch Node Renaming'
    bl_icon = 'FILE_FONT'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        obj = context.active_object
        editing_enabled = obj and obj.data and obj.data.get(constants.MESH_EDITING_ENABLED, False)

        box = layout.box()
        col = box.column()
        col.enabled = obj and obj.mode == 'EDIT' and editing_enabled

        col.row().label(text='Naming Scheme')
        col.prop(ui_props, 'batch_node_renaming_naming_scheme', text = "")
        col.prop(ui_props, 'batch_node_renaming_node_idx', text = "Node Index")

        operator_text = 'Stop' if jb_globals.batch_node_renaming_enabled else 'Start'
        col.operator(JBEAM_EDITOR_OT_batch_node_renaming.bl_idname, text=operator_text)


class JBEAM_EDITOR_PT_jbeam_settings(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Settings'
    bl_icon = 'SETTINGS'

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        obj = context.active_object
        if not obj: return
        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh): return
        # editing_enabled = obj_data.get(constants.MESH_EDITING_ENABLED, False) # Not strictly needed for settings display
        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        if obj_data.get(constants.MESH_JBEAM_PART) is not None:
            # --- Node Creation Box (Existing Structure) ---
            node_naming_box = layout.box()
            row = node_naming_box.row(align=True)
            row.prop(ui_props, "show_new_node_naming_panel", icon="TRIA_DOWN" if ui_props.show_new_node_naming_panel else "TRIA_RIGHT", icon_only=True, emboss=False)
            row.label(text="New Node Naming", icon='OUTLINER_DATA_FONT')
            if ui_props.show_new_node_naming_panel:
                node_naming_col = node_naming_box.column(align=True)
                node_naming_col.prop(ui_props, 'use_node_naming_prefixes')
                node_naming_col.separator()
                prefix_row = node_naming_col.row(align=True)
                prefix_row.enabled = ui_props.use_node_naming_prefixes # Enable/disable based on toggle
                # <<< MODIFIED: Show JSON pairs property >>>
                prefix_row.prop(ui_props, 'new_node_symmetrical_pairs', text="Pairs")
                # row = node_naming_col.row(align=True); row.enabled = ui_props.use_node_naming_prefixes
                # split = row.split(factor=0.5); split.label(text="Left Side:"); split.prop(ui_props, 'new_node_prefix_left', text="")
                # <<< END MODIFIED >>>
                row = node_naming_col.row(align=True); row.enabled = ui_props.use_node_naming_prefixes
                split = row.split(factor=0.5); split.label(text="Center:"); split.prop(ui_props, 'new_node_prefix_middle', text="")
                # row = node_naming_col.row(align=True); row.enabled = ui_props.use_node_naming_prefixes
                # split = row.split(factor=0.5); split.label(text="Right Side:"); split.prop(ui_props, 'new_node_prefix_right', text="")
                row = node_naming_col.row(); row.enabled = ui_props.use_node_naming_prefixes; row.alignment = 'CENTER'; row.label(text="Position:")
                row = node_naming_col.row(align=True); row.enabled = ui_props.use_node_naming_prefixes; row.prop(ui_props, 'new_node_prefix_position', expand=True)


            # --- Node Visualization Box (New Structure) ---
            node_vis_box = layout.box()
            row = node_vis_box.row(align=True)
            # Use the property added in properties.py
            row.prop(ui_props, "show_node_visualization_settings", icon="TRIA_DOWN" if ui_props.show_node_visualization_settings else "TRIA_RIGHT", icon_only=True, emboss=False)
            row.label(text="Node Visualization", icon='OUTLINER_OB_POINTCLOUD')
            if ui_props.show_node_visualization_settings:
                # Content previously in JBEAM_EDITOR_PT_node_visualization.draw
                node_vis_col = node_vis_box.column(align=True) # Content column

                node_vis_col.prop(ui_props, 'toggle_node_ids_text', text="Show Node IDs Text")
                row = node_vis_col.row(); row.enabled = ui_props.toggle_node_ids_text; row.prop(ui_props, 'node_id_font_size', text="Font Size")
                row = node_vis_col.row(); row.enabled = ui_props.toggle_node_ids_text; row.prop(ui_props, 'node_id_outline_size', text="Outline Size")
                row = node_vis_col.row(); row.enabled = ui_props.toggle_node_ids_text; row.prop(ui_props, 'node_id_text_offset', text="Text Offset")
                node_vis_col.separator() # Separator before group filter
                # <<< MOVED: Toggle for Node Group Text >>>
                row = node_vis_col.row(); row.enabled = ui_props.toggle_node_ids_text; row.prop(ui_props, 'toggle_node_group_text')
                # --- Node Group Filter Section ---
                node_vis_col.prop(ui_props, 'toggle_node_group_filter')
                row = node_vis_col.row()
                row.enabled = ui_props.toggle_node_ids_text and ui_props.toggle_node_group_filter # Enable dropdown only if master toggle and filter toggle are on
                row.prop(ui_props, 'node_group_to_show', text="") # Text label provided by the property itself ("Group to Show")
                node_vis_col.separator() # <<< MOVED: Empty line underneath the selection box
                # --- End Node Group Filter Section ---

                node_vis_col.prop(ui_props, 'toggle_cross_part_node_ids_vis')

                # --- Dynamic Node Coloring Section ---
                node_vis_col.separator()
                node_vis_col.prop(ui_props, 'use_dynamic_node_coloring')
                dyn_box = node_vis_col.box()
                dyn_box.enabled = ui_props.use_dynamic_node_coloring
                dyn_col = dyn_box.column(align=True)
                dyn_col.label(text="Parameter: nodeWeight") # <<< REMOVED ICON
                dyn_col.prop(ui_props, 'use_auto_node_thresholds', text="Auto Thresholds")
                # <<< MODIFIED: Display auto thresholds or manual inputs >>>
                if ui_props.use_auto_node_thresholds:
                    dyn_col.label(text=f"Min: {ui_props.auto_node_threshold_min_display}") # Display Min
                    dyn_col.label(text=f"Max: {ui_props.auto_node_threshold_max_display}") # Display Max
                else:
                    row_low = dyn_col.row(); row_low.prop(ui_props, 'dynamic_node_color_threshold_low', text="Low Threshold") # Keep text for manual input
                    row_high = dyn_col.row(); row_high.prop(ui_props, 'dynamic_node_color_threshold_high', text="High Threshold")
                # <<< END MODIFIED >>>
                node_vis_col.separator() # Separator before dot settings
                # --- Node Dot Visualization ---
                node_vis_col.prop(ui_props, 'toggle_node_dots_vis', text="Show Node Dots")
                dot_box = node_vis_col.box()
                dot_box.enabled = ui_props.toggle_node_dots_vis # Enable/disable based on toggle
                dot_col = dot_box.column(align=True)
                dot_col.prop(ui_props, 'node_dot_size') # Keep dot size customization
                # --- End Dynamic Node Coloring Section ---

            # --- 3D Lines Box (New Structure) ---
            line_vis_box = layout.box()
            row = line_vis_box.row(align=True)
            # Use the property added in properties.py
            row.prop(ui_props, "show_line_visualization_settings", icon="TRIA_DOWN" if ui_props.show_line_visualization_settings else "TRIA_RIGHT", icon_only=True, emboss=False)
            row.label(text="3D Lines", icon='MOD_WIREFRAME')
            if ui_props.show_line_visualization_settings:
                # Content previously in JBEAM_EDITOR_PT_line_visualization.draw
                line_vis_col = line_vis_box.column(align=True) # Content column

                line_vis_col.prop(ui_props, 'toggle_master_vis')
                line_vis_col.separator()

                # --- Beam Visualization (Collapsible Sub-Section) ---
                # This sub-section keeps its own toggle logic as it's nested
                beam_vis_box = line_vis_col.box()
                row = beam_vis_box.row(align=True)
                row.prop(ui_props, "show_beam_visualization_panel", icon="TRIA_DOWN" if ui_props.show_beam_visualization_panel else "TRIA_RIGHT", icon_only=True, emboss=False)
                row.label(text="Beam Visualization", icon='MOD_BEVEL')

                if ui_props.show_beam_visualization_panel:
                    beam_vis_col = beam_vis_box.column(align=True)

                    # Dynamic Coloring Section
                    beam_vis_col.prop(ui_props, 'use_dynamic_beam_coloring')
                    dyn_box = beam_vis_col.box()
                    dyn_box.enabled = ui_props.use_dynamic_beam_coloring
                    dyn_col = dyn_box.column(align=True)
                    dyn_col.prop(ui_props, 'dynamic_coloring_parameter', text="Parameter") # Parameter selection remains
                    dyn_col.prop(ui_props, 'use_auto_thresholds', text="Auto Thresholds")
                    # <<< MODIFIED: Display auto thresholds or manual inputs >>>
                    if ui_props.use_auto_thresholds:
                        dyn_col.label(text=f"Min: {ui_props.auto_beam_threshold_min_display}") # Display Min
                        dyn_col.label(text=f"Max: {ui_props.auto_beam_threshold_max_display}") # Display Max
                    else:
                        row_low = dyn_col.row(); row_low.prop(ui_props, 'dynamic_color_threshold_low', text="Low Threshold") # Keep text for manual input
                        row_high = dyn_col.row(); row_high.prop(ui_props, 'dynamic_color_threshold_high', text="High Threshold")
                    # <<< END MODIFIED >>>
                    beam_vis_col.separator()

                    # Individual Beam Type Settings
                    is_dynamic = ui_props.use_dynamic_beam_coloring
                    # Normal Beams
                    beam_vis_col.prop(ui_props, 'toggle_beams_vis')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'beam_color')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic # <<< ADDED: Enable/disable width based on dynamic coloring
                    row.prop(ui_props, 'beam_width')
                    beam_vis_col.separator()
                    # Anisotropic Beams
                    beam_vis_col.prop(ui_props, 'toggle_anisotropic_beams_vis')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'anisotropic_beam_color')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'anisotropic_beam_width')
                    beam_vis_col.separator()
                    # Support Beams
                    beam_vis_col.prop(ui_props, 'toggle_support_beams_vis')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'support_beam_color')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'support_beam_width')
                    beam_vis_col.separator()
                    # Hydro Beams
                    beam_vis_col.prop(ui_props, 'toggle_hydro_beams_vis')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'hydro_beam_color')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'hydro_beam_width')
                    beam_vis_col.separator()
                    # Bounded Beams
                    beam_vis_col.prop(ui_props, 'toggle_bounded_beams_vis')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'bounded_beam_color')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'bounded_beam_width')
                    beam_vis_col.separator()
                    # LBeams
                    beam_vis_col.prop(ui_props, 'toggle_lbeam_beams_vis')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'lbeam_beam_color')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'lbeam_beam_width')
                    beam_vis_col.separator()
                    # Pressured Beams
                    beam_vis_col.prop(ui_props, 'toggle_pressured_beams_vis')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'pressured_beam_color')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'pressured_beam_width')
                    beam_vis_col.separator()
                    # Cross-Part Beams
                    beam_vis_col.prop(ui_props, 'toggle_cross_part_beams_vis')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'cross_part_beam_color')
                    row = beam_vis_col.row(); row.enabled = not is_dynamic
                    row.prop(ui_props, 'cross_part_beam_width')
                    # --- End Beam Type Settings ---

                # --- Torsionbar, Rail Visualization (Remain outside Beam Vis sub-section) ---
                line_vis_col.separator()
                # Torsionbars
                line_vis_col.prop(ui_props, 'toggle_torsionbars_vis')
                row = line_vis_col.row(); row.enabled = ui_props.toggle_torsionbars_vis
                row.prop(ui_props, 'torsionbar_color')
                row = line_vis_col.row(); row.enabled = ui_props.toggle_torsionbars_vis
                row.prop(ui_props, 'torsionbar_mid_color')
                row = line_vis_col.row(); row.enabled = ui_props.toggle_torsionbars_vis # <<< ADDED: Enable/disable width based on toggle
                row.prop(ui_props, 'torsionbar_width')
                line_vis_col.separator()
                # Rails
                line_vis_col.prop(ui_props, 'toggle_rails_vis')
                row = line_vis_col.row(); row.enabled = ui_props.toggle_rails_vis
                row.prop(ui_props, 'rail_color')
                row = line_vis_col.row(); row.enabled = ui_props.toggle_rails_vis # <<< ADDED: Enable/disable width based on toggle
                row.prop(ui_props, 'rail_width')
                # --- End Torsionbar/Rail ---

            # --- Affect Node References Box (Existing) ---
            box = layout.box()
            col = box.column(align=True)
            col.prop(ui_props, 'affect_node_references', text="Affect Node References")

            # --- Tooltips Section (Existing) ---
            tooltips_box = layout.box() # <<< MOVED outside the main settings box for grouping
            row = tooltips_box.row(align=True)
            row.prop(ui_props, "show_tooltips_panel", icon="TRIA_DOWN" if ui_props.show_tooltips_panel else "TRIA_RIGHT", icon_only=True, emboss=False)
            row.label(text="Tooltips", icon='INFO')
            if ui_props.show_tooltips_panel:
                tooltips_col = tooltips_box.column(align=True)
                tooltips_col.label(text="Placement:")
                tooltips_col.prop(ui_props, 'tooltip_placement', text="")
                tooltips_col.prop(ui_props, 'tooltip_padding_x')
                tooltips_col.separator()
                row = tooltips_col.row(align=True); row.prop(ui_props, 'toggle_line_tooltip', text="Show Line #")
                row = tooltips_col.row(align=True); row.prop(ui_props, 'line_tooltip_color', text=""); row.enabled = ui_props.toggle_line_tooltip
                row = tooltips_col.row(align=True); row.prop(ui_props, 'toggle_params_tooltip', text="Show Parameters")
                row = tooltips_col.row(align=True); row.enabled = ui_props.toggle_params_tooltip
                split = row.split(factor=0.5, align=True); split.prop(ui_props, 'params_tooltip_color', text="Parameter"); split.prop(ui_props, 'params_value_tooltip_color', text="Value")

            # --- Other General Settings (Existing) ---
            other_box = layout.box() # <<< Grouped remaining settings
            col = other_box.column(align=True)
            col.prop(ui_props, 'highlight_element_on_click', text="3D Highlight from Text")
            row = col.row(); row.enabled = ui_props.highlight_element_on_click
            row.prop(ui_props, 'highlight_thickness_multiplier', text="Highlight Thickness")

            col.separator()
            col.prop(ui_props, 'show_selected_beam_outline', text="Show Selected Beam Outline")
            row = col.row(); row.enabled = ui_props.show_selected_beam_outline
            row.prop(ui_props, 'selected_beam_thickness_multiplier', text="Selected Beam Multiplier")

# <<< ADDED: Panel for PC Filtering >>>
class JBEAM_EDITOR_PT_pc_filter(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Part-Config Filter'
    bl_icon = 'FILTER'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Show if there are any imported PC files
        return hasattr(context.scene, 'jbeam_editor_imported_pc_files') and context.scene.jbeam_editor_imported_pc_files

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        imported_pcs = scene.jbeam_editor_imported_pc_files
        active_filters = {item.path for item in scene.jbeam_editor_active_pc_filters}

        box = layout.box()
        if not imported_pcs:
            # This should not happen due to poll(), but check anyway
            box.label(text="No .pc files found during folder import.")
            return

        row = box.row()
        row.label(text="Active PC Filters:")
        row.operator(JBEAM_EDITOR_OT_clear_pc_filters.bl_idname, text="", icon='X')

        col = box.column(align=True)
        for i, pc_item in enumerate(imported_pcs):
            # Row for toggle button and reload button
            row = col.row(align=True) # Keep align=True
            # <<< MODIFIED: Determine if this item is active >>>
            is_active = pc_item.path in active_filters
            # Toggle button (takes most space)
            op = row.operator(JBEAM_EDITOR_OT_toggle_pc_filter.bl_idname, text=Path(pc_item.path).name, emboss=is_active, icon='CHECKBOX_HLT' if is_active else 'CHECKBOX_DEHLT') # <<< MODIFIED: Set emboss based on is_active
            op.pc_file_path = pc_item.path
            op.index = i
            # <<< ADDED: Edit button >>>
            op_edit = row.operator(JBEAM_EDITOR_OT_open_file_in_editor.bl_idname, text="", icon='TEXT', emboss=is_active) # <<< MODIFIED: Set emboss
            op_edit.filepath = pc_item.path
            # <<< ADDED: Save button >>>
            op_save = row.operator(JBEAM_EDITOR_OT_save_pc_file_to_disk.bl_idname, text="", icon='FILE_TICK', emboss=is_active) # <<< MODIFIED: Set emboss
            op_save.filepath = pc_item.path
            # <<< END ADDED >>>
            # <<< END ADDED >>>
            # Reload button (small icon button)
            op_reload = row.operator(JBEAM_EDITOR_OT_reload_pc_file.bl_idname, text="", icon='FILE_REFRESH', emboss=is_active) # <<< MODIFIED: Set emboss
            op_reload.pc_file_path = pc_item.path
# <<< END ADDED >>>

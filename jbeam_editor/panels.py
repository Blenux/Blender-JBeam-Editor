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
    JBEAM_EDITOR_OT_open_text_editor_split, # <<< ADD THIS IMPORT
)

class JBEAM_EDITOR_PT_transform_panel_ext(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_label = 'JBeam'

    # Poll method checks if editing is enabled
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and obj.data.get(constants.MESH_EDITING_ENABLED, False)

    def draw(self, context):
        layout = self.layout
        layout.operator(JBEAM_EDITOR_OT_force_jbeam_sync.bl_idname, text='Force JBeam Sync')


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
                col.row().label(text='JBeam Node ID')
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
    bl_label = 'Find Node by ID (3D Viewport)'
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

        row = col.row(align=True)
        row.prop(ui_props, 'search_node_id', text="")
        row.operator(JBEAM_EDITOR_OT_find_node.bl_idname, text="", icon='VIEWZOOM')


class JBEAM_EDITOR_PT_jbeam_properties_panel(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Properties'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        col = box.column()

        obj = context.active_object
        if not obj:
            col.label(text="No active object.")
            return
        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh) or obj_data.get(constants.MESH_JBEAM_PART) is None:
            col.label(text="Active object is not a JBeam mesh.")
            return

        editing_enabled = obj_data.get(constants.MESH_EDITING_ENABLED, False)
        if not editing_enabled:
            col.label(text="JBeam editing disabled for this object.")
            return

        veh_model = obj_data.get(constants.MESH_VEHICLE_MODEL)

        if obj.mode != 'EDIT':
            col.label(text="Enter Edit Mode to see properties.")
            return

        bm = None
        try:
            bm = bmesh.from_edit_mesh(obj_data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
        except Exception as e:
            print(f"Error getting bmesh for properties panel: {e}")
            col.label(text="Error accessing mesh data.")
            return

        if jb_globals.curr_vdata is None:
            col.label(text="JBeam data not loaded.")
            return

        if len(jb_globals.selected_nodes) == 1:
            if 'nodes' in jb_globals.curr_vdata:
                vert_index, node_id = jb_globals.selected_nodes[0]
                if node_id in jb_globals.curr_vdata['nodes']:
                    node = jb_globals.curr_vdata['nodes'][node_id]
                    col.label(text=f"Node: {node_id}")
                    for k in sorted(node.keys(), key=lambda x: str(x)):
                        if k == 'pos' or k == Metadata or k == 'posNoOffset': continue
                        val = node[k]
                        str_val = repr(val)
                        col.row().label(text=f'- {k}: {str_val}')
                else:
                    col.label(text=f"Node '{node_id}' not found in JBeam data.")
            else:
                col.label(text="'nodes' section not found.")

        elif len(jb_globals.selected_beams) == 1:
            if 'beams' in jb_globals.curr_vdata:
                edge_index, beam_indices_str = jb_globals.selected_beams[0]
                try:
                    e = bm.edges[edge_index]
                except (IndexError, ReferenceError) as get_edge_err:
                    col.label(text=f"Error accessing selected beam: {get_edge_err}")
                    return

                part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
                beam_indices = beam_indices_str.split(',')

                if not beam_indices or not part_origin_layer:
                     col.label(text="Beam data missing.")
                     return

                part_origin = e[part_origin_layer].decode('utf-8')
                try:
                    beam_idx_in_part = int(beam_indices[0])
                except ValueError:
                    col.label(text="Invalid beam index.")
                    return

                global_beam_idx = -1
                current_part_beam_count = 0
                for i, b in enumerate(jb_globals.curr_vdata['beams']):
                    if b.get('partOrigin') == part_origin:
                        current_part_beam_count += 1
                        if current_part_beam_count == beam_idx_in_part:
                            global_beam_idx = i
                            break

                if global_beam_idx != -1 and global_beam_idx < len(jb_globals.curr_vdata['beams']):
                    beam = jb_globals.curr_vdata['beams'][global_beam_idx]
                    col.label(text=f"Beam: {beam.get('id1:', '?')}-{beam.get('id2:', '?')} (Index {beam_idx_in_part} in {part_origin})")
                    for k in sorted(beam.keys(), key=lambda x: str(x)):
                        if k in ('id1:', 'id2:', 'partOrigin') or k == Metadata:
                            continue
                        val = beam[k]
                        str_val = repr(val)
                        col.row().label(text=f'- {k}: {str_val}')
                else:
                    col.label(text=f"Beam index {beam_idx_in_part} not found in part '{part_origin}'.")
            else:
                col.label(text="'beams' section not found.")

        elif len(jb_globals.selected_tris_quads) == 1:
            face_data = jb_globals.selected_tris_quads[0]
            f, face_idx_in_part = face_data[0], face_data[1]
            num_verts = len(f.verts)

            face_type = None
            if num_verts == 3:
                face_type = 'triangles'
            elif num_verts == 4:
                face_type = 'quads'

            if face_type and face_type in jb_globals.curr_vdata:
                face_idx_layer = bm.faces.layers.int.get(constants.FL_FACE_IDX)
                part_origin_layer = bm.faces.layers.string.get(constants.FL_FACE_PART_ORIGIN)

                if not face_idx_layer or not part_origin_layer:
                    col.label(text="Face data missing.")
                    return

                part_origin = f[part_origin_layer].decode('utf-8')

                global_face_idx = -1
                current_part_face_count = 0
                for i, face_entry in enumerate(jb_globals.curr_vdata[face_type]):
                    if face_entry.get('partOrigin') == part_origin:
                        current_part_face_count += 1
                        if current_part_face_count == face_idx_in_part:
                            global_face_idx = i
                            break

                if global_face_idx != -1 and global_face_idx < len(jb_globals.curr_vdata[face_type]):
                    face = jb_globals.curr_vdata[face_type][global_face_idx]
                    ids = [face.get(f'id{x+1}:', '?') for x in range(num_verts)]
                    col.label(text=f"{face_type.capitalize()[:-1]}: {'-'.join(ids)} (Index {face_idx_in_part} in {part_origin})")

                    for k in sorted(face.keys(), key=lambda x: str(x)):
                        if k.startswith('id') and k.endswith(':'): continue
                        if k == 'partOrigin': continue
                        val = face[k]
                        str_val = repr(val)
                        col.row().label(text=f'- {k}: {str_val}')
                else:
                     col.label(text=f"{face_type.capitalize()[:-1]} index {face_idx_in_part} not found in part '{part_origin}'.")
            elif face_type:
                col.label(text=f"'{face_type}' section not found.")
            else:
                 col.label(text="Selected face is not a triangle or quad.")
        else:
            col.label(text="Select a single node, beam, or face to see properties.")


class JBEAM_EDITOR_PT_batch_node_renaming(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Batch Node Renaming'
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

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        obj = context.active_object
        if not obj:
            return

        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return

        editing_enabled = obj_data.get(constants.MESH_EDITING_ENABLED, False)

        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        if obj_data.get(constants.MESH_JBEAM_PART) is not None:
            box = layout.box()
            col = box.column(align=True)
            # Keep settings panel enabled even if editing is disabled,
            # but specific features might depend on editing state internally.
            # col.enabled = editing_enabled # Removed this line

            col.label(text="General:")

            # --- Tooltips Section --- <<< MODIFIED >>>
            tooltips_box = col.box()
            row = tooltips_box.row(align=True)
            row.prop(ui_props, "show_tooltips_panel",
                     icon="TRIA_DOWN" if ui_props.show_tooltips_panel else "TRIA_RIGHT",
                     icon_only=True, emboss=False)
            row.label(text="Tooltips")

            if ui_props.show_tooltips_panel:
                tooltips_col = tooltips_box.column(align=True)
                tooltips_col.label(text="Placement:")
                tooltips_col.prop(ui_props, 'tooltip_placement', text="")
                tooltips_col.separator()

                # --- Shared Tooltip Settings ---
                row = tooltips_col.row(align=True)
                row.prop(ui_props, 'toggle_line_tooltip', text="Show Line #")
                row = tooltips_col.row(align=True)
                row.prop(ui_props, 'line_tooltip_color', text="")
                row.enabled = ui_props.toggle_line_tooltip # Use shared toggle
                row = tooltips_col.row(align=True)
                row.prop(ui_props, 'toggle_params_tooltip', text="Show Parameters")
                row = tooltips_col.row(align=True)
                row.enabled = ui_props.toggle_params_tooltip # Use shared toggle
                split = row.split(factor=0.5, align=True)
                split.prop(ui_props, 'params_tooltip_color', text="Parameter")
                split.prop(ui_props, 'params_value_tooltip_color', text="Value")
            # --- End Tooltips Section ---

            # --- Master Visualization Toggle --- <<< ADDED >>>
            col.separator() # Separator before the master toggle
            col.prop(ui_props, 'toggle_master_vis') # Add the master toggle here
            col.separator() # Separator after the master toggle

            # --- Beam Visualization (Collapsible) ---
            beam_vis_box = col.box()
            row = beam_vis_box.row(align=True)
            row.prop(ui_props, "show_beam_visualization_panel",
                     icon="TRIA_DOWN" if ui_props.show_beam_visualization_panel else "TRIA_RIGHT",
                     icon_only=True, emboss=False)
            row.label(text="Beam Visualization")

            if ui_props.show_beam_visualization_panel:
                # The update function handles enabling/disabling based on master toggle
                # No changes needed inside this 'if' block for the master toggle itself
                beam_vis_col = beam_vis_box.column(align=True)

                beam_vis_col.prop(ui_props, 'toggle_beams_vis')
                row = beam_vis_col.row(); row.enabled = ui_props.toggle_beams_vis
                row.prop(ui_props, 'beam_color')
                beam_vis_col.prop(ui_props, 'beam_width')

                beam_vis_col.prop(ui_props, 'toggle_anisotropic_beams_vis')
                row = beam_vis_col.row(); row.enabled = ui_props.toggle_anisotropic_beams_vis
                row.prop(ui_props, 'anisotropic_beam_color')
                beam_vis_col.prop(ui_props, 'anisotropic_beam_width')

                beam_vis_col.prop(ui_props, 'toggle_support_beams_vis')
                row = beam_vis_col.row(); row.enabled = ui_props.toggle_support_beams_vis
                row.prop(ui_props, 'support_beam_color')
                beam_vis_col.prop(ui_props, 'support_beam_width')

                beam_vis_col.prop(ui_props, 'toggle_hydro_beams_vis')
                row = beam_vis_col.row(); row.enabled = ui_props.toggle_hydro_beams_vis
                row.prop(ui_props, 'hydro_beam_color')
                beam_vis_col.prop(ui_props, 'hydro_beam_width')

                beam_vis_col.prop(ui_props, 'toggle_bounded_beams_vis')
                row = beam_vis_col.row(); row.enabled = ui_props.toggle_bounded_beams_vis
                row.prop(ui_props, 'bounded_beam_color')
                beam_vis_col.prop(ui_props, 'bounded_beam_width')

                beam_vis_col.prop(ui_props, 'toggle_lbeam_beams_vis')
                row = beam_vis_col.row(); row.enabled = ui_props.toggle_lbeam_beams_vis
                row.prop(ui_props, 'lbeam_beam_color')
                beam_vis_col.prop(ui_props, 'lbeam_beam_width')

                beam_vis_col.prop(ui_props, 'toggle_pressured_beams_vis')
                row = beam_vis_col.row(); row.enabled = ui_props.toggle_pressured_beams_vis
                row.prop(ui_props, 'pressured_beam_color')
                beam_vis_col.prop(ui_props, 'pressured_beam_width')

            col.separator() # Separator after Beam Visualization

            # --- Other General Settings ---
            col.prop(ui_props, 'affect_node_references', text="Affect Node References")
            col.prop(ui_props, 'highlight_element_on_click', text="3D Highlight from Text")
            # Add the highlight thickness property
            row = col.row()
            row.enabled = ui_props.highlight_element_on_click # Enable only if highlighting is on
            row.prop(ui_props, 'highlight_thickness_multiplier', text="Highlight Thickness")

            # --- Node Visualization ---
            col.separator()
            col.label(text="Node Visualization:")
            col.prop(ui_props, 'toggle_node_ids_text', text="Show Node IDs Text")
            row = col.row()
            row.enabled = ui_props.toggle_node_ids_text
            row.prop(ui_props, 'node_id_font_size', text="Font Size")
            row = col.row()
            row.enabled = ui_props.toggle_node_ids_text
            row.prop(ui_props, 'node_id_outline_size', text="Outline Size")

            # --- Cross-Part Beam Visualization ---
            col.separator()
            col.label(text="Cross-Part Beam Visualization:")
            col.prop(ui_props, 'toggle_cross_part_beams_vis')
            row = col.row(); row.enabled = ui_props.toggle_cross_part_beams_vis
            row.prop(ui_props, 'cross_part_beam_color')
            col.prop(ui_props, 'cross_part_beam_width')

            # --- Torsionbar Visualization ---
            col.separator()
            col.label(text="Torsionbar Visualization:")
            col.prop(ui_props, 'toggle_torsionbars_vis')
            row = col.row(); row.enabled = ui_props.toggle_torsionbars_vis
            row.prop(ui_props, 'torsionbar_color')
            row = col.row(); row.enabled = ui_props.toggle_torsionbars_vis
            row.prop(ui_props, 'torsionbar_mid_color')
            col.prop(ui_props, 'torsionbar_width')

            # --- Rail Visualization ---
            col.separator()
            col.label(text="Rail Visualization:")
            col.prop(ui_props, 'toggle_rails_vis')
            row = col.row(); row.enabled = ui_props.toggle_rails_vis
            row.prop(ui_props, 'rail_color')
            col.prop(ui_props, 'rail_width')

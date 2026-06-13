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
import traceback

# Import from local modules
from . import constants
from . import globals as jb_globals # Import globals
# Import drawing module to access its state/functions if needed later
# <<< ADDED: Import drawing module directly for setting dirty flag >>>
from . import drawing
# Import the update function from drawing.py after it's defined there
# This avoids circular import if drawing needs properties
# <<< MODIFIED: Removed _update_dynamic_beam_coloring from this import >>>
# <<< MODIFIED: Added _tag_redraw_3d_views >>>
from .drawing import (
    _update_toggle_cross_part_beams_vis,
    veh_render_dirty,
    _tag_redraw_3d_views, # <<< ADDED
)

# <<< ADDED: Import the helper function >>>
from .operators import _find_and_select_node_id_logic
# <<< ADDED: Import globals >>>
from . import globals as jb_globals

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
            # Update mesh visually
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
def _update_search_node_id(self, context):
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
    search_id = self.search_node_id.strip()
    if search_id: # Only attempt search if the field is not empty
        # Call the helper logic. Feedback is handled by the helper.
        _find_and_select_node_id_logic(context, search_id)
    # No return needed for update callbacks
# <<< END MODIFIED FUNCTION _update_search_node_id >>>

# <<< Update function for dynamic coloring properties (defined here) >>>
def _update_dynamic_beam_coloring(self, context):
    """Sets the render dirty flag when dynamic beam coloring settings change."""
    context.scene.jbeam_editor_veh_render_dirty = True
    setattr(drawing, 'veh_render_dirty', True)

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


class UIProperties(bpy.types.PropertyGroup):
    input_node_id: bpy.props.StringProperty(
        name="Input Node ID",
        description="",
        default="",
        update=on_input_node_id_field_updated
    )

    # Node Search Property
    search_node_id: bpy.props.StringProperty(
        name="Search Node ID",
        description="Enter the Node ID to find and select (Press Enter to search)",
        default="",
        update=_update_search_node_id # Keep the update callback assigned
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

    # <<< ADDED: Dynamic Node Coloring Properties >>>
    use_dynamic_node_coloring: bpy.props.BoolProperty(
        name="Use Dynamic Coloring (Node Weight)",
        description="Color Node IDs based on the 'nodeWeight' parameter using a blue-cyan-green-yellow-red gradient",
        default=False,
        update=_update_dynamic_node_coloring # Ensure this update function is assigned
    )
    use_auto_node_thresholds: bpy.props.BoolProperty(
        name="Use Auto Thresholds",
        description="Automatically determine Low/High thresholds based on the actual min/max nodeWeight values in the active part(s)",
        default=True,
        update=_update_dynamic_node_coloring # Ensure this update function is assigned
    )
    # Note: dynamic_node_coloring_parameter is omitted as it's fixed to 'nodeWeight' for now.
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
    # <<< END ADDED >>>

    # <<< ADDED: Cross-Part Node ID Visibility Toggle >>>
    toggle_cross_part_node_ids_vis: bpy.props.BoolProperty(
        name="Show Cross-Part Node IDs",
        description="Toggles the visibility of node IDs defined in other parts but referenced by the active part",
        default=True,
        update=_update_cross_part_node_ids_vis # Use the new update function
    )
    # <<< END ADDED >>>

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
        min=-0.0,
        max=50000000.0,
        update=_update_dynamic_beam_coloring
    )
    dynamic_color_threshold_high: bpy.props.FloatProperty(
        name="High Threshold",
        description="Values above or equal to this threshold are red. Values between Low and High transition through the gradient (used when Auto Thresholds is off)", # <<< Updated description
        default=5000000.0,
        min=-0.0,
        max=50000000.0,
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

    new_node_prefix_left: bpy.props.StringProperty(
        name="Left Prefix/Suffix",
        description="Prefix/Suffix for newly created nodes with positive X coordinate + reference for automatic symmetry target",
        default="l",
    )
    new_node_prefix_middle: bpy.props.StringProperty(
        name="Middle Prefix/Suffix",
        description="Prefix/Suffix for newly created nodes with near-zero X coordinate",
        default="",
    )
    new_node_prefix_right: bpy.props.StringProperty(
        name="Right Prefix/Suffix",
        description="Prefix/Suffix for newly created nodes with negative X coordinate + reference for automatic symmetry target",
        default="r",
    )
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

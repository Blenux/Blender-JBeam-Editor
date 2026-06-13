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

bl_info = {
    "name": "Blender JBeam Editor",
    "description": "Modify BeamNG JBeam files in a 3D editor!",
    "author": "BeamNG",
    "version": (0, 2, 56),
    "blender": (4, 2, 0),
    "location": "File > Import > JBeam File / File > Export > JBeam File",
    "warning": "",
    "doc_url": "https://github.com/BeamNG/Blender-JBeam-Editor/blob/vehicle_importer/docs/user/user_docs.md",
    "tracker_url": "https://github.com/BeamNG/Blender-JBeam-Editor/issues",
    "support": "COMMUNITY",
    "category": "Development",
}

import base64
import pickle
import uuid
import sys
import traceback # Ensure traceback is imported

import bpy
import blf
import bmesh

from bpy.app.handlers import persistent

from blf import position as blfpos
from blf import size as blfsize
from blf import draw as blfdraw
from blf import color as blfcolor
from blf import dimensions as blfdims

from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector, Matrix # Import Matrix

from . import constants
from . import import_jbeam
from . import export_jbeam
from . import import_vehicle
from . import export_vehicle
from . import text_editor
from . import sjsonast
from . import bng_sjson # Import bng_sjson
# Import utils for show_message_box >>>
from . import utils
from .utils import Metadata # Ensure Metadata is imported
from .text_editor import SCENE_SHORT_TO_FULL_FILENAME # Import scene constant

if not constants.UNIT_TESTING:
    import gpu
    from gpu_extras.batch import batch_for_shader

check_file_interval = 0.1
poll_active_ops_interval = 0.1

draw_handle = None
draw_handle2 = None

_do_export = False
_force_do_export = False

prev_obj_selected = None
curr_vdata = None

selected_nodes = []
selected_beams = []
selected_tris_quads = []
_selected_beam_line_info = None
_selected_beam_params_info = None
_selected_node_params_info = None # <<< ADDED: Global for node params tooltip
_selected_node_line_info = None # <<< NEW: Global for node line tooltip

veh_render_dirty = False
# rename_enabled = False # <<< REMOVED
batch_node_renaming_enabled = False
previous_selected_indices = set()

# --- Visualization Batches & Coords ---
beam_render_shader = None
beam_render_batch = None # NORMAL beams (Green)
beam_coords = []

# <<< ADDED: Anisotropic Beam Visualization >>>
anisotropic_beam_render_batch = None
anisotropic_beam_coords = []
# <<< END ADDED >>>

# <<< ADDED: Support Beam Visualization >>>
support_beam_render_batch = None
support_beam_coords = []
# <<< END ADDED >>>

# <<< ADDED: Hydro Beam Visualization >>>
hydro_beam_render_batch = None
hydro_beam_coords = []
# <<< END ADDED >>>

# <<< ADDED: Bounded Beam Visualization >>>
bounded_beam_render_batch = None
bounded_beam_coords = []
# <<< END ADDED >>>

# <<< ADDED: LBeam Visualization >>>
lbeam_render_batch = None
lbeam_coords = []
# <<< END ADDED >>>

# <<< ADDED: Pressured Beam Visualization >>>
pressured_beam_render_batch = None
pressured_beam_coords = []
# <<< END ADDED >>>

torsionbar_render_batch = None
torsionbar_coords = []
torsionbar_red_render_batch = None
torsionbar_red_coords = []

rail_render_batch = None
rail_coords = []

# --- Cross-Part Beam Visualization --- # <<< RENAMED COMMENT
cross_part_beam_render_batch = None # <<< RENAMED VARIABLE
cross_part_beam_coords = []         # <<< RENAMED VARIABLE
all_nodes_cache: dict[str, tuple[Vector, str, str]] = {} # {node_id: (world_pos, source_filepath, part_origin)} # <<< RENAMED VARIABLE & UPDATED STRUCTURE
all_nodes_cache_dirty = True # Flag to rebuild cache # <<< RENAMED VARIABLE


# Add this function
def _update_toggle_cross_part_beams_vis(self, context): # <<< RENAMED FUNCTION
    """Update function for the cross-part beam visibility toggle."""
    global all_nodes_cache_dirty # <<< USE RENAMED FLAG
    scene = context.scene
    # Always trigger a redraw/rebuild when the toggle changes
    scene.jbeam_editor_veh_render_dirty = True
    # Mark cache dirty ONLY when toggling ON. If toggling OFF,
    # the draw_callback_view function handles clearing and marking clean.
    if self.toggle_cross_part_beams_vis: # <<< USE RENAMED PROPERTY
        all_nodes_cache_dirty = True # <<< USE RENAMED FLAG
    # No 'else' needed here, draw_callback_view handles the 'off' case.


# Refresh property input field UI
# Simplified rename logic >>>
def on_input_node_id_field_updated(self, context: bpy.types.Context):
    global _force_do_export
    global selected_nodes

    scene = context.scene
    ui_props = scene.ui_properties
    obj = context.active_object

    # Basic checks: Ensure we have a valid JBeam object, editing is enabled, and exactly one node is selected.
    if (obj is None or
            obj.data.get(constants.MESH_JBEAM_PART) is None or
            not obj.data.get(constants.MESH_EDITING_ENABLED, False) or
            len(selected_nodes) != 1):
        return

    try:
        # Get the index of the selected vertex
        selected_vert_index = selected_nodes[0][0]
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
            _force_do_export = True
            # Update mesh visually
            bmesh.update_edit_mesh(obj_data)

        # No need to free bm from edit mesh

    except IndexError:
        print(f"Error: Could not access selected vertex with index {selected_nodes[0][0]} during rename attempt.")
    except Exception as e:
        print(f"Error during node rename: {e}")
        traceback.print_exc()

    # Trigger UI redraw for potentially other panels/areas
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in ['VIEW_3D', 'PROPERTIES']:
                area.tag_redraw()

class UIProperties(bpy.types.PropertyGroup):
    input_node_id: bpy.props.StringProperty(
        name="Input Node ID",
        description="",
        default="",
        update=on_input_node_id_field_updated
    )

    # <<< ADDED: Node Search Property >>>
    search_node_id: bpy.props.StringProperty(
        name="Search Node ID",
        description="Enter the Node ID to find and select",
        default="",
    )
    # <<< END ADDED >>>

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

    toggle_node_ids_text: bpy.props.BoolProperty(
        name="Toggle NodeIDs Text",
        description="Toggles the text of NodeIDs",
        default=True
    )

    # <<< ADDED: Node ID Font Size Property >>>
    node_id_font_size: bpy.props.IntProperty(
        name="Node ID Font Size",
        description="Adjust the font size for the Node ID text in the viewport",
        default=12,
        min=6,
        max=36,
        # No update function needed, draw_callback_px reads it directly
    )
    # <<< END ADDED >>>

    # <<< NEW: Node ID Outline Size Property >>>
    node_id_outline_size: bpy.props.IntProperty(
        name="Node ID Outline Size",
        description="Adjust the pixel thickness of the Node ID text outline (0 for no outline)",
        default=2,
        min=0,
        max=5, # Keep a reasonable max to avoid performance issues
        # No update function needed, draw_callback_px reads it directly
    )
    # <<< END NEW >>>

    # --- Tooltip Panel Toggle --- # <<< ADDED >>>
    show_tooltips_panel: bpy.props.BoolProperty(
        name="Tooltips",
        description="Expand to see tooltip options",
        default=False,
    )
    # <<< END ADDED >>>

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

    # --- Beam Tooltips ---
    show_beam_tooltips_panel: bpy.props.BoolProperty(
        name="Beam Tooltips",
        description="Expand to see beam tooltip options",
        default=False,
    )
    toggle_beam_line_tooltip: bpy.props.BoolProperty(
        name="Show Beam Line Tooltip",
        description="Shows the JBeam file line number for a selected beam",
        default=True
    )
    beam_line_tooltip_color: bpy.props.FloatVectorProperty(
        name="Line Tooltip Color",
        description="Color of the beam line number tooltip text",
        subtype='COLOR',
        default=(1.0, 1.0, 0.0, 1.0),
        min=0.0, max=1.0,
        size=4
    )
    toggle_beam_params_tooltip: bpy.props.BoolProperty(
        name="Show Beam Params Tooltip",
        description="Shows the parameters for a selected beam (mirrors Properties panel)", # Updated description
        default=True
    )
    beam_params_tooltip_color: bpy.props.FloatVectorProperty(
        name="Params Name Color", # Clarify this is for the name
        description="Color of the beam parameter name tooltip text",
        subtype='COLOR',
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0, max=1.0,
        size=4
    )
    beam_params_value_tooltip_color: bpy.props.FloatVectorProperty(
        name="Params Value Color",
        description="Color of the beam parameter value tooltip text",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0), # Default Green
        min=0.0, max=1.0,
        size=4
    )

    # --- Node Tooltips ---
    show_node_tooltips_panel: bpy.props.BoolProperty(
        name="Node Tooltips",
        description="Expand to see node tooltip options",
        default=False,
    )
    # <<< NEW: Node Line Tooltip Properties >>>
    toggle_node_line_tooltip: bpy.props.BoolProperty(
        name="Show Node Line Tooltip",
        description="Shows the JBeam file line number for a selected node",
        default=True
    )
    node_line_tooltip_color: bpy.props.FloatVectorProperty(
        name="Line Tooltip Color",
        description="Color of the node line number tooltip text",
        subtype='COLOR',
        default=(1.0, 1.0, 0.0, 1.0),
        min=0.0, max=1.0,
        size=4
    )
    # <<< END NEW >>>
    toggle_node_params_tooltip: bpy.props.BoolProperty(
        name="Show Node Params Tooltip",
        description="Shows the parameters for a selected node (mirrors Properties panel)", # Updated description
        default=True
    )
    node_params_tooltip_color: bpy.props.FloatVectorProperty(
        name="Params Name Color", #  Clarify this is for the name
        description="Color of the node parameter name tooltip text",
        subtype='COLOR',
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0, max=1.0,
        size=4
    )
    node_params_value_tooltip_color: bpy.props.FloatVectorProperty(
        name="Params Value Color",
        description="Color of the node parameter value tooltip text",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0), # Default Green
        min=0.0, max=1.0,
        size=4
    )

    # --- REMOVED: Individual Node Parameter Toggles ---

    affect_node_references: bpy.props.BoolProperty(
        name="Affect Node References",
        description="Toggles updating JBeam entries who references nodes. E.g. deleting a beam who references a node being deleted",
        default=False
    )

    # <<< ADDED: Beam Visualization Panel Toggle >>>
    show_beam_visualization_panel: bpy.props.BoolProperty(
        name="Beam Visualization",
        description="Expand to see beam visualization options",
        default=False, # Default to closed/collapsed
    )
    # <<< END ADDED >>>

    # Beam visualization properties (NORMAL)
    toggle_beams_vis: bpy.props.BoolProperty(
        name="Show Normal Beams",
        description="Toggles the visibility of normal beams (Green Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )
    beam_color: bpy.props.FloatVectorProperty(
        name="Normal Beam Color",
        description="Color of the normal beam visualization lines",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0), # Green
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )
    beam_width: bpy.props.FloatProperty(
        name="Normal Beam Width",
        description="Line width for normal beam visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )

    # <<< ADDED: Anisotropic Beam Visualization Properties >>>
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
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    # <<< END ADDED >>>

    # <<< ADDED: Support Beam Visualization Properties >>>
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
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    # <<< END ADDED >>>

    # <<< ADDED: Hydro Beam Visualization Properties >>>
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
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    # <<< END ADDED >>>

    # <<< ADDED: Bounded Beam Visualization Properties >>>
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
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    # <<< END ADDED >>>

    # <<< ADDED: LBeam Visualization Properties >>>
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
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    # <<< END ADDED >>>

    # <<< ADDED: Pressured Beam Visualization Properties >>>
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
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    # <<< END ADDED >>>

    # Torsionbar visualization properties
    toggle_torsionbars_vis: bpy.props.BoolProperty(
        name="Show Torsionbars",
        description="Toggles the visibility of torsionbars (Blue/Red Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )
    torsionbar_color: bpy.props.FloatVectorProperty(
        name="Torsionbar Color",
        description="Color of the outer torsionbar visualization segments",
        subtype='COLOR',
        default=(0.0, 0.0, 1.0, 1.0), # Blue
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )
    torsionbar_mid_color: bpy.props.FloatVectorProperty(
        name="Torsionbar Mid Color",
        description="Color of the middle torsionbar visualization segment",
        subtype='COLOR',
        default=(1.0, 0.0, 0.0, 1.0), # Red
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )
    torsionbar_width: bpy.props.FloatProperty(
        name="Torsionbar Width",
        description="Line width for torsionbar visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )

    # Rail visualization properties
    toggle_rails_vis: bpy.props.BoolProperty(
        name="Show Rails",
        description="Toggles the visibility of rails (Yellow Lines)",
        default=True,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )
    rail_color: bpy.props.FloatVectorProperty(
        name="Rail Color",
        description="Color of the rail visualization lines",
        subtype='COLOR',
        default=(1.0, 1.0, 0.0, 1.0), # Yellow
        min=0.0, max=1.0,
        size=4,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )
    rail_width: bpy.props.FloatProperty(
        name="Rail Width",
        description="Line width for rail visualization",
        default=1.0,
        min=0.1, max=10.0,
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True) # Trigger redraw
    )

    # --- Cross-Part Beam Visualization --- # <<< RENAMED SECTION
    toggle_cross_part_beams_vis: bpy.props.BoolProperty( # <<< RENAMED PROPERTY
        name="Show Cross-Part Beams", # <<< RENAMED LABEL
        description="Toggles the visibility of beams connecting to nodes defined in other parts (Purple Lines)", # <<< UPDATED DESCRIPTION
        default=True,
        # Use the dedicated update function instead of the lambda
        update=_update_toggle_cross_part_beams_vis # <<< USE RENAMED UPDATE FUNCTION
    )
    cross_part_beam_color: bpy.props.FloatVectorProperty( # <<< RENAMED PROPERTY
        name="Cross-Part Beam Color", # <<< RENAMED LABEL
        description="Color of the cross-part beam visualization lines", # <<< UPDATED DESCRIPTION
        subtype='COLOR',
        default=(0.5, 0.7, 1.0, 1.0),
        min=0.0, max=1.0,
        size=4,
        # Keep redraw trigger for color/width changes
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )
    cross_part_beam_width: bpy.props.FloatProperty( # <<< RENAMED PROPERTY
        name="Cross-Part Beam Width", # <<< RENAMED LABEL
        description="Line width for cross-part beam visualization", # <<< UPDATED DESCRIPTION
        default=1.0,
        min=0.1, max=10.0,
        # Keep redraw trigger for color/width changes
        update=lambda self, context: setattr(context.scene, 'jbeam_editor_veh_render_dirty', True)
    )


# Undo action (supposed to use this instead of Blender's undo)
class JBEAM_EDITOR_OT_force_jbeam_sync(bpy.types.Operator):
    bl_idname = "jbeam_editor.force_jbeam_sync"
    bl_label = "Force JBeam Sync"
    bl_description = "Manually syncs JBeam file with the mesh. Use it when the JBeam file doesn't get updated after a JBeam mesh operation (e.g. transforming a vertex with the input boxes above)"

    def invoke(self, context, event):
        print('Force JBeam Sync!')
        global _force_do_export
        _force_do_export = True
        return {'FINISHED'}


# Undo action (supposed to use this instead of Blender's undo)
class JBEAM_EDITOR_OT_undo(bpy.types.Operator):
    bl_idname = "jbeam_editor.undo"
    bl_label = "Undo"

    def invoke(self, context, event):
        print('undoing!')
        text_editor.on_undo_redo(context, True)
        refresh_curr_vdata(True)
        return {'FINISHED'}


# Redo action (supposed to use this instead of Blender's redo)
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
        global selected_nodes
        # Check active object validity AND editing enabled
        obj = context.active_object
        if not obj or obj.data.get(constants.MESH_JBEAM_PART) is None or not obj.data.get(constants.MESH_EDITING_ENABLED, False):
            return False
        return len(selected_nodes) in (2,3,4)

    def invoke(self, context, event):
        global selected_nodes

        obj = context.active_object
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        init_node_id_layer = bm.verts.layers.string[constants.VL_INIT_NODE_ID]
        is_fake_layer = bm.verts.layers.int[constants.VL_NODE_IS_FAKE]
        # Ensure lookup table for index access
        bm.verts.ensure_lookup_table()

        export = False

        len_selected_verts = len(selected_nodes)

        new_verts = []
        # Iterate through indices and node IDs
        for vert_index, node_id in selected_nodes:
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
                # Assign part origin based on the active object's part >>>
                e[beam_part_origin_layer] = bytes(obj_data[constants.MESH_JBEAM_PART], 'utf-8')
                export = True
            else:
                # If edge exists but isn't a JBeam beam yet, mark it as new
                if existing_edge[beam_indices_layer].decode('utf-8') == '':
                    existing_edge[beam_indices_layer] = bytes('-1', 'utf-8')
                    # Assign part origin based on the active object's part >>>
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
                # Assign part origin based on the active object's part >>>
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
            global _force_do_export
            _force_do_export = True

        return {'FINISHED'}


# Flip JBeam faces
class JBEAM_EDITOR_OT_flip_jbeam_faces(bpy.types.Operator):
    bl_idname = "jbeam_editor.flip_jbeam_faces"
    bl_label = "Flip Face(s)"

    @classmethod
    def poll(cls, context):
        global selected_tris_quads
        # Check active object validity AND editing enabled
        obj = context.active_object
        if not obj or obj.data.get(constants.MESH_JBEAM_PART) is None or not obj.data.get(constants.MESH_EDITING_ENABLED, False):
            return False
        return len(selected_tris_quads) > 0

    def invoke(self, context, event):
        global selected_tris_quads

        obj = context.active_object
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        face_flip_flag_layer = bm.faces.layers.int[constants.FL_FACE_FLIP_FLAG]

        face: bmesh.types.BMFace
        face_idx: int
        for (face, face_idx) in selected_tris_quads:
            # Toggle the flip flag instead of just setting to 1
            current_flag = face[face_flip_flag_layer]
            face[face_flip_flag_layer] = 1 - current_flag # Toggle 0 to 1 and 1 to 0
            face.normal_flip() # Also flip the Blender face normal for visual consistency

        # Update mesh after flipping normals
        bmesh.update_edit_mesh(obj_data)
        # No need to free bm from edit mesh

        global _force_do_export
        _force_do_export = True

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

        global batch_node_renaming_enabled
        batch_node_renaming_enabled = not batch_node_renaming_enabled
        if not batch_node_renaming_enabled:
            ui_props.batch_node_renaming_node_idx = 1
        return {'FINISHED'}

# <<< ADDED: Node Search Operator >>>
class JBEAM_EDITOR_OT_find_node(bpy.types.Operator):
    bl_idname = "jbeam_editor.find_node"
    bl_label = "Find Node"
    bl_description = "Find and select the specified node ID in the active object (Vertex Mode only)" # Updated description

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
# <<< END ADDED >>>


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
        layout.operator('jbeam_editor.force_jbeam_sync', text='Force JBeam Sync')


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
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None # Removed MESH_EDITING_ENABLED check here

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
            try: # Add try-except for bmesh access
                bm = bmesh.from_edit_mesh(obj_data)
            except Exception as e:
                print(f"Error getting bmesh for JBeam panel: {e}")
                self.layout.label(text="Error accessing mesh data.")
                return
        # No need for bmesh in object mode for this panel's current functionality

        scene = context.scene
        ui_props = scene.ui_properties

        jbeam_part_name = obj_data.get(constants.MESH_JBEAM_PART) # Use .get() for safety

        layout = self.layout
        if jbeam_part_name: # Check if it's a JBeam mesh
            layout.label(text=f'{jbeam_part_name}')

            # --- Existing Functionality Box ---
            action_box = layout.box()
            col = action_box.column()
            # Disable action box content if not in edit mode or editing disabled
            col.enabled = obj.mode == 'EDIT' and editing_enabled # Keep this line as is

            global selected_nodes
            global selected_beams
            global selected_tris_quads
            len_selected_verts = len(selected_nodes)
            len_selected_faces = len(selected_tris_quads)

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
                col.row().operator('jbeam_editor.add_beam_tri_quad', text=label)

            if len_selected_faces > 0:
                col.row().operator('jbeam_editor.flip_jbeam_faces')

        # No need to free bm from edit mesh

# <<< ADDED: Find Node Panel >>>
class JBEAM_EDITOR_PT_find_node(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel" # Make it a sub-panel
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Find Node'
    bl_options = {'DEFAULT_CLOSED'} # Start collapsed

    # Poll method checks if it's a JBeam object
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        # Allow panel to show, but content might be disabled
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        ui_props = scene.ui_properties
        obj = context.active_object

        if not obj or not obj.data:
            layout.label(text="No active object.")
            return

        # Check if editing is enabled for enable/disable logic
        editing_enabled = obj.data.get(constants.MESH_EDITING_ENABLED, False)

        box = layout.box()
        col = box.column(align=True)
        # Disable content if not in edit mode or editing disabled
        col.enabled = obj.mode == 'EDIT' and editing_enabled

        row = col.row(align=True)
        row.prop(ui_props, 'search_node_id', text="")
        # The operator button will now inherit the active state from the row
        # Use the operator's poll method to control its active state (greyed out)
        row.operator(JBEAM_EDITOR_OT_find_node.bl_idname, text="", icon='VIEWZOOM')
# <<< END ADDED >>>


class JBEAM_EDITOR_PT_jbeam_properties_panel(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Properties'
    bl_options = {'DEFAULT_CLOSED'}

    # Poll method checks if editing is enabled
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        # Allow panel to show even if editing is disabled
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None # Removed MESH_EDITING_ENABLED check

    def draw(self, context):
        global curr_vdata

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

        # Check if editing is enabled for this specific panel's content
        editing_enabled = obj_data.get(constants.MESH_EDITING_ENABLED, False)
        if not editing_enabled:
            col.label(text="JBeam editing disabled for this object.")
            return

        veh_model = obj_data.get(constants.MESH_VEHICLE_MODEL)

        if obj.mode != 'EDIT':
            col.label(text="Enter Edit Mode to see properties.")
            return

        bm = None
        try: # Add try-except for bmesh access
            bm = bmesh.from_edit_mesh(obj_data)
            bm.verts.ensure_lookup_table() # Ensure lookup table
            bm.edges.ensure_lookup_table() # Ensure lookup table
            bm.faces.ensure_lookup_table() # Ensure lookup table
        except Exception as e:
            print(f"Error getting bmesh for properties panel: {e}")
            col.label(text="Error accessing mesh data.")
            return

        global selected_nodes
        global selected_beams
        global selected_tris_quads

        if curr_vdata is None:
            col.label(text="JBeam data not loaded.")
            # No need to free bm from edit mesh
            return

        if len(selected_nodes) == 1:
            if 'nodes' in curr_vdata:
                # Get index and node_id
                vert_index, node_id = selected_nodes[0]

                if node_id in curr_vdata['nodes']:
                    node = curr_vdata['nodes'][node_id]
                    col.label(text=f"Node: {node_id}")
                    for k in sorted(node.keys(), key=lambda x: str(x)):
                        if k == 'pos' or k == Metadata or k == 'posNoOffset': continue # Don't show raw position, Metadata, or posNoOffset
                        val = node[k]
                        str_val = repr(val)
                        col.row().label(text=f'- {k}: {str_val}')
                else:
                    col.label(text=f"Node '{node_id}' not found in JBeam data.")
            else:
                col.label(text="'nodes' section not found.")

        elif len(selected_beams) == 1:
            if 'beams' in curr_vdata:
                edge_data = selected_beams[0]
                e, beam_indices_str = edge_data[0], edge_data[1] # e is the BMEdge object
                part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
                beam_indices = beam_indices_str.split(',')

                if not beam_indices or not part_origin_layer:
                     col.label(text="Beam data missing.")
                     return

                part_origin = e[part_origin_layer].decode('utf-8')
                try:
                    beam_idx_in_part = int(beam_indices[0]) # Use first index if multiple beams share edge
                except ValueError:
                    col.label(text="Invalid beam index.")
                    return

                # Find the correct global beam index
                global_beam_idx = -1
                current_part_beam_count = 0
                for i, b in enumerate(curr_vdata['beams']):
                    # Check if beam belongs to the part associated with the Blender edge
                    if b.get('partOrigin') == part_origin:
                        current_part_beam_count += 1
                        if current_part_beam_count == beam_idx_in_part:
                            global_beam_idx = i
                            break

                if global_beam_idx != -1 and global_beam_idx < len(curr_vdata['beams']):
                    beam = curr_vdata['beams'][global_beam_idx]
                    col.label(text=f"Beam: {beam.get('id1:', '?')}-{beam.get('id2:', '?')} (Index {beam_idx_in_part} in {part_origin})")
                    for k in sorted(beam.keys(), key=lambda x: str(x)):
                        if k in ('id1:', 'id2:', 'partOrigin') or k == Metadata: # Exclude Metadata class
                            continue
                        val = beam[k]
                        str_val = repr(val)
                        col.row().label(text=f'- {k}: {str_val}')
                else:
                    col.label(text=f"Beam index {beam_idx_in_part} not found in part '{part_origin}'.")
            else:
                col.label(text="'beams' section not found.")

        elif len(selected_tris_quads) == 1:
            face_data = selected_tris_quads[0]
            f, face_idx_in_part = face_data[0], face_data[1] # f is the BMFace object
            num_verts = len(f.verts)

            face_type = None
            if num_verts == 3:
                face_type = 'triangles'
            elif num_verts == 4:
                face_type = 'quads'

            if face_type and face_type in curr_vdata:
                face_idx_layer = bm.faces.layers.int.get(constants.FL_FACE_IDX)
                part_origin_layer = bm.faces.layers.string.get(constants.FL_FACE_PART_ORIGIN)

                if not face_idx_layer or not part_origin_layer:
                    col.label(text="Face data missing.")
                    return

                part_origin = f[part_origin_layer].decode('utf-8')

                # Find the correct global face index
                global_face_idx = -1
                current_part_face_count = 0
                for i, face_entry in enumerate(curr_vdata[face_type]):
                     # Check if face belongs to the part associated with the Blender face
                    if face_entry.get('partOrigin') == part_origin:
                        current_part_face_count += 1
                        if current_part_face_count == face_idx_in_part:
                            global_face_idx = i
                            break

                if global_face_idx != -1 and global_face_idx < len(curr_vdata[face_type]):
                    face = curr_vdata[face_type][global_face_idx]
                    ids = [face.get(f'id{x+1}:', '?') for x in range(num_verts)]
                    col.label(text=f"{face_type.capitalize()[:-1]}: {'-'.join(ids)} (Index {face_idx_in_part} in {part_origin})")

                    for k in sorted(face.keys(), key=lambda x: str(x)):
                        if k.startswith('id') and k.endswith(':'): continue # Don't repeat IDs
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

        # No need to free bm from edit mesh


class JBEAM_EDITOR_PT_batch_node_renaming(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel" # Make it a sub-panel
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Batch Node Renaming'
    bl_options = {'DEFAULT_CLOSED'} # Start collapsed

    # Poll method checks if editing is enabled
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        # Allow panel to show, but content will be disabled if editing is off
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        obj = context.active_object # Get active object again for enable/disable logic
        editing_enabled = obj and obj.data and obj.data.get(constants.MESH_EDITING_ENABLED, False)

        box = layout.box()
        col = box.column()
        # Disable content if not in edit mode or editing disabled
        col.enabled = obj and obj.mode == 'EDIT' and editing_enabled

        col.row().label(text='Naming Scheme')
        col.prop(ui_props, 'batch_node_renaming_naming_scheme', text = "")
        col.prop(ui_props, 'batch_node_renaming_node_idx', text = "Node Index")

        operator_text = 'Stop' if batch_node_renaming_enabled else 'Start'
        col.operator(JBEAM_EDITOR_OT_batch_node_renaming.bl_idname, text=operator_text)


class JBEAM_EDITOR_PT_jbeam_settings(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Settings'

    # Poll method checks if editing is enabled
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        # Allow panel to show, but content might be disabled
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None

    def draw(self, context):
        obj = context.active_object
        if not obj:
            return

        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return

        # Check if editing is enabled for enable/disable logic
        editing_enabled = obj_data.get(constants.MESH_EDITING_ENABLED, False)

        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        # Check if it's a JBeam mesh before drawing settings (redundant due to poll, but safe)
        if obj_data.get(constants.MESH_JBEAM_PART) is not None:
            box = layout.box()
            col = box.column(align=True) # Align elements in the column
            # Disable content if editing is disabled
            col.enabled = editing_enabled

            col.label(text="General:")
            col.prop(ui_props, 'affect_node_references', text="Affect Node References")

            # --- Tooltips Section --- # <<< NEW SECTION >>>
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

                # --- Node Tooltips Sub-panel ---
                node_tooltip_box = tooltips_col.box()
                row = node_tooltip_box.row(align=True)
                row.prop(ui_props, "show_node_tooltips_panel",
                         icon="TRIA_DOWN" if ui_props.show_node_tooltips_panel else "TRIA_RIGHT",
                         icon_only=True, emboss=False)
                row.label(text="Node Tooltips")

                if ui_props.show_node_tooltips_panel:
                    node_tooltip_col = node_tooltip_box.column(align=True)

                    # Node Line Tooltip Row
                    row = node_tooltip_col.row(align=True)
                    row.prop(ui_props, 'toggle_node_line_tooltip', text="Show Line #")
                    row = node_tooltip_col.row(align=True)
                    row.prop(ui_props, 'node_line_tooltip_color', text="")
                    row.enabled = ui_props.toggle_node_line_tooltip # Disable colors if main toggle is off

                    # Parameters Tooltip
                    row = node_tooltip_col.row(align=True)
                    row.prop(ui_props, 'toggle_node_params_tooltip', text="Show Params")
                    row = node_tooltip_col.row(align=True)
                    row.enabled = ui_props.toggle_node_params_tooltip # Disable colors if main toggle is off
                    # Use split to place colors side-by-side on the new row
                    split = row.split(factor=0.5, align=True)
                    split.prop(ui_props, 'node_params_tooltip_color', text="Name") # Add text labels
                    split.prop(ui_props, 'node_params_value_tooltip_color', text="Value") # Add text labels

                # --- Beam Tooltips Sub-panel ---
                beam_tooltip_box = tooltips_col.box()
                row = beam_tooltip_box.row(align=True)
                row.prop(ui_props, "show_beam_tooltips_panel",
                         icon="TRIA_DOWN" if ui_props.show_beam_tooltips_panel else "TRIA_RIGHT",
                         icon_only=True, emboss=False)
                row.label(text="Beam Tooltips")

                if ui_props.show_beam_tooltips_panel:
                    beam_tooltip_col = beam_tooltip_box.column(align=True)

                    # Line Number Tooltip
                    row = beam_tooltip_col.row(align=True)
                    row.prop(ui_props, 'toggle_beam_line_tooltip', text="Show Line #")
                    row = beam_tooltip_col.row(align=True)
                    row.prop(ui_props, 'beam_line_tooltip_color', text="")
                    row.enabled = ui_props.toggle_beam_line_tooltip # Disable colors if main toggle is off

                    # Parameters Tooltip
                    row = beam_tooltip_col.row(align=True)
                    row.prop(ui_props, 'toggle_beam_params_tooltip', text="Show Params")
                    row = beam_tooltip_col.row(align=True)
                    row.enabled = ui_props.toggle_beam_params_tooltip # Disable colors if main toggle is off
                    # Use split to place colors side-by-side on the new row
                    split = row.split(factor=0.5, align=True)
                    split.prop(ui_props, 'beam_params_tooltip_color', text="") # Add text labels
                    split.prop(ui_props, 'beam_params_value_tooltip_color', text="") # Add text labels

            # --- End Tooltips Section ---

            col.separator()
            col.label(text="Node Visualization:")
            col.prop(ui_props, 'toggle_node_ids_text', text="Show Node IDs Text")

            # Font Size Slider
            row = col.row()
            row.enabled = ui_props.toggle_node_ids_text # Disable slider if text is off
            row.prop(ui_props, 'node_id_font_size', text="Font Size")

            # <<< NEW: Outline Size Slider >>>
            row = col.row()
            row.enabled = ui_props.toggle_node_ids_text # Disable slider if text is off
            row.prop(ui_props, 'node_id_outline_size', text="Outline Size")
            # <<< END NEW >>>

            # --- Beam Visualization (Collapsible) ---
            col.separator()
            beam_vis_box = col.box() # Wrap in a box
            row = beam_vis_box.row(align=True)
            # Add toggle property
            row.prop(ui_props, "show_beam_visualization_panel",
                     icon="TRIA_DOWN" if ui_props.show_beam_visualization_panel else "TRIA_RIGHT",
                     icon_only=True, emboss=False)
            row.label(text="Beam Visualization") # Label next to toggle

            if ui_props.show_beam_visualization_panel: # Check toggle
                beam_vis_col = beam_vis_box.column(align=True) # Column for the content

                # Normal Beams
                beam_vis_col.prop(ui_props, 'toggle_beams_vis')
                row = beam_vis_col.row()
                row.enabled = ui_props.toggle_beams_vis
                row.prop(ui_props, 'beam_color')
                beam_vis_col.prop(ui_props, 'beam_width')

                # Anisotropic Beams
                beam_vis_col.prop(ui_props, 'toggle_anisotropic_beams_vis')
                row = beam_vis_col.row()
                row.enabled = ui_props.toggle_anisotropic_beams_vis
                row.prop(ui_props, 'anisotropic_beam_color')
                beam_vis_col.prop(ui_props, 'anisotropic_beam_width')

                # Support Beams
                beam_vis_col.prop(ui_props, 'toggle_support_beams_vis')
                row = beam_vis_col.row()
                row.enabled = ui_props.toggle_support_beams_vis
                row.prop(ui_props, 'support_beam_color')
                beam_vis_col.prop(ui_props, 'support_beam_width')

                # Hydro Beams
                beam_vis_col.prop(ui_props, 'toggle_hydro_beams_vis')
                row = beam_vis_col.row()
                row.enabled = ui_props.toggle_hydro_beams_vis
                row.prop(ui_props, 'hydro_beam_color')
                beam_vis_col.prop(ui_props, 'hydro_beam_width')

                # Bounded Beams
                beam_vis_col.prop(ui_props, 'toggle_bounded_beams_vis')
                row = beam_vis_col.row()
                row.enabled = ui_props.toggle_bounded_beams_vis
                row.prop(ui_props, 'bounded_beam_color')
                beam_vis_col.prop(ui_props, 'bounded_beam_width')

                # LBeams
                beam_vis_col.prop(ui_props, 'toggle_lbeam_beams_vis')
                row = beam_vis_col.row()
                row.enabled = ui_props.toggle_lbeam_beams_vis
                row.prop(ui_props, 'lbeam_beam_color')
                beam_vis_col.prop(ui_props, 'lbeam_beam_width')

                # Pressured Beams
                beam_vis_col.prop(ui_props, 'toggle_pressured_beams_vis')
                row = beam_vis_col.row()
                row.enabled = ui_props.toggle_pressured_beams_vis
                row.prop(ui_props, 'pressured_beam_color')
                beam_vis_col.prop(ui_props, 'pressured_beam_width')

            # --- Cross-Part Beam Visualization (Moved Here) --- # <<< RENAMED SECTION
            col.separator()
            col.label(text="Cross-Part Beam Visualization:") # <<< RENAMED LABEL
            col.prop(ui_props, 'toggle_cross_part_beams_vis') # <<< USE RENAMED PROPERTY
            row = col.row()
            row.enabled = ui_props.toggle_cross_part_beams_vis # <<< USE RENAMED PROPERTY
            row.prop(ui_props, 'cross_part_beam_color') # <<< USE RENAMED PROPERTY
            col.prop(ui_props, 'cross_part_beam_width') # <<< USE RENAMED PROPERTY
            # --- END MOVED ---

            # --- Torsionbar Visualization ---
            col.separator()
            col.label(text="Torsionbar Visualization:")
            col.prop(ui_props, 'toggle_torsionbars_vis')
            row = col.row()
            row.enabled = ui_props.toggle_torsionbars_vis
            row.prop(ui_props, 'torsionbar_color')
            row = col.row()
            row.enabled = ui_props.toggle_torsionbars_vis
            row.prop(ui_props, 'torsionbar_mid_color')
            col.prop(ui_props, 'torsionbar_width')

            # --- Rail Visualization ---
            col.separator()
            col.label(text="Rail Visualization:")
            col.prop(ui_props, 'toggle_rails_vis')
            row = col.row()
            row.enabled = ui_props.toggle_rails_vis
            row.prop(ui_props, 'rail_color')
            col.prop(ui_props, 'rail_width')


def update_all_nodes_cache(context: bpy.types.Context): # <<< RENAMED FUNCTION
    """Scans ALL loaded JBeam text files and caches node positions and part origins."""
    global all_nodes_cache, all_nodes_cache_dirty # <<< USE RENAMED GLOBALS
    print("Updating all nodes cache...")
    all_nodes_cache.clear() # <<< USE RENAMED CACHE
    scene = context.scene
    ui_props = scene.ui_properties

    if not ui_props.toggle_cross_part_beams_vis: # <<< USE RENAMED PROPERTY
        print("Cross-part beam visualization disabled, skipping cache update.")
        all_nodes_cache_dirty = False # Cache is up-to-date (empty) # <<< USE RENAMED FLAG
        return

    # Get the mapping, defaulting to an empty dict if it doesn't exist
    short_to_full_map = scene.get(SCENE_SHORT_TO_FULL_FILENAME, {})

    if not short_to_full_map: # Check if the map is empty
        print("Scene mapping not found or empty, cannot update nodes cache yet.")
        all_nodes_cache_dirty = False # Mark as clean (nothing to cache) # <<< USE RENAMED FLAG
        return

    for short_name, text_obj in bpy.data.texts.items():
        full_filepath = short_to_full_map.get(short_name)

        if not full_filepath: # Still need to check if filepath exists in map
            continue

        # Heuristic check if it's likely a JBeam file (ends with .jbeam)
        if not full_filepath.lower().endswith('.jbeam'):
            continue

        try:
            file_content = text_obj.as_string()
            if not file_content: continue

            # Quick check for "nodes" key before full parsing
            if '"nodes"' not in file_content:
                continue

            # Parse the SJSON content
            padded_content = file_content + chr(127) * 2
            c, i = bng_sjson._skip_white_space(padded_content, 0, full_filepath)
            parsed_data = None
            if c == 123: # Starts with '{'
                parsed_data, _ = bng_sjson._read_object(padded_content, i, full_filepath)
            else:
                # Don't warn for every non-JBeam file, just skip silently
                # print(f"Warning: File {full_filepath} does not start with '{{', skipping node cache.")
                continue

            if not parsed_data: continue

            # Assume nodes are defined within parts at the top level
            for part_name, part_data in parsed_data.items():
                if isinstance(part_data, dict) and 'nodes' in part_data:
                    nodes_section = part_data['nodes']
                    if isinstance(nodes_section, list) and len(nodes_section) > 1:
                        # Assume standard ["id", "posX", "posY", "posZ"] header
                        header = nodes_section[0]
                        try:
                            id_idx = header.index("id")
                            x_idx = header.index("posX")
                            y_idx = header.index("posY")
                            z_idx = header.index("posZ")
                        except (ValueError, IndexError):
                            # Don't warn for every part with a non-standard header
                            # print(f"Warning: Invalid node header in {full_filepath} > {part_name}, skipping nodes.")
                            continue

                        for node_row in nodes_section[1:]:
                            if isinstance(node_row, list) and len(node_row) > max(id_idx, x_idx, y_idx, z_idx):
                                node_id = node_row[id_idx]
                                try:
                                    # <<< MODIFICATION START >>>
                                    # Check if position values are strings (likely expressions)
                                    pos_x_val = node_row[x_idx]
                                    pos_y_val = node_row[y_idx]
                                    pos_z_val = node_row[z_idx]

                                    # Attempt conversion, skip if it fails (due to expressions)
                                    pos = Vector((float(pos_x_val), float(pos_y_val), float(pos_z_val)))
                                    # <<< MODIFICATION END >>>

                                    if node_id in all_nodes_cache: # <<< USE RENAMED CACHE
                                        pass # Overwrite with the last one found
                                    # <<< Store part_name as part_origin >>>
                                    all_nodes_cache[node_id] = (pos, full_filepath, part_name) # <<< USE RENAMED CACHE & ADD part_name
                                # <<< MODIFICATION START >>>
                                # Catch ValueError specifically, which occurs for expressions like '$=...'
                                except ValueError:
                                     # It's expected that some nodes use expressions. Silently skip them
                                     # during this external cache build.
                                     # Optional: print a debug message if needed, but avoid user-facing warnings.
                                     # if constants.DEBUG:
                                     #    print(f"Debug: Skipping node '{node_id}' in {full_filepath} due to expression-based position.")
                                     pass
                                except TypeError as e:
                                     # Catch other potential type errors during conversion
                                     print(f"Warning: Could not parse node position for '{node_id}' in {full_filepath} (TypeError): {e}")
                                # <<< MODIFICATION END >>>

        except Exception as e:
            print(f"Error processing file {full_filepath} for node cache: {e}", file=sys.stderr)
            traceback.print_exc()

    print(f"All nodes cache updated with {len(all_nodes_cache)} nodes.") # <<< USE RENAMED CACHE
    all_nodes_cache_dirty = False # Mark cache as clean # <<< USE RENAMED FLAG


def refresh_curr_vdata(force_refresh=False):
    global prev_obj_selected
    global curr_vdata
    global veh_render_dirty
    global all_nodes_cache_dirty # <<< USE RENAMED FLAG

    context = bpy.context
    scene = context.scene # Get scene
    ui_props = scene.ui_properties # Get ui_props

    selected_obj_name = None
    jbeam_part = None
    is_new_jbeam_object = False # Flag to check if we switched *to* a JBeam object

    obj = context.active_object
    if obj is not None:
        obj_data = obj.data
        if obj_data and obj_data.get(constants.MESH_JBEAM_PART) is not None:
            jbeam_part = obj_data.get(constants.MESH_JBEAM_PART)
            selected_obj_name = obj.name
            if prev_obj_selected != selected_obj_name:
                 is_new_jbeam_object = True # Switched to this JBeam object
        else:
            selected_obj_name = None
            jbeam_part = None
    else:
        selected_obj_name = None

    object_changed = prev_obj_selected != selected_obj_name

    if force_refresh or object_changed:
        if jbeam_part is not None and obj is not None: # Make sure obj exists and is JBeam
            collection = obj.users_collection[0] if obj.users_collection else None # Check if object is in a collection
            veh_model = collection.get(constants.COLLECTION_VEHICLE_MODEL) if collection else None

            try:
                if veh_model is not None and collection.get(constants.COLLECTION_VEHICLE_BUNDLE):
                    curr_vdata = pickle.loads(base64.b64decode(collection[constants.COLLECTION_VEHICLE_BUNDLE]))['vdata']
                elif obj_data.get(constants.MESH_SINGLE_JBEAM_PART_DATA):
                    curr_vdata = pickle.loads(base64.b64decode(obj_data[constants.MESH_SINGLE_JBEAM_PART_DATA]))
                else:
                    curr_vdata = None # Data might not be loaded yet or invalid state
            except (TypeError, KeyError, EOFError, pickle.UnpicklingError, base64.binascii.Error) as e:
                 print(f"Error loading JBeam data for {selected_obj_name}: {e}", file=sys.stderr) # Print to stderr
                 curr_vdata = None
        else:
            curr_vdata = None # Clear data if not a JBeam object

        # --- Cache Update Logic --- # <<< USE RENAMED PROPERTY
        # Update cache if the object changed *to* a valid JBeam object OR if forced
        if ui_props.toggle_cross_part_beams_vis and (is_new_jbeam_object or force_refresh):
             all_nodes_cache_dirty = True # Mark cache dirty, update happens in draw_callback_view if needed # <<< USE RENAMED FLAG

        veh_render_dirty = True # Always set render dirty if object changed or forced
        prev_obj_selected = selected_obj_name

part_name_to_obj: dict[str, bpy.types.Object] = {}

# Draws a 3D text at each vertex position of their assigned node ID
def draw_callback_px(context: bpy.types.Context):
    scene = context.scene
    ui_props = scene.ui_properties
    if not hasattr(context, 'scene') or not hasattr(scene, 'ui_properties'):
        return
    font_id = 0

    active_obj = context.active_object
    # Check if active object is a JBeam object AND selected >>>
    is_valid_jbeam_obj = False
    is_selected = False
    # Add check for MESH_EDITING_ENABLED >>>
    is_editing_enabled = False
    if active_obj and active_obj.data and active_obj.data.get(constants.MESH_JBEAM_PART) is not None:
        is_valid_jbeam_obj = True
        # Check MESH_EDITING_ENABLED >>>
        is_editing_enabled = active_obj.data.get(constants.MESH_EDITING_ENABLED, False)
        # Check if the active object is actually in the list of selected objects
        if active_obj in context.selected_objects:
            is_selected = True

    # Update condition to include is_editing_enabled >>>
    # Condition to draw: Must be a valid JBeam object AND selected AND editing enabled
    should_draw = is_valid_jbeam_obj and is_selected and is_editing_enabled

    if not should_draw:
        return # Don't draw if not a valid JBeam object OR not selected OR editing disabled
    active_obj_data = active_obj.data

    collection = active_obj.users_collection[0] if active_obj.users_collection else None
    is_vehicle_part = collection is not None and collection.get(constants.COLLECTION_VEHICLE_MODEL) is not None

    bm = None
    try:
        if active_obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(active_obj_data)
        elif not is_vehicle_part: # Only need bmesh for single parts in object mode
            bm = bmesh.new()
            bm.from_mesh(active_obj_data)
    except Exception as e:
        print(f"Error accessing bmesh for {active_obj.name}: {e}", file=sys.stderr)
        # Don't return here if it's a vehicle part, we might still draw other parts
        if not is_vehicle_part:
            return

    # Common drawing setup
    ctxRegion = context.region
    ctxRegionData = context.region_data
    lblfPosition = blfpos
    lblfDraw = blfdraw
    lblfDims = blfdims
    # Use UI property for font size >>>
    blfsize(font_id, ui_props.node_id_font_size)
    default_color = (1.0, 1.0, 1.0, 1.0) # Store default color
    # <<< ADDED: Yellow color for selected nodes >>>
    selected_color = (1.0, 1.0, 0.0, 1.0) # Yellow
    # <<< END ADDED >>>
    black_color = (0.0, 0.0, 0.0, 1.0) # Black outline color
    # Read outline size from UI property >>>
    outline_size = ui_props.node_id_outline_size

    # --- Helper function for drawing text with outline ---
    def draw_text_with_outline(font_id, text, x, y, text_color):
        # Only draw outlines if size > 0 >>>
        if outline_size > 0:
            # Draw black outlines using outline_size >>>
            blfcolor(font_id, *black_color)
            lblfPosition(font_id, x - outline_size, y, 0)
            lblfDraw(font_id, text)
            lblfPosition(font_id, x + outline_size, y, 0)
            lblfDraw(font_id, text)
            lblfPosition(font_id, x, y - outline_size, 0)
            lblfDraw(font_id, text)
            lblfPosition(font_id, x, y + outline_size, 0)
            lblfDraw(font_id, text)
            # Optional: Add diagonal offsets for thicker outline (consider performance)
            if outline_size > 1: # Only add diagonals if outline is thicker
                 lblfPosition(font_id, x - outline_size, y - outline_size, 0); lblfDraw(font_id, text)
                 lblfPosition(font_id, x + outline_size, y - outline_size, 0); lblfDraw(font_id, text)
                 lblfPosition(font_id, x - outline_size, y + outline_size, 0); lblfDraw(font_id, text)
                 lblfPosition(font_id, x + outline_size, y + outline_size, 0); lblfDraw(font_id, text)

        # Draw original text
        blfcolor(font_id, *text_color)
        lblfPosition(font_id, x, y, 0)
        lblfDraw(font_id, text)
    # --- End helper function ---

    # --- Node ID Drawing ---
    toggleNodeText = ui_props.toggle_node_ids_text
    if toggleNodeText:
        # <<< ADDED: Create set of selected indices for quick lookup >>>
        selected_indices_set = {idx for idx, _ in selected_nodes} if active_obj.mode == 'EDIT' else set()
        # <<< END ADDED >>>

        if is_vehicle_part:
            part_name_to_obj.clear()
            for obj in collection.all_objects:
                 # Check if the object in the collection is a JBeam part before adding
                if obj.data and obj.data.get(constants.MESH_JBEAM_PART):
                     part_name_to_obj[obj.data[constants.MESH_JBEAM_PART]] = obj

            for part_name, obj in part_name_to_obj.items():
                if not obj.visible_get():
                    continue

                part_bm = None
                obj_data = obj.data
                try:
                    # Use active edit bmesh if it's the current part, otherwise create temp bmesh
                    if obj == active_obj and active_obj.mode == 'EDIT':
                        part_bm = bm # Use the bm obtained earlier for the active object in edit mode
                    else:
                        part_bm = bmesh.new()
                        part_bm.from_mesh(obj_data)

                    node_id_layer = part_bm.verts.layers.string.get(constants.VL_NODE_ID)
                    is_fake_layer = part_bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)

                    if not node_id_layer or not is_fake_layer:
                        if part_bm != bm and part_bm: part_bm.free()
                        continue

                    part_bm.verts.ensure_lookup_table()

                    for v in part_bm.verts:
                        if v[is_fake_layer] == 1 or v.hide:
                            continue

                        coord = obj.matrix_world @ v.co
                        node_id = v[node_id_layer].decode('utf-8')

                        pos_text = location_3d_to_region_2d(ctxRegion, ctxRegionData, coord)
                        if pos_text:
                            # Choose color based on selection >>>
                            text_color = selected_color if obj == active_obj and v.index in selected_indices_set else default_color
                            # Use the helper function to draw with outline
                            draw_text_with_outline(font_id, node_id, pos_text[0], pos_text[1], text_color)

                except Exception as e:
                    print(f"Error processing part {obj.name} for drawing: {e}", file=sys.stderr)
                finally:
                     # Free the temporary bmesh if it was created
                     if part_bm and part_bm != bm:
                        part_bm.free()

        elif bm: # Single Part Drawing Logic (Object or Edit Mode)
            node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
            is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)

            if node_id_layer and is_fake_layer:
                bm.verts.ensure_lookup_table()
                for v in bm.verts:
                    if v[is_fake_layer] == 1 or v.hide:
                        continue

                    coord = active_obj.matrix_world @ v.co
                    node_id = v[node_id_layer].decode('utf-8')

                    pos_text = location_3d_to_region_2d(ctxRegion, ctxRegionData, coord)
                    if pos_text:
                        # Choose color based on selection >>>
                        text_color = selected_color if v.index in selected_indices_set else default_color
                        # Use the helper function to draw with outline
                        draw_text_with_outline(font_id, node_id, pos_text[0], pos_text[1], text_color)

    # --- Cross-Part Node ID Drawing --- # <<< RENAMED SECTION
    # Check if cross-part beams are visible, cache exists, JBeam data is loaded, AND Node ID text is toggled on >>>
    if ui_props.toggle_cross_part_beams_vis and ui_props.toggle_node_ids_text and all_nodes_cache and curr_vdata and 'beams' in curr_vdata: # <<< ADDED toggle_node_ids_text CHECK & USE RENAMED PROPERTY & CACHE
        cross_part_color = ui_props.cross_part_beam_color # Get color once # <<< USE RENAMED PROPERTY

        # --- Identify target external/cross-part nodes connected to the active part ---
        target_other_part_node_ids = set()
        active_part_name = active_obj_data.get(constants.MESH_JBEAM_PART)

        if active_part_name: # Ensure we have an active part name
            for beam in curr_vdata['beams']:
                # Check if beam belongs to the currently active part
                if beam.get('partOrigin') == active_part_name:
                    id1 = beam.get('id1:')
                    id2 = beam.get('id2:')

                    # Check if id1 or id2 belongs to a *different* part using the cache
                    node1_cache_data = all_nodes_cache.get(id1) # <<< USE RENAMED CACHE
                    node2_cache_data = all_nodes_cache.get(id2) # <<< USE RENAMED CACHE

                    # If node1 is in cache AND its partOrigin is different from active part
                    if node1_cache_data and node1_cache_data[2] != active_part_name:
                        target_other_part_node_ids.add(id1)
                    # If node2 is in cache AND its partOrigin is different from active part
                    if node2_cache_data and node2_cache_data[2] != active_part_name:
                        target_other_part_node_ids.add(id2)
        # --- End Identification ---

        # Iterate through the cached nodes
        for node_id, (world_pos, _, part_origin) in all_nodes_cache.items(): # <<< USE RENAMED CACHE & UNPACK part_origin
            # --- Draw only if it's a target node connected to the active part ---
            if node_id in target_other_part_node_ids:
                # Convert 3D position to 2D screen coordinates
                pos_text = location_3d_to_region_2d(ctxRegion, ctxRegionData, world_pos)
                if pos_text:
                    # Use the helper function to draw with outline, using cross-part color
                    draw_text_with_outline(font_id, node_id, pos_text[0], pos_text[1], cross_part_color)

    # --- Tooltip Positioning Calculations ---
    padding_x = 65 # Default padding for left alignment
    padding_y = 20
    region_width = ctxRegion.width
    region_height = ctxRegion.height
    line_height = lblfDims(font_id, "X")[1]
    line_padding = 4 # Vertical padding between lines
    tooltip_placement = ui_props.tooltip_placement

    # Calculate bottom_left_x based on placement
    est_max_width = 250 # Increased estimate for potentially longer lines
    if tooltip_placement == 'BOTTOM_LEFT':
        bottom_left_x = padding_x
    elif tooltip_placement == 'BOTTOM_CENTER':
        bottom_left_x = region_width / 2 - est_max_width / 2
    elif tooltip_placement == 'BOTTOM_RIGHT':
        bottom_left_x = region_width - est_max_width - padding_x
    else: # Default to left
        bottom_left_x = padding_x

    # --- Beam Tooltip Drawing ---
    global _selected_beam_line_info, _selected_beam_params_info
    beam_params_height = 0
    beam_line_height_offset = 0

    # Calculate height needed for beam params first
    if ui_props.toggle_beam_params_tooltip and _selected_beam_params_info is not None:
        params_list = _selected_beam_params_info.get('params_list')
        if params_list:
            beam_params_height = len(params_list) * (line_height + line_padding)

    # Calculate Y position for beam line tooltip (above params)
    if ui_props.toggle_beam_line_tooltip and _selected_beam_line_info is not None:
        line_num = _selected_beam_line_info.get('line')
        if line_num is not None:
            beam_line_y = padding_y + beam_params_height # Position above params
            beam_line_height_offset = line_height + line_padding # Account for its height
            tooltip_text = f"Line: {line_num+1}"
            # Use helper function for outline
            draw_text_with_outline(font_id, tooltip_text, bottom_left_x, beam_line_y, ui_props.beam_line_tooltip_color)

    # Draw Beam Parameters Tooltip (below line number)
    if ui_props.toggle_beam_params_tooltip and _selected_beam_params_info is not None:
        params_list = _selected_beam_params_info.get('params_list')
        if params_list:
            name_color = ui_props.beam_params_tooltip_color
            value_color = ui_props.beam_params_value_tooltip_color
            start_y = padding_y + (len(params_list) - 1) * (line_height + line_padding) # Start from bottom

            for i, (key, value_repr) in enumerate(params_list):
                current_y = start_y - (i * (line_height + line_padding))
                key_text = f"{key}: "

                # Draw Key with outline
                draw_text_with_outline(font_id, key_text, bottom_left_x, current_y, name_color)

                # Calculate position for value
                key_width = lblfDims(font_id, key_text)[0]
                value_x = bottom_left_x + key_width

                # Draw Value with outline
                draw_text_with_outline(font_id, value_repr, value_x, current_y, value_color)

    # --- Node Tooltip Drawing ---
    global _selected_node_line_info, _selected_node_params_info
    node_params_height = 0
    node_line_height_offset = 0
    # Calculate total height occupied by beam tooltips
    total_beam_tooltip_height = beam_params_height + beam_line_height_offset

    # Calculate height needed for node params
    if ui_props.toggle_node_params_tooltip and _selected_node_params_info is not None:
        params_list = _selected_node_params_info.get('params_list')
        if params_list:
            node_params_height = len(params_list) * (line_height + line_padding)

    # Calculate Y position for node line tooltip (above node params and beam tooltips)
    if ui_props.toggle_node_line_tooltip and _selected_node_line_info is not None:
        line_num = _selected_node_line_info.get('line')
        if line_num is not None:
            node_line_y = padding_y + total_beam_tooltip_height + node_params_height # Position above everything else
            node_line_height_offset = line_height + line_padding # Account for its height
            tooltip_text = f"Line: {line_num}"
            # Use helper function for outline
            draw_text_with_outline(font_id, tooltip_text, bottom_left_x, node_line_y, ui_props.node_line_tooltip_color)

    # Draw Node Parameters Tooltip (below node line number, above beam tooltips)
    if ui_props.toggle_node_params_tooltip and _selected_node_params_info is not None:
        params_list = _selected_node_params_info.get('params_list')
        if params_list:
            name_color = ui_props.node_params_tooltip_color
            value_color = ui_props.node_params_value_tooltip_color
            # Start Y position is above beam tooltips
            start_y = padding_y + total_beam_tooltip_height + (len(params_list) - 1) * (line_height + line_padding)

            for i, (key, value_repr) in enumerate(params_list):
                current_y = start_y - (i * (line_height + line_padding))
                key_text = f"{key}: "

                # Draw Key with outline
                draw_text_with_outline(font_id, key_text, bottom_left_x, current_y, name_color)

                # Calculate position for value
                key_width = lblfDims(font_id, key_text)[0]
                value_x = bottom_left_x + key_width

                # Draw Value with outline
                draw_text_with_outline(font_id, value_repr, value_x, current_y, value_color)

    # Final cleanup
    # Free bmesh if it was created for a single part in object mode
    if bm and not is_vehicle_part and active_obj.mode != 'EDIT':
        bm.free()


def draw_callback_view(context: bpy.types.Context):
    global veh_render_dirty
    global beam_render_shader
    global beam_render_batch # NORMAL
    global beam_coords       # NORMAL
    # <<< ADDED: Anisotropic >>>
    global anisotropic_beam_render_batch
    global anisotropic_beam_coords
    # <<< END ADDED >>>
    # <<< ADDED: Support >>>
    global support_beam_render_batch
    global support_beam_coords
    # <<< END ADDED >>>
    # <<< ADDED: Hydro >>>
    global hydro_beam_render_batch
    global hydro_beam_coords
    # <<< END ADDED >>>
    # <<< ADDED: Bounded >>>
    global bounded_beam_render_batch
    global bounded_beam_coords
    # <<< END ADDED >>>
    # <<< ADDED: LBeam >>>
    global lbeam_render_batch
    global lbeam_coords
    # <<< END ADDED >>>
    # <<< ADDED: Pressured >>>
    global pressured_beam_render_batch
    global pressured_beam_coords
    # <<< END ADDED >>>
    global torsionbar_render_batch
    global torsionbar_coords
    global torsionbar_red_render_batch
    global torsionbar_red_coords
    global rail_render_batch
    global rail_coords
    # <<< ADDED: Cross-part globals >>> # <<< RENAMED COMMENT
    global cross_part_beam_render_batch # <<< RENAMED VARIABLE
    global cross_part_beam_coords       # <<< RENAMED VARIABLE
    global all_nodes_cache              # <<< RENAMED VARIABLE
    global all_nodes_cache_dirty        # <<< RENAMED VARIABLE
    # <<< END ADDED >>>

    scene = context.scene
    ui_props = scene.ui_properties
    # Check if ui_properties exists
    if not hasattr(context, 'scene') or not hasattr(scene, 'ui_properties'):
        return

    active_obj = context.active_object
    # Check if active object is valid JBeam AND selected
    is_valid_jbeam_obj = False
    is_selected = False
    if active_obj and active_obj.data and active_obj.data.get(constants.MESH_JBEAM_PART) is not None:
        is_valid_jbeam_obj = True
        # Check if the active object is actually in the list of selected objects
        if active_obj in context.selected_objects:
            is_selected = True

    # Condition to draw: Must be a valid JBeam object AND selected
    should_draw = is_valid_jbeam_obj and is_selected

    if not should_draw:
        # Clear batches if the object is not valid JBeam OR not selected
        batches_were_cleared = False
        if beam_render_batch: beam_render_batch = None; batches_were_cleared = True
        # <<< ADDED: Clear new batches >>>
        if anisotropic_beam_render_batch: anisotropic_beam_render_batch = None; batches_were_cleared = True
        if support_beam_render_batch: support_beam_render_batch = None; batches_were_cleared = True
        if hydro_beam_render_batch: hydro_beam_render_batch = None; batches_were_cleared = True
        if bounded_beam_render_batch: bounded_beam_render_batch = None; batches_were_cleared = True
        if lbeam_render_batch: lbeam_render_batch = None; batches_were_cleared = True
        if pressured_beam_render_batch: pressured_beam_render_batch = None; batches_were_cleared = True
        # <<< END ADDED >>>
        if torsionbar_render_batch: torsionbar_render_batch = None; batches_were_cleared = True
        if torsionbar_red_render_batch: torsionbar_red_render_batch = None; batches_were_cleared = True
        if rail_render_batch: rail_render_batch = None; batches_were_cleared = True
        # <<< ADDED: Clear cross-part beam batch >>> # <<< RENAMED COMMENT
        if cross_part_beam_render_batch: cross_part_beam_render_batch = None; batches_were_cleared = True # <<< RENAMED VARIABLE
        # <<< END ADDED >>>

        # Clear coordinates only if batches were actually cleared to avoid unnecessary clearing
        if batches_were_cleared:
            beam_coords.clear()
            # <<< ADDED: Clear new coords >>>
            anisotropic_beam_coords.clear()
            support_beam_coords.clear()
            hydro_beam_coords.clear()
            bounded_beam_coords.clear()
            lbeam_coords.clear()
            pressured_beam_coords.clear()
            # <<< END ADDED >>>
            torsionbar_coords.clear()
            torsionbar_red_coords.clear()
            rail_coords.clear()
            # <<< ADDED: Clear cross-part beam coords >>> # <<< RENAMED COMMENT
            cross_part_beam_coords.clear() # <<< RENAMED VARIABLE
            # <<< END ADDED >>>
            veh_render_dirty = True # Ensure rebuild on re-selection
        return # Don't draw if not a valid JBeam object OR not selected

    # --- Shader and Dirty Check ---
    if beam_render_shader is None:
        beam_render_shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    # <<< ADDED: Check if node cache needs update >>> # <<< USE RENAMED PROPERTY & FLAG
    if ui_props.toggle_cross_part_beams_vis and all_nodes_cache_dirty:
        update_all_nodes_cache(context) # Update the cache if dirty and toggle is on # <<< USE RENAMED FUNCTION
        veh_render_dirty = True # Force geometry rebuild after cache update
    elif not ui_props.toggle_cross_part_beams_vis and all_nodes_cache: # <<< USE RENAMED PROPERTY & CACHE
        # Clear cache and batches if toggle was turned off
        all_nodes_cache.clear() # <<< USE RENAMED CACHE
        cross_part_beam_coords.clear() # <<< USE RENAMED VARIABLE
        cross_part_beam_render_batch = None # <<< USE RENAMED VARIABLE
        all_nodes_cache_dirty = False # Cache is clean (empty) # <<< USE RENAMED FLAG
        # Don't necessarily set veh_render_dirty, only the cross-part part changed
    # <<< END ADDED >>>

    # Check for missing batches based on toggles
    batches_missing = (
        (ui_props.toggle_beams_vis and beam_render_batch is None) or
        # <<< ADDED: Check new batches >>>
        (ui_props.toggle_anisotropic_beams_vis and anisotropic_beam_render_batch is None) or
        (ui_props.toggle_support_beams_vis and support_beam_render_batch is None) or
        (ui_props.toggle_hydro_beams_vis and hydro_beam_render_batch is None) or
        (ui_props.toggle_bounded_beams_vis and bounded_beam_render_batch is None) or
        (ui_props.toggle_lbeam_beams_vis and lbeam_render_batch is None) or
        (ui_props.toggle_pressured_beams_vis and pressured_beam_render_batch is None) or
        # <<< END ADDED >>>
        (ui_props.toggle_torsionbars_vis and torsionbar_render_batch is None) or
        (ui_props.toggle_torsionbars_vis and torsionbar_red_render_batch is None) or
        (ui_props.toggle_rails_vis and rail_render_batch is None) or
        # <<< Check cross-part batch >>> # <<< RENAMED COMMENT
        (ui_props.toggle_cross_part_beams_vis and cross_part_beam_render_batch is None and all_nodes_cache) # <<< USE RENAMED PROPERTY, VARIABLE, CACHE
    )
    if batches_missing:
        veh_render_dirty = True

    if veh_render_dirty:
        # Clear coordinate lists before rebuilding
        beam_coords.clear()
        # <<< ADDED: Clear new coords >>>
        anisotropic_beam_coords.clear()
        support_beam_coords.clear()
        hydro_beam_coords.clear()
        bounded_beam_coords.clear()
        lbeam_coords.clear()
        pressured_beam_coords.clear()
        # <<< END ADDED >>>
        torsionbar_coords.clear()
        torsionbar_red_coords.clear()
        rail_coords.clear()
        # <<< ADDED: Clear cross-part coords >>> # <<< RENAMED COMMENT
        cross_part_beam_coords.clear() # <<< USE RENAMED VARIABLE
        # <<< END ADDED >>>

        # active_obj is guaranteed to be valid JBeam and selected here
        active_obj_data = active_obj.data

        # --- Vehicle Data Gathering ---
        collection = active_obj.users_collection[0] if active_obj.users_collection else None
        is_vehicle_part = collection is not None and collection.get(constants.COLLECTION_VEHICLE_MODEL) is not None

        # Determine import type based on stored data constants
        is_single_part_import = not is_vehicle_part and active_obj_data.get(constants.MESH_SINGLE_JBEAM_PART_DATA) is not None

        # Map node IDs to their hidden status and current position/matrix
        node_id_to_hide_status: dict[str, bool] = {}
        node_id_to_pos_matrix_map: dict[str, tuple[Vector, Matrix]] = {} # Use Matrix type hint

        current_part_name = active_obj_data.get(constants.MESH_JBEAM_PART) # Get current part name

        # --- Beam Type Mapping ---
        # Map Blender edge index (within its part) to JBeam beam type
        edge_idx_to_beam_type_map: dict[tuple[str, int], str] = {} # {(part_name, edge_index): beamType}

        # Pre-process beam data to get beam types if curr_vdata is available
        if curr_vdata and 'beams' in curr_vdata:
            beam_part_counters = {} # {part_name: current_beam_index_in_part}
            for global_beam_idx, beam_data in enumerate(curr_vdata['beams']):
                part_origin = beam_data.get('partOrigin')
                beam_type = beam_data.get('beamType', '|NORMAL') # Default to NORMAL
                if part_origin:
                    # Increment counter for this part
                    current_idx_in_part = beam_part_counters.get(part_origin, 0) + 1
                    beam_part_counters[part_origin] = current_idx_in_part
                    # Map the (part, index_in_part) to the beam type
                    edge_idx_to_beam_type_map[(part_origin, current_idx_in_part)] = beam_type
        # --- End Beam Type Mapping ---


        if is_vehicle_part:
            # Use part_name_to_obj which should be populated by draw_callback_px or needs population here
            if not part_name_to_obj: # Populate if empty
                 for obj_iter in collection.all_objects:
                     # Check if the object in the collection is a JBeam part
                    if obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART):
                        part_name_to_obj[obj_iter.data[constants.MESH_JBEAM_PART]] = obj_iter

            for obj_iter in collection.all_objects:
                # Only process visible JBeam parts in the collection
                if obj_iter.visible_get() and obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART) is not None:
                    obj_iter_data = obj_iter.data
                    part_name = obj_iter_data.get(constants.MESH_JBEAM_PART) # Get part name for this object
                    bm = None
                    try:
                        # Get bmesh, handle edit mode for active object
                        if obj_iter == active_obj and active_obj.mode == 'EDIT':
                            bm = bmesh.from_edit_mesh(obj_iter_data)
                        else:
                            bm = bmesh.new()
                            bm.from_mesh(obj_iter_data)

                        # Get layers safely
                        beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
                        node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
                        is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
                        beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN) # Needed for edge key

                        # Populate node maps
                        if node_id_layer and is_fake_layer:
                            bm.verts.ensure_lookup_table()
                            obj_matrix_copy = obj_iter.matrix_world.copy() # Copy matrix once per object
                            for v in bm.verts:
                                if v[is_fake_layer] == 0: # Only consider real nodes
                                    node_id = v[node_id_layer].decode('utf-8')
                                    node_id_to_hide_status[node_id] = v.hide
                                    # Store local coord and object matrix
                                    node_id_to_pos_matrix_map[node_id] = (v.co.copy(), obj_matrix_copy)

                        # Gather Beam Coords (using mesh edges and beam type map)
                        if beam_indices_layer and beam_part_origin_layer:
                            bm.edges.ensure_lookup_table()
                            for e in bm.edges:
                                # Check if edge itself or connected verts are hidden
                                if e.hide or any(v.hide for v in e.verts):
                                    continue
                                # Check if it's a JBeam beam (index is not empty)
                                beam_idx_str = e[beam_indices_layer].decode('utf-8')
                                if beam_idx_str != '' and beam_idx_str != '-1': # Check it's a valid JBeam beam
                                    try:
                                        # Use the first index if multiple beams share the edge
                                        first_beam_idx_in_part = int(beam_idx_str.split(',')[0])
                                        edge_part_origin = e[beam_part_origin_layer].decode('utf-8') # Get origin from edge layer

                                        # Get beam type from the pre-processed map
                                        beam_type = edge_idx_to_beam_type_map.get((edge_part_origin, first_beam_idx_in_part), '|NORMAL')

                                        v1, v2 = e.verts[0], e.verts[1]
                                        world_pos1 = obj_iter.matrix_world @ v1.co
                                        world_pos2 = obj_iter.matrix_world @ v2.co

                                        # Add coordinates to the correct list based on beam type
                                        if beam_type == '|ANISOTROPIC':
                                            anisotropic_beam_coords.append(world_pos1)
                                            anisotropic_beam_coords.append(world_pos2)
                                        elif beam_type == '|SUPPORT':
                                            support_beam_coords.append(world_pos1)
                                            support_beam_coords.append(world_pos2)
                                        # <<< ADDED: Check new beam types >>>
                                        elif beam_type == '|HYDRO':
                                            hydro_beam_coords.append(world_pos1)
                                            hydro_beam_coords.append(world_pos2)
                                        elif beam_type == '|BOUNDED':
                                            bounded_beam_coords.append(world_pos1)
                                            bounded_beam_coords.append(world_pos2)
                                        elif beam_type == '|LBEAM':
                                            lbeam_coords.append(world_pos1)
                                            lbeam_coords.append(world_pos2)
                                        elif beam_type == '|PRESSURED':
                                            pressured_beam_coords.append(world_pos1)
                                            pressured_beam_coords.append(world_pos2)
                                        # <<< END ADDED >>>
                                        else: # Default to NORMAL
                                            beam_coords.append(world_pos1)
                                            beam_coords.append(world_pos2)

                                    except (ValueError, IndexError) as parse_err:
                                        print(f"Warning: Could not parse beam index '{beam_idx_str}' for edge in part '{part_name}'. Error: {parse_err}", file=sys.stderr)

                    except Exception as e:
                        print(f"Error getting geometry data from {obj_iter.name}: {e}", file=sys.stderr) # Print to stderr
                    finally:
                        # Free bmesh if created, don't free the active edit mesh
                        if bm and not (obj_iter == active_obj and active_obj.mode == 'EDIT'):
                            bm.free()

            # Gather Torsionbar Coords (using node_id_to_pos_matrix_map)
            if curr_vdata and 'torsionbars' in curr_vdata and isinstance(curr_vdata['torsionbars'], list):
                torsionbars_data = curr_vdata['torsionbars']
                for tb in torsionbars_data:
                    id1, id2, id3, id4 = tb.get('id1:'), tb.get('id2:'), tb.get('id3:'), tb.get('id4:')
                    if not all([id1, id2, id3, id4]): continue # Skip if any ID is missing in the JBeam data itself

                    # Check hidden status first
                    if (node_id_to_hide_status.get(id1, False) or
                        node_id_to_hide_status.get(id2, False) or
                        node_id_to_hide_status.get(id3, False) or
                        node_id_to_hide_status.get(id4, False)):
                        continue

                    # Get positions based on import type
                    world_pos1, world_pos2, world_pos3, world_pos4 = None, None, None, None
                    all_nodes_found = True
                    missing_nodes_for_warning = [] # Track missing nodes specifically for vehicle import warnings

                    for node_id in [id1, id2, id3, id4]:
                        pos_data = node_id_to_pos_matrix_map.get(node_id) # Check internal map first
                        world_pos = None
                        if pos_data:
                            world_pos = pos_data[1] @ pos_data[0] # Calculate world position from matrix and local coord
                        # Only check external cache if it's a vehicle import and the feature is enabled
                        elif is_vehicle_part and ui_props.toggle_cross_part_beams_vis: # <<< USE RENAMED PROPERTY
                            ext_pos_data = all_nodes_cache.get(node_id) # <<< USE RENAMED CACHE
                            if ext_pos_data:
                                world_pos = ext_pos_data[0] # External position is already world

                        if world_pos is None:
                            all_nodes_found = False
                            # Only track missing nodes for warning if it's a vehicle import
                            if is_vehicle_part:
                                missing_nodes_for_warning.append(node_id)
                            # For single part imports, just break silently if a node isn't in the current object
                            # For vehicle imports, break after checking all nodes to list all missing ones in the warning
                            if is_single_part_import:
                                break

                        # Assign to correct variable if found
                        if node_id == id1: world_pos1 = world_pos
                        elif node_id == id2: world_pos2 = world_pos
                        elif node_id == id3: world_pos3 = world_pos
                        elif node_id == id4: world_pos4 = world_pos

                    # After checking all nodes for this torsionbar:
                    if not all_nodes_found:
                        # Print warning only for vehicle imports if nodes were missing and torsionbars are visible
                        if is_vehicle_part and missing_nodes_for_warning and ui_props.toggle_torsionbars_vis:
                            print(f"Warning: Could not find position data for torsionbar nodes {missing_nodes_for_warning}", file=sys.stderr)
                        continue # Skip adding coords for this torsionbar

                    # If all nodes were found
                    torsionbar_coords.append(world_pos1); torsionbar_coords.append(world_pos2)
                    torsionbar_coords.append(world_pos3); torsionbar_coords.append(world_pos4)
                    torsionbar_red_coords.append(world_pos2); torsionbar_red_coords.append(world_pos3)


            # Gather Rail Coords (using node_id_to_pos_matrix_map AND all_nodes_cache) # <<< USE RENAMED CACHE
            if curr_vdata and 'rails' in curr_vdata and isinstance(curr_vdata['rails'], dict):
                rails_data = curr_vdata['rails']
                for rail_name, rail_info in rails_data.items():
                    links = rail_info.get('links:')
                    if isinstance(links, list) and len(links) == 2:
                        id1, id2 = links[0], links[1]
                        if not all([id1, id2]): continue # Skip if any ID is missing in the JBeam data itself

                        # Check hidden status first
                        if node_id_to_hide_status.get(id1, False) or node_id_to_hide_status.get(id2, False):
                            continue

                        # Get positions based on import type
                        world_pos1, world_pos2 = None, None
                        all_nodes_found = True
                        missing_nodes_for_warning = [] # Track missing nodes specifically for vehicle import warnings

                        for node_id in [id1, id2]:
                            pos_data = node_id_to_pos_matrix_map.get(node_id) # Check internal map first
                            world_pos = None
                            if pos_data:
                                world_pos = pos_data[1] @ pos_data[0] # Calculate world position
                            # Only check external cache if it's a vehicle import and the feature is enabled
                            elif is_vehicle_part and ui_props.toggle_cross_part_beams_vis: # <<< USE RENAMED PROPERTY
                                ext_pos_data = all_nodes_cache.get(node_id) # <<< USE RENAMED CACHE
                                if ext_pos_data:
                                    world_pos = ext_pos_data[0] # External position is already world

                            if world_pos is None:
                                all_nodes_found = False
                                # Only track missing nodes for warning if it's a vehicle import
                                if is_vehicle_part:
                                    missing_nodes_for_warning.append(node_id)
                                # For single part imports, just break silently if a node isn't in the current object
                                # For vehicle imports, break after checking all nodes to list all missing ones in the warning
                                if is_single_part_import:
                                    break

                            # Assign to correct variable if found
                            if node_id == id1: world_pos1 = world_pos
                            elif node_id == id2: world_pos2 = world_pos

                        # After checking all nodes for this rail:
                        if not all_nodes_found:
                            # Print warning only for vehicle imports if nodes were missing and rails are visible
                            if is_vehicle_part and missing_nodes_for_warning and ui_props.toggle_rails_vis:
                                 print(f"Warning: Could not find position data for rail nodes {missing_nodes_for_warning}", file=sys.stderr)
                            continue # Skip adding coords for this rail

                        # If all nodes were found
                        rail_coords.append(world_pos1); rail_coords.append(world_pos2)

            # Gather Cross-Part Beam Logic (using node_id_to_pos_matrix_map AND all_nodes_cache) # <<< RENAMED SECTION
            if ui_props.toggle_cross_part_beams_vis and curr_vdata and 'beams' in curr_vdata: # <<< USE RENAMED PROPERTY
                for beam in curr_vdata['beams']:
                    beam_part_origin = beam.get('partOrigin')
                    # Only process beams originating from the currently active part
                    if beam_part_origin != current_part_name: continue

                    id1, id2 = beam.get('id1:'), beam.get('id2:')
                    if not id1 or not id2: continue

                    # Get node data from internal map (active part) and the full cache
                    pos1_data = node_id_to_pos_matrix_map.get(id1)
                    pos2_data = node_id_to_pos_matrix_map.get(id2)
                    cache1_data = all_nodes_cache.get(id1) # <<< USE RENAMED CACHE
                    cache2_data = all_nodes_cache.get(id2) # <<< USE RENAMED CACHE

                    world_pos1, world_pos2 = None, None
                    is_cross_part = False

                    # Case 1: Node 1 is internal (active part), Node 2 is external (different part)
                    if pos1_data and cache2_data and cache2_data[2] != current_part_name:
                        if node_id_to_hide_status.get(id1, False) or node_id_to_hide_status.get(id2, False): continue # Check hide status using internal map for id1
                        co1, matrix1 = pos1_data
                        world_pos1 = matrix1 @ co1
                        world_pos2 = cache2_data[0] # Position from cache
                        is_cross_part = True

                    # Case 2: Node 1 is external (different part), Node 2 is internal (active part)
                    elif cache1_data and cache1_data[2] != current_part_name and pos2_data:
                        if node_id_to_hide_status.get(id1, False) or node_id_to_hide_status.get(id2, False): continue # Check hide status using internal map for id2
                        world_pos1 = cache1_data[0] # Position from cache
                        co2, matrix2 = pos2_data
                        world_pos2 = matrix2 @ co2
                        is_cross_part = True

                    # Add coordinates if it's a cross-part beam and positions were found
                    if is_cross_part and world_pos1 and world_pos2:
                        cross_part_beam_coords.append(world_pos1) # <<< USE RENAMED VARIABLE
                        cross_part_beam_coords.append(world_pos2) # <<< USE RENAMED VARIABLE

        # --- Single Part Data Gathering ---
        else: # is_valid_jbeam_obj is True, but not part of a vehicle collection
            if active_obj.visible_get():
                part_name = active_obj_data.get(constants.MESH_JBEAM_PART) # Get part name
                bm = None
                try:
                    if active_obj.mode == 'EDIT':
                        bm = bmesh.from_edit_mesh(active_obj_data)
                    else:
                        bm = bmesh.new()
                        bm.from_mesh(active_obj_data)

                    # Get layers safely
                    node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
                    is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
                    beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
                    beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN) # Needed for edge key

                    # Populate node maps for single part
                    if node_id_layer and is_fake_layer:
                        bm.verts.ensure_lookup_table()
                        obj_matrix_copy = active_obj.matrix_world.copy() # Copy matrix once
                        for v in bm.verts:
                            if v[is_fake_layer] == 0: # Only consider real nodes
                                node_id = v[node_id_layer].decode('utf-8')
                                node_id_to_hide_status[node_id] = v.hide
                                node_id_to_pos_matrix_map[node_id] = (v.co.copy(), obj_matrix_copy)

                    # Gather Beams (using mesh edges and beam type map)
                    if beam_indices_layer and beam_part_origin_layer:
                        bm.edges.ensure_lookup_table()
                        for e in bm.edges:
                            if e.hide or any(v.hide for v in e.verts):
                                continue
                            beam_idx_str = e[beam_indices_layer].decode('utf-8')
                            if beam_idx_str != '' and beam_idx_str != '-1': # Check it's a valid JBeam beam
                                try:
                                    first_beam_idx_in_part = int(beam_idx_str.split(',')[0])
                                    edge_part_origin = e[beam_part_origin_layer].decode('utf-8')

                                    beam_type = edge_idx_to_beam_type_map.get((edge_part_origin, first_beam_idx_in_part), '|NORMAL')

                                    v1, v2 = e.verts[0], e.verts[1]
                                    world_pos1 = active_obj.matrix_world @ v1.co
                                    world_pos2 = active_obj.matrix_world @ v2.co

                                    if beam_type == '|ANISOTROPIC':
                                        anisotropic_beam_coords.append(world_pos1)
                                        anisotropic_beam_coords.append(world_pos2)
                                    elif beam_type == '|SUPPORT':
                                        support_beam_coords.append(world_pos1)
                                        support_beam_coords.append(world_pos2)
                                    # <<< ADDED: Check new beam types >>>
                                    elif beam_type == '|HYDRO':
                                        hydro_beam_coords.append(world_pos1)
                                        hydro_beam_coords.append(world_pos2)
                                    elif beam_type == '|BOUNDED':
                                        bounded_beam_coords.append(world_pos1)
                                        bounded_beam_coords.append(world_pos2)
                                    elif beam_type == '|LBEAM':
                                        lbeam_coords.append(world_pos1)
                                        lbeam_coords.append(world_pos2)
                                    elif beam_type == '|PRESSURED':
                                        pressured_beam_coords.append(world_pos1)
                                        pressured_beam_coords.append(world_pos2)
                                    # <<< END ADDED >>>
                                    else: # Default to NORMAL
                                        beam_coords.append(world_pos1)
                                        beam_coords.append(world_pos2)

                                except (ValueError, IndexError) as parse_err:
                                    print(f"Warning: Could not parse beam index '{beam_idx_str}' for edge in part '{part_name}'. Error: {parse_err}", file=sys.stderr)

                    # Gather Torsionbars (using node_id_to_pos_matrix_map)
                    if curr_vdata and 'torsionbars' in curr_vdata and isinstance(curr_vdata['torsionbars'], list):
                        torsionbars_data = curr_vdata['torsionbars']
                        for tb in torsionbars_data:
                            id1, id2, id3, id4 = tb.get('id1:'), tb.get('id2:'), tb.get('id3:'), tb.get('id4:')
                            if not all([id1, id2, id3, id4]): continue # Skip if any ID is missing in the JBeam data itself

                            # Check hidden status first
                            if (node_id_to_hide_status.get(id1, False) or
                                node_id_to_hide_status.get(id2, False) or
                                node_id_to_hide_status.get(id3, False) or
                                node_id_to_hide_status.get(id4, False)):
                                continue

                            # Get positions based on import type
                            world_pos1, world_pos2, world_pos3, world_pos4 = None, None, None, None
                            all_nodes_found = True
                            missing_nodes_for_warning = [] # Track missing nodes specifically for vehicle import warnings

                            for node_id in [id1, id2, id3, id4]:
                                pos_data = node_id_to_pos_matrix_map.get(node_id) # Check internal map first
                                world_pos = None
                                if pos_data:
                                    world_pos = pos_data[1] @ pos_data[0] # Calculate world position from matrix and local coord
                                # Only check external cache if it's a vehicle import and the feature is enabled
                                elif is_vehicle_part and ui_props.toggle_cross_part_beams_vis: # <<< USE RENAMED PROPERTY
                                    ext_pos_data = all_nodes_cache.get(node_id) # <<< USE RENAMED CACHE
                                    if ext_pos_data:
                                        world_pos = ext_pos_data[0] # External position is already world

                                if world_pos is None:
                                    all_nodes_found = False
                                    # Only track missing nodes for warning if it's a vehicle import
                                    if is_vehicle_part:
                                        missing_nodes_for_warning.append(node_id)
                                    # For single part imports, just break silently if a node isn't in the current object
                                    # For vehicle imports, break after checking all nodes to list all missing ones in the warning
                                    if is_single_part_import:
                                        break

                                # Assign to correct variable if found
                                if node_id == id1: world_pos1 = world_pos
                                elif node_id == id2: world_pos2 = world_pos
                                elif node_id == id3: world_pos3 = world_pos
                                elif node_id == id4: world_pos4 = world_pos

                            # After checking all nodes for this torsionbar:
                            if not all_nodes_found:
                                # Print warning only for vehicle imports if nodes were missing and torsionbars are visible
                                if is_vehicle_part and missing_nodes_for_warning and ui_props.toggle_torsionbars_vis:
                                    print(f"Warning: Could not find position data for torsionbar nodes {missing_nodes_for_warning}", file=sys.stderr)
                                continue # Skip adding coords for this torsionbar

                            # If all nodes were found
                            torsionbar_coords.append(world_pos1); torsionbar_coords.append(world_pos2)
                            torsionbar_coords.append(world_pos3); torsionbar_coords.append(world_pos4)
                            torsionbar_red_coords.append(world_pos2); torsionbar_red_coords.append(world_pos3)


                    # Gather Rail Coords (using node_id_to_pos_matrix_map AND all_nodes_cache) # <<< USE RENAMED CACHE
                    if curr_vdata and 'rails' in curr_vdata and isinstance(curr_vdata['rails'], dict):
                        rails_data = curr_vdata['rails']
                        for rail_name, rail_info in rails_data.items():
                            links = rail_info.get('links:')
                            if isinstance(links, list) and len(links) == 2:
                                id1, id2 = links[0], links[1]
                                if not all([id1, id2]): continue # Skip if any ID is missing in the JBeam data itself

                                # Check hidden status first
                                if node_id_to_hide_status.get(id1, False) or node_id_to_hide_status.get(id2, False):
                                    continue

                                # Get positions based on import type
                                world_pos1, world_pos2 = None, None
                                all_nodes_found = True
                                missing_nodes_for_warning = [] # Track missing nodes specifically for vehicle import warnings

                                for node_id in [id1, id2]:
                                    pos_data = node_id_to_pos_matrix_map.get(node_id) # Check internal map first
                                    world_pos = None
                                    if pos_data:
                                        world_pos = pos_data[1] @ pos_data[0] # Calculate world position
                                    # Only check external cache if it's a vehicle import and the feature is enabled
                                    elif is_vehicle_part and ui_props.toggle_cross_part_beams_vis: # <<< USE RENAMED PROPERTY
                                        ext_pos_data = all_nodes_cache.get(node_id) # <<< USE RENAMED CACHE
                                        if ext_pos_data:
                                            world_pos = ext_pos_data[0] # External position is already world

                                    if world_pos is None:
                                        all_nodes_found = False
                                        # Only track missing nodes for warning if it's a vehicle import
                                        if is_vehicle_part:
                                            missing_nodes_for_warning.append(node_id)
                                        # For single part imports, just break silently if a node isn't in the current object
                                        # For vehicle imports, break after checking all nodes to list all missing ones in the warning
                                        if is_single_part_import:
                                            break

                                    # Assign to correct variable if found
                                    if node_id == id1: world_pos1 = world_pos
                                    elif node_id == id2: world_pos2 = world_pos

                                # After checking all nodes for this rail:
                                if not all_nodes_found:
                                    # Print warning only for vehicle imports if nodes were missing and rails are visible
                                    if is_vehicle_part and missing_nodes_for_warning and ui_props.toggle_rails_vis:
                                         print(f"Warning: Could not find position data for rail nodes {missing_nodes_for_warning}", file=sys.stderr)
                                    continue # Skip adding coords for this rail

                                # If all nodes were found
                                rail_coords.append(world_pos1); rail_coords.append(world_pos2)

                    # Gather Cross-Part Beam Logic (using node_id_to_pos_matrix_map AND all_nodes_cache) # <<< RENAMED SECTION
                    if ui_props.toggle_cross_part_beams_vis and curr_vdata and 'beams' in curr_vdata: # <<< USE RENAMED PROPERTY
                        obj_matrix = active_obj.matrix_world # Get matrix once
                        for beam in curr_vdata['beams']:
                            # Only process beams originating from the currently active part
                            if beam.get('partOrigin') != current_part_name: continue

                            id1, id2 = beam.get('id1:'), beam.get('id2:')
                            if not id1 or not id2: continue

                            # Get node data from internal map (active part) and the full cache
                            pos1_data = node_id_to_pos_matrix_map.get(id1)
                            pos2_data = node_id_to_pos_matrix_map.get(id2)
                            cache1_data = all_nodes_cache.get(id1) # <<< USE RENAMED CACHE
                            cache2_data = all_nodes_cache.get(id2) # <<< USE RENAMED CACHE

                            world_pos1, world_pos2 = None, None
                            is_cross_part = False

                            # Case 1: Node 1 is internal (active part), Node 2 is external (different part)
                            if pos1_data and cache2_data and cache2_data[2] != current_part_name:
                                if node_id_to_hide_status.get(id1, False) or node_id_to_hide_status.get(id2, False): continue
                                co1, _ = pos1_data
                                world_pos1 = obj_matrix @ co1
                                world_pos2 = cache2_data[0] # Position from cache
                                is_cross_part = True

                            # Case 2: Node 1 is external (different part), Node 2 is internal (active part)
                            elif cache1_data and cache1_data[2] != current_part_name and pos2_data:
                                if node_id_to_hide_status.get(id1, False) or node_id_to_hide_status.get(id2, False): continue
                                world_pos1 = cache1_data[0] # Position from cache
                                co2, _ = pos2_data
                                world_pos2 = obj_matrix @ co2
                                is_cross_part = True

                            # Add coordinates if it's a cross-part beam and positions were found
                            if is_cross_part and world_pos1 and world_pos2:
                                cross_part_beam_coords.append(world_pos1) # <<< USE RENAMED VARIABLE
                                cross_part_beam_coords.append(world_pos2) # <<< USE RENAMED VARIABLE

                except Exception as e:
                    print(f"Error getting geometry data from {active_obj.name}: {e}", file=sys.stderr) # Print to stderr
                finally:
                    # Free bmesh if created, don't free the active edit mesh
                    if bm and not (active_obj.mode == 'EDIT'):
                        bm.free()

        # Create batches only if coordinates were generated
        if beam_coords:
            beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": beam_coords})
        else:
            beam_render_batch = None # Ensure batch is None if no coords

        # <<< ADDED: Create new batches >>>
        if anisotropic_beam_coords:
            anisotropic_beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": anisotropic_beam_coords})
        else:
            anisotropic_beam_render_batch = None

        if support_beam_coords:
            support_beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": support_beam_coords})
        else:
            support_beam_render_batch = None

        if hydro_beam_coords:
            hydro_beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": hydro_beam_coords})
        else:
            hydro_beam_render_batch = None

        if bounded_beam_coords:
            bounded_beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": bounded_beam_coords})
        else:
            bounded_beam_render_batch = None

        if lbeam_coords:
            lbeam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": lbeam_coords})
        else:
            lbeam_render_batch = None

        if pressured_beam_coords:
            pressured_beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": pressured_beam_coords})
        else:
            pressured_beam_render_batch = None
        # <<< END ADDED >>>

        if torsionbar_coords:
            torsionbar_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": torsionbar_coords})
        else:
            torsionbar_render_batch = None

        if torsionbar_red_coords: # New batch for red segments
            torsionbar_red_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": torsionbar_red_coords})
        else:
            torsionbar_red_render_batch = None

        if rail_coords:
            rail_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": rail_coords})
        else:
            rail_render_batch = None

        # <<< ADDED: Create cross-part beam batch >>> # <<< RENAMED COMMENT
        if cross_part_beam_coords: # <<< USE RENAMED VARIABLE
            cross_part_beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": cross_part_beam_coords}) # <<< USE RENAMED VARIABLE x2
        else:
            cross_part_beam_render_batch = None # Ensure batch is None if no coords # <<< USE RENAMED VARIABLE
        # <<< END ADDED >>>

        veh_render_dirty = False # Reset dirty flag AFTER potentially rebuilding

    # --- Drawing ---
    # Only draw if the batches exist and the corresponding toggle is enabled
    gpu.state.depth_test_set('LESS_EQUAL') # Enable depth test once

    # Draw Normal Beams
    if beam_render_batch is not None and ui_props.toggle_beams_vis: # Check toggle
        beam_render_shader.uniform_float("color", ui_props.beam_color) # Use UI color
        gpu.state.line_width_set(ui_props.beam_width) # Use UI width
        gpu.state.depth_mask_set(True) # Enable depth writing
        beam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False) # Disable depth writing (optional, depends on desired effect)

    # <<< ADDED: Draw Anisotropic Beams >>>
    if anisotropic_beam_render_batch is not None and ui_props.toggle_anisotropic_beams_vis:
        beam_render_shader.uniform_float("color", ui_props.anisotropic_beam_color)
        gpu.state.line_width_set(ui_props.anisotropic_beam_width)
        gpu.state.depth_mask_set(True)
        anisotropic_beam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False)
    # <<< END ADDED >>>

    # <<< ADDED: Draw Support Beams >>>
    if support_beam_render_batch is not None and ui_props.toggle_support_beams_vis:
        beam_render_shader.uniform_float("color", ui_props.support_beam_color)
        gpu.state.line_width_set(ui_props.support_beam_width)
        gpu.state.depth_mask_set(True)
        support_beam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False)
    # <<< END ADDED >>>

    # <<< ADDED: Draw Hydro Beams >>>
    if hydro_beam_render_batch is not None and ui_props.toggle_hydro_beams_vis:
        beam_render_shader.uniform_float("color", ui_props.hydro_beam_color)
        gpu.state.line_width_set(ui_props.hydro_beam_width)
        gpu.state.depth_mask_set(True)
        hydro_beam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False)
    # <<< END ADDED >>>

    # <<< ADDED: Draw Bounded Beams >>>
    if bounded_beam_render_batch is not None and ui_props.toggle_bounded_beams_vis:
        beam_render_shader.uniform_float("color", ui_props.bounded_beam_color)
        gpu.state.line_width_set(ui_props.bounded_beam_width)
        gpu.state.depth_mask_set(True)
        bounded_beam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False)
    # <<< END ADDED >>>

    # <<< ADDED: Draw LBeams >>>
    if lbeam_render_batch is not None and ui_props.toggle_lbeam_beams_vis:
        beam_render_shader.uniform_float("color", ui_props.lbeam_beam_color)
        gpu.state.line_width_set(ui_props.lbeam_beam_width)
        gpu.state.depth_mask_set(True)
        lbeam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False)
    # <<< END ADDED >>>

    # <<< ADDED: Draw Pressured Beams >>>
    if pressured_beam_render_batch is not None and ui_props.toggle_pressured_beams_vis:
        beam_render_shader.uniform_float("color", ui_props.pressured_beam_color)
        gpu.state.line_width_set(ui_props.pressured_beam_width)
        gpu.state.depth_mask_set(True)
        pressured_beam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False)
    # <<< END ADDED >>>

    # Draw Torsionbars (Outer Segments)
    if torsionbar_render_batch is not None and ui_props.toggle_torsionbars_vis:
        beam_render_shader.uniform_float("color", ui_props.torsionbar_color) # Use UI color for outer
        gpu.state.line_width_set(ui_props.torsionbar_width) # Use UI width
        gpu.state.depth_mask_set(True) # Enable depth writing
        torsionbar_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False) # Disable depth writing

    # Draw Torsionbars (Middle Segments) - Use new UI color
    if torsionbar_red_render_batch is not None and ui_props.toggle_torsionbars_vis:
        beam_render_shader.uniform_float("color", ui_props.torsionbar_mid_color) # Use UI color for middle
        gpu.state.line_width_set(ui_props.torsionbar_width) # Use UI width (or define a separate one)
        gpu.state.depth_mask_set(True) # Enable depth writing
        torsionbar_red_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False) # Disable depth writing

    # Draw Rails
    if rail_render_batch is not None and ui_props.toggle_rails_vis: # Check toggle
        beam_render_shader.uniform_float("color", ui_props.rail_color) # Use UI color
        gpu.state.line_width_set(ui_props.rail_width) # Use UI width
        gpu.state.depth_mask_set(True)
        rail_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False)

    # <<< ADDED: Draw Cross-Part Beams >>> # <<< RENAMED COMMENT
    if cross_part_beam_render_batch is not None and ui_props.toggle_cross_part_beams_vis: # <<< USE RENAMED VARIABLE & PROPERTY
        beam_render_shader.uniform_float("color", ui_props.cross_part_beam_color) # <<< USE RENAMED PROPERTY
        gpu.state.line_width_set(ui_props.cross_part_beam_width) # <<< USE RENAMED PROPERTY
        gpu.state.depth_mask_set(True)
        cross_part_beam_render_batch.draw(beam_render_shader) # <<< USE RENAMED VARIABLE
        gpu.state.depth_mask_set(False)
    # <<< END ADDED >>>

    # Reset states
    gpu.state.line_width_set(1.0)


def menu_func_import(self, context):
    self.layout.operator(import_jbeam.JBEAM_EDITOR_OT_import_jbeam.bl_idname, text="JBeam File (.jbeam)")


def menu_func_export(self, context):
    self.layout.operator(export_jbeam.JBEAM_EDITOR_OT_export_jbeam.bl_idname, text="Selected JBeam Part(s)")


def menu_func_import_vehicle(self, context):
    self.layout.operator(import_vehicle.JBEAM_EDITOR_OT_import_vehicle.bl_idname, text="Part Config File (.pc)")

# https://blenderartists.org/t/make-latest-created-collection-active/1350762/5
def find_layer_collection_recursive(find, col):
    if col.collection == find: # Check current layer collection first
        return col
    for c in col.children:
        found = find_layer_collection_recursive(find, c) # Recurse into children
        if found:
            return found
    return None # Not found in this branch

# Helper function to find the line number of a beam in the AST
def find_beam_line_number(jbeam_filepath: str, target_part_origin: str, target_beam_idx_in_part: int):
    """
    Finds the 1-based line number of a specific beam definition in a JBeam file.

    Args:
        jbeam_filepath: The full path to the JBeam file.
        target_part_origin: The name of the JBeam part the beam belongs to.
        target_beam_idx_in_part: The 1-based index of the beam within its part's 'beams' section.

    Returns:
        The line number (int) or None if not found or an error occurs.
    """
    file_content = text_editor.read_int_file(jbeam_filepath)
    if not file_content:
        print(f"Error: Could not read internal file: {jbeam_filepath}", file=sys.stderr)
        return None

    try:
        ast_data = sjsonast.parse(file_content)
        if not ast_data:
            print(f"Error: Could not parse AST for: {jbeam_filepath}", file=sys.stderr)
            return None

        ast_nodes = ast_data['ast']['nodes']
        sjsonast.calculate_char_positions(ast_nodes) # Calculate positions needed for line counting

        # --- AST Traversal Logic ---
        stack = []
        in_dict = True # Start at root level (usually a dict)
        pos_in_arr = 0
        temp_dict_key = None
        dict_key = None
        current_part_name = None
        in_target_part = False
        in_beams_section = False
        beam_idx_counter = 0 # 0-based counter for beams within the target part

        i = 0
        while i < len(ast_nodes):
            node: sjsonast.ASTNode = ast_nodes[i]
            node_type = node.data_type

            if node_type == 'wsc':
                i += 1
                continue

            # --- Dictionary Logic ---
            if in_dict:
                if node_type == '{': # Going down into a dictionary
                    if dict_key is not None:
                        stack.append((dict_key, in_dict))
                        # Check if we are entering the target part
                        if len(stack) == 1 and dict_key == target_part_origin:
                            in_target_part = True
                            current_part_name = dict_key
                        # Check if we are entering the 'beams' section within the target part
                        elif in_target_part and len(stack) == 2 and dict_key == 'beams':
                            in_beams_section = True
                    # Reset for the new level
                    pos_in_arr = 0
                    temp_dict_key = None
                    dict_key = None
                    in_dict = True # Still in a dict

                elif node_type == '[': # Going down into an array
                    if dict_key is not None:
                        stack.append((dict_key, in_dict))
                        # Check if we are entering the 'beams' section within the target part
                        if in_target_part and len(stack) == 2 and dict_key == 'beams':
                            in_beams_section = True
                    # Reset for the new level
                    pos_in_arr = 0
                    temp_dict_key = None
                    dict_key = None
                    in_dict = False # Now in an array

                elif node_type == '}': # Going up from a dictionary
                    if stack:
                        prev_key, prev_in_dict = stack.pop()
                        # Check if we are leaving the target part
                        if len(stack) == 0 and prev_key == target_part_origin:
                            in_target_part = False
                            current_part_name = None
                        # Check if we are leaving the 'beams' section
                        elif in_target_part and len(stack) == 1 and prev_key == 'beams':
                            in_beams_section = False
                        in_dict = prev_in_dict
                        pos_in_arr = 0 # Reset array pos when going up to dict
                    else:
                        in_dict = None # Should not happen for valid SJSON

                elif node_type == ']': # Going up from an array (Error case within dict logic)
                     print(f"Error: Unexpected ']' while expecting dict elements near pos {node.start_pos}", file=sys.stderr)
                     return None

                else: # Defining key-value pair
                    if temp_dict_key is None:
                        if node_type == '"':
                            temp_dict_key = node.value
                        # Add handling for non-quoted keys if necessary
                    elif node_type == ':':
                        dict_key = temp_dict_key
                    elif dict_key is not None: # Value node
                        # Reset key tracking for the next pair
                        temp_dict_key = None
                        dict_key = None

            # --- Array Logic ---
            else: # In an array object
                if node_type == '[': # Going down into a nested array
                    stack.append((pos_in_arr, in_dict))
                    # If we are in the beams section, this is a beam entry
                    if in_beams_section:
                        beam_idx_counter += 1
                        # Check if this is the target beam
                        if beam_idx_counter == target_beam_idx_in_part:
                            # Found the beam! Calculate line number.
                            start_char_pos = node.start_pos
                            # Add 1 because line numbers are 1-based, add another 1 because the count is *before* the newline
                            line_number = file_content[:start_char_pos].count('\n') + 1
                            return line_number
                    # Reset for the new level
                    pos_in_arr = 0
                    temp_dict_key = None
                    dict_key = None
                    in_dict = False # Still in an array

                elif node_type == '{': # Going down into a dictionary within the array
                    stack.append((pos_in_arr, in_dict))
                    # Reset for the new level
                    pos_in_arr = 0
                    temp_dict_key = None
                    dict_key = None
                    in_dict = True # Now in a dict

                elif node_type == ']': # Going up from an array
                    if stack:
                        prev_key_or_idx, prev_in_dict = stack.pop()
                         # Check if we are leaving the 'beams' section array
                        if in_target_part and len(stack) == 1 and stack[0][0] == 'beams':
                             in_beams_section = False
                        in_dict = prev_in_dict
                        pos_in_arr = prev_key_or_idx + 1 if not prev_in_dict else 0
                    else:
                        in_dict = None # Should not happen

                elif node_type == '}': # Going up from a dictionary (Error case within array logic)
                    print(f"Error: Unexpected '}}' while expecting array elements near pos {node.start_pos}", file=sys.stderr)
                    return None

                else: # Value node within the array
                    pos_in_arr += 1

            i += 1

        # If loop finishes without finding the beam
        print(f"Warning: Beam index {target_beam_idx_in_part} not found in part '{target_part_origin}' in file {jbeam_filepath}", file=sys.stderr)
        return None

    except Exception as e:
        print(f"Error finding beam line number: {e}", file=sys.stderr)
        traceback.print_exc()
        return None

# <<< NEW FUNCTION >>>
# Helper function to find the line number of a node in the AST
def find_node_line_number(jbeam_filepath: str, target_part_origin: str, target_node_id: str):
    """
    Finds the 1-based line number of a specific node definition in a JBeam file.

    Args:
        jbeam_filepath: The full path to the JBeam file.
        target_part_origin: The name of the JBeam part the node belongs to.
        target_node_id: The ID of the node to find.

    Returns:
        The line number (int) or None if not found or an error occurs.
    """
    file_content = text_editor.read_int_file(jbeam_filepath)
    if not file_content:
        print(f"Error: Could not read internal file: {jbeam_filepath}", file=sys.stderr)
        return None

    try:
        ast_data = sjsonast.parse(file_content)
        if not ast_data:
            print(f"Error: Could not parse AST for: {jbeam_filepath}", file=sys.stderr)
            return None

        ast_nodes = ast_data['ast']['nodes']
        sjsonast.calculate_char_positions(ast_nodes) # Calculate positions needed for line counting

        # --- AST Traversal Logic ---
        stack = []
        in_dict = True # Start at root level (usually a dict)
        pos_in_arr = 0
        temp_dict_key = None
        dict_key = None
        current_part_name = None
        in_target_part = False
        in_nodes_section = False
        node_header = []
        node_id_column_index = -1

        i = 0
        while i < len(ast_nodes):
            node: sjsonast.ASTNode = ast_nodes[i]
            node_type = node.data_type

            if node_type == 'wsc':
                i += 1
                continue

            # --- Dictionary Logic ---
            if in_dict:
                if node_type == '{': # Going down into a dictionary
                    if dict_key is not None:
                        stack.append((dict_key, in_dict))
                        # Check if we are entering the target part
                        if len(stack) == 1 and dict_key == target_part_origin:
                            in_target_part = True
                            current_part_name = dict_key
                        # Check if we are entering the 'nodes' section within the target part
                        elif in_target_part and len(stack) == 2 and dict_key == 'nodes':
                            in_nodes_section = True
                            node_header = [] # Reset header when entering nodes section
                            node_id_column_index = -1
                    # Reset for the new level
                    pos_in_arr = 0
                    temp_dict_key = None
                    dict_key = None
                    in_dict = True # Still in a dict

                elif node_type == '[': # Going down into an array
                    if dict_key is not None:
                        stack.append((dict_key, in_dict))
                        # Check if we are entering the 'nodes' section array within the target part
                        if in_target_part and len(stack) == 2 and dict_key == 'nodes':
                            in_nodes_section = True
                            node_header = [] # Reset header when entering nodes section
                            node_id_column_index = -1
                    # Reset for the new level
                    pos_in_arr = 0
                    temp_dict_key = None
                    dict_key = None
                    in_dict = False # Now in an array

                elif node_type == '}': # Going up from a dictionary
                    if stack:
                        prev_key, prev_in_dict = stack.pop()
                        # Check if we are leaving the target part
                        if len(stack) == 0 and prev_key == target_part_origin:
                            in_target_part = False
                            current_part_name = None
                        # Check if we are leaving the 'nodes' section
                        elif in_target_part and len(stack) == 1 and prev_key == 'nodes':
                            in_nodes_section = False
                        in_dict = prev_in_dict
                        pos_in_arr = 0 # Reset array pos when going up to dict
                    else:
                        in_dict = None # Should not happen for valid SJSON

                elif node_type == ']': # Going up from an array (Error case within dict logic)
                     print(f"Error: Unexpected ']' while expecting dict elements near pos {node.start_pos}", file=sys.stderr)
                     return None

                else: # Defining key-value pair
                    if temp_dict_key is None:
                        if node_type == '"':
                            temp_dict_key = node.value
                        # Add handling for non-quoted keys if necessary
                    elif node_type == ':':
                        dict_key = temp_dict_key
                    elif dict_key is not None: # Value node
                        # Reset key tracking for the next pair
                        temp_dict_key = None
                        dict_key = None

            # --- Array Logic ---
            else: # In an array object
                if node_type == '[': # Going down into a nested array (a node row)
                    stack.append((pos_in_arr, in_dict))
                    # Reset for the new level (node row)
                    pos_in_arr = 0
                    temp_dict_key = None
                    dict_key = None
                    in_dict = False # Still in an array

                elif node_type == '{': # Going down into a dictionary within the array (shouldn't happen in standard nodes)
                    stack.append((pos_in_arr, in_dict))
                    # Reset for the new level
                    pos_in_arr = 0
                    temp_dict_key = None
                    dict_key = None
                    in_dict = True # Now in a dict

                elif node_type == ']': # Going up from an array
                    if stack:
                        prev_key_or_idx, prev_in_dict = stack.pop()
                         # Check if we are leaving the 'nodes' section array
                        if in_target_part and len(stack) == 1 and stack[0][0] == 'nodes':
                             in_nodes_section = False
                        in_dict = prev_in_dict
                        pos_in_arr = prev_key_or_idx + 1 if not prev_in_dict else 0
                    else:
                        in_dict = None # Should not happen

                elif node_type == '}': # Going up from a dictionary (Error case within array logic)
                    print(f"Error: Unexpected '}}' while expecting array elements near pos {node.start_pos}", file=sys.stderr)
                    return None

                else: # Value node within the array
                    if in_nodes_section:
                        # Check if we are in the header row (first row of the array)
                        if stack[-1][0] == 0: # stack[-1][0] is the index of the current array (the node row)
                            if node_type == '"':
                                node_header.append(node.value)
                                if node.value == 'id':
                                    node_id_column_index = pos_in_arr # Store the index of the 'id' column
                        # Check if we are in a data row and have found the 'id' column index
                        elif node_id_column_index != -1 and pos_in_arr == node_id_column_index:
                            if node_type == '"' and node.value == target_node_id:
                                # Found the target node! Find the start of its row definition.
                                row_start_node_index = i - (node_id_column_index * 2) # Estimate based on "value", "wsc" pairs
                                while row_start_node_index > 0 and ast_nodes[row_start_node_index].data_type != '[':
                                    row_start_node_index -= 1

                                if ast_nodes[row_start_node_index].data_type == '[':
                                    start_char_pos = ast_nodes[row_start_node_index].start_pos
                                    # Add 1 because line numbers are 1-based, add another 1 because the count is *before* the newline
                                    line_number = file_content[:start_char_pos].count('\n') + 1
                                    return line_number
                                else:
                                    print(f"Error: Could not find start '[' for node row {target_node_id}", file=sys.stderr)
                                    return None

                    pos_in_arr += 1

            i += 1

        # If loop finishes without finding the node
        print(f"Warning: Node ID '{target_node_id}' not found in part '{target_part_origin}' in file {jbeam_filepath}", file=sys.stderr)
        return None

    except Exception as e:
        print(f"Error finding node line number: {e}", file=sys.stderr)
        traceback.print_exc()
        return None
# <<< END NEW FUNCTION >>>

# Batch Renaming Logic & Tooltip Updates ---
def _depsgraph_callback(context: bpy.types.Context, scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    global _do_export
    global _force_do_export
    global veh_render_dirty

    global selected_nodes
    global selected_beams
    global selected_tris_quads
    global _selected_beam_line_info
    global _selected_beam_params_info
    global _selected_node_params_info # <<< ADDED: Use node params global
    global _selected_node_line_info # <<< NEW: Use node line global
    global previous_selected_indices

    reimporting_jbeam = False

    if isinstance(scene.get('jbeam_editor_reimporting_jbeam'), int):
        scene['jbeam_editor_reimporting_jbeam'] -= 1
        if scene['jbeam_editor_reimporting_jbeam'] < 0:
            scene['jbeam_editor_reimporting_jbeam'] = 0
        else:
            reimporting_jbeam = True

    ui_props = scene.ui_properties

    active_obj = context.active_object
    # Early exit if no object or no data
    if active_obj is None or active_obj.data is None:
        _selected_beam_line_info = None
        _selected_beam_params_info = None
        _selected_node_params_info = None
        _selected_node_line_info = None # <<< NEW: Clear node line info
        return
    active_obj_data = active_obj.data

    # Check if it's a JBeam object (don't need MESH_EDITING_ENABLED here for refresh)
    is_jbeam_obj = active_obj_data.get(constants.MESH_JBEAM_PART) is not None
    if not is_jbeam_obj:
        _selected_beam_line_info = None
        _selected_beam_params_info = None
        _selected_node_params_info = None
        _selected_node_line_info = None # <<< NEW: Clear node line info
        # Still call refresh_curr_vdata to clear data if needed
        refresh_curr_vdata()
        return

    # Refresh data based on current active object (will set veh_render_dirty if needed)
    refresh_curr_vdata()

    # --- MOVED CODE ---
    # Show the file in the text editor regardless of mode
    jbeam_filepath = active_obj_data.get(constants.MESH_JBEAM_FILE_PATH)
    if jbeam_filepath:
        text_editor.show_int_file(jbeam_filepath)
    # --- END MOVED CODE ---

    # Only proceed with Edit Mode logic if in Edit Mode and editing is enabled
    mesh_editing_enabled = active_obj_data.get(constants.MESH_EDITING_ENABLED, False)
    if active_obj.mode != 'EDIT' or not mesh_editing_enabled:
        _selected_beam_line_info = None
        _selected_beam_params_info = None
        _selected_node_params_info = None
        _selected_node_line_info = None # <<< NEW: Clear node line info
        return # Exit if not in edit mode or editing disabled

    # --- The rest of the function assumes Edit Mode ---

    active_obj_eval: bpy.types.Object = active_obj.evaluated_get(depsgraph)

    if not reimporting_jbeam:
        for update in depsgraph.updates:
            if update.id.original == active_obj:
                if update.is_updated_geometry or update.is_updated_transform:
                    _do_export = True
                    veh_render_dirty = True # Also set render dirty on geometry/transform changes

    veh_model = active_obj_data.get(constants.MESH_VEHICLE_MODEL)
    if veh_model is not None:
        veh_collection = bpy.data.collections.get(veh_model)
        if veh_collection is not None:
            current_active_layer_col = context.view_layer.active_layer_collection
            if current_active_layer_col is None or current_active_layer_col.collection != veh_collection:
                layer = find_layer_collection_recursive(veh_collection, context.view_layer.layer_collection)
                if layer is not None:
                    context.view_layer.active_layer_collection = layer

    # Get BMesh for Edit Mode
    bm = None
    try:
        bm = bmesh.from_edit_mesh(active_obj_data)
    except Exception as e:
        print(f"Error getting bmesh in depsgraph callback: {e}", file=sys.stderr)
        return

    # Get layers safely
    init_node_id_layer = bm.verts.layers.string.get(constants.VL_INIT_NODE_ID)
    node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
    is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
    beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
    face_idx_layer = bm.faces.layers.int.get(constants.FL_FACE_IDX)
    beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
    face_part_origin_layer = bm.faces.layers.string.get(constants.FL_FACE_PART_ORIGIN)
    node_part_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN)

    # Check if essential layers exist
    if not all([init_node_id_layer, node_id_layer, is_fake_layer, beam_indices_layer, face_idx_layer, beam_part_origin_layer, face_part_origin_layer, node_part_origin_layer]):
        print("Warning: One or more JBeam layers missing from mesh.", file=sys.stderr)
        # No need to free bm here as it's from edit mesh
        return

    # Ensure lookup tables
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # Store current counts before processing additions
    current_vert_count = active_obj_data.get(constants.MESH_VERTEX_COUNT, 0)
    current_edge_count = active_obj_data.get(constants.MESH_EDGE_COUNT, 0)
    current_face_count = active_obj_data.get(constants.MESH_FACE_COUNT, 0)
    new_vert_count = len(bm.verts)
    new_edge_count = len(bm.edges)
    new_face_count = len(bm.faces)

    # --- Batch Renaming and Selection Tracking ---
    current_selected_indices = set()
    newly_selected_vert_index = -1
    num_currently_selected = 0

    for v in bm.verts:
        if v[is_fake_layer]:
            continue
        if v.select:
            current_selected_indices.add(v.index)
            num_currently_selected += 1
            if v.index not in previous_selected_indices:
                if newly_selected_vert_index == -1:
                    newly_selected_vert_index = v.index
                else:
                    newly_selected_vert_index = -2

    if batch_node_renaming_enabled and newly_selected_vert_index >= 0:
        try:
            vert_to_rename = bm.verts[newly_selected_vert_index]
            new_node_id: str = ui_props.batch_node_renaming_naming_scheme
            if '#' in new_node_id:
                new_node_id = new_node_id.replace('#', f'{ui_props.batch_node_renaming_node_idx}')
                vert_to_rename[node_id_layer] = bytes(new_node_id, 'utf-8')
                ui_props.batch_node_renaming_node_idx += 1
                _force_do_export = True
            else:
                 print(f"Warning: Batch rename scheme '{ui_props.batch_node_renaming_naming_scheme}' does not contain '#'. No rename performed.")
        except IndexError:
            print(f"Error: Could not find vertex with index {newly_selected_vert_index} for renaming.")
        except Exception as rename_err:
             print(f"Error during batch renaming: {rename_err}")

    # --- Update selected_nodes list ---
    selected_nodes.clear()
    for idx in current_selected_indices:
        try:
            v = bm.verts[idx]
            selected_nodes.append((idx, v[init_node_id_layer].decode('utf-8')))
        except IndexError:
            pass

    previous_selected_indices = current_selected_indices

    # --- Process newly added vertices ---
    for i, v in enumerate(bm.verts):
        if i >= current_vert_count:
            new_node_id = str(uuid.uuid4())
            new_node_id_bytes = bytes(new_node_id, 'utf-8')
            v[init_node_id_layer] = new_node_id_bytes
            v[node_id_layer] = new_node_id_bytes
            # Assign part origin based on the active object's part
            v[node_part_origin_layer] = bytes(active_obj_data[constants.MESH_JBEAM_PART], 'utf-8')

    # --- Process Edges ---
    selected_beams.clear()
    for i, e in enumerate(bm.edges):
        beam_indices = e[beam_indices_layer].decode('utf-8')
        if i >= current_edge_count:
            if beam_indices == '':
                e[beam_indices_layer] = bytes('-1', 'utf-8')
                # Assign part origin based on the active object's part
                e[beam_part_origin_layer] = bytes(active_obj_data[constants.MESH_JBEAM_PART], 'utf-8')
        if beam_indices != '' and e.select:
            selected_beams.append((e, beam_indices))

    # --- Process Faces ---
    selected_tris_quads.clear()
    for i, f in enumerate(bm.faces):
        face_idx = f[face_idx_layer]
        if i >= current_face_count:
            if face_idx == 0:
                f[face_idx_layer] = -1
                # Assign part origin based on the active object's part
                f[face_part_origin_layer] = bytes(active_obj_data[constants.MESH_JBEAM_PART], 'utf-8')
        if face_idx != 0 and f.select:
            selected_tris_quads.append((f, face_idx))

    # Update counts
    if new_vert_count != current_vert_count:
        active_obj_data[constants.MESH_VERTEX_COUNT] = new_vert_count
    if new_edge_count != current_edge_count:
        active_obj_data[constants.MESH_EDGE_COUNT] = new_edge_count
    if new_face_count != current_face_count:
        active_obj_data[constants.MESH_FACE_COUNT] = new_face_count

    # Update UI input field
    # Removed rename_enabled logic >>>
    if len(selected_nodes) == 1:
        vert_index, init_node_id = selected_nodes[0]
        try:
            v = bm.verts[vert_index]
            current_node_id = v[node_id_layer].decode('utf-8')
            # Update the UI field if it doesn't match the actual current node ID
            if ui_props.input_node_id != current_node_id:
                ui_props.input_node_id = current_node_id
        except IndexError:
             # Clear the UI field if the vertex index is somehow invalid
             if ui_props.input_node_id != "":
                 ui_props.input_node_id = ""

    # --- Tooltip Logic ---
    _selected_beam_line_info = None
    _selected_beam_params_info = None
    _selected_node_params_info = None
    _selected_node_line_info = None # <<< NEW: Clear node line info

    # --- Node Tooltip Logic ---
    if len(selected_nodes) == 1:
        vert_index, node_id = selected_nodes[0]
        node_world_pos = active_obj.matrix_world @ bm.verts[vert_index].co # Get world pos once

        # --- Node Params Tooltip ---
        if curr_vdata and 'nodes' in curr_vdata and node_id in curr_vdata['nodes']:
            node_data = curr_vdata['nodes'][node_id]
            params_list = []

            # Iterate through all keys shown in the Properties panel
            for k in sorted(node_data.keys(), key=lambda x: str(x)):
                # Filter out keys not shown in the Properties panel
                if k == Metadata or k == 'pos' or k == 'posNoOffset': continue
                val = node_data[k]
                params_list.append((k, repr(val))) # Add all relevant keys

            if params_list:
                _selected_node_params_info = {'params_list': params_list, 'pos': node_world_pos}
            else:
                # Provide a default message if no parameters are found (unlikely but safe)
                _selected_node_params_info = {'params_list': [("(No properties)", "")], 'pos': node_world_pos}

        # --- NEW: Node Line Tooltip ---
        try:
            target_part_origin = bm.verts[vert_index][node_part_origin_layer].decode('utf-8')
            if target_part_origin and jbeam_filepath:
                line_num = find_node_line_number(jbeam_filepath, target_part_origin, node_id)
                if line_num is not None:
                    _selected_node_line_info = {'line': line_num, 'pos': node_world_pos}
        except Exception as find_err:
            print(f"Error processing node line tooltip: {find_err}", file=sys.stderr)
            traceback.print_exc()
        # --- END NEW ---

    # --- Beam Tooltip Logic ---
    elif len(selected_beams) == 1:
        e, beam_indices_str = selected_beams[0]
        beam_indices = beam_indices_str.split(',')
        if beam_indices:
            try:
                target_beam_idx_in_part = int(beam_indices[0])
                target_part_origin = e[beam_part_origin_layer].decode('utf-8')
                midpoint = active_obj.matrix_world @ ((e.verts[0].co + e.verts[1].co) / 2)

                # Get Line Number (Keep this part)
                if target_beam_idx_in_part > 0 and target_part_origin and jbeam_filepath:
                    line_num = find_beam_line_number(jbeam_filepath, target_part_origin, target_beam_idx_in_part)
                    if line_num is not None:
                        _selected_beam_line_info = {'line': line_num, 'midpoint': midpoint}

                # Get Parameters (Simplified)
                if curr_vdata and 'beams' in curr_vdata and target_beam_idx_in_part > 0:
                    global_beam_idx = -1
                    current_part_beam_count = 0
                    for i, b in enumerate(curr_vdata['beams']):
                        if b.get('partOrigin') == target_part_origin:
                            current_part_beam_count += 1
                            if current_part_beam_count == target_beam_idx_in_part:
                                global_beam_idx = i
                                break

                    if global_beam_idx != -1 and global_beam_idx < len(curr_vdata['beams']):
                        beam_data = curr_vdata['beams'][global_beam_idx]
                        params_list = []

                        # Iterate through all keys shown in the Properties panel
                        for k in sorted(beam_data.keys(), key=lambda x: str(x)):
                            # Filter out keys not shown in the Properties panel
                            if k in ('id1:', 'id2:', 'partOrigin') or k == Metadata: continue
                            val = beam_data[k]
                            params_list.append((k, repr(val))) # Add all relevant keys

                        if params_list:
                            _selected_beam_params_info = {'params_list': params_list, 'midpoint': midpoint}
                        else:
                            # Provide a default message if no parameters are found
                            _selected_beam_params_info = {'params_list': [("(No properties)", "")], 'midpoint': midpoint}
                    else:
                        print(f"  Warning: Global beam index {global_beam_idx} not found or invalid for part '{target_part_origin}'.")

            except ValueError:
                print(f"Warning: Could not parse beam index: {beam_indices_str}", file=sys.stderr)
            except Exception as find_err:
                 print(f"Error processing beam tooltips: {find_err}", file=sys.stderr)
                 traceback.print_exc()

    # No need to free bm as it's from edit mesh


@persistent
def depsgraph_callback(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    context = bpy.context
    try:
        _depsgraph_callback(context, scene, depsgraph)
    except Exception as e:
        print(f"Error in depsgraph callback: {e}", file=sys.stderr)
        traceback.print_exc()


@persistent
def check_files_for_changes():
    context = bpy.context
    try:
        changed = text_editor.check_open_int_file_for_changes(context)
        if changed:
            refresh_curr_vdata(True)
    except Exception as e:
        print(f"Error checking files for changes: {e}", file=sys.stderr)
    return check_file_interval

op_no_export = {
    'OBJECT_OT_editmode_toggle',
    JBEAM_EDITOR_OT_batch_node_renaming.bl_idname,
    'VIEW3D_OT_rotate', # Don't export during view rotation
    'VIEW3D_OT_move',   # Don't export during view panning
    'VIEW3D_OT_zoom',   # Don't export during view zoom
    'VIEW3D_OT_dolly',  # Don't export during view dolly
    'SCREEN_OT_screen_full_area', # Don't export when toggling fullscreen
    'SCREEN_OT_back_to_previous', # Don't export when going back from fullscreen
    'OBJECT_OT_select', # Avoid export on simple selection changes if possible
    'MESH_OT_select_all',
    'MESH_OT_select_linked',
    'MESH_OT_select_more',
    'MESH_OT_select_less',
    'MESH_OT_select_random',
    'MESH_OT_select_mirror',
    'MESH_OT_select_similar',
    'MESH_OT_select_mode',
    # <<< ADDED: Don't export when using the find node operator >>>
    'jbeam_editor.find_node',
    # <<< END ADDED >>>
}
_last_op = None

@persistent
def poll_active_operators():
    global _last_op
    global _do_export
    global _force_do_export

    context = bpy.context
    op = context.active_operator

    try: # Add try-except
        active_obj = context.active_object
        # Check if active object is valid JBeam AND editing enabled before exporting
        if active_obj is not None and active_obj.data is not None:
            active_obj_data = active_obj.data
            # Use .get() for safety and check MESH_EDITING_ENABLED
            if active_obj_data.get(constants.MESH_JBEAM_PART) is not None and active_obj_data.get(constants.MESH_EDITING_ENABLED, False):
                # Trigger export JBeam/Vehicle on current operator finishing
                # Check if the operator is not None, different from the last one, and not in the ignore list
                should_export = _force_do_export or (_do_export and op is not None and op != _last_op and op.bl_idname not in op_no_export)

                if should_export:
                    veh_model = active_obj_data.get(constants.MESH_VEHICLE_MODEL)
                    if veh_model is not None:
                        # Export Vehicle
                        export_vehicle.auto_export(active_obj, veh_model)
                    else:
                        # Export Single Part
                        export_jbeam.auto_export(active_obj)

                    refresh_curr_vdata(True) # Refresh data after export

                    _do_export = False
                    _force_do_export = False
        # Reset export flags if object is not valid JBeam or editing disabled
        else:
            _do_export = False
            _force_do_export = False

    except Exception as e:
        print(f"Error polling active operators: {e}", file=sys.stderr) # Print to stderr
        _do_export = False # Reset flags on error to prevent loops
        _force_do_export = False
    finally:
         _last_op = op # Update last operator even if export didn't happen

    return poll_active_ops_interval


@persistent
def on_post_register():
    # this will happen 0.1 seconds after addon registration completes.
    global draw_handle
    global draw_handle2
    try:
        # Ensure context is valid before adding handlers
        if bpy.context.window_manager and bpy.context.window:
            draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_px, (bpy.context,), 'WINDOW', 'POST_PIXEL')

            if not constants.UNIT_TESTING:
                draw_handle2 = bpy.types.SpaceView3D.draw_handler_add(draw_callback_view, (bpy.context,), 'WINDOW', 'POST_VIEW')
        else:
             print("Warning: Could not add draw handlers, context invalid during registration.", file=sys.stderr) # Print to stderr
    except Exception as e:
        print(f"Error adding draw handlers: {e}", file=sys.stderr) # Print to stderr


classes = (
    UIProperties,
    JBEAM_EDITOR_OT_force_jbeam_sync,
    JBEAM_EDITOR_OT_undo,
    JBEAM_EDITOR_OT_redo,
    #JBEAM_EDITOR_OT_convert_to_jbeam_mesh,
    JBEAM_EDITOR_OT_add_beam_tri_quad,
    JBEAM_EDITOR_OT_flip_jbeam_faces,
    JBEAM_EDITOR_OT_batch_node_renaming,
    JBEAM_EDITOR_OT_find_node, # Operator is already here
    JBEAM_EDITOR_PT_transform_panel_ext,
    JBEAM_EDITOR_PT_jbeam_panel,
    # <<< ADDED: Register Find Node Panel >>>
    JBEAM_EDITOR_PT_find_node,
    # <<< END ADDED >>>
    JBEAM_EDITOR_PT_jbeam_properties_panel,
    JBEAM_EDITOR_PT_batch_node_renaming,
    JBEAM_EDITOR_PT_jbeam_settings,
    import_jbeam.JBEAM_EDITOR_OT_import_jbeam,
    import_jbeam.JBEAM_EDITOR_OT_choose_jbeam,
    export_jbeam.JBEAM_EDITOR_OT_export_jbeam,
    import_vehicle.JBEAM_EDITOR_OT_import_vehicle,
    #export_vehicle.JBEAM_EDITOR_OT_export_vehicle,
)

custom_keymaps = []


def init_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if not kc: # Keyconfig path changed in 4.x? Check if addon keyconfig exists
        print("Warning: Addon keyconfig not found, cannot register keymaps.", file=sys.stderr) # Print to stderr
        return None, []
    km = kc.keymaps.new(name="Window", space_type='EMPTY') # Use EMPTY or WINDOW
    kmi = [
        km.keymap_items.new("jbeam_editor.undo", 'LEFT_BRACKET', 'PRESS', ctrl=True),
        km.keymap_items.new("jbeam_editor.redo", 'RIGHT_BRACKET', 'PRESS', ctrl=True),
    ]
    return km, kmi


def register():
    global classes, custom_keymaps

    for c in classes:
        bpy.utils.register_class(c)

    if not bpy.app.background:
        km, kmi = init_keymaps()
        if km: # Check if keymap was created
            for k_item in kmi:
                custom_keymaps.append((km, k_item)) # Store keymap item itself

    bpy.types.Scene.ui_properties = bpy.props.PointerProperty(type=UIProperties)
    # Add scene property to trigger redraws from property updates
    bpy.types.Scene.jbeam_editor_veh_render_dirty = bpy.props.BoolProperty(default=False)

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_vehicle)

    # Clear existing handlers before appending (safety measure)
    while bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.pop()
    bpy.app.handlers.depsgraph_update_post.append(depsgraph_callback)

    # Use try-except for timer registration
    try:
        if not bpy.app.timers.is_registered(on_post_register):
             bpy.app.timers.register(on_post_register, first_interval=0.1, persistent=True)
        if not bpy.app.timers.is_registered(check_files_for_changes):
            bpy.app.timers.register(check_files_for_changes, first_interval=check_file_interval, persistent=True)
        if not bpy.app.timers.is_registered(poll_active_operators):
            bpy.app.timers.register(poll_active_operators, first_interval=poll_active_ops_interval, persistent=True)
    except Exception as e:
        print(f"Error registering timers: {e}", file=sys.stderr) # Print to stderr


def unregister():
    global classes, custom_keymaps, draw_handle, draw_handle2

    # Unregister timers first
    if bpy.app.timers.is_registered(on_post_register):
        bpy.app.timers.unregister(on_post_register)
    if bpy.app.timers.is_registered(check_files_for_changes):
        bpy.app.timers.unregister(check_files_for_changes)
    if bpy.app.timers.is_registered(poll_active_operators):
        bpy.app.timers.unregister(poll_active_operators)

    # Remove draw handlers
    if draw_handle:
        bpy.types.SpaceView3D.draw_handler_remove(draw_handle, 'WINDOW')
        draw_handle = None
    if not constants.UNIT_TESTING and draw_handle2:
        bpy.types.SpaceView3D.draw_handler_remove(draw_handle2, 'WINDOW')
        draw_handle2 = None

    # Remove depsgraph handler
    if depsgraph_callback in bpy.app.handlers.depsgraph_update_post:
         bpy.app.handlers.depsgraph_update_post.remove(depsgraph_callback)

    # Remove menu items
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
        bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_vehicle)
    except Exception as e:
        print(f"Error removing menu functions: {e}", file=sys.stderr) # Print to stderr

    # Unregister classes
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
             print(f"Could not unregister class {c.__name__}", file=sys.stderr) # Might already be unregistered

    # Unregister keymaps
    for km, kmi in custom_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception as e:
            print(f"Error removing keymap item: {e}", file=sys.stderr) # Print to stderr
    custom_keymaps.clear()

    # Delete custom property group
    try:
        if hasattr(bpy.types.Scene, 'ui_properties'):
            del bpy.types.Scene.ui_properties
        if hasattr(bpy.types.Scene, 'jbeam_editor_veh_render_dirty'):
            del bpy.types.Scene.jbeam_editor_veh_render_dirty
    except Exception as e:
        print(f"Error deleting UI properties: {e}", file=sys.stderr) # Print to stderr


# This allows you to run the script directly from Blender's Text editor
# to test the add-on without having to install it.
if __name__ == "__main__":
    # Clean up previous registration if run multiple times
    try:
        unregister()
    except Exception as e:
        pass # Ignore errors during cleanup before registration
    register()

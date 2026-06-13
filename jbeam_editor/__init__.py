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
    "version": (0, 2, 53),
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
from .utils import Metadata # Ensure Metadata is imported

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

veh_render_dirty = False
rename_enabled = False
batch_node_renaming_enabled = False
# rail_render_batch = None # Defined below with other batches
# rail_coords = [] # Defined below with other batches
previous_selected_indices = set()


# Refresh property input field UI
def on_input_node_id_field_updated(self, context: bpy.types.Context):
    global _force_do_export
    global selected_nodes
    global rename_enabled

    scene = context.scene
    ui_props = scene.ui_properties

    obj = context.active_object
    # Check if object is valid JBeam AND editing enabled (important for this specific action)
    if obj is None or obj.data.get(constants.MESH_JBEAM_PART) is None or not obj.data.get(constants.MESH_EDITING_ENABLED, False) or len(selected_nodes) == 0:
        return

    if rename_enabled:
        # Get the index of the selected vertex
        selected_vert_index = selected_nodes[0][0]
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        # Ensure lookup table is available for index access
        bm.verts.ensure_lookup_table()

        # Set the selected mesh's selected vertex node_id attribute to the UI node_id input field value
        node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID]
        # Access the vertex from the current bmesh using the index
        bm.verts[selected_vert_index][node_id_layer] = bytes(ui_props.input_node_id, 'utf-8')

        bm.free()
        _force_do_export = True

    rename_enabled = True

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
        default=(1.0, 1.0, 1.0, 1.0), # Default White
        min=0.0, max=1.0,
        size=4
    )
    toggle_beam_params_tooltip: bpy.props.BoolProperty(
        name="Show Beam Params Tooltip",
        description="Shows the parameters for a selected beam",
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
    # <<< ADDED: Color property for beam parameter values >>>
    beam_params_value_tooltip_color: bpy.props.FloatVectorProperty(
        name="Params Value Color",
        description="Color of the beam parameter value tooltip text",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0), # Default Green
        min=0.0, max=1.0,
        size=4
    )
    # <<< END ADDED >>>

    # Beam Parameter Toggles
    tooltip_show_beamSpring: bpy.props.BoolProperty(name="beamSpring", default=True)
    tooltip_show_beamDamp: bpy.props.BoolProperty(name="beamDamp", default=True)
    tooltip_show_beamDeform: bpy.props.BoolProperty(name="beamDeform", default=True)
    tooltip_show_beamStrength: bpy.props.BoolProperty(name="beamStrength", default=True)
    tooltip_show_beamPrecompression: bpy.props.BoolProperty(name="beamPrecompression", default=True)
    tooltip_show_beamType: bpy.props.BoolProperty(name="beamType", default=True)
    tooltip_show_beamLongBound: bpy.props.BoolProperty(name="beamLongBound", default=True)
    tooltip_show_beamShortBound: bpy.props.BoolProperty(name="beamShortBound", default=True)
    tooltip_show_beamLimitSpring: bpy.props.BoolProperty(name="beamLimitSpring", default=True)
    tooltip_show_beamLimitDamp: bpy.props.BoolProperty(name="beamLimitDamp", default=True)
    tooltip_show_beamPrecompressionRange: bpy.props.BoolProperty(name="beamPrecompressionRange", default=True)
    tooltip_show_beamDampRebound: bpy.props.BoolProperty(name="beamDampRebound", default=True)
    tooltip_show_beamDampFast: bpy.props.BoolProperty(name="beamDampFast", default=True)
    tooltip_show_beamDampReboundFast: bpy.props.BoolProperty(name="beamDampReboundFast", default=True)
    tooltip_show_beamDampVelocitySplit: bpy.props.BoolProperty(name="beamDampVelocitySplit", default=True)
    tooltip_show_beamDampCutoffHz: bpy.props.BoolProperty(name="beamDampCutoffHz", default=True)
    tooltip_show_beamDeformReboundThreshold: bpy.props.BoolProperty(name="beamDeformReboundThreshold", default=True)
    tooltip_show_breakGroup: bpy.props.BoolProperty(name="breakGroup", default=True)
    tooltip_show_breakGroupType: bpy.props.BoolProperty(name="breakGroupType", default=True)
    tooltip_show_disableMeshBreaking: bpy.props.BoolProperty(name="disableMeshBreaking", default=True)
    tooltip_show_disableTriangleBreaking: bpy.props.BoolProperty(name="disableTriangleBreaking", default=True)
    tooltip_show_soundTriggerBeam: bpy.props.BoolProperty(name="soundTriggerBeam", default=True)
    tooltip_show_optional: bpy.props.BoolProperty(name="optional", default=True)
    tooltip_show_group: bpy.props.BoolProperty(name="group", default=True)
    # --- ADDED: New Beam Parameter Toggles ---
    tooltip_show_dampCutoffHz: bpy.props.BoolProperty(name="dampCutoffHz", default=True)
    tooltip_show_deformGroup: bpy.props.BoolProperty(name="deformGroup", default=True)
    tooltip_show_deformLimitExpansion: bpy.props.BoolProperty(name="deformLimitExpansion", default=True)
    tooltip_show_deformLimitStress: bpy.props.BoolProperty(name="deformLimitStress", default=True)
    tooltip_show_deformationTriggerRatio: bpy.props.BoolProperty(name="deformationTriggerRatio", default=True)
    tooltip_show_name_beam: bpy.props.BoolProperty(name="name (beam)", default=True) # Renamed to avoid conflict
    tooltip_show_partName_beam: bpy.props.BoolProperty(name="partName (beam)", default=True) # Renamed to avoid conflict
    tooltip_show_slotType_beam: bpy.props.BoolProperty(name="slotType (beam)", default=True) # Renamed to avoid conflict
    # --- END ADDED ---

    # --- Node Tooltips ---
    show_node_tooltips_panel: bpy.props.BoolProperty(
        name="Node Tooltips",
        description="Expand to see node tooltip options",
        default=False,
    )
    toggle_node_params_tooltip: bpy.props.BoolProperty(
        name="Show Node Params Tooltip",
        description="Shows the parameters for a selected node",
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
    # <<< ADDED: Color property for node parameter values >>>
    node_params_value_tooltip_color: bpy.props.FloatVectorProperty(
        name="Params Value Color",
        description="Color of the node parameter value tooltip text",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0), # Default Green
        min=0.0, max=1.0,
        size=4
    )
    # <<< END ADDED >>>

    # Node Parameter Toggles
    tooltip_show_nodeWeight: bpy.props.BoolProperty(name="nodeWeight", default=True)
    tooltip_show_nodeMaterial: bpy.props.BoolProperty(name="nodeMaterial", default=True)
    tooltip_show_collision: bpy.props.BoolProperty(name="collision", default=True)
    tooltip_show_selfCollision: bpy.props.BoolProperty(name="selfCollision", default=True)
    tooltip_show_frictionCoef: bpy.props.BoolProperty(name="frictionCoef", default=True)
    tooltip_show_slidingFrictionCoef: bpy.props.BoolProperty(name="slidingFrictionCoef", default=True)
    tooltip_show_group_node: bpy.props.BoolProperty(name="group (node)", default=True)
    tooltip_show_couplerGroup: bpy.props.BoolProperty(name="couplerGroup", default=True)
    tooltip_show_loadMembers: bpy.props.BoolProperty(name="loadMembers", default=True)
    tooltip_show_isCoupler: bpy.props.BoolProperty(name="isCoupler", default=True)
    tooltip_show_couplerStrength: bpy.props.BoolProperty(name="couplerStrength", default=True)
    tooltip_show_couplerPullStrength: bpy.props.BoolProperty(name="couplerPullStrength", default=True)
    tooltip_show_couplerPosition: bpy.props.BoolProperty(name="couplerPosition", default=True)
    tooltip_show_couplerDirection: bpy.props.BoolProperty(name="couplerDirection", default=True)
    tooltip_show_couplerRadius: bpy.props.BoolProperty(name="couplerRadius", default=True)
    tooltip_show_couplerStartRadius: bpy.props.BoolProperty(name="couplerStartRadius", default=True)
    tooltip_show_couplerBreakTriggerBeam: bpy.props.BoolProperty(name="couplerBreakTriggerBeam", default=True)
    tooltip_show_couplerSoundVolumeFactor: bpy.props.BoolProperty(name="couplerSoundVolumeFactor", default=True)
    tooltip_show_couplerSoundNode: bpy.props.BoolProperty(name="couplerSoundNode", default=True)
    tooltip_show_couplerLockedSoundEvent: bpy.props.BoolProperty(name="couplerLockedSoundEvent", default=True)
    tooltip_show_couplerUnlockedSoundEvent: bpy.props.BoolProperty(name="couplerUnlockedSoundEvent", default=True)
    tooltip_show_couplerLockable: bpy.props.BoolProperty(name="couplerLockable", default=True)
    tooltip_show_couplerAllowUnlocking: bpy.props.BoolProperty(name="couplerAllowUnlocking", default=True)
    tooltip_show_couplerAutoLockRadius: bpy.props.BoolProperty(name="couplerAutoLockRadius", default=True)
    tooltip_show_couplerAngleLimit: bpy.props.BoolProperty(name="couplerAngleLimit", default=True)
    tooltip_show_couplerAngleLimitSpring: bpy.props.BoolProperty(name="couplerAngleLimitSpring", default=True)
    tooltip_show_couplerAngleLimitDamp: bpy.props.BoolProperty(name="couplerAngleLimitDamp", default=True)
    tooltip_show_couplerAngleLimitDeform: bpy.props.BoolProperty(name="couplerAngleLimitDeform", default=True)
    tooltip_show_couplerAngleLimitStrength: bpy.props.BoolProperty(name="couplerAngleLimitStrength", default=True)
    tooltip_show_couplerBreakGroup: bpy.props.BoolProperty(name="couplerBreakGroup", default=True)
    tooltip_show_engineGroup: bpy.props.BoolProperty(name="engineGroup", default=True)
    tooltip_show_enablePowertrain: bpy.props.BoolProperty(name="enablePowertrain", default=True)
    tooltip_show_name: bpy.props.BoolProperty(name="name", default=True)
    tooltip_show_inputName: bpy.props.BoolProperty(name="inputName", default=True)
    tooltip_show_pressurePSI: bpy.props.BoolProperty(name="pressurePSI", default=True)
    tooltip_show_soundFile: bpy.props.BoolProperty(name="soundFile", default=True)
    tooltip_show_volumePerPSI: bpy.props.BoolProperty(name="volumePerPSI", default=True)
    tooltip_show_attackTime: bpy.props.BoolProperty(name="attackTime", default=True)
    tooltip_show_decayTime: bpy.props.BoolProperty(name="decayTime", default=True)
    tooltip_show_maxVolumedB: bpy.props.BoolProperty(name="maxVolumedB", default=True)
    tooltip_show_radius: bpy.props.BoolProperty(name="radius", default=True)
    tooltip_show_fixed: bpy.props.BoolProperty(name="fixed", default=True)
    tooltip_show_category: bpy.props.BoolProperty(name="category", default=True)
    tooltip_show_pressureDamage: bpy.props.BoolProperty(name="pressureDamage", default=True)
    tooltip_show_hubGroup: bpy.props.BoolProperty(name="hubGroup", default=True)
    tooltip_show_pressureRatePSI: bpy.props.BoolProperty(name="pressureRatePSI", default=True)
    tooltip_show_maxPressurePSI: bpy.props.BoolProperty(name="maxPressurePSI", default=True)
    tooltip_show_boostPSI: bpy.props.BoolProperty(name="boostPSI", default=True)
    tooltip_show_supercharger: bpy.props.BoolProperty(name="supercharger", default=True)
    tooltip_show_turbocharger: bpy.props.BoolProperty(name="turbocharger", default=True)
    tooltip_show_nitroSoundFile: bpy.props.BoolProperty(name="nitroSoundFile", default=True)
    tooltip_show_nitroVolumePerPSI: bpy.props.BoolProperty(name="nitroVolumePerPSI", default=True)
    tooltip_show_nitroAttackTime: bpy.props.BoolProperty(name="nitroAttackTime", default=True)
    tooltip_show_nitroDecayTime: bpy.props.BoolProperty(name="nitroDecayTime", default=True)
    tooltip_show_nitroMaxVolumedB: bpy.props.BoolProperty(name="nitroMaxVolumedB", default=True)
    tooltip_show_cameraDistance: bpy.props.BoolProperty(name="cameraDistance", default=True)
    tooltip_show_cameraRotation: bpy.props.BoolProperty(name="cameraRotation", default=True)
    tooltip_show_cameraLookAt: bpy.props.BoolProperty(name="cameraLookAt", default=True)
    tooltip_show_cameraFOV: bpy.props.BoolProperty(name="cameraFOV", default=True)
    tooltip_show_cameraOffset: bpy.props.BoolProperty(name="cameraOffset", default=True)
    tooltip_show_cameraMinDistance: bpy.props.BoolProperty(name="cameraMinDistance", default=True)
    tooltip_show_cameraMaxDistance: bpy.props.BoolProperty(name="cameraMaxDistance", default=True)
    tooltip_show_cameraFocusPoint: bpy.props.BoolProperty(name="cameraFocusPoint", default=True)
    tooltip_show_cameraMode: bpy.props.BoolProperty(name="cameraMode", default=True)
    tooltip_show_cameraShake: bpy.props.BoolProperty(name="cameraShake", default=True)
    tooltip_show_cameraShakeMultiplier: bpy.props.BoolProperty(name="cameraShakeMultiplier", default=True)
    tooltip_show_cameraShakeMaxSpeed: bpy.props.BoolProperty(name="cameraShakeMaxSpeed", default=True)
    tooltip_show_cameraShakeMaxAngle: bpy.props.BoolProperty(name="cameraShakeMaxAngle", default=True)
    tooltip_show_cameraShakeMaxOffset: bpy.props.BoolProperty(name="cameraShakeMaxOffset", default=True)
    tooltip_show_cameraShakeFrequency: bpy.props.BoolProperty(name="cameraShakeFrequency", default=True)
    tooltip_show_cameraShakeDamping: bpy.props.BoolProperty(name="cameraShakeDamping", default=True)
    tooltip_show_cameraShakeRandomness: bpy.props.BoolProperty(name="cameraShakeRandomness", default=True)
    tooltip_show_cameraShakeRandomSeed: bpy.props.BoolProperty(name="cameraShakeRandomSeed", default=True)
    tooltip_show_cameraShakeRandomFrequency: bpy.props.BoolProperty(name="cameraShakeRandomFrequency", default=True)
    tooltip_show_cameraShakeRandomDamping: bpy.props.BoolProperty(name="cameraShakeRandomDamping", default=True)
    tooltip_show_cameraShakeRandomOffset: bpy.props.BoolProperty(name="cameraShakeRandomOffset", default=True)
    tooltip_show_cameraShakeRandomAngle: bpy.props.BoolProperty(name="cameraShakeRandomAngle", default=True)
    tooltip_show_cameraShakeRandomMaxSpeed: bpy.props.BoolProperty(name="cameraShakeRandomMaxSpeed", default=True)
    tooltip_show_cameraShakeRandomMaxAngle: bpy.props.BoolProperty(name="cameraShakeRandomMaxAngle", default=True)
    tooltip_show_cameraShakeRandomMaxOffset: bpy.props.BoolProperty(name="cameraShakeRandomMaxOffset", default=True)
    tooltip_show_cameraShakeRandomFrequencyMultiplier: bpy.props.BoolProperty(name="cameraShakeRandomFrequencyMultiplier", default=True)
    tooltip_show_cameraShakeRandomDampingMultiplier: bpy.props.BoolProperty(name="cameraShakeRandomDampingMultiplier", default=True)
    tooltip_show_cameraShakeRandomOffsetMultiplier: bpy.props.BoolProperty(name="cameraShakeRandomOffsetMultiplier", default=True)
    tooltip_show_cameraShakeRandomAngleMultiplier: bpy.props.BoolProperty(name="cameraShakeRandomAngleMultiplier", default=True)
    tooltip_show_cameraShakeRandomMaxSpeedMultiplier: bpy.props.BoolProperty(name="cameraShakeRandomMaxSpeedMultiplier", default=True)
    tooltip_show_cameraShakeRandomMaxAngleMultiplier: bpy.props.BoolProperty(name="cameraShakeRandomMaxAngleMultiplier", default=True)
    tooltip_show_cameraShakeRandomMaxOffsetMultiplier: bpy.props.BoolProperty(name="cameraShakeRandomMaxOffsetMultiplier", default=True)
    tooltip_show_burnRate: bpy.props.BoolProperty(name="burnRate", default=True)
    tooltip_show_chemEnergy: bpy.props.BoolProperty(name="chemEnergy", default=True)
    tooltip_show_flashPoint: bpy.props.BoolProperty(name="flashPoint", default=True)
    tooltip_show_partName: bpy.props.BoolProperty(name="partName", default=True)
    tooltip_show_selfIgnitionCoef: bpy.props.BoolProperty(name="selfIgnitionCoef", default=True)
    tooltip_show_slotType: bpy.props.BoolProperty(name="slotType", default=True)
    tooltip_show_smokePoint: bpy.props.BoolProperty(name="smokePoint", default=True)
    tooltip_show_specHeat: bpy.props.BoolProperty(name="specHeat", default=True)
    tooltip_show_totalOffset: bpy.props.BoolProperty(name="totalOffset", default=True)

    affect_node_references: bpy.props.BoolProperty(
        name="Affect Node References",
        description="Toggles updating JBeam entries who references nodes. E.g. deleting a beam who references a node being deleted",
        default=False
    )

    # Beam visualization properties
    toggle_beams_vis: bpy.props.BoolProperty(
        name="Show Beams",
        description="Toggles the visibility of beams (Green Lines)",
        default=True
    )
    beam_color: bpy.props.FloatVectorProperty(
        name="Beam Color",
        description="Color of the beam visualization lines",
        subtype='COLOR',
        default=(0.0, 1.0, 0.0, 1.0), # Green
        min=0.0, max=1.0,
        size=4
    )
    beam_width: bpy.props.FloatProperty(
        name="Beam Width",
        description="Line width for beam visualization (Green Lines)",
        default=1.0,
        min=0.1, max=10.0
    )

    # Torsionbar visualization properties
    toggle_torsionbars_vis: bpy.props.BoolProperty(
        name="Show Torsionbars",
        description="Toggles the visibility of torsionbars (Blue/Red Lines)",
        default=True
    )
    torsionbar_color: bpy.props.FloatVectorProperty(
        name="Torsionbar Color",
        description="Color of the outer torsionbar visualization segments",
        subtype='COLOR',
        default=(0.0, 0.0, 1.0, 1.0), # Blue
        min=0.0, max=1.0,
        size=4
    )
    torsionbar_mid_color: bpy.props.FloatVectorProperty(
        name="Torsionbar Mid Color",
        description="Color of the middle torsionbar visualization segment",
        subtype='COLOR',
        default=(1.0, 0.0, 0.0, 1.0), # Red
        min=0.0, max=1.0,
        size=4
    )
    torsionbar_width: bpy.props.FloatProperty(
        name="Torsionbar Width",
        description="Line width for torsionbar visualization",
        default=1.0,
        min=0.1, max=10.0
    )

    # Rail visualization properties
    toggle_rails_vis: bpy.props.BoolProperty(
        name="Show Rails",
        description="Toggles the visibility of rails (Yellow Lines)",
        default=True
    )
    rail_color: bpy.props.FloatVectorProperty(
        name="Rail Color",
        description="Color of the rail visualization lines",
        subtype='COLOR',
        default=(1.0, 1.0, 0.0, 1.0), # Yellow
        min=0.0, max=1.0,
        size=4
    )
    rail_width: bpy.props.FloatProperty(
        name="Rail Width",
        description="Line width for rail visualization",
        default=1.0,
        min=0.1, max=10.0
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
            new_v = bm.verts.new(v.co)
            new_v[init_node_id_layer] = bytes(node_id, 'utf-8')
            new_v[is_fake_layer] = 1
            new_verts.append(new_v)

        if len_selected_verts == 2:
            beam_indices_layer = bm.edges.layers.string[constants.EL_BEAM_INDICES]
            e = bm.edges.new(new_verts)
            e[beam_indices_layer] = bytes('-1', 'utf-8')
            export = True

        elif len_selected_verts in (3,4):
            face_idx_layer = bm.faces.layers.int[constants.FL_FACE_IDX]
            f = bm.faces.new(new_verts)
            f[face_idx_layer] = -1
            export = True

        # Update the edit mesh if in edit mode
        if obj.mode == 'EDIT':
            bmesh.update_edit_mesh(obj_data)
        bm.free()

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
            face[face_flip_flag_layer] = 1

        bm.free()

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
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and obj.data.get(constants.MESH_EDITING_ENABLED, False)

    def draw(self, context):
        obj = context.active_object
        if not obj:
            return

        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return

        bm = None
        try: # Add try-except for bmesh access
            if obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(obj_data)
            else:
                bm = bmesh.new()
                bm.from_mesh(obj_data)
        except Exception as e:
            print(f"Error getting bmesh for JBeam panel: {e}")
            self.layout.label(text="Error accessing mesh data.")
            return


        scene = context.scene
        ui_props = scene.ui_properties

        jbeam_part_name = obj_data.get(constants.MESH_JBEAM_PART) # Use .get() for safety

        layout = self.layout
        if jbeam_part_name: # Check if it's a JBeam mesh
            layout.label(text=f'{jbeam_part_name}')

            box = layout.box()
            col = box.column()

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

        if bm and not (obj.mode == 'EDIT'): bm.free() # Free bmesh if it was created


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
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and obj.data.get(constants.MESH_EDITING_ENABLED, False)

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
            if bm: bm.free()
            return

        if len(selected_nodes) == 1:
            if 'nodes' in curr_vdata:
                # Get index and node_id
                vert_index, node_id = selected_nodes[0]
                # v = bm.verts[vert_index] # Get vertex if needed, but not used here

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
                     if bm: bm.free()
                     return

                part_origin = e[part_origin_layer].decode('utf-8')
                try:
                    beam_idx_in_part = int(beam_indices[0]) # Use first index if multiple beams share edge
                except ValueError:
                    col.label(text="Invalid beam index.")
                    if bm: bm.free()
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
                    if bm: bm.free()
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

        if bm and not (obj.mode == 'EDIT'): bm.free() # Free bmesh


class JBEAM_EDITOR_PT_batch_node_renaming(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Batch Node Renaming'

    # Poll method checks if editing is enabled
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and obj.data.get(constants.MESH_EDITING_ENABLED, False)

    def draw(self, context):
        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        box = layout.box()
        col = box.column()
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
        return obj and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None and obj.data.get(constants.MESH_EDITING_ENABLED, False)

    def draw(self, context):
        obj = context.active_object
        if not obj:
            return

        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return

        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        # Check if it's a JBeam mesh before drawing settings (redundant due to poll, but safe)
        if obj_data.get(constants.MESH_JBEAM_PART) is not None:
            box = layout.box()
            col = box.column(align=True) # Align elements in the column

            col.label(text="General:")
            col.prop(ui_props, 'affect_node_references', text="Affect Node References")
            # <<< ADDED: Tooltip Placement Setting >>>
            col.label(text="Tooltip Placement:")
            col.prop(ui_props, 'tooltip_placement', text="")
            # <<< END ADDED >>>

            col.separator()
            col.label(text="Node Visualization:")
            col.prop(ui_props, 'toggle_node_ids_text', text="Show Node IDs Text")

            # --- Node Tooltips Sub-panel ---
            node_tooltip_box = col.box()
            row = node_tooltip_box.row(align=True)
            row.prop(ui_props, "show_node_tooltips_panel",
                     icon="TRIA_DOWN" if ui_props.show_node_tooltips_panel else "TRIA_RIGHT",
                     icon_only=True, emboss=False)
            row.label(text="Node Tooltips")

            if ui_props.show_node_tooltips_panel:
                node_tooltip_col = node_tooltip_box.column(align=True)

                # Parameters Tooltip (Main Toggle + Colors)
                row = node_tooltip_col.row(align=True)
                row.prop(ui_props, 'toggle_node_params_tooltip', text="Params")
                # Add value color property >>>
                split = row.split(factor=0.5, align=True)
                split.prop(ui_props, 'node_params_tooltip_color', text="")
                split.prop(ui_props, 'node_params_value_tooltip_color', text="")

                # Main box for all parameters, enabled by the main toggle
                node_param_main_box = node_tooltip_col.box()
                node_param_main_box.enabled = ui_props.toggle_node_params_tooltip

                # --- Coupler Parameters Sub-Box ---
                coupler_box = node_param_main_box.box()
                coupler_box.label(text="Coupler Parameters:")
                coupler_grid = coupler_box.grid_flow(row_major=True, columns=1, align=True)

                # --- Camera Parameters Sub-Box ---
                camera_box = node_param_main_box.box()
                camera_box.label(text="Camera Parameters:")
                camera_grid = camera_box.grid_flow(row_major=True, columns=1, align=True)

                # --- Other Parameters Sub-Box ---
                other_box = node_param_main_box.box()
                other_box.label(text="Other Parameters:")
                other_grid = other_box.grid_flow(row_major=True, columns=1, align=True)

                # Map parameter names to their corresponding UI toggle property
                node_param_toggle_map = {
                    'nodeWeight': 'tooltip_show_nodeWeight',
                    'nodeMaterial': 'tooltip_show_nodeMaterial',
                    'collision': 'tooltip_show_collision',
                    'selfCollision': 'tooltip_show_selfCollision',
                    'frictionCoef': 'tooltip_show_frictionCoef',
                    'slidingFrictionCoef': 'tooltip_show_slidingFrictionCoef',
                    'group': 'tooltip_show_group_node', # Use the renamed property
                    'couplerGroup': 'tooltip_show_couplerGroup',
                    'loadMembers': 'tooltip_show_loadMembers',
                    'isCoupler': 'tooltip_show_isCoupler',
                    'couplerStrength': 'tooltip_show_couplerStrength',
                    'couplerPullStrength': 'tooltip_show_couplerPullStrength',
                    'couplerPosition': 'tooltip_show_couplerPosition',
                    'couplerDirection': 'tooltip_show_couplerDirection',
                    'couplerRadius': 'tooltip_show_couplerRadius',
                    'couplerStartRadius': 'tooltip_show_couplerStartRadius',
                    'couplerBreakTriggerBeam': 'tooltip_show_couplerBreakTriggerBeam',
                    'couplerSoundVolumeFactor': 'tooltip_show_couplerSoundVolumeFactor',
                    'couplerSoundNode': 'tooltip_show_couplerSoundNode',
                    'couplerLockedSoundEvent': 'tooltip_show_couplerLockedSoundEvent',
                    'couplerUnlockedSoundEvent': 'tooltip_show_couplerUnlockedSoundEvent',
                    'couplerLockable': 'tooltip_show_couplerLockable',
                    'couplerAllowUnlocking': 'tooltip_show_couplerAllowUnlocking',
                    'couplerAutoLockRadius': 'tooltip_show_couplerAutoLockRadius',
                    'couplerAngleLimit': 'tooltip_show_couplerAngleLimit',
                    'couplerAngleLimitSpring': 'tooltip_show_couplerAngleLimitSpring',
                    'couplerAngleLimitDamp': 'tooltip_show_couplerAngleLimitDamp',
                    'couplerAngleLimitDeform': 'tooltip_show_couplerAngleLimitDeform',
                    'couplerAngleLimitStrength': 'tooltip_show_couplerAngleLimitStrength',
                    'couplerBreakGroup': 'tooltip_show_couplerBreakGroup',
                    'engineGroup': 'tooltip_show_engineGroup',
                    'enablePowertrain': 'tooltip_show_enablePowertrain',
                    'name': 'tooltip_show_name',
                    'inputName': 'tooltip_show_inputName',
                    'pressurePSI': 'tooltip_show_pressurePSI',
                    'soundFile': 'tooltip_show_soundFile',
                    'volumePerPSI': 'tooltip_show_volumePerPSI',
                    'attackTime': 'tooltip_show_attackTime',
                    'decayTime': 'tooltip_show_decayTime',
                    'maxVolumedB': 'tooltip_show_maxVolumedB',
                    'radius': 'tooltip_show_radius',
                    'fixed': 'tooltip_show_fixed',
                    'category': 'tooltip_show_category',
                    'pressureDamage': 'tooltip_show_pressureDamage',
                    'hubGroup': 'tooltip_show_hubGroup',
                    'pressureRatePSI': 'tooltip_show_pressureRatePSI',
                    'maxPressurePSI': 'tooltip_show_maxPressurePSI',
                    'boostPSI': 'tooltip_show_boostPSI',
                    'supercharger': 'tooltip_show_supercharger',
                    'turbocharger': 'tooltip_show_turbocharger',
                    'nitroSoundFile': 'tooltip_show_nitroSoundFile',
                    'nitroVolumePerPSI': 'tooltip_show_nitroVolumePerPSI',
                    'nitroAttackTime': 'tooltip_show_nitroAttackTime',
                    'nitroDecayTime': 'tooltip_show_nitroDecayTime',
                    'nitroMaxVolumedB': 'tooltip_show_nitroMaxVolumedB',
                    'cameraDistance': 'tooltip_show_cameraDistance',
                    'cameraRotation': 'tooltip_show_cameraRotation',
                    'cameraLookAt': 'tooltip_show_cameraLookAt',
                    'cameraFOV': 'tooltip_show_cameraFOV',
                    'cameraOffset': 'tooltip_show_cameraOffset',
                    'cameraMinDistance': 'tooltip_show_cameraMinDistance',
                    'cameraMaxDistance': 'tooltip_show_cameraMaxDistance',
                    'cameraFocusPoint': 'tooltip_show_cameraFocusPoint',
                    'cameraMode': 'tooltip_show_cameraMode',
                    'cameraShake': 'tooltip_show_cameraShake',
                    'cameraShakeMultiplier': 'tooltip_show_cameraShakeMultiplier',
                    'cameraShakeMaxSpeed': 'tooltip_show_cameraShakeMaxSpeed',
                    'cameraShakeMaxAngle': 'tooltip_show_cameraShakeMaxAngle',
                    'cameraShakeMaxOffset': 'tooltip_show_cameraShakeMaxOffset',
                    'cameraShakeFrequency': 'tooltip_show_cameraShakeFrequency',
                    'cameraShakeDamping': 'tooltip_show_cameraShakeDamping',
                    'cameraShakeRandomness': 'tooltip_show_cameraShakeRandomness',
                    'cameraShakeRandomSeed': 'tooltip_show_cameraShakeRandomSeed',
                    'cameraShakeRandomFrequency': 'tooltip_show_cameraShakeRandomFrequency',
                    'cameraShakeRandomDamping': 'tooltip_show_cameraShakeRandomDamping',
                    'cameraShakeRandomOffset': 'tooltip_show_cameraShakeRandomOffset',
                    'cameraShakeRandomAngle': 'tooltip_show_cameraShakeRandomAngle',
                    'cameraShakeRandomMaxSpeed': 'tooltip_show_cameraShakeRandomMaxSpeed',
                    'cameraShakeRandomMaxAngle': 'tooltip_show_cameraShakeRandomMaxAngle',
                    'cameraShakeRandomMaxOffset': 'tooltip_show_cameraShakeRandomMaxOffset',
                    'cameraShakeRandomFrequencyMultiplier': 'tooltip_show_cameraShakeRandomFrequencyMultiplier',
                    'cameraShakeRandomDampingMultiplier': 'tooltip_show_cameraShakeRandomDampingMultiplier',
                    'cameraShakeRandomOffsetMultiplier': 'tooltip_show_cameraShakeRandomOffsetMultiplier',
                    'cameraShakeRandomAngleMultiplier': 'tooltip_show_cameraShakeRandomAngleMultiplier',
                    'cameraShakeRandomMaxSpeedMultiplier': 'tooltip_show_cameraShakeRandomMaxSpeedMultiplier',
                    'cameraShakeRandomMaxAngleMultiplier': 'tooltip_show_cameraShakeRandomMaxAngleMultiplier',
                    'cameraShakeRandomMaxOffsetMultiplier': 'tooltip_show_cameraShakeRandomMaxOffsetMultiplier',
                    'burnRate': 'tooltip_show_burnRate',
                    'chemEnergy': 'tooltip_show_chemEnergy',
                    'flashPoint': 'tooltip_show_flashPoint',
                    'partName': 'tooltip_show_partName',
                    'selfIgnitionCoef': 'tooltip_show_selfIgnitionCoef',
                    'slotType': 'tooltip_show_slotType',
                    'smokePoint': 'tooltip_show_smokePoint',
                    'specHeat': 'tooltip_show_specHeat',
                    'totalOffset': 'tooltip_show_totalOffset',
                }

                # Iterate and assign properties to the correct grid
                for param_key, prop_name in node_param_toggle_map.items():
                    # Special case for 'group' to avoid conflict with beam group
                    text_override = "group" if prop_name == 'tooltip_show_group_node' else None

                    if "coupler" in param_key.lower():
                        coupler_grid.prop(ui_props, prop_name, text=text_override if text_override else param_key)
                    elif "camera" in param_key.lower():
                        camera_grid.prop(ui_props, prop_name, text=text_override if text_override else param_key)
                    else:
                        # These new ones will automatically go here
                        other_grid.prop(ui_props, prop_name, text=text_override if text_override else param_key)


            # --- Beam Visualization ---
            col.separator()
            col.label(text="Beam Visualization:")
            col.prop(ui_props, 'toggle_beams_vis')
            row = col.row()
            row.enabled = ui_props.toggle_beams_vis
            row.prop(ui_props, 'beam_color')
            col.prop(ui_props, 'beam_width')

            # --- Beam Tooltips Sub-panel ---
            beam_tooltip_box = col.box()
            row = beam_tooltip_box.row(align=True)
            row.prop(ui_props, "show_beam_tooltips_panel",
                     icon="TRIA_DOWN" if ui_props.show_beam_tooltips_panel else "TRIA_RIGHT",
                     icon_only=True, emboss=False)
            row.label(text="Beam Tooltips")

            if ui_props.show_beam_tooltips_panel:
                beam_tooltip_col = beam_tooltip_box.column(align=True)

                # Line Number Tooltip
                row = beam_tooltip_col.row(align=True)
                row.prop(ui_props, 'toggle_beam_line_tooltip', text="Line #")
                row.prop(ui_props, 'beam_line_tooltip_color', text="")

                # Parameters Tooltip (Main Toggle + Colors)
                row = beam_tooltip_col.row(align=True)
                row.prop(ui_props, 'toggle_beam_params_tooltip', text="Params")
                # Add value color property >>>
                split = row.split(factor=0.5, align=True)
                split.prop(ui_props, 'beam_params_tooltip_color', text="")
                split.prop(ui_props, 'beam_params_value_tooltip_color', text="")

                # Individual Parameter Toggles
                beam_param_box = beam_tooltip_col.box()
                beam_param_box.enabled = ui_props.toggle_beam_params_tooltip
                beam_param_grid = beam_param_box.grid_flow(row_major=True, columns=1, align=True) # Adjust columns as needed

                # Map parameter names to their corresponding UI toggle property
                beam_param_toggle_map = {
                    'beamSpring': 'tooltip_show_beamSpring',
                    'beamDamp': 'tooltip_show_beamDamp',
                    'beamDeform': 'tooltip_show_beamDeform',
                    'beamStrength': 'tooltip_show_beamStrength',
                    'beamPrecompression': 'tooltip_show_beamPrecompression',
                    'beamType': 'tooltip_show_beamType',
                    'beamLongBound': 'tooltip_show_beamLongBound',
                    'beamShortBound': 'tooltip_show_beamShortBound',
                    'beamLimitSpring': 'tooltip_show_beamLimitSpring',
                    'beamLimitDamp': 'tooltip_show_beamLimitDamp',
                    'beamPrecompressionRange': 'tooltip_show_beamPrecompressionRange',
                    'beamDampRebound': 'tooltip_show_beamDampRebound',
                    'beamDampFast': 'tooltip_show_beamDampFast',
                    'beamDampReboundFast': 'tooltip_show_beamDampReboundFast',
                    'beamDampVelocitySplit': 'tooltip_show_beamDampVelocitySplit',
                    'beamDampCutoffHz': 'tooltip_show_beamDampCutoffHz',
                    'beamDeformReboundThreshold': 'tooltip_show_beamDeformReboundThreshold',
                    'breakGroup': 'tooltip_show_breakGroup',
                    'breakGroupType': 'tooltip_show_breakGroupType',
                    'disableMeshBreaking': 'tooltip_show_disableMeshBreaking',
                    'disableTriangleBreaking': 'tooltip_show_disableTriangleBreaking',
                    'soundTriggerBeam': 'tooltip_show_soundTriggerBeam',
                    'optional': 'tooltip_show_optional',
                    'group': 'tooltip_show_group',
                    # --- ADDED: New Mappings ---
                    'dampCutoffHz': 'tooltip_show_dampCutoffHz',
                    'deformGroup': 'tooltip_show_deformGroup',
                    'deformLimitExpansion': 'tooltip_show_deformLimitExpansion',
                    'deformLimitStress': 'tooltip_show_deformLimitStress',
                    'deformationTriggerRatio': 'tooltip_show_deformationTriggerRatio',
                    'name': 'tooltip_show_name_beam', # Use renamed property
                    'partName': 'tooltip_show_partName_beam', # Use renamed property
                    'slotType': 'tooltip_show_slotType_beam', # Use renamed property
                    # --- END ADDED ---
                }

                # Iterate and display properties in the grid
                for param_key, prop_name in beam_param_toggle_map.items():
                    # Special case for 'name', 'partName', 'slotType' to avoid conflict with node params
                    text_override = None
                    if prop_name == 'tooltip_show_name_beam': text_override = "name"
                    elif prop_name == 'tooltip_show_partName_beam': text_override = "partName"
                    elif prop_name == 'tooltip_show_slotType_beam': text_override = "slotType"

                    beam_param_grid.prop(ui_props, prop_name, text=text_override if text_override else param_key)


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


def refresh_curr_vdata(force_refresh=False):
    global prev_obj_selected
    global curr_vdata
    global veh_render_dirty

    context = bpy.context
    selected_obj_name = None
    jbeam_part = None

    obj = context.active_object
    if obj is not None:
        obj_data = obj.data
        # <<< MODIFICATION START: Check if it's a JBeam object >>>
        if obj_data and obj_data.get(constants.MESH_JBEAM_PART) is not None:
            jbeam_part = obj_data.get(constants.MESH_JBEAM_PART)
            selected_obj_name = obj.name
        else:
            # It's not a JBeam object, clear name and part
            selected_obj_name = None
            jbeam_part = None
        # <<< MODIFICATION END >>>
    else:
        selected_obj_name = None

    if force_refresh or prev_obj_selected != selected_obj_name:
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

        veh_render_dirty = True
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
    # <<< MODIFICATION START: Check if active object is a JBeam object >>>
    if active_obj is None or active_obj.data is None or active_obj.data.get(constants.MESH_JBEAM_PART) is None:
        return # Don't draw if not a JBeam object
    # <<< MODIFICATION END >>>
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
    blfsize(font_id, 12)
    blfcolor(font_id, 1, 1, 1, 1) # Default white color

    # --- Node ID Drawing ---
    toggleNodeText = ui_props.toggle_node_ids_text
    if toggleNodeText:
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
                            lblfPosition(font_id, pos_text[0], pos_text[1], 0)
                            lblfDraw(font_id, node_id)

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
                        lblfPosition(font_id, pos_text[0], pos_text[1], 0)
                        lblfDraw(font_id, node_id)
            else:
                print(f"Warning: Node ID layers not found on single part {active_obj.name}", file=sys.stderr)

    # --- Beam Line Tooltip Drawing ---
    global _selected_beam_line_info
    if ui_props.toggle_beam_line_tooltip and _selected_beam_line_info is not None:
        line_num = _selected_beam_line_info['line']
        midpoint = _selected_beam_line_info['midpoint']
        if line_num is not None and midpoint is not None:
            pos_text = location_3d_to_region_2d(ctxRegion, ctxRegionData, midpoint)
            if pos_text:
                tooltip_text = f"Line: {line_num+1}"
                blfcolor(font_id, *ui_props.beam_line_tooltip_color)
                lblfPosition(font_id, pos_text[0] + 10, pos_text[1] + 10, 0)
                lblfDraw(font_id, tooltip_text)
                blfcolor(font_id, 1, 1, 1, 1) # Reset color

    # --- Beam Parameters Tooltip Drawing ---
    global _selected_beam_params_info
    if ui_props.toggle_beam_params_tooltip and _selected_beam_params_info is not None:
        params_list = _selected_beam_params_info.get('params_list')
        if params_list:
            padding_x = 65 # Default padding for left alignment
            padding_y = 20
            region_width = ctxRegion.width # <<< ADDED: Get region width
            region_height = ctxRegion.height
            bottom_left_y = padding_y

            # Calculate bottom_left_x based on placement >>>
            tooltip_placement = ui_props.tooltip_placement
            if tooltip_placement == 'BOTTOM_LEFT':
                bottom_left_x = padding_x
            elif tooltip_placement == 'BOTTOM_CENTER':
                bottom_left_x = region_width / 2 - 100 # Simple center offset, adjust as needed
            elif tooltip_placement == 'BOTTOM_RIGHT':
                bottom_left_x = region_width - 200 - padding_x # Simple right offset, adjust as needed
            else: # Default to left
                bottom_left_x = padding_x

            name_color = ui_props.beam_params_tooltip_color
            value_color = ui_props.beam_params_value_tooltip_color

            line_height = lblfDims(font_id, "X")[1]
            start_y = bottom_left_y + (len(params_list) -1) * (line_height + 4)

            for i, (key, value_repr) in enumerate(params_list):
                current_y = start_y - (i * (line_height + 4))
                key_text = f"{key}: "

                # Draw Key
                blfcolor(font_id, *name_color)
                lblfPosition(font_id, bottom_left_x, current_y, 0)
                lblfDraw(font_id, key_text)

                # Calculate position for value
                key_width = lblfDims(font_id, key_text)[0]
                value_x = bottom_left_x + key_width

                # Draw Value
                blfcolor(font_id, *value_color)
                lblfPosition(font_id, value_x, current_y, 0)
                lblfDraw(font_id, value_repr)

            blfcolor(font_id, 1, 1, 1, 1) # Reset color

    # --- Node Parameters Tooltip Drawing ---
    global _selected_node_params_info
    if ui_props.toggle_node_params_tooltip and _selected_node_params_info is not None:
        params_list = _selected_node_params_info.get('params_list')
        if params_list:
            node_padding_x = 65 # Default padding for left alignment
            node_padding_y = 20
            region_width = ctxRegion.width # <<< ADDED: Get region width
            region_height = ctxRegion.height
            bottom_left_y = node_padding_y

            # Estimate beam tooltip height if it's visible
            beam_tooltip_height = 0
            if ui_props.toggle_beam_params_tooltip and _selected_beam_params_info:
                beam_params_list = _selected_beam_params_info.get('params_list')
                if beam_params_list:
                    beam_line_height = lblfDims(font_id, "X")[1]
                    beam_tooltip_height = len(beam_params_list) * (beam_line_height + 4) + 5

            # Adjust node tooltip y position based on beam tooltip height
            bottom_left_y += beam_tooltip_height

            # Calculate bottom_left_x based on placement >>>
            tooltip_placement = ui_props.tooltip_placement
            if tooltip_placement == 'BOTTOM_LEFT':
                bottom_left_x = node_padding_x
            elif tooltip_placement == 'BOTTOM_CENTER':
                bottom_left_x = region_width / 2 - 100 # Simple center offset, adjust as needed
            elif tooltip_placement == 'BOTTOM_RIGHT':
                bottom_left_x = region_width - 200 - node_padding_x # Simple right offset, adjust as needed
            else: # Default to left
                bottom_left_x = node_padding_x

            name_color = ui_props.node_params_tooltip_color
            value_color = ui_props.node_params_value_tooltip_color

            line_height = lblfDims(font_id, "X")[1]
            start_y = bottom_left_y + (len(params_list) -1) * (line_height + 4)

            for i, (key, value_repr) in enumerate(params_list):
                current_y = start_y - (i * (line_height + 4))
                key_text = f"{key}: "

                # Draw Key
                blfcolor(font_id, *name_color)
                lblfPosition(font_id, bottom_left_x, current_y, 0)
                lblfDraw(font_id, key_text)

                # Calculate position for value
                key_width = lblfDims(font_id, key_text)[0]
                value_x = bottom_left_x + key_width

                # Draw Value
                blfcolor(font_id, *value_color)
                lblfPosition(font_id, value_x, current_y, 0)
                lblfDraw(font_id, value_repr)

            blfcolor(font_id, 1, 1, 1, 1) # Reset color

    # Final cleanup
    # Free bmesh if it was created for a single part in object mode
    if bm and not is_vehicle_part and active_obj.mode != 'EDIT':
        bm.free()


beam_render_shader = None
beam_render_batch = None
beam_coords = [] # Renamed from coords to be specific

torsionbar_render_batch = None
torsionbar_coords = []
torsionbar_red_render_batch = None # New batch for red segments
torsionbar_red_coords = []       # New coords for red segments

rail_render_batch = None
rail_coords = []


def draw_callback_view(context: bpy.types.Context):
    global veh_render_dirty
    global beam_render_shader
    global beam_render_batch
    global beam_coords
    global torsionbar_render_batch
    global torsionbar_coords
    global torsionbar_red_render_batch # New
    global torsionbar_red_coords       # New
    global rail_render_batch
    global rail_coords

    scene = context.scene
    ui_props = scene.ui_properties
    # Check if ui_properties exists
    if not hasattr(context, 'scene') or not hasattr(scene, 'ui_properties'):
        return

    active_obj = context.active_object
    # <<< MODIFICATION START: Check if active object is a JBeam object >>>
    is_valid_jbeam_obj = False
    if active_obj and active_obj.data and active_obj.data.get(constants.MESH_JBEAM_PART) is not None: 
        is_valid_jbeam_obj = True

    if not is_valid_jbeam_obj:
        # Clear batches if the object is not valid JBeam
        if beam_render_batch: beam_render_batch = None
        if torsionbar_render_batch: torsionbar_render_batch = None
        if torsionbar_red_render_batch: torsionbar_red_render_batch = None
        if rail_render_batch: rail_render_batch = None
        # Clear coordinates as well if dirty flag wasn't already set (e.g., object just deselected)
        if not veh_render_dirty:
            beam_coords.clear()
            torsionbar_coords.clear()
            torsionbar_red_coords.clear()
            rail_coords.clear()
        veh_render_dirty = False # Ensure dirty flag is false
        return # Don't draw if not a valid JBeam object
    # <<< MODIFICATION END >>>

    if beam_render_shader is None:
        beam_render_shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    if veh_render_dirty:
        beam_coords.clear()
        torsionbar_coords.clear() # Clear torsionbar coords too
        torsionbar_red_coords.clear() # Clear red torsionbar coords
        rail_coords.clear() # Clear rail coords

        # active_obj is guaranteed to be valid JBeam here due to the check above
        active_obj_data = active_obj.data

        collection = active_obj.users_collection[0] if active_obj.users_collection else None
        is_vehicle_part = collection is not None and collection.get(constants.COLLECTION_VEHICLE_MODEL) is not None

        # Map node IDs to their hidden status and current position/matrix
        node_id_to_hide_status: dict[str, bool] = {}
        # Stores {node_id: (local_coord.copy(), matrix_world.copy())}
        node_id_to_pos_matrix_map: dict[str, tuple[Vector, Matrix]] = {} # Use Matrix type hint

        # --- Vehicle Data Gathering ---
        if is_vehicle_part:
            # Use part_name_to_obj which should be populated by draw_callback_px or needs population here
            if not part_name_to_obj: # Populate if empty
                 for obj in collection.all_objects:
                     # Check if the object in the collection is a JBeam part
                    if obj.data and obj.data.get(constants.MESH_JBEAM_PART):
                        part_name_to_obj[obj.data[constants.MESH_JBEAM_PART]] = obj

            for obj in collection.all_objects:
                # Only process visible JBeam parts in the collection
                if obj.visible_get() and obj.data and obj.data.get(constants.MESH_JBEAM_PART) is not None: 
                    obj_data = obj.data
                    bm = None
                    try:
                        # Get bmesh, handle edit mode for active object
                        if obj == active_obj and active_obj.mode == 'EDIT':
                            bm = bmesh.from_edit_mesh(obj_data)
                        else:
                            bm = bmesh.new()
                            bm.from_mesh(obj_data)

                        # Get layers safely
                        beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
                        node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
                        is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)

                        # Populate node maps
                        if node_id_layer and is_fake_layer:
                            bm.verts.ensure_lookup_table()
                            obj_matrix_copy = obj.matrix_world.copy() # Copy matrix once per object
                            for v in bm.verts:
                                if v[is_fake_layer] == 0: # Only consider real nodes
                                    node_id = v[node_id_layer].decode('utf-8')
                                    node_id_to_hide_status[node_id] = v.hide
                                    # Store local coord and object matrix
                                    node_id_to_pos_matrix_map[node_id] = (v.co.copy(), obj_matrix_copy)

                        # Gather Beam Coords (using mesh edges)
                        if beam_indices_layer:
                            bm.edges.ensure_lookup_table()
                            for e in bm.edges:
                                # Check if edge itself or connected verts are hidden
                                if e.hide or any(v.hide for v in e.verts):
                                    continue
                                # Check if it's a JBeam beam (index is not empty)
                                if e[beam_indices_layer].decode('utf-8') != '':
                                    v1, v2 = e.verts[0], e.verts[1]
                                    # Calculate world coords directly for beams
                                    beam_coords.append(obj.matrix_world @ v1.co)
                                    beam_coords.append(obj.matrix_world @ v2.co)

                    except Exception as e:
                        print(f"Error getting geometry data from {obj.name}: {e}", file=sys.stderr) # Print to stderr
                    finally:
                        # Free bmesh if created, don't free the active edit mesh
                        if bm and not (obj == active_obj and active_obj.mode == 'EDIT'):
                            bm.free()

            # Gather Torsionbar Coords (using node_id_to_pos_matrix_map)
            if curr_vdata and 'torsionbars' in curr_vdata:
                torsionbars_data = curr_vdata['torsionbars']
                for tb in torsionbars_data:
                    id1, id2, id3, id4 = tb.get('id1:'), tb.get('id2:'), tb.get('id3:'), tb.get('id4:')

                    # Check if any involved node is hidden
                    if (node_id_to_hide_status.get(id1, False) or
                        node_id_to_hide_status.get(id2, False) or
                        node_id_to_hide_status.get(id3, False) or
                        node_id_to_hide_status.get(id4, False)):
                        continue

                    # Get positions and matrices from the map
                    data1 = node_id_to_pos_matrix_map.get(id1)
                    data2 = node_id_to_pos_matrix_map.get(id2)
                    data3 = node_id_to_pos_matrix_map.get(id3)
                    data4 = node_id_to_pos_matrix_map.get(id4)

                    if data1 and data2 and data3 and data4:
                        co1, matrix1 = data1
                        co2, matrix2 = data2
                        co3, matrix3 = data3
                        co4, matrix4 = data4

                        # Calculate world positions using stored coords and matrices
                        world_pos1 = matrix1 @ co1
                        world_pos2 = matrix2 @ co2
                        world_pos3 = matrix3 @ co3
                        world_pos4 = matrix4 @ co4

                        # Segments 1-2 and 3-4 go to the regular (blue) list
                        torsionbar_coords.append(world_pos1)
                        torsionbar_coords.append(world_pos2)
                        torsionbar_coords.append(world_pos3) # Start third line
                        torsionbar_coords.append(world_pos4) # End third line

                        # Segment 2-3 goes to the red list
                        torsionbar_red_coords.append(world_pos2) # Start second line
                        torsionbar_red_coords.append(world_pos3) # End second line
                    else:
                        # Optional: Warn if a node wasn't found in the map
                        missing_nodes = [nid for nid, data in [(id1, data1), (id2, data2), (id3, data3), (id4, data4)] if not data]
                        if missing_nodes:
                            print(f"Warning: Could not find position data for torsionbar nodes {missing_nodes}", file=sys.stderr)


            # Gather Rail Coords (using node_id_to_pos_matrix_map)
            if curr_vdata and 'rails' in curr_vdata:
                rails_data = curr_vdata['rails']
                for rail_name, rail_info in rails_data.items():
                    links = rail_info.get('links:')
                    if isinstance(links, list) and len(links) == 2:
                        id1, id2 = links[0], links[1]

                        # Check if any involved node is hidden
                        if (node_id_to_hide_status.get(id1, False) or
                            node_id_to_hide_status.get(id2, False)):
                            continue

                        # Get positions and matrices from the map
                        data1 = node_id_to_pos_matrix_map.get(id1)
                        data2 = node_id_to_pos_matrix_map.get(id2)

                        if data1 and data2:
                            co1, matrix1 = data1
                            co2, matrix2 = data2

                            # Calculate world positions
                            world_pos1 = matrix1 @ co1
                            world_pos2 = matrix2 @ co2

                            rail_coords.append(world_pos1)
                            rail_coords.append(world_pos2)
                        else:
                            # Optional: Warn if a node wasn't found
                            missing_nodes = [nid for nid, data in [(id1, data1), (id2, data2)] if not data]
                            if missing_nodes:
                                print(f"Warning: Could not find position data for rail nodes {missing_nodes}", file=sys.stderr)


        # --- Single Part Data Gathering ---
        else: # is_valid_jbeam_obj is True, but not part of a vehicle collection
            if active_obj.visible_get():
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

                    # Populate node maps for single part
                    if node_id_layer and is_fake_layer:
                        bm.verts.ensure_lookup_table()
                        obj_matrix_copy = active_obj.matrix_world.copy() # Copy matrix once
                        for v in bm.verts:
                            if v[is_fake_layer] == 0: # Only consider real nodes
                                node_id = v[node_id_layer].decode('utf-8')
                                node_id_to_hide_status[node_id] = v.hide
                                node_id_to_pos_matrix_map[node_id] = (v.co.copy(), obj_matrix_copy)

                    # Gather Beams (using mesh edges)
                    if beam_indices_layer:
                        bm.edges.ensure_lookup_table()
                        for e in bm.edges:
                            if e.hide or any(v.hide for v in e.verts):
                                continue
                            if e[beam_indices_layer].decode('utf-8') != '':
                                v1, v2 = e.verts[0], e.verts[1]
                                beam_coords.append(active_obj.matrix_world @ v1.co)
                                beam_coords.append(active_obj.matrix_world @ v2.co)

                    # Gather Torsionbars (using node_id_to_pos_matrix_map)
                    if curr_vdata and 'torsionbars' in curr_vdata:
                        torsionbars_data = curr_vdata['torsionbars']
                        for tb in torsionbars_data:
                            id1, id2, id3, id4 = tb.get('id1:'), tb.get('id2:'), tb.get('id3:'), tb.get('id4:')

                            if (node_id_to_hide_status.get(id1, False) or
                                node_id_to_hide_status.get(id2, False) or
                                node_id_to_hide_status.get(id3, False) or
                                node_id_to_hide_status.get(id4, False)):
                                continue

                            data1 = node_id_to_pos_matrix_map.get(id1)
                            data2 = node_id_to_pos_matrix_map.get(id2)
                            data3 = node_id_to_pos_matrix_map.get(id3)
                            data4 = node_id_to_pos_matrix_map.get(id4)

                            if data1 and data2 and data3 and data4:
                                co1, matrix1 = data1 # Matrix will be the same for single part
                                co2, matrix2 = data2
                                co3, matrix3 = data3
                                co4, matrix4 = data4

                                world_pos1 = matrix1 @ co1
                                world_pos2 = matrix2 @ co2
                                world_pos3 = matrix3 @ co3
                                world_pos4 = matrix4 @ co4

                                torsionbar_coords.append(world_pos1)
                                torsionbar_coords.append(world_pos2)
                                torsionbar_coords.append(world_pos3)
                                torsionbar_coords.append(world_pos4)

                                torsionbar_red_coords.append(world_pos2)
                                torsionbar_red_coords.append(world_pos3)
                            else:
                                missing_nodes = [nid for nid, data in [(id1, data1), (id2, data2), (id3, data3), (id4, data4)] if not data]
                                if missing_nodes:
                                    print(f"Warning: Could not find position data for torsionbar nodes {missing_nodes}", file=sys.stderr)


                    # Gather Rail Coords (using node_id_to_pos_matrix_map)
                    if curr_vdata and 'rails' in curr_vdata:
                        rails_data = curr_vdata['rails']
                        for rail_name, rail_info in rails_data.items():
                            links = rail_info.get('links:')
                            if isinstance(links, list) and len(links) == 2:
                                id1, id2 = links[0], links[1]

                                if (node_id_to_hide_status.get(id1, False) or
                                    node_id_to_hide_status.get(id2, False)):
                                    continue

                                data1 = node_id_to_pos_matrix_map.get(id1)
                                data2 = node_id_to_pos_matrix_map.get(id2)

                                if data1 and data2:
                                    co1, matrix1 = data1 # Matrix will be the same
                                    co2, matrix2 = data2

                                    world_pos1 = matrix1 @ co1
                                    world_pos2 = matrix2 @ co2

                                    rail_coords.append(world_pos1)
                                    rail_coords.append(world_pos2)
                                else:
                                    missing_nodes = [nid for nid, data in [(id1, data1), (id2, data2)] if not data]
                                    if missing_nodes:
                                        print(f"Warning: Could not find position data for rail nodes {missing_nodes}", file=sys.stderr)


                except Exception as e:
                    print(f"Error getting geometry data from {active_obj.name}: {e}", file=sys.stderr) # Print to stderr
                finally:
                    # Free bmesh if created, don't free the active edit mesh
                    if bm and not (active_obj.mode == 'EDIT'):
                        bm.free()

        # Create batches
        if beam_coords:
            beam_render_batch = batch_for_shader(beam_render_shader, 'LINES', {"pos": beam_coords})
        else:
            beam_render_batch = None

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

        veh_render_dirty = False # Reset dirty flag

    # --- Drawing ---
    gpu.state.depth_test_set('LESS_EQUAL') # Enable depth test once

    # Draw Beams
    if beam_render_batch is not None and ui_props.toggle_beams_vis: # Check toggle
        beam_render_shader.uniform_float("color", ui_props.beam_color) # Use UI color
        gpu.state.line_width_set(ui_props.beam_width) # Use UI width
        gpu.state.depth_mask_set(True) # Enable depth writing
        beam_render_batch.draw(beam_render_shader)
        gpu.state.depth_mask_set(False) # Disable depth writing (optional, depends on desired effect)

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
        import traceback
        traceback.print_exc()
        return None

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
        return
    active_obj_data = active_obj.data

    # Check if it's a JBeam object (don't need MESH_EDITING_ENABLED here for refresh)
    is_jbeam_obj = active_obj_data.get(constants.MESH_JBEAM_PART) is not None
    if not is_jbeam_obj:
        _selected_beam_line_info = None
        _selected_beam_params_info = None
        _selected_node_params_info = None
        # Still call refresh_curr_vdata to clear data if needed
        refresh_curr_vdata()
        return

    # Refresh data based on current active object (will set veh_render_dirty if needed)
    refresh_curr_vdata()

    # Only proceed with Edit Mode logic if in Edit Mode and editing is enabled
    mesh_editing_enabled = active_obj_data.get(constants.MESH_EDITING_ENABLED, False)
    if active_obj.mode != 'EDIT' or not mesh_editing_enabled:
        _selected_beam_line_info = None
        _selected_beam_params_info = None
        _selected_node_params_info = None
        return # Exit if not in edit mode or editing disabled

    # --- The rest of the function assumes Edit Mode ---

    active_obj_eval: bpy.types.Object = active_obj.evaluated_get(depsgraph)

    jbeam_filepath = active_obj_data.get(constants.MESH_JBEAM_FILE_PATH)
    if jbeam_filepath:
        text_editor.show_int_file(jbeam_filepath)

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
    if len(selected_nodes) == 1:
        vert_index, init_node_id = selected_nodes[0]
        try:
            v = bm.verts[vert_index]
            current_node_id = v[node_id_layer].decode('utf-8')
            global rename_enabled
            rename_enabled = False
            if ui_props.input_node_id != current_node_id:
                ui_props.input_node_id = current_node_id
        except IndexError:
             if ui_props.input_node_id != "":
                 ui_props.input_node_id = ""

    # --- Tooltip Logic ---
    _selected_beam_line_info = None
    _selected_beam_params_info = None
    _selected_node_params_info = None

    # --- Node Tooltip Logic ---
    if len(selected_nodes) == 1:
        vert_index, node_id = selected_nodes[0]
        if curr_vdata and 'nodes' in curr_vdata and node_id in curr_vdata['nodes']:
            node_data = curr_vdata['nodes'][node_id]
            params_list = []
            node_world_pos = active_obj.matrix_world @ bm.verts[vert_index].co

            node_param_toggle_map = {
                'nodeWeight': 'tooltip_show_nodeWeight', 'nodeMaterial': 'tooltip_show_nodeMaterial',
                'collision': 'tooltip_show_collision', 'selfCollision': 'tooltip_show_selfCollision',
                'frictionCoef': 'tooltip_show_frictionCoef', 'slidingFrictionCoef': 'tooltip_show_slidingFrictionCoef',
                'group': 'tooltip_show_group_node', 'couplerGroup': 'tooltip_show_couplerGroup',
                'loadMembers': 'tooltip_show_loadMembers', 'isCoupler': 'tooltip_show_isCoupler',
                'couplerStrength': 'tooltip_show_couplerStrength', 'couplerPullStrength': 'tooltip_show_couplerPullStrength',
                'couplerPosition': 'tooltip_show_couplerPosition', 'couplerDirection': 'tooltip_show_couplerDirection',
                'couplerRadius': 'tooltip_show_couplerRadius', 'couplerStartRadius': 'tooltip_show_couplerStartRadius',
                'couplerBreakTriggerBeam': 'tooltip_show_couplerBreakTriggerBeam', 'couplerSoundVolumeFactor': 'tooltip_show_couplerSoundVolumeFactor',
                'couplerSoundNode': 'tooltip_show_couplerSoundNode', 'couplerLockedSoundEvent': 'tooltip_show_couplerLockedSoundEvent',
                'couplerUnlockedSoundEvent': 'tooltip_show_couplerUnlockedSoundEvent', 'couplerLockable': 'tooltip_show_couplerLockable',
                'couplerAllowUnlocking': 'tooltip_show_couplerAllowUnlocking', 'couplerAutoLockRadius': 'tooltip_show_couplerAutoLockRadius',
                'couplerAngleLimit': 'tooltip_show_couplerAngleLimit', 'couplerAngleLimitSpring': 'tooltip_show_couplerAngleLimitSpring',
                'couplerAngleLimitDamp': 'tooltip_show_couplerAngleLimitDamp', 'couplerAngleLimitDeform': 'tooltip_show_couplerAngleLimitDeform',
                'couplerAngleLimitStrength': 'tooltip_show_couplerAngleLimitStrength', 'couplerBreakGroup': 'tooltip_show_couplerBreakGroup',
                'engineGroup': 'tooltip_show_engineGroup', 'enablePowertrain': 'tooltip_show_enablePowertrain',
                'name': 'tooltip_show_name', 'inputName': 'tooltip_show_inputName',
                'pressurePSI': 'tooltip_show_pressurePSI', 'soundFile': 'tooltip_show_soundFile',
                'volumePerPSI': 'tooltip_show_volumePerPSI', 'attackTime': 'tooltip_show_attackTime',
                'decayTime': 'tooltip_show_decayTime', 'maxVolumedB': 'tooltip_show_maxVolumedB',
                'radius': 'tooltip_show_radius', 'fixed': 'tooltip_show_fixed',
                'category': 'tooltip_show_category', 'pressureDamage': 'tooltip_show_pressureDamage',
                'hubGroup': 'tooltip_show_hubGroup', 'pressureRatePSI': 'tooltip_show_pressureRatePSI',
                'maxPressurePSI': 'tooltip_show_maxPressurePSI', 'boostPSI': 'tooltip_show_boostPSI',
                'supercharger': 'tooltip_show_supercharger', 'turbocharger': 'tooltip_show_turbocharger',
                'nitroSoundFile': 'tooltip_show_nitroSoundFile', 'nitroVolumePerPSI': 'tooltip_show_nitroVolumePerPSI',
                'nitroAttackTime': 'tooltip_show_nitroAttackTime', 'nitroDecayTime': 'tooltip_show_nitroDecayTime',
                'nitroMaxVolumedB': 'tooltip_show_nitroMaxVolumedB', 'cameraDistance': 'tooltip_show_cameraDistance',
                'cameraRotation': 'tooltip_show_cameraRotation', 'cameraLookAt': 'tooltip_show_cameraLookAt',
                'cameraFOV': 'tooltip_show_cameraFOV', 'cameraOffset': 'tooltip_show_cameraOffset',
                'cameraMinDistance': 'tooltip_show_cameraMinDistance', 'cameraMaxDistance': 'tooltip_show_cameraMaxDistance',
                'cameraFocusPoint': 'tooltip_show_cameraFocusPoint', 'cameraMode': 'tooltip_show_cameraMode',
                'cameraShake': 'tooltip_show_cameraShake', 'cameraShakeMultiplier': 'tooltip_show_cameraShakeMultiplier',
                'cameraShakeMaxSpeed': 'tooltip_show_cameraShakeMaxSpeed', 'cameraShakeMaxAngle': 'tooltip_show_cameraShakeMaxAngle',
                'cameraShakeMaxOffset': 'tooltip_show_cameraShakeMaxOffset', 'cameraShakeFrequency': 'tooltip_show_cameraShakeFrequency',
                'cameraShakeDamping': 'tooltip_show_cameraShakeDamping', 'cameraShakeRandomness': 'tooltip_show_cameraShakeRandomness',
                'cameraShakeRandomSeed': 'tooltip_show_cameraShakeRandomSeed', 'cameraShakeRandomFrequency': 'tooltip_show_cameraShakeRandomFrequency',
                'cameraShakeRandomDamping': 'tooltip_show_cameraShakeRandomDamping', 'cameraShakeRandomOffset': 'tooltip_show_cameraShakeRandomOffset',
                'cameraShakeRandomAngle': 'tooltip_show_cameraShakeRandomAngle', 'cameraShakeRandomMaxSpeed': 'tooltip_show_cameraShakeRandomMaxSpeed',
                'cameraShakeRandomMaxAngle': 'tooltip_show_cameraShakeRandomMaxAngle', 'cameraShakeRandomMaxOffset': 'tooltip_show_cameraShakeRandomMaxOffset',
                'cameraShakeRandomFrequencyMultiplier': 'tooltip_show_cameraShakeRandomFrequencyMultiplier', 'cameraShakeRandomDampingMultiplier': 'tooltip_show_cameraShakeRandomDampingMultiplier',
                'cameraShakeRandomOffsetMultiplier': 'tooltip_show_cameraShakeRandomOffsetMultiplier', 'cameraShakeRandomAngleMultiplier': 'tooltip_show_cameraShakeRandomAngleMultiplier',
                'cameraShakeRandomMaxSpeedMultiplier': 'tooltip_show_cameraShakeRandomMaxSpeedMultiplier', 'cameraShakeRandomMaxAngleMultiplier': 'tooltip_show_cameraShakeRandomMaxAngleMultiplier',
                'cameraShakeRandomMaxOffsetMultiplier': 'tooltip_show_cameraShakeRandomMaxOffsetMultiplier', 'burnRate': 'tooltip_show_burnRate',
                'chemEnergy': 'tooltip_show_chemEnergy', 'flashPoint': 'tooltip_show_flashPoint',
                'partName': 'tooltip_show_partName', 'selfIgnitionCoef': 'tooltip_show_selfIgnitionCoef',
                'slotType': 'tooltip_show_slotType', 'smokePoint': 'tooltip_show_smokePoint',
                'specHeat': 'tooltip_show_specHeat', 'totalOffset': 'tooltip_show_totalOffset',
            }

            for k in sorted(node_data.keys(), key=lambda x: str(x)):
                if k == Metadata or k == 'pos' or k == 'posNoOffset': continue
                val = node_data[k]
                toggle_prop_name = node_param_toggle_map.get(k)
                if toggle_prop_name and getattr(ui_props, toggle_prop_name, False):
                    params_list.append((k, repr(val)))
                elif toggle_prop_name is None: pass

            if params_list:
                _selected_node_params_info = {'params_list': params_list, 'pos': node_world_pos}
            else:
                _selected_node_params_info = {'params_list': [("(No params shown)", "")], 'pos': node_world_pos}

    # --- Beam Tooltip Logic ---
    elif len(selected_beams) == 1:
        e, beam_indices_str = selected_beams[0]
        beam_indices = beam_indices_str.split(',')
        if beam_indices:
            try:
                target_beam_idx_in_part = int(beam_indices[0])
                target_part_origin = e[beam_part_origin_layer].decode('utf-8')
                midpoint = active_obj.matrix_world @ ((e.verts[0].co + e.verts[1].co) / 2)

                # Get Line Number
                if target_beam_idx_in_part > 0 and target_part_origin and jbeam_filepath:
                    line_num = find_beam_line_number(jbeam_filepath, target_part_origin, target_beam_idx_in_part)
                    if line_num is not None:
                        _selected_beam_line_info = {'line': line_num, 'midpoint': midpoint}

                # Get Parameters
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

                        beam_param_toggle_map = {
                            'beamSpring': 'tooltip_show_beamSpring', 'beamDamp': 'tooltip_show_beamDamp',
                            'beamDeform': 'tooltip_show_beamDeform', 'beamStrength': 'tooltip_show_beamStrength',
                            'beamPrecompression': 'tooltip_show_beamPrecompression', 'beamType': 'tooltip_show_beamType',
                            'beamLongBound': 'tooltip_show_beamLongBound', 'beamShortBound': 'tooltip_show_beamShortBound',
                            'beamLimitSpring': 'tooltip_show_beamLimitSpring', 'beamLimitDamp': 'tooltip_show_beamLimitDamp',
                            'beamPrecompressionRange': 'tooltip_show_beamPrecompressionRange', 'beamDampRebound': 'tooltip_show_beamDampRebound',
                            'beamDampFast': 'tooltip_show_beamDampFast', 'beamDampReboundFast': 'tooltip_show_beamDampReboundFast',
                            'beamDampVelocitySplit': 'tooltip_show_beamDampVelocitySplit', 'beamDampCutoffHz': 'tooltip_show_beamDampCutoffHz',
                            'beamDeformReboundThreshold': 'tooltip_show_beamDeformReboundThreshold', 'breakGroup': 'tooltip_show_breakGroup',
                            'breakGroupType': 'tooltip_show_breakGroupType', 'disableMeshBreaking': 'tooltip_show_disableMeshBreaking',
                            'disableTriangleBreaking': 'tooltip_show_disableTriangleBreaking', 'soundTriggerBeam': 'tooltip_show_soundTriggerBeam',
                            'optional': 'tooltip_show_optional', 'group': 'tooltip_show_group',
                            'dampCutoffHz': 'tooltip_show_dampCutoffHz', 'deformGroup': 'tooltip_show_deformGroup',
                            'deformLimitExpansion': 'tooltip_show_deformLimitExpansion', 'deformLimitStress': 'tooltip_show_deformLimitStress',
                            'deformationTriggerRatio': 'tooltip_show_deformationTriggerRatio', 'name': 'tooltip_show_name_beam',
                            'partName': 'tooltip_show_partName_beam', 'slotType': 'tooltip_show_slotType_beam',
                        }

                        for k in sorted(beam_data.keys(), key=lambda x: str(x)):
                            if k in ('id1:', 'id2:', 'partOrigin') or k == Metadata: continue
                            val = beam_data[k]
                            toggle_prop_name = beam_param_toggle_map.get(k)
                            if toggle_prop_name and getattr(ui_props, toggle_prop_name, False):
                                params_list.append((k, repr(val)))
                            elif toggle_prop_name is None: pass

                        if params_list:
                            _selected_beam_params_info = {'params_list': params_list, 'midpoint': midpoint}
                        else:
                             _selected_beam_params_info = {'params_list': [("(No params shown)", "")], 'midpoint': midpoint}
                    else:
                        print(f"  Warning: Global beam index {global_beam_idx} not found or invalid for part '{target_part_origin}'.")

            except ValueError:
                print(f"Warning: Could not parse beam index: {beam_indices_str}", file=sys.stderr)
            except Exception as find_err:
                 print(f"Error processing beam tooltips: {find_err}", file=sys.stderr)
                 import traceback
                 traceback.print_exc()

    # No need to free bm as it's from edit mesh


@persistent
def depsgraph_callback(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    context = bpy.context
    try:
        _depsgraph_callback(context, scene, depsgraph)
    except Exception as e:
        print(f"Error in depsgraph callback: {e}", file=sys.stderr)
        import traceback
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
    JBEAM_EDITOR_PT_transform_panel_ext,
    JBEAM_EDITOR_PT_jbeam_panel,
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

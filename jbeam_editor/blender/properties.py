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

from ..core import constants
from . import state


# Refresh property input field UI
def on_input_node_id_field_updated(self, context: bpy.types.Context):
    scene = context.scene
    ui_props = scene.ui_properties

    obj = context.active_object
    if obj is None or len(state.selected_nodes) == 0:
        return

    if state.rename_enabled:
        selected_vert = state.selected_nodes[0][0]
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)

        # Set the selected mesh's selected vertex node_id attribute to the UI node_id input field value
        node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID]
        selected_vert[node_id_layer] = bytes(ui_props.input_node_id, 'utf-8')

        bm.free()
        state._force_do_export = True

    state.rename_enabled = True

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

    affect_node_references: bpy.props.BoolProperty(
        name="Affect Node References",
        description="Toggles updating JBeam entries who references nodes. E.g. deleting a beam who references a node being deleted",
        default=False
    )

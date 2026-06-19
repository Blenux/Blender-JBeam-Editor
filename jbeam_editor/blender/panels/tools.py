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

from .. import state
from ..operators.nodes import JBEAM_EDITOR_OT_batch_node_renaming


class JBEAM_EDITOR_PT_batch_node_renaming(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Batch Node Renaming'

    def draw(self, context):
        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        box = layout.box()
        col = box.column()
        col.row().label(text='Naming Scheme')
        col.prop(ui_props, 'batch_node_renaming_naming_scheme', text = "")
        col.prop(ui_props, 'batch_node_renaming_node_idx', text = "Node Index")

        operator_text = 'Stop' if state.batch_node_renaming_enabled else 'Start'
        col.operator(JBEAM_EDITOR_OT_batch_node_renaming.bl_idname, text=operator_text)

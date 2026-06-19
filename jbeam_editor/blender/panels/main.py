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

from .. import state
from ...core import constants
from ..operators.core import JBEAM_EDITOR_OT_force_jbeam_sync


class JBEAM_EDITOR_PT_transform_panel_ext(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_label = 'JBeam'

    def draw(self, context):
        layout = self.layout
        layout.operator('jbeam_editor.force_jbeam_sync', text='Force JBeam Sync')


class JBEAM_EDITOR_PT_jbeam_panel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'JBeam'

    def draw(self, context):
        obj = context.active_object
        if not obj:
            return

        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return

        bm = None
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj_data)
        else:
            bm = bmesh.new()
            bm.from_mesh(obj_data)

        scene = context.scene
        ui_props = scene.ui_properties

        jbeam_part_name = obj_data[constants.MESH_JBEAM_PART]

        layout = self.layout
        layout.label(text=f'{jbeam_part_name}')

        # If mesh isn't a JBeam mesh (it doesn't have node id attributes), give user option to convert it to one (add node id attributes)
        if obj_data.get(constants.MESH_JBEAM_PART) is None:
            # TODO: FIX FOR NEXT UPDATE
            #layout.operator('jbeam_editor.convert_to_jbeam_mesh', text='Convert to JBeam Mesh')
            pass
        else:
            box = layout.box()
            col = box.column()

            len_selected_verts = len(state.selected_nodes)
            len_selected_faces = len(state.selected_tris_quads)

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

        bm.free()


class JBEAM_EDITOR_PT_jbeam_settings(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Settings'

    def draw(self, context):
        obj = context.active_object
        if not obj:
            return

        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return

        bm = None
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj_data)
        else:
            bm = bmesh.new()
            bm.from_mesh(obj_data)

        scene = context.scene
        ui_props = scene.ui_properties
        layout = self.layout

        # If mesh isn't a JBeam mesh (it doesn't have node id attributes), give user option to convert it to one (add node id attributes)
        if obj_data.get(constants.MESH_JBEAM_PART) is None:
            # TODO: FIX FOR NEXT UPDATE
            #layout.operator('jbeam_editor.convert_to_jbeam_mesh', text='Convert to JBeam Mesh')
            pass
        else:
            box = layout.box()
            col = box.column()

            col.prop(ui_props, 'toggle_node_ids_text', text="Toggle Node IDs Text")
            col.prop(ui_props, 'affect_node_references', text="Affect Node References")

        bm.free()

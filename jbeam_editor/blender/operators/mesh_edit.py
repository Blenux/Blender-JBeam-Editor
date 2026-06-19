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
from bpy.types import Operator

from .. import state
from ...core import constants


# Add JBeam beam/triangle/quad
class JBEAM_EDITOR_OT_add_beam_tri_quad(bpy.types.Operator):
    bl_idname = "jbeam_editor.add_beam_tri_quad"
    bl_label = "Add Beam/Triangle/Quad"

    @classmethod
    def poll(cls, context):
        return len(state.selected_nodes) in (2,3,4)

    def invoke(self, context, event):
        obj = context.active_object
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        init_node_id_layer = bm.verts.layers.string[constants.VL_INIT_NODE_ID]
        is_fake_layer = bm.verts.layers.int[constants.VL_NODE_IS_FAKE]

        export = False

        len_selected_verts = len(state.selected_nodes)

        new_verts = []
        for node in state.selected_nodes:
            v, node_id = node[0], node[1]
            new_v = bm.verts.new(v.co)
            new_v[init_node_id_layer] = bytes(node_id, 'utf-8')
            new_v[is_fake_layer] = 1
            new_verts.append(new_v)

        if len_selected_verts == 2:
            beam_indices_layer = bm.edges.layers.string[constants.EL_BEAM_INDICES]
            e = bm.edges.new(new_verts)
            e[beam_indices_layer] = bytes('-1', 'utf-8')
            if obj.mode != 'EDIT':
                bm.to_mesh(obj_data)
            export = True

        elif len_selected_verts in (3,4):
            face_idx_layer = bm.faces.layers.int[constants.FL_FACE_IDX]
            f = bm.faces.new(new_verts)
            f[face_idx_layer] = -1
            if obj.mode != 'EDIT':
                bm.to_mesh(obj_data)
            export = True

        bm.free()

        if export:
            state._force_do_export = True

        return {'FINISHED'}


# Flip JBeam faces
class JBEAM_EDITOR_OT_flip_jbeam_faces(bpy.types.Operator):
    bl_idname = "jbeam_editor.flip_jbeam_faces"
    bl_label = "Flip Face(s)"

    @classmethod
    def poll(cls, context):
        return len(state.selected_tris_quads) > 0

    def invoke(self, context, event):
        obj = context.active_object
        obj_data = obj.data
        bm = bmesh.from_edit_mesh(obj_data)
        face_flip_flag_layer = bm.faces.layers.int[constants.FL_FACE_FLIP_FLAG]

        face: bmesh.types.BMFace
        face_idx: int
        for (face, face_idx) in state.selected_tris_quads:
            face[face_flip_flag_layer] = 1

        bm.free()

        state._force_do_export = True

        return {'FINISHED'}

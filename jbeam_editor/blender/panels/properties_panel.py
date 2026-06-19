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


class JBEAM_EDITOR_PT_jbeam_properties_panel(bpy.types.Panel):
    bl_parent_id = "JBEAM_EDITOR_PT_jbeam_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'JBeam'
    bl_label = 'Properties'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        col = box.column()

        obj = context.active_object
        if not obj:
            return
        obj_data = obj.data
        if not isinstance(obj_data, bpy.types.Mesh):
            return
        veh_model = obj_data.get(constants.MESH_VEHICLE_MODEL)

        if obj.mode != 'EDIT':
            return

        bm = bmesh.from_edit_mesh(obj_data)

        if len(state.selected_nodes) == 1:
            if state.curr_vdata is not None and 'nodes' in state.curr_vdata:
                vert_data = state.selected_nodes[0]
                v, node_id = vert_data[0], vert_data[1]

                if node_id in state.curr_vdata['nodes']:
                    node = state.curr_vdata['nodes'][node_id]

                    for k in sorted(node.keys(), key=lambda x: str(x)):
                        val = node[k]
                        str_val = repr(val)
                        col.row().label(text=f'- {k}: {str_val}')

        elif len(state.selected_beams) == 1:
            if state.curr_vdata is not None and 'beams' in state.curr_vdata:
                edge_data = state.selected_beams[0]
                e, beam_indices = edge_data[0], edge_data[1]
                part_origin_layer = bm.edges.layers.string[constants.EL_BEAM_PART_ORIGIN]
                part_origin = e[part_origin_layer].decode('utf-8')
                beam_idx = int(beam_indices.split(',')[0])

                exist = False
                i = 0
                if veh_model is not None:
                    for i, b in enumerate(state.curr_vdata['beams']):
                        if b['partOrigin'] == part_origin:
                            exist = True
                            break
                else:
                    exist = True

                global_beam_idx = i + beam_idx - 1
                if exist and global_beam_idx < len(state.curr_vdata['beams']):
                    beam = state.curr_vdata['beams'][global_beam_idx]

                    for k in sorted(beam.keys(), key=lambda x: str(x)):
                        val = beam[k]
                        str_val = repr(val)
                        col.row().label(text=f'- {k}: {str_val}')

        elif len(state.selected_tris_quads) == 1:
            if state.curr_vdata is not None:
                face_data = state.selected_tris_quads[0]
                f, face_indices = face_data[0], face_data[1]
                num_verts = len(f.verts)

                face_type = None

                if num_verts == 3:
                    face_type = 'triangles'
                elif num_verts == 4:
                    face_type = 'quads'

                if face_type in state.curr_vdata:
                    face_idx_layer = bm.faces.layers.int[constants.FL_FACE_IDX]
                    part_origin_layer = bm.faces.layers.string[constants.FL_FACE_PART_ORIGIN]

                    face_idx = f[face_idx_layer]
                    part_origin = f[part_origin_layer].decode('utf-8')

                    exist = False
                    i = 0
                    if veh_model is not None:
                        for i, b in enumerate(state.curr_vdata[face_type]):
                            if b['partOrigin'] == part_origin:
                                exist = True
                                break
                    else:
                        exist = True

                    global_face_idx = i + face_idx - 1
                    if exist and global_face_idx < len(state.curr_vdata[face_type]):
                        face = state.curr_vdata[face_type][global_face_idx]

                        for k in sorted(face.keys(), key=lambda x: str(x)):
                            val = face[k]
                            str_val = repr(val)
                            col.row().label(text=f'- {k}: {str_val}')

        bm.free()

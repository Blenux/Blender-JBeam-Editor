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

import base64
from pathlib import Path
import pickle
import traceback
import sys # Added for printing warnings

import bpy
import bmesh
from mathutils import Vector

from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty
from bpy.types import Operator

from . import constants
from . import utils
from . import text_editor

from .jbeam import io as jbeam_io
from .jbeam import table_schema as jbeam_table_schema
from .jbeam import node_beam as jbeam_node_beam

_jbeam_file_path = None
_jbeam_file_data = None
_jbeam_part_choices = None


def get_vertices_edges_faces(vdata: dict):

    node_index_to_id = []
    node_id_to_index = {}

    vertices = []
    edges = []
    tris = []
    quads = []

    node_index_to_id_append = node_index_to_id.append
    node_index_to_id_extend = node_index_to_id.extend
    vertices_append = vertices.append
    vertices_extend = vertices.extend
    edges_append = edges.append
    tris_append = tris.append
    quads_append = quads.append

    # Process nodes section
    if 'nodes' in vdata:
        nodes: dict[str, dict] = vdata['nodes']

        if 'triangles' in vdata:
            for tri in vdata['triangles']:
                # Use .get() for safety >>>
                part_origin = tri.get('partOrigin') # Get part origin if available
                ids = (tri['id1:'], tri['id2:'], tri['id3:'])
                if len(set(ids)) == 3 and all(x in nodes for x in ids):
                    n1, n2, n3 = nodes[ids[0]], nodes[ids[1]], nodes[ids[2]]

                    vert_idx = len(node_index_to_id)
                    vert_idxs = (vert_idx, vert_idx + 1, vert_idx + 2)
                    node_index_to_id_extend((ids[0], ids[1], ids[2]))
                    vertices_extend(((n1['pos'], 1), (n2['pos'], 1), (n3['pos'], 1)))

                    tris_append(vert_idxs)
                else:
                    tris_append(None)

        # Translate quads to faces
        if 'quads' in vdata:
            for quad in vdata['quads']:
                # Use .get() for safety >>>
                part_origin = quad.get('partOrigin') # Get part origin if available
                ids = (quad['id1:'], quad['id2:'], quad['id3:'], quad['id4:'])
                if len(set(ids)) == 4 and all(x in nodes for x in ids):
                    n1, n2, n3, n4 = nodes[ids[0]], nodes[ids[1]], nodes[ids[2]], nodes[ids[3]]

                    vert_idx = len(node_index_to_id)
                    vert_idxs = (vert_idx, vert_idx + 1, vert_idx + 2, vert_idx + 3)
                    node_index_to_id_extend((ids[0], ids[1], ids[2], ids[3]))
                    vertices_extend(((n1['pos'], 1), (n2['pos'], 1), (n3['pos'], 1), (n4['pos'], 1)))

                    quads_append(vert_idxs)
                else:
                    quads_append(None)

        # Translate nodes to vertices
        for node_id, node in nodes.items():
            node_index_to_id_append(node_id)
            node_id_to_index[node_id] = len(vertices)
            vertices_append((node['pos'], 0))

        # Translate beams to edges
        if 'beams' in vdata:
            for beam in vdata['beams']:
                # Use .get() for safety >>>
                part_origin = beam.get('partOrigin') # Get part origin if available
                ids = (beam['id1:'], beam['id2:'])
                if len(set(ids)) == 2 and all(x in nodes for x in ids):
                    edge_tup_sorted = tuple(sorted(ids))
                    edges_append((node_id_to_index[edge_tup_sorted[0]], node_id_to_index[edge_tup_sorted[1]]))
                else:
                    edges_append(None)

    return vertices, edges, tris, quads, node_index_to_id


def generate_part_mesh(obj: bpy.types.Object, obj_data: bpy.types.Mesh, bm: bmesh.types.BMesh, vdata: dict, part: str, jbeam_file_path: str, vertices: list, edges: list, tris: list, quads: list, node_index_to_id: list):
    bm_verts = bm.verts
    bm_verts_new = bm_verts.new
    bm_edges = bm.edges
    bm_edges_new = bm_edges.new
    bm_faces = bm.faces
    bm_faces_new = bm_faces.new

    # Add node ID field to all vertices
    init_node_id_layer = bm_verts.layers.string.new(constants.VL_INIT_NODE_ID)
    node_id_layer = bm_verts.layers.string.new(constants.VL_NODE_ID)
    node_origin_layer = bm_verts.layers.string.new(constants.VL_NODE_PART_ORIGIN)
    node_is_fake_layer = bm_verts.layers.int.new(constants.VL_NODE_IS_FAKE)

    beam_origin_layer = bm_edges.layers.string.new(constants.EL_BEAM_PART_ORIGIN)
    beam_indices_layer = bm_edges.layers.string.new(constants.EL_BEAM_INDICES)

    face_origin_layer = bm_faces.layers.string.new(constants.FL_FACE_PART_ORIGIN)
    face_idx_layer = bm_faces.layers.int.new(constants.FL_FACE_IDX)
    face_flip_flag_layer = bm_faces.layers.int.new(constants.FL_FACE_FLIP_FLAG)

    inv_matrix_world = obj.matrix_world.inverted()
    bytes_part = bytes(part, 'utf-8')
    transformed_positions = {}

    # Ensure 'nodes' exists before iterating >>>
    if 'nodes' in vdata:
        nodes: dict[str, dict] = vdata['nodes']
        for i, (pos, is_fake) in enumerate(vertices):
            node_id = node_index_to_id[i]
            if node_id not in transformed_positions:
                transformed_positions[node_id] = inv_matrix_world @ Vector(pos)
            v = bm_verts_new(transformed_positions[node_id])
            bytes_node_id = bytes(node_id, 'utf-8')
            v[init_node_id_layer] = bytes_node_id
            v[node_id_layer] = bytes_node_id
            # Use .get() for safety, default to current part >>>
            node_part_origin = nodes.get(node_id, {}).get('partOrigin', part)
            v[node_origin_layer] = bytes(node_part_origin, 'utf-8')
            v[node_is_fake_layer] = is_fake

    bm_verts.ensure_lookup_table()

    added_edges = {}

    for i, edge in enumerate(edges, 1):
        if edge is not None:
            if not edge in added_edges:
                # Check if vertices exist before creating edge >>>
                try:
                    v1 = bm_verts[edge[0]]
                    v2 = bm_verts[edge[1]]
                    e = bm_edges_new((v1, v2))
                    e[beam_indices_layer] = bytes(f'{i}', 'utf-8')
                    e[beam_origin_layer] = bytes_part
                    added_edges[edge] = e
                except IndexError:
                    print(f"Warning: Vertex index out of range for edge {i}. Skipping edge.", file=sys.stderr)
            else:
                e = added_edges[edge]
                last_indices = e[beam_indices_layer].decode('utf-8')
                e[beam_indices_layer] = bytes(f'{last_indices},{i}', 'utf-8')

    for i, tri in enumerate(tris, 1):
        if tri is not None:
            # Check if vertices exist before creating face >>>
            try:
                v1 = bm_verts[tri[0]]
                v2 = bm_verts[tri[1]]
                v3 = bm_verts[tri[2]]
                f = bm_faces_new((v1, v2, v3))
                f[face_idx_layer] = i
                f[face_origin_layer] = bytes_part
            except IndexError:
                print(f"Warning: Vertex index out of range for triangle {i}. Skipping triangle.", file=sys.stderr)

    for i, quad in enumerate(quads, 1):
        if quad is not None:
            # Check if vertices exist before creating face >>>
            try:
                v1 = bm_verts[quad[0]]
                v2 = bm_verts[quad[1]]
                v3 = bm_verts[quad[2]]
                v4 = bm_verts[quad[3]]
                f = bm_faces_new((v1, v2, v3, v4))
                f[face_idx_layer] = i
                f[face_origin_layer] = bytes_part
            except IndexError:
                print(f"Warning: Vertex index out of range for quad {i}. Skipping quad.", file=sys.stderr)

    obj_data[constants.MESH_JBEAM_PART] = part
    obj_data[constants.MESH_JBEAM_FILE_PATH] = jbeam_file_path
    obj_data[constants.MESH_VERTEX_COUNT] = len(bm_verts)
    obj_data[constants.MESH_EDGE_COUNT] = len(bm_edges)
    obj_data[constants.MESH_FACE_COUNT] = len(bm_faces)
    obj_data[constants.MESH_EDITING_ENABLED] = True


def import_jbeam_part(context: bpy.types.Context, jbeam_file_path: str, jbeam_file_data: dict, chosen_part: str):
    ui_props = context.scene.ui_properties
    try:
        # Prevent overriding a jbeam part that already exists in scene!
        jbeam_collection: bpy.types.Collection | None = bpy.data.collections.get('JBeam Objects')
        if jbeam_collection is not None:
            if jbeam_collection.all_objects.get(chosen_part) is not None:
                raise Exception(f'{chosen_part} already exists in scene!')

        part_data = jbeam_file_data[chosen_part]
        if not jbeam_table_schema.process(part_data):
            raise Exception('JBeam processing error.')
        if not jbeam_table_schema.post_process(part_data):
            raise Exception('JBeam processing error.')
        jbeam_node_beam.process(part_data)

        # Ensure partOrigin is set for single part import >>>
        if 'beams' in part_data:
            beams_updated_count = 0
            for beam in part_data['beams']:
                # Check if it's missing or None
                if beam.get('partOrigin') is None:
                    beam['partOrigin'] = chosen_part
                    beams_updated_count += 1
            # Commented out the print statement
            # if beams_updated_count > 0:
            #      print(f"Assigned missing 'partOrigin' to {beams_updated_count} beams in '{chosen_part}'.")

        vertices, edges, tris, quads, node_ids = get_vertices_edges_faces(part_data)

        obj_data = bpy.data.meshes.new(chosen_part)
        #export_jbeam.last_exported_jbeams[chosen_part] = {'in_filepath': jbeam_file_path}

        # make object from mesh
        obj = bpy.data.objects.new(chosen_part, obj_data)

        bm = bmesh.new()
        # Removed bm.from_mesh(obj_data) as it's a new mesh >>>
        # bm.from_mesh(obj_data)
        generate_part_mesh(obj, obj_data, bm, part_data, chosen_part, jbeam_file_path, vertices, edges, tris, quads, node_ids)
        bm.to_mesh(obj_data)
        bm.free() # Free bmesh after use >>>

        obj_data.update()
        obj_data[constants.MESH_SINGLE_JBEAM_PART_DATA] = base64.b64encode(pickle.dumps(part_data, -1)).decode('ascii')

        # make collection
        jbeam_collection = bpy.data.collections.get('JBeam Objects')
        if jbeam_collection is None:
            jbeam_collection = bpy.data.collections.new('JBeam Objects')
            context.scene.collection.children.link(jbeam_collection)

        # add object to scene collection
        jbeam_collection.objects.link(obj)

        # print('Done importing JBeam.') # <<< REMOVED: Reduce console spam
        # utils.show_message_box('INFO', 'Import JBeam', 'Done importing JBeam.') # <<< REMOVED: Reduce popups during folder import
        return True
    except Exception as ex:
        tb = traceback.TracebackException.from_exception(ex, capture_locals=True)
        print("".join(tb.format()))
        utils.show_message_box('ERROR', 'Import JBeam', 'ERROR importing JBeam. Check the "System Console" for details.')
        return False


def reimport_jbeam(context: bpy.types.Context, jbeam_objects: bpy.types.Collection, obj: bpy.types.Object, jbeam_file_path: str, regenerate_mesh: bool):
    obj_data: bpy.types.Mesh = obj.data
    part_data = None # Initialize part_data to None
    ui_props = context.scene.ui_properties
    try:
        # Reimport object
        jbeam_file_data, cached_changed = jbeam_io.get_jbeam(jbeam_file_path, True, True)
        if jbeam_file_data is None:
            raise Exception('Failed to load/parse JBeam file.')

        chosen_part = obj_data[constants.MESH_JBEAM_PART]
        part_data = jbeam_file_data[chosen_part]

        if not jbeam_table_schema.process(part_data):
            raise Exception('JBeam processing error.')
        if not jbeam_table_schema.post_process(part_data):
            raise Exception('JBeam processing error.')
        jbeam_node_beam.process(part_data)

        # Ensure partOrigin is set for single part reimport >>>
        if 'beams' in part_data:
            beams_updated_count = 0
            for beam in part_data['beams']:
                 # Check if it's missing or None
                if beam.get('partOrigin') is None:
                    beam['partOrigin'] = chosen_part
                    beams_updated_count += 1
            # Commented out the print statement
            # if beams_updated_count > 0:
            #      print(f"Assigned missing 'partOrigin' to {beams_updated_count} beams in '{chosen_part}' during reimport.")

        if regenerate_mesh:
            vertices, edges, tris, quads, node_ids = get_vertices_edges_faces(part_data)

            # Store hidden edge AND VERTEX states ---
            hidden_edges_state = {} # <<< ADDED: Dictionary to store edge hidden states
            hidden_verts_state = {} # <<< ADDED
            hidden_faces_state = {} # <<< ADDED: Dictionary to store face hidden states
            # <<< ADDED: Store selection states >>>
            selected_node_ids_to_restore = set()
            selected_beam_defining_node_ids_to_restore = set()
            selected_face_defining_node_ids_to_restore = set()
            was_in_edit_mode_and_active = obj.mode == 'EDIT' # Store if we were in edit mode for this specific object
            temp_bm = None
            try:
                if obj.mode == 'EDIT':
                    # Get bmesh from edit mesh *before* clearing
                    temp_bm = bmesh.from_edit_mesh(obj_data)
                else:
                    # Get bmesh from object data *before* clearing
                    # No selection to store if not in edit mode for this object
                    temp_bm = bmesh.new()
                    temp_bm.from_mesh(obj_data)

                # --- Store Selection State (if in Edit Mode for this object) ---
                if was_in_edit_mode_and_active:
                    node_id_layer_sel_store = temp_bm.verts.layers.string.get(constants.VL_NODE_ID)
                    if node_id_layer_sel_store:
                        temp_bm.verts.ensure_lookup_table()
                        for v_store in temp_bm.verts:
                            if v_store.select:
                                selected_node_ids_to_restore.add(v_store[node_id_layer_sel_store].decode('utf-8'))

                        temp_bm.edges.ensure_lookup_table()
                        for e_store in temp_bm.edges:
                            if e_store.select and len(e_store.verts) == 2:
                                v1_id = e_store.verts[0][node_id_layer_sel_store].decode('utf-8')
                                v2_id = e_store.verts[1][node_id_layer_sel_store].decode('utf-8')
                                selected_beam_defining_node_ids_to_restore.add(frozenset({v1_id, v2_id}))
                        temp_bm.faces.ensure_lookup_table()
                        for f_store in temp_bm.faces:
                            if f_store.select:
                                face_node_ids = frozenset({v[node_id_layer_sel_store].decode('utf-8') for v in f_store.verts})
                                selected_face_defining_node_ids_to_restore.add(face_node_ids)
                # --- Store Edge Hidden State (Revised) ---
                node_id_layer_v_store = temp_bm.verts.layers.string.get(constants.VL_NODE_ID)
                is_fake_layer_v_store = temp_bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
                beam_indices_layer_e_store = temp_bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
                beam_origin_layer_e_store = temp_bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)

                if node_id_layer_v_store and is_fake_layer_v_store and beam_indices_layer_e_store and beam_origin_layer_e_store:
                    temp_bm.edges.ensure_lookup_table()
                    temp_bm.verts.ensure_lookup_table() # For accessing edge.verts
                    for edge in temp_bm.edges:
                        indices_str = edge[beam_indices_layer_e_store].decode('utf-8')
                        if indices_str and indices_str != '-1': # Check if it's a JBeam beam
                            v1, v2 = edge.verts[0], edge.verts[1]
                            if v1[is_fake_layer_v_store] == 0 and v2[is_fake_layer_v_store] == 0: # Check if nodes are real
                                try:
                                    node_id1 = v1[node_id_layer_v_store].decode('utf-8')
                                    node_id2 = v2[node_id_layer_v_store].decode('utf-8')
                                    part_origin = edge[beam_origin_layer_e_store].decode('utf-8')
                                    key = (part_origin, frozenset({node_id1, node_id2}))
                                    hidden_edges_state[key] = edge.hide
                                except Exception as e_store_err:
                                    print(f"Warning: Could not store hidden state for edge ({node_id1 if 'node_id1' in locals() else '?'}-{node_id2 if 'node_id2' in locals() else '?'}): {e_store_err}", file=sys.stderr)
                # --- End Store Edge Hidden State (Revised) ---
                # Store Vertex Hidden State ---
                node_id_layer = temp_bm.verts.layers.string.get(constants.VL_NODE_ID)
                # Get node origin layer for vertex state key >>>
                node_origin_layer = temp_bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN)
                is_fake_layer = temp_bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
                # Check node_origin_layer exists >>>
                if node_id_layer and node_origin_layer and is_fake_layer:
                    temp_bm.verts.ensure_lookup_table()
                    for vert in temp_bm.verts:
                        if vert[is_fake_layer] == 0: # Only store real nodes
                            node_id = vert[node_id_layer].decode('utf-8')
                            # Use part_origin from vertex layer for key >>>
                            part_origin = vert[node_origin_layer].decode('utf-8')
                            hidden_verts_state[(part_origin, node_id)] = vert.hide

                # Store Face Hidden State --- <<< ADDED >>>
                face_origin_layer_store = temp_bm.faces.layers.string.get(constants.FL_FACE_PART_ORIGIN)
                # Use the same node_id_layer and is_fake_layer as for vertices/edges for consistency
                if face_origin_layer_store and node_id_layer_v_store and is_fake_layer_v_store:
                    temp_bm.faces.ensure_lookup_table()
                    temp_bm.verts.ensure_lookup_table() # Ensure verts table for face.verts
                    for face in temp_bm.faces:
                        try:
                            part_origin = face[face_origin_layer_store].decode('utf-8')
                            node_ids_for_face = []
                            all_verts_real_and_valid = True
                            for v_face in face.verts:
                                if v_face[is_fake_layer_v_store] != 0:
                                    all_verts_real_and_valid = False
                                    break
                                node_id_bytes = v_face[node_id_layer_v_store]
                                if not node_id_bytes or node_id_bytes.decode('utf-8').startswith('TEMP_'): # Skip if TEMP_
                                    all_verts_real_and_valid = False
                                    break
                                node_ids_for_face.append(node_id_bytes.decode('utf-8'))

                            if all_verts_real_and_valid and len(node_ids_for_face) >= 3: # Faces must have at least 3 verts
                                key = (part_origin, frozenset(node_ids_for_face))
                                hidden_faces_state[key] = face.hide
                        except Exception as face_state_err:
                            print(f"Warning: Could not store hidden state for face defined by nodes {node_ids_for_face if 'node_ids_for_face' in locals() else '?'}: {face_state_err}", file=sys.stderr)
                # --- End Store Face Hidden State --- <<< END ADDED >>>

            except Exception as e:
                 print(f"Error storing hidden states: {e}", file=sys.stderr) # Combined error message
            finally:
                if temp_bm and obj.mode != 'EDIT': # Free temp bmesh if it wasn't the edit mesh
                    temp_bm.free()
            # Store hidden states ---

            # Now get the main bm and clear it
            if obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(obj_data)
                bm.clear()
            else:
                bm = bmesh.new()
                bm.from_mesh(obj_data)
                bm.clear()

            # Generate the new mesh content
            generate_part_mesh(obj, obj_data, bm, part_data, chosen_part, jbeam_file_path, vertices, edges, tris, quads, node_ids)

            # Apply hidden edge AND VERTEX states ---
            # --- Apply Edge Hidden State (Revised) ---
            node_id_layer_v_apply = bm.verts.layers.string.get(constants.VL_NODE_ID)
            is_fake_layer_v_apply = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
            beam_indices_layer_e_apply = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
            beam_origin_layer_e_apply = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)

            if node_id_layer_v_apply and is_fake_layer_v_apply and beam_indices_layer_e_apply and beam_origin_layer_e_apply and hidden_edges_state:
                bm.edges.ensure_lookup_table()
                bm.verts.ensure_lookup_table()
                for edge in bm.edges:
                    indices_str = edge[beam_indices_layer_e_apply].decode('utf-8')
                    if indices_str and indices_str != '-1': # Check if it's a JBeam beam
                        v1, v2 = edge.verts[0], edge.verts[1]
                        if v1[is_fake_layer_v_apply] == 0 and v2[is_fake_layer_v_apply] == 0: # Check if nodes are real
                            try:
                                node_id1 = v1[node_id_layer_v_apply].decode('utf-8')
                                node_id2 = v2[node_id_layer_v_apply].decode('utf-8')
                                part_origin = edge[beam_origin_layer_e_apply].decode('utf-8')
                                key = (part_origin, frozenset({node_id1, node_id2}))
                                if key in hidden_edges_state:
                                    edge.hide = hidden_edges_state[key]
                            except Exception as e_apply_err:
                                print(f"Warning: Could not apply hidden state for edge ({node_id1 if 'node_id1' in locals() else '?'}-{node_id2 if 'node_id2' in locals() else '?'}): {e_apply_err}", file=sys.stderr)
            # --- End Apply Edge Hidden State (Revised) ---
            # Apply Vertex Hidden State ---
            node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
            # Get node origin layer for vertex state key >>>
            node_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN)
            is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
            # Check node_origin_layer exists >>>
            if node_id_layer and node_origin_layer and is_fake_layer and hidden_verts_state:
                bm.verts.ensure_lookup_table()
                for vert in bm.verts:
                     if vert[is_fake_layer] == 0: # Only apply to real nodes
                        node_id = vert[node_id_layer].decode('utf-8')
                        # Use part_origin from vertex layer for key >>>
                        part_origin = vert[node_origin_layer].decode('utf-8')
                        if (part_origin, node_id) in hidden_verts_state:
                            vert.hide = hidden_verts_state[(part_origin, node_id)]

            # Apply Face Hidden State --- <<< ADDED >>>
            face_origin_layer_apply = bm.faces.layers.string.get(constants.FL_FACE_PART_ORIGIN)
            # Use the same node_id_layer and is_fake_layer as for vertices/edges for consistency
            if face_origin_layer_apply and node_id_layer and is_fake_layer and hidden_faces_state:
                bm.faces.ensure_lookup_table()
                bm.verts.ensure_lookup_table() # Ensure verts table for face.verts
                for face in bm.faces:
                    try:
                        part_origin = face[face_origin_layer_apply].decode('utf-8')
                        node_ids_for_face = []
                        all_verts_real_and_valid = True
                        for v_face in face.verts:
                            if v_face[is_fake_layer] != 0:
                                all_verts_real_and_valid = False
                                break
                            node_id_bytes = v_face[node_id_layer]
                            if not node_id_bytes or node_id_bytes.decode('utf-8').startswith('TEMP_'): # Skip if TEMP_
                                all_verts_real_and_valid = False
                                break
                            node_ids_for_face.append(node_id_bytes.decode('utf-8'))
                        if all_verts_real_and_valid and len(node_ids_for_face) >= 3:
                            key = (part_origin, frozenset(node_ids_for_face))
                            if key in hidden_faces_state:
                                face.hide = hidden_faces_state[key]
                    except Exception as apply_face_state_err:
                        print(f"Warning: Could not apply hidden state for face defined by nodes {node_ids_for_face if 'node_ids_for_face' in locals() else '?'}: {apply_face_state_err}", file=sys.stderr)
            # --- End Apply Face Hidden State --- <<< END ADDED >>>

            # <<< ADDED: Apply selection states >>>
            if was_in_edit_mode_and_active: # Only restore if we were in edit mode for this object
                node_id_layer_apply_sel = bm.verts.layers.string.get(constants.VL_NODE_ID)
                if node_id_layer_apply_sel:
                    bm.verts.ensure_lookup_table()
                    for v_apply in bm.verts:
                        v_apply.select_set(v_apply[node_id_layer_apply_sel].decode('utf-8') in selected_node_ids_to_restore)

                    bm.edges.ensure_lookup_table()
                    for e_apply in bm.edges:
                        if len(e_apply.verts) == 2:
                            v1_id = e_apply.verts[0][node_id_layer_apply_sel].decode('utf-8')
                            v2_id = e_apply.verts[1][node_id_layer_apply_sel].decode('utf-8')
                            e_apply.select_set(frozenset({v1_id, v2_id}) in selected_beam_defining_node_ids_to_restore)
                        else:
                            e_apply.select_set(False)

                    bm.faces.ensure_lookup_table()
                    for f_apply in bm.faces:
                        face_node_ids = frozenset({v[node_id_layer_apply_sel].decode('utf-8') for v in f_apply.verts})
                        f_apply.select_set(face_node_ids in selected_face_defining_node_ids_to_restore)

                # Crucial for Edit Mode selection to update visually and for handlers
                bm.select_flush_mode()
            # <<< END ADDED >>>

            bm.normal_update()

            # Write bmesh back to Blender mesh
            if obj.mode == 'EDIT':
                bmesh.update_edit_mesh(obj_data)
            else:
                bm.to_mesh(obj_data)
            bm.free() # Free the bmesh used for generation/modification
            obj_data.update()

        # Update the stored JBeam data regardless of mesh regeneration
        # Ensure part_data exists before trying to dump/encode it
        if 'part_data' in locals() and part_data is not None:
             obj_data[constants.MESH_SINGLE_JBEAM_PART_DATA] = base64.b64encode(pickle.dumps(part_data, -1)).decode('ascii')
        else:
            # If parsing/processing failed severely, part_data might not exist
            # Clear the bundle in this case too
            if constants.MESH_SINGLE_JBEAM_PART_DATA in obj_data:
                try:
                    # Set to encoded None to ensure safe failure on next load
                    obj_data[constants.MESH_SINGLE_JBEAM_PART_DATA] = base64.b64encode(pickle.dumps(None, -1)).decode('ascii')
                    print("Cleared single part data due to processing failure before assignment.")
                except Exception as clear_err:
                    print(f"Error clearing single part data after processing failure: {clear_err}", file=sys.stderr)

        context.scene['jbeam_editor_reimporting_jbeam'] = 1 # Prevents exporting jbeam

        if ui_props.show_console_warnings_missing_nodes: print('Done reimporting JBeam.')
        return True
    except Exception as e: # Catch potential exceptions during the process
        # On error reimporting jbeam, remove mesh data
        traceback.print_exc()

        # Attempt to clear mesh data safely
        try:
            if obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(obj_data)
                bm.clear()
                bmesh.update_edit_mesh(obj_data) # Update edit mesh after clearing
                bm.free() # Free edit bmesh
            else:
                bm = bmesh.new()
                bm.from_mesh(obj_data)
                bm.clear()
                bm.to_mesh(obj_data) # Write cleared mesh back
                bm.free() # Free temp bmesh
            obj_data.update()
        except Exception as clear_error:
            print(f"Error clearing mesh data after reimport failure: {clear_error}", file=sys.stderr)

        # Clear the data bundle on any failure >>>
        if constants.MESH_SINGLE_JBEAM_PART_DATA in obj_data:
            try:
                # Set to encoded None to ensure safe failure on next load
                obj_data[constants.MESH_SINGLE_JBEAM_PART_DATA] = base64.b64encode(pickle.dumps(None, -1)).decode('ascii')
                print("Cleared single part data due to reimport error.")
            except Exception as clear_err:
                print(f"Error clearing single part data: {clear_err}", file=sys.stderr)

        obj_data[constants.MESH_EDITING_ENABLED] = False # Disable editing on failure

        return False


def on_file_change(context: bpy.types.Context, filename: str, regenerate_mesh: bool):
    jbeam_objects: bpy.types.Collection | None = bpy.data.collections.get('JBeam Objects')
    if jbeam_objects is None:
        return

    for obj in jbeam_objects.all_objects[:]:
        if obj is None:
            continue
        obj_data = obj.data
        if obj_data is None:
            continue

        jbeam_filepath = obj_data.get(constants.MESH_JBEAM_FILE_PATH)
        if jbeam_filepath is None or jbeam_filepath != filename:
            continue

        reimport_jbeam(context, jbeam_objects, obj, filename, regenerate_mesh)


class JBEAM_EDITOR_OT_choose_jbeam(Operator):
    bl_idname = 'jbeam_editor.choose_jbeam'
    bl_label = 'Choose JBeam'
    bl_description = 'Choose the JBeam part to import'
    bl_options = {'REGISTER', 'UNDO'}

    def part_choices_for_enum_property(self, context):
        arr = []

        # Check if _jbeam_part_choices is None >>>
        if _jbeam_part_choices is None:
            return arr

        for x in _jbeam_part_choices:
            arr.append((x,x,''))

        return arr

    import_all_parts: BoolProperty(
        name='Import All Parts',
        description='',
        default = False,
    )

    dropdown_parts: bpy.props.EnumProperty(
        name='Select a Part',
        description='',
        default=None,
        items=part_choices_for_enum_property,
    )

    # User clicked OK, JBeam part is chosen
    def execute(self, context):
        # Check if choices/data are None >>>
        if _jbeam_part_choices is None or _jbeam_file_data is None or _jbeam_file_path is None:
            utils.show_message_box('ERROR', 'Choose JBeam Part', 'Internal error: JBeam data not loaded.')
            return {'CANCELLED'}

        chosen_part = self.dropdown_parts

        if self.import_all_parts:
            for part in _jbeam_part_choices:
                res = import_jbeam_part(context, _jbeam_file_path, _jbeam_file_data, part)
                if not res:
                    return {'CANCELLED'}
        else:
            # Ensure a part was actually selected >>>
            if not chosen_part:
                 utils.show_message_box('ERROR', 'Choose JBeam Part', 'No JBeam part selected.')
                 return {'CANCELLED'}
            res = import_jbeam_part(context, _jbeam_file_path, _jbeam_file_data, chosen_part)
            if not res:
                return {'CANCELLED'}

        text_editor.check_int_files_for_changes(context, [_jbeam_file_path], False, False)

        return {'FINISHED'}

    # Show dialog of JBeam parts to choose from after importing JBeam file
    def invoke(self, context, event):
        # Check if choices exist before invoking dialog >>>
        if not _jbeam_part_choices:
            utils.show_message_box('WARNING', 'Import JBeam', 'No JBeam parts found in the selected file.')
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)


class JBEAM_EDITOR_OT_import_jbeam(Operator, ImportHelper):
    bl_idname = 'jbeam_editor.import_jbeam'
    bl_label = 'Import JBeam'
    bl_description = 'Import a BeamNG JBeam file'
    filename_ext = ".jbeam"

    filter_glob: StringProperty(
        default="*.jbeam",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    set_chosen_part: BoolProperty(
        default = False,
        options={'HIDDEN'},
    )

    chosen_part: StringProperty(
        default = '',
        options={'HIDDEN'},
    )

    import_all_parts: BoolProperty(
        name='Import All Parts',
        description='',
        default = False,
        # Removed HIDDEN option so it shows in file browser >>>
        #options={'HIDDEN'},
    )

    def execute(self, context):
        global _jbeam_file_path
        global _jbeam_file_data
        global _jbeam_part_choices

        _jbeam_file_path = Path(self.filepath).as_posix()
        _jbeam_file_data, cached_changed = jbeam_io.get_jbeam(_jbeam_file_path, False, True)

        if _jbeam_file_data is None:
            utils.show_message_box('ERROR', 'Import JBeam', 'ERROR importing JBeam. Check the "System Console" for details.')
            return {'CANCELLED'}

        # # Set from unit tests
        if self.set_chosen_part:
            res = import_jbeam_part(context, _jbeam_file_path, _jbeam_file_data, self.chosen_part)
            if not res:
                return {'CANCELLED'}

            text_editor.check_int_files_for_changes(context, [_jbeam_file_path], False, False)
            return {'FINISHED'}

        part_choices = []
        for k in _jbeam_file_data.keys():
            part_choices.append(k)

        _jbeam_part_choices = part_choices

        # Check if part_choices is empty >>>
        if not part_choices:
            utils.show_message_box('WARNING', 'Import JBeam', 'No JBeam parts found in the selected file.')
            _jbeam_file_path = None # Clear global state
            _jbeam_file_data = None
            return {'CANCELLED'}

        if self.import_all_parts:
            for part in _jbeam_part_choices:
                res = import_jbeam_part(context, _jbeam_file_path, _jbeam_file_data, part)
                if not res:
                    # Clear global state on failure
                    _jbeam_file_path = None
                    _jbeam_file_data = None
                    _jbeam_part_choices = None
                    return {'CANCELLED'}
            # Clear global state on success
            _jbeam_file_path = None
            _jbeam_file_data = None
            _jbeam_part_choices = None
            return {'FINISHED'}

        # If only one part, import it directly without dialog
        if len(_jbeam_part_choices) == 1:
            res = import_jbeam_part(context, _jbeam_file_path, _jbeam_file_data, _jbeam_part_choices[0])
            if not res:
                 # Clear global state on failure
                _jbeam_file_path = None
                _jbeam_file_data = None
                _jbeam_part_choices = None
                return {'CANCELLED'}
            text_editor.check_int_files_for_changes(context, [_jbeam_file_path], False, False)
             # Clear global state on success
            _jbeam_file_path = None
            _jbeam_file_data = None
            _jbeam_part_choices = None
            return {'FINISHED'}

        # Otherwise, show the dialog
        bpy.ops.jbeam_editor.choose_jbeam('INVOKE_DEFAULT')

        return {'FINISHED'}

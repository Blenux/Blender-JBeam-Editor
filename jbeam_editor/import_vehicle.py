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
import re
import sys # Make sure sys is imported if not already
import pickle
import traceback

import bpy
import bmesh
from mathutils import Vector

from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty
from bpy.types import Operator

from . import constants
from . import utils
from . import text_editor

from .jbeam import io as jbeam_io
from .jbeam import slot_system as jbeam_slot_system
from .jbeam import variables as jbeam_variables
from .jbeam import table_schema as jbeam_table_schema
from .jbeam import node_beam as jbeam_node_beam

import timeit


def load_vehicle(vehicle_directories: list[str], vehicle_config: dict, model_name: str, reimporting_files_changed: dict | None):
    """load all the jbeam and construct the thing in memory"""
    print('Reading JBeam files...')
    t0 = timeit.default_timer()
    jbeam_parsing_errors, io_ctx = jbeam_io.start_loading(vehicle_directories, vehicle_config, reimporting_files_changed)
    t1 = timeit.default_timer()
    print('Done reading JBeam files. Time =', round(t1 - t0, 2), 's')
    print('Model name:', model_name)

    if 'mainPartName' not in vehicle_config:
        vehicle_config['mainPartName'] = jbeam_io.get_main_part_name(io_ctx)

    print('Finding parts...')
    vehicle, unify_journal, chosen_parts, active_parts_orig = jbeam_slot_system.find_parts(io_ctx, vehicle_config)
    if vehicle is None:
        raise Exception('JBeam processing error.')

    # Map parts to JBeam file
    veh_parts = [*chosen_parts.values()]
    veh_part_to_file_map = {}
    veh_files = []
    for directory in vehicle_directories:
        files = jbeam_io.dir_to_files_map.get(directory)
        if files is not None:
            for file in files:
                file_added = False
                if file in jbeam_io.file_to_parts_name_map:
                    parts = jbeam_io.file_to_parts_name_map[file]
                    for part in parts:
                        if part in veh_parts:
                            if part not in veh_part_to_file_map:
                                veh_part_to_file_map[part] = file
                                if not file_added:
                                    veh_files.append(file)
                                    file_added = True

    print('Applying variables...')
    all_variables = jbeam_variables.process_parts(vehicle, unify_journal, vehicle_config)

    print('Unifying parts...')
    jbeam_slot_system.init_unify_parts(vehicle)
    jbeam_slot_system.unify_part_journal(io_ctx, unify_journal)
    jbeam_variables.process_unified_vehicle(vehicle, all_variables)

    print('Assembling tables ...')
    if not jbeam_table_schema.process(vehicle):
        raise Exception('JBeam processing error.')

    # Exclusive to Python vehicle importer
    if not jbeam_table_schema.post_process(vehicle):
        raise Exception('JBeam processing error.')

    jbeam_node_beam.process(vehicle)

    # Attempt to add partOrigin post-hoc >>>
    # This is a workaround assuming jbeam_node_beam.process should have done this.
    # It relies on the 'nodes' still having partOrigin after processing.
    if 'nodes' in vehicle and 'beams' in vehicle:
        # Create a map of node ID to its partOrigin
        node_origins = {nid: data.get('partOrigin') for nid, data in vehicle['nodes'].items() if data.get('partOrigin') is not None}
        beams_updated_count = 0
        beams_missing_origin = 0
        beams_unable_to_fix = 0

        for beam in vehicle['beams']:
            # Check if partOrigin is missing or explicitly None
            if beam.get('partOrigin') is None:
                beams_missing_origin += 1
                inferred_origin = None
                # Try to infer origin from the first node
                node1_id = beam.get('id1:')
                if node1_id:
                    inferred_origin = node_origins.get(node1_id)

                # If first node didn't work, try the second node
                if inferred_origin is None:
                    node2_id = beam.get('id2:')
                    if node2_id:
                        inferred_origin = node_origins.get(node2_id)

                # If we found an origin, assign it
                if inferred_origin is not None:
                    beam['partOrigin'] = inferred_origin
                    beams_updated_count += 1
                else:
                    beams_unable_to_fix += 1
                    # Optional: Log the beam that couldn't be fixed
                    # print(f"Warning: Could not infer partOrigin for beam: {beam.get('id1:', '?')}-{beam.get('id2:', '?')}")

        # Only print a warning if some beams could not be fixed
        if beams_unable_to_fix > 0:
            print(f"Warning: Unable to fix missing 'partOrigin' for {beams_unable_to_fix} beams during vehicle load.")
        # Commented out the original message:
        # if beams_missing_origin > 0:
        #     print(f"Attempted to fix missing 'partOrigin' in {beams_missing_origin} beams. Successfully updated: {beams_updated_count}. Unable to fix: {beams_unable_to_fix}")

    vehicle['vehicleDirectory'] = vehicle_directories[0]
    vehicle['activeParts'] = active_parts_orig
    vehicle['model'] = model_name

    jbeam_io.finish_loading()

    t2 = timeit.default_timer()
    print('Done loading JBeam. Time =', round(t2 - t1, 2), 's')

    return jbeam_parsing_errors, {
        'vehicleDirectory' : vehicle_directories[0],
        'vdata'            : vehicle,
        'config'           : vehicle_config,
        'mainPartName'     : vehicle_config['mainPartName'],
        'chosenParts'      : chosen_parts,
        'partToFileMap'    : veh_part_to_file_map,
        'vehFiles'         : veh_files,
        'ioCtx'            : io_ctx,
    }


def build_config(config_path, reimporting=False):
    res = {}
    pc_filetext = None

    # On importing vehicle, read from disk. On reimporting vehicle, read from internal Blender text.
    if reimporting:
        pc_filetext = text_editor.read_int_file(config_path)
    else:
        pc_filetext = text_editor.write_from_ext_to_int_file(config_path)

    if pc_filetext is None:
        raise Exception("Failed to read .pc file.")

    file_data = utils.sjson_decode(pc_filetext, config_path)
    if not file_data:
        raise Exception("Failed to parse .pc file.")

    res['partConfigFilename'] = config_path
    if file_data.get('format') == 2:
        file_data['format'] = None
        res.update(file_data)
    else:
        res['parts'] = file_data

    return res


def get_vertices_edges_faces(vehicle_bundle: dict):
    vdata = vehicle_bundle['vdata']

    node_index_to_id = []
    node_id_to_index = {}

    vertices = []
    parts_edges = {}
    parts_tris = {}
    parts_quads = {}

    node_index_to_id_append = node_index_to_id.append
    node_index_to_id_extend = node_index_to_id.extend
    vertices_append = vertices.append
    vertices_extend = vertices.extend

    if 'nodes' in vdata:
        nodes: dict[str, dict] = vdata['nodes']

        if 'triangles' in vdata:
            for tri in vdata['triangles']:
                # Use .get() for safety >>>
                part_origin = tri.get('partOrigin') # Get part origin if available
                if part_origin is None: continue # Skip if no origin (shouldn't happen after fix)

                part_blender_tris = parts_tris.setdefault(part_origin, [])

                ids = (tri['id1:'], tri['id2:'], tri['id3:'])
                if len(set(ids)) == 3 and all(x in nodes for x in ids):
                    n1, n2, n3 = nodes[ids[0]], nodes[ids[1]], nodes[ids[2]]

                    vert_idx = len(node_index_to_id)
                    vert_idxs = (vert_idx, vert_idx + 1, vert_idx + 2)
                    node_index_to_id_extend((ids[0], ids[1], ids[2]))
                    vertices_extend(((n1['pos'], 1), (n2['pos'], 1), (n3['pos'], 1)))

                    part_blender_tris.append(vert_idxs)
                else:
                    part_blender_tris.append(None)

        # Translate quads to faces
        if 'quads' in vdata:
            for quad in vdata['quads']:
                 # Use .get() for safety >>>
                part_origin = quad.get('partOrigin') # Get part origin if available
                if part_origin is None: continue # Skip if no origin

                part_blender_quads = parts_quads.setdefault(part_origin, [])

                ids = (quad['id1:'], quad['id2:'], quad['id3:'], quad['id4:'])
                if len(set(ids)) == 4 and all(x in nodes for x in ids):
                    n1, n2, n3, n4 = nodes[ids[0]], nodes[ids[1]], nodes[ids[2]], nodes[ids[3]]

                    vert_idx = len(node_index_to_id)
                    vert_idxs = (vert_idx, vert_idx + 1, vert_idx + 2, vert_idx + 3)
                    node_index_to_id_extend((ids[0], ids[1], ids[2], ids[3]))
                    vertices_extend(((n1['pos'], 1), (n2['pos'], 1), (n3['pos'], 1), (n4['pos'], 1)))

                    part_blender_quads.append(vert_idxs)
                else:
                    part_blender_quads.append(None)

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
                if part_origin is None: continue # Skip if no origin

                edges = parts_edges.setdefault(part_origin, [])

                ids = (beam['id1:'], beam['id2:'])
                if len(set(ids)) == 2 and all(x in nodes for x in ids):
                    edge_tup_sorted = tuple(sorted(ids))
                    edges.append((node_id_to_index[edge_tup_sorted[0]], node_id_to_index[edge_tup_sorted[1]]))
                else:
                    edges.append(None)

    return vertices, parts_edges, parts_tris, parts_quads, node_index_to_id


def generate_part_mesh(obj: bpy.types.Object, obj_data: bpy.types.Mesh, bm: bmesh.types.BMesh, vehicle_bundle: dict, part: str, vertices: list, edges: list, tris: list, quads: list, node_index_to_id: list):
    vdata = vehicle_bundle['vdata']
    vehicle_model = vdata['model']
    jbeam_filepath = vehicle_bundle['partToFileMap'][part]

    bm_verts = bm.verts
    bm_verts_new = bm_verts.new
    bm_edges = bm.edges
    bm_edges_new = bm_edges.new
    bm_faces = bm.faces
    bm_faces_new = bm_faces.new

    # Add node ID field to all vertices/edges/faces
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

    # Ensure 'nodes' exists before iterating >>>
    if 'nodes' in vdata:
        nodes: dict[str, dict] = vdata['nodes']
        transformed_positions = {}

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
                    print(f"Warning: Vertex index out of range for edge {i} in part '{part}'. Skipping edge.", file=sys.stderr)
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
                 print(f"Warning: Vertex index out of range for triangle {i} in part '{part}'. Skipping triangle.", file=sys.stderr)

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
                print(f"Warning: Vertex index out of range for quad {i} in part '{part}'. Skipping quad.", file=sys.stderr)

    obj_data[constants.MESH_JBEAM_PART] = part
    obj_data[constants.MESH_JBEAM_FILE_PATH] = jbeam_filepath
    obj_data[constants.MESH_VEHICLE_MODEL] = vehicle_model
    obj_data[constants.MESH_VERTEX_COUNT] = len(bm_verts)
    obj_data[constants.MESH_EDGE_COUNT] = len(bm_edges)
    obj_data[constants.MESH_FACE_COUNT] = len(bm_faces)
    obj_data[constants.MESH_EDITING_ENABLED] = True


def generate_meshes(vehicle_bundle: dict):
    context = bpy.context
    io_ctx = vehicle_bundle['ioCtx']
    veh_files = vehicle_bundle['vehFiles']
    vdata = vehicle_bundle['vdata']
    vehicle_model = vdata['model']
    main_part_name = vehicle_bundle['mainPartName']
    pc_filepath = vehicle_bundle['config']['partConfigFilename']
    parts = vehicle_bundle['chosenParts'].values()

    vertices, parts_edges, parts_tris, parts_quads, node_index_to_id = get_vertices_edges_faces(vehicle_bundle)

    # make collection
    vehicle_parts_collection = bpy.data.collections.new(vehicle_model)
    context.scene.collection.children.link(vehicle_parts_collection)

    for part in parts:
        if part == '': # skip slots with empty parts
            continue

        bm = bmesh.new()
        obj_data = bpy.data.meshes.new(part)
        # make object from mesh
        part_obj = bpy.data.objects.new(part, obj_data)
        generate_part_mesh(part_obj, obj_data, bm, vehicle_bundle, part, vertices, parts_edges.get(part, []), parts_tris.get(part, []), parts_quads.get(part, []), node_index_to_id)
        bm.to_mesh(obj_data)
        bm.free() # Free bmesh after use >>>
        obj_data.update()

        # add object to vehicle collection
        vehicle_parts_collection.objects.link(part_obj)

    # store vehicle data in collection
    vehicle_parts_collection[constants.COLLECTION_VEHICLE_BUNDLE] = base64.b64encode(pickle.dumps(vehicle_bundle, -1)).decode('ascii')
    vehicle_parts_collection[constants.COLLECTION_IO_CTX] = io_ctx
    vehicle_parts_collection[constants.COLLECTION_VEH_FILES] = veh_files
    vehicle_parts_collection[constants.COLLECTION_PC_FILEPATH] = pc_filepath
    vehicle_parts_collection[constants.COLLECTION_VEHICLE_MODEL] = vehicle_model
    vehicle_parts_collection[constants.COLLECTION_MAIN_PART] = main_part_name

    return vehicle_parts_collection


def _reimport_vehicle(context: bpy.types.Context, veh_collection: bpy.types.Collection, vehicle_bundle: dict):
    # context = bpy.context # context is already passed as an argument
    io_ctx = vehicle_bundle['ioCtx']
    veh_files = vehicle_bundle['vehFiles']
    vdata = vehicle_bundle['vdata']
    vehicle_model = vdata['model']
    main_part_name = vehicle_bundle['mainPartName']
    pc_filepath = vehicle_bundle['config']['partConfigFilename']
    parts = vehicle_bundle['chosenParts'].values()

    vertices, parts_edges, parts_tris, parts_quads, node_index_to_id = get_vertices_edges_faces(vehicle_bundle)

    parts_set = set()
    prev_active_obj_name = context.active_object.name if context.active_object else None
    objs = veh_collection.all_objects

    # Store hidden states for ALL parts first ---
    all_hidden_edges_state = {}
    all_hidden_verts_state = {} # <<< ADDED: Dictionary to store vertex hidden states
    for part in parts:
        if part == '' or part not in objs: # Skip empty parts or parts not in the collection
            continue

        obj = objs[part]
        obj_data = obj.data
        temp_bm = None
        try:
            # Check if this specific object is in edit mode, otherwise create from mesh data
            if obj == context.active_object and obj.mode == 'EDIT':
                temp_bm = bmesh.from_edit_mesh(obj_data)
            else:
                temp_bm = bmesh.new()
                temp_bm.from_mesh(obj_data)

            # --- Store Edge Hidden State (Existing) ---
            beam_indices_layer = temp_bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
            beam_origin_layer = temp_bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
            if beam_indices_layer and beam_origin_layer:
                temp_bm.edges.ensure_lookup_table()
                for edge in temp_bm.edges:
                    indices_str = edge[beam_indices_layer].decode('utf-8')
                    # Only store for existing JBeam beams
                    if indices_str and indices_str != '-1':
                        try:
                            first_index = int(indices_str.split(',')[0])
                            part_origin = edge[beam_origin_layer].decode('utf-8')
                            # Use part_origin from the edge layer itself for the key
                            all_hidden_edges_state[(part_origin, first_index)] = edge.hide
                        except (ValueError, IndexError):
                             print(f"Warning: Could not parse beam index for storing hidden state (vehicle): {indices_str}", file=sys.stderr)

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
                        all_hidden_verts_state[(part_origin, node_id)] = vert.hide

        except Exception as e:
            print(f"Error storing hidden state for {part}: {e}", file=sys.stderr)
        finally:
            if temp_bm:
                # Only free if it wasn't the active edit mesh OR if it was created temporarily
                if not (obj == context.active_object and obj.mode == 'EDIT'):
                    temp_bm.free()
    # Store hidden states ---


    # --- Loop through parts to regenerate/update meshes ---
    for part in parts:
        if part == '': # skip slots with empty parts
            continue

        obj: bpy.types.Object = None
        obj_data: bpy.types.Mesh = None
        bm = None
        if part in objs:
            obj = objs[part]
            obj_data = obj.data

            # Clear the mesh *after* storing states for all parts
            if obj == context.active_object and obj.mode == 'EDIT': # Check if this specific obj is the active one in edit mode
                bm = bmesh.from_edit_mesh(obj_data)
                bm.clear()
            else:
                bm = bmesh.new()
                bm.from_mesh(obj_data)
                bm.clear()
        else:
            # Create new object if it didn't exist
            bm = bmesh.new()
            obj_data = bpy.data.meshes.new(part)
            obj = bpy.data.objects.new(part, obj_data)
            veh_collection.objects.link(obj) # add object to scene collection

        # Generate the mesh content
        generate_part_mesh(obj, obj_data, bm, vehicle_bundle, part, vertices, parts_edges.get(part, []), parts_tris.get(part, []), parts_quads.get(part, []), node_index_to_id)

        # Apply hidden edge states ---
        beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
        beam_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
        if beam_indices_layer and beam_origin_layer and all_hidden_edges_state:
            bm.edges.ensure_lookup_table()
            for edge in bm.edges:
                indices_str = edge[beam_indices_layer].decode('utf-8')
                if indices_str and indices_str != '-1':
                    try:
                        first_index = int(indices_str.split(',')[0])
                        part_origin = edge[beam_origin_layer].decode('utf-8')
                        # Use part_origin from the edge layer itself for lookup
                        if (part_origin, first_index) in all_hidden_edges_state:
                            edge.hide = all_hidden_edges_state[(part_origin, first_index)]
                    except (ValueError, IndexError):
                        print(f"Warning: Could not parse beam index for applying hidden state (vehicle): {indices_str}", file=sys.stderr)
        # Apply hidden edge states ---

        # Apply hidden vertex states ---
        node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
        # Get node origin layer for vertex state key >>>
        node_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN)
        is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
        # Check node_origin_layer exists >>>
        if node_id_layer and node_origin_layer and is_fake_layer and all_hidden_verts_state:
            bm.verts.ensure_lookup_table()
            for vert in bm.verts:
                 if vert[is_fake_layer] == 0: # Only apply to real nodes
                    node_id = vert[node_id_layer].decode('utf-8')
                    # Use part_origin from vertex layer for key >>>
                    part_origin = vert[node_origin_layer].decode('utf-8')
                    if (part_origin, node_id) in all_hidden_verts_state:
                        vert.hide = all_hidden_verts_state[(part_origin, node_id)]

        bm.normal_update()

        # Write back to mesh
        if obj == context.active_object and obj.mode == 'EDIT': # Check if this specific obj is the active one in edit mode
            bmesh.update_edit_mesh(obj_data)
        else:
            bm.to_mesh(obj_data)
        bm.free() # Free the bmesh used for generation/modification
        obj_data.update()
        parts_set.add(part) # Keep track of parts that were processed

    # Delete objects from vehicle collection that aren't part of the *new* parts configuration
    obj_datas_to_remove = []
    obj: bpy.types.Object
    for obj in veh_collection.all_objects[:]: # Iterate over a copy
        if obj.name not in parts_set:
            obj_datas_to_remove.append(obj.data)
            bpy.data.objects.remove(obj, do_unlink=True)

    # Clean up mesh data for removed objects
    for obj_data in obj_datas_to_remove:
        # Check if mesh data still exists before removing
        if obj_data and obj_data.name in bpy.data.meshes:
             bpy.data.meshes.remove(obj_data, do_unlink=True)

    # Update collection properties
    vehicle_bundle['vdata'] = vdata # Ensure the modified vdata (with partOrigin fixes) is saved back >>>
    veh_collection[constants.COLLECTION_VEHICLE_BUNDLE] = base64.b64encode(pickle.dumps(vehicle_bundle, -1)).decode('ascii') # Update bundle after changes
    veh_collection[constants.COLLECTION_IO_CTX] = io_ctx
    veh_collection[constants.COLLECTION_VEH_FILES] = veh_files
    veh_collection[constants.COLLECTION_PC_FILEPATH] = pc_filepath
    veh_collection[constants.COLLECTION_VEHICLE_MODEL] = vehicle_model
    veh_collection[constants.COLLECTION_MAIN_PART] = main_part_name

    # Restore active object selection if it still exists
    if prev_active_obj_name is not None and prev_active_obj_name in context.scene.objects:
        context.view_layer.objects.active = context.scene.objects[prev_active_obj_name]


def reimport_vehicle(context: bpy.types.Context, veh_collection: bpy.types.Collection, jbeam_files: dict, regenerate_mesh: bool):
    vehicle_bundle = None # Initialize vehicle_bundle
    try:
        config_path = veh_collection[constants.COLLECTION_PC_FILEPATH]

        re_match = re.match(r'(.*/vehicles)/([^/]+)', config_path)
        if re_match is None:
            raise Exception(f'{config_path} is not located in a valid path!')

        vehicles_vehicle_dir = re_match.group(0)
        vehicles_dir = re_match.group(1)
        model_name = re_match.group(2)

        vehicle_directories = [vehicles_vehicle_dir, Path(vehicles_dir).joinpath('common').as_posix()]

        vehicle_config = build_config(config_path, True)

        jbeam_parsing_errors, vehicle_bundle = load_vehicle(vehicle_directories, vehicle_config, model_name, jbeam_files)

        if regenerate_mesh:
            # Create Blender meshes from JBeam data
            _reimport_vehicle(context, veh_collection, vehicle_bundle)

        # Save the potentially modified vehicle_bundle back >>>
        # Ensure vehicle_bundle exists before trying to dump/encode it
        if 'vehicle_bundle' in locals() and vehicle_bundle is not None:
            veh_collection[constants.COLLECTION_VEHICLE_BUNDLE] = base64.b64encode(pickle.dumps(vehicle_bundle, -1)).decode('ascii')
        else:
             # If load_vehicle failed severely, vehicle_bundle might not exist
             # Clear the bundle in this case too
             if constants.COLLECTION_VEHICLE_BUNDLE in veh_collection:
                try:
                    # Set to encoded None to ensure safe failure on next load
                    veh_collection[constants.COLLECTION_VEHICLE_BUNDLE] = base64.b64encode(pickle.dumps(None, -1)).decode('ascii')
                    print("Cleared vehicle bundle due to load_vehicle failure before assignment.")
                except Exception as clear_err:
                    print(f"Error clearing vehicle bundle after load failure: {clear_err}", file=sys.stderr)

        context.scene['jbeam_editor_reimporting_jbeam'] = 1 # Prevents exporting jbeam

        # obj: bpy.types.Object
        # for obj in veh_collection.all_objects[:]:
        #     obj_data: bpy.types.Mesh = obj.data
        #     obj.hide_set(obj_data.get(constants.MESH_PREV_HIDDEN))
        #     obj_data[constants.MESH_EDITING_ENABLED] = True

        if len(jbeam_parsing_errors) == 0:
            print('Done reimporting vehicle.')
        else:
            print('WARNING, done reimporting vehicle with errors. Some parts may not be imported.')
        return True
    except Exception as e: # Catch specific exceptions if possible, but broad Exception for now
        traceback.print_exc()
        # Clear the bundle on any failure during reimport >>>
        if constants.COLLECTION_VEHICLE_BUNDLE in veh_collection:
            try:
                # Set to encoded None to ensure safe failure on next load
                veh_collection[constants.COLLECTION_VEHICLE_BUNDLE] = base64.b64encode(pickle.dumps(None, -1)).decode('ascii')
                print("Cleared vehicle bundle due to reimport error.")
            except Exception as clear_err:
                 print(f"Error clearing vehicle bundle: {clear_err}", file=sys.stderr)
        # The commented-out code for hiding objects can remain commented out
        return False


def import_vehicle(context: bpy.types.Context, config_path: str):
    try:
        # Import and process JBeam data
        re_match = re.match(r'(.*/vehicles)/([^/]+)', config_path)
        if re_match is None:
            raise Exception(f'{config_path} is not located in a valid path!')

        vehicles_vehicle_dir = re_match.group(0)
        vehicles_dir = re_match.group(1)
        model_name = re_match.group(2)

        vehicle_directories = [vehicles_vehicle_dir, Path(vehicles_dir).joinpath('common').as_posix()]

        # Prevent overriding a vehicle that already exists in scene!
        if bpy.data.collections.get(model_name):
            raise Exception(f'{model_name} already exists in scene!')

        jbeam_io.invalidate_cache_on_new_import(vehicles_vehicle_dir)

        vehicle_config = build_config(config_path)

        jbeam_parsing_errors, vehicle_bundle = load_vehicle(vehicle_directories, vehicle_config, model_name, None)

        # Create Blender meshes from JBeam data
        generate_meshes(vehicle_bundle)

        text_editor.check_all_int_files_for_changes(context, False, False)

        print('Done importing vehicle.')

        if len(jbeam_parsing_errors) == 0:
            utils.show_message_box('INFO', 'Import Vehicle', 'Done importing vehicle.')
        else:
            utils.show_message_box('ERROR', 'Import Vehicle', 'Done importing vehicle. WARNING some JBeam parts may not be imported due to JBeam parsing errors. Check the "System Console" for details.')

        return True
    except Exception as ex:
        tb = traceback.TracebackException.from_exception(ex, capture_locals=True)
        print("".join(tb.format()))
        utils.show_message_box('ERROR', 'Import Vehicle', 'ERROR importing vehicle. Check the "System Console" for details.')
        return False


def on_files_change(context: bpy.types.Context, files_changed: dict, regenerate_mesh: bool):
    collections = bpy.data.collections

    for collection in collections:
        if collection.get(constants.COLLECTION_VEHICLE_MODEL) is None:
            continue

        # import cProfile, pstats, io
        # import pstats
        # pr = cProfile.Profile()
        # with cProfile.Profile() as pr:
        #     import_vehicle.reimport_vehicle(veh_collection, filename)
        #     stats = pstats.Stats(pr)
        #     stats.strip_dirs().sort_stats('cumtime').print_stats()

        reimport_vehicle(context, collection, files_changed, regenerate_mesh)


class JBEAM_EDITOR_OT_import_vehicle(Operator, ImportHelper):
    bl_idname = 'jbeam_editor.import_vehicle'
    bl_label = 'Import Vehicle'
    bl_description = 'Import a BeamNG Part Config file (.pc)'
    filename_ext = ".pc"

    filter_glob: StringProperty(
        default="*.pc",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    def execute(self, context):
        pc_config_path = Path(self.filepath).as_posix()
        res = import_vehicle(context, pc_config_path)
        if not res:
            return {'CANCELLED'}
        return {'FINISHED'}

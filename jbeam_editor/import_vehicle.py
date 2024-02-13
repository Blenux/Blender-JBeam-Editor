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

from pathlib import Path
import re
import sys
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


def load_jbeam(vehicle_directories: list[str], vehicle_config: dict, reimporting_files_changed: dict | None):
    """load all the jbeam and construct the thing in memory"""
    print('Reading JBeam files...')
    t0 = timeit.default_timer()
    io_ctx = jbeam_io.start_loading(vehicle_directories, vehicle_config, reimporting_files_changed)
    if io_ctx is None:
        return None

    t1 = timeit.default_timer()
    print('Done reading JBeam files. Time =', round(t1 - t0, 2), 's')

    # figure out the model name based on the directory given
    re_match = re.search(r'/vehicles/([^/]+)', vehicle_directories[0])
    model_name = re_match.group(1) if re_match is not None else None
    print('Model name:', model_name)

    if 'mainPartName' not in vehicle_config:
        vehicle_config['mainPartName'] = jbeam_io.get_main_part_name(io_ctx)

    print('Finding parts...')
    vehicle, unify_journal, chosen_parts, active_parts_orig = jbeam_slot_system.find_parts(io_ctx, vehicle_config)
    if vehicle is None or unify_journal is None:
        return None

    # Map parts to JBeam file
    veh_parts = list(chosen_parts.values())
    veh_part_to_file_map = {}
    veh_files = []
    for directory in vehicle_directories:
        for file in jbeam_io.dir_to_files_map[directory]:
            file_added = False
            if file in jbeam_io.file_to_parts_name_map:
                parts = jbeam_io.file_to_parts_name_map[file]
                for part in parts:
                    if part in veh_parts:
                        veh_part_to_file_map[part] = file
                        if not file_added:
                            veh_files.append(file)
                            file_added = True

    print('Applying variables...')
    all_variables = jbeam_variables.process_parts(vehicle, unify_journal, vehicle_config)

    print('Unifying parts...')
    if jbeam_slot_system.unify_part_journal(io_ctx, unify_journal) is None:
        return None

    jbeam_variables.process_unified_vehicle(vehicle, all_variables)

    print('Assembling tables ...')
    if jbeam_table_schema.process(vehicle) is None:
        print('*** preparation error"', file=sys.stderr)
        return None

    # Exclusive to Python vehicle importer
    jbeam_table_schema.post_process(vehicle)

    jbeam_node_beam.process(vehicle)

    vehicle['vehicleDirectory'] = vehicle_directories[0]
    vehicle['activeParts'] = active_parts_orig
    vehicle['model'] = model_name

    jbeam_io.finish_loading()

    t2 = timeit.default_timer()
    print('Done loading JBeam. Time =', round(t2 - t1, 2), 's')

    return {
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
        return None

    file_data = utils.sjson_decode(pc_filetext, config_path)
    if not file_data:
        return None

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
    parts_faces = {}

    node_index_to_id_append = node_index_to_id.append
    vertices_append = vertices.append

    if 'nodes' in vdata:
        nodes: dict[str, dict] = vdata['nodes']

        if 'triangles' in vdata:
            for tri in vdata['triangles']:
                part_origin = tri['partOrigin']
                faces = parts_faces.setdefault(part_origin, [])

                ids = (tri['id1:'], tri['id2:'], tri['id3:'])
                if len(set(ids)) == 3 and all(x in nodes for x in ids):
                    n1, n2, n3 = nodes[ids[0]], nodes[ids[1]], nodes[ids[2]]

                    n1_vert_idx = len(node_index_to_id)
                    node_index_to_id_append(ids[0])
                    vertices_append((n1['pos'], True))

                    n2_vert_idx = len(node_index_to_id)
                    node_index_to_id_append(ids[1])
                    vertices_append((n2['pos'], True))

                    n3_vert_idx = len(node_index_to_id)
                    node_index_to_id_append(ids[2])
                    vertices_append((n3['pos'], True))

                    faces.append((3, (n1_vert_idx, n2_vert_idx, n3_vert_idx)))
                else:
                    faces.append((3, None))

        # Translate quads to faces
        if 'quads' in vdata:
            for quad in vdata['quads']:
                part_origin = quad['partOrigin']
                faces = parts_faces.setdefault(part_origin, [])

                ids = (quad['id1:'], quad['id2:'], quad['id3:'], quad['id4:'])
                if len(set(ids)) == 4 and all(x in nodes for x in ids):
                    n1, n2, n3, n4 = nodes[ids[0]], nodes[ids[1]], nodes[ids[2]], nodes[ids[3]]

                    n1_vert_idx = len(node_index_to_id)
                    node_index_to_id_append(ids[0])
                    vertices_append((n1['pos'], True))

                    n2_vert_idx = len(node_index_to_id)
                    node_index_to_id_append(ids[1])
                    vertices_append((n2['pos'], True))

                    n3_vert_idx = len(node_index_to_id)
                    node_index_to_id_append(ids[2])
                    vertices_append((n3['pos'], True))

                    n4_vert_idx = len(node_index_to_id)
                    node_index_to_id_append(ids[3])
                    vertices_append((n4['pos'], True))

                    faces.append((4, (n1_vert_idx, n2_vert_idx, n3_vert_idx, n4_vert_idx)))
                else:
                    faces.append((4, None))

        # Translate nodes to vertices
        for i, (node_id, node) in enumerate(nodes.items()):
            node_index_to_id_append(node_id)
            node_id_to_index[node_id] = len(vertices)
            vertices_append((node['pos'], False))

        # Translate beams to edges
        if 'beams' in vdata:
            for beam in vdata['beams']:
                part_origin = beam['partOrigin']
                edges = parts_edges.setdefault(part_origin, [])

                ids = (beam['id1:'], beam['id2:'])
                if len(set(ids)) == 2 and all(x in nodes for x in ids):
                    edge_tup_sorted = tuple(sorted(ids))
                    edges.append((node_id_to_index[edge_tup_sorted[0]], node_id_to_index[edge_tup_sorted[1]]))
                else:
                    edges.append(None)

    return vertices, parts_edges, parts_faces, node_index_to_id


def generate_part_mesh(obj: bpy.types.Object, obj_data: bpy.types.Mesh, bm: bmesh.types.BMesh, vehicle_bundle: dict, part: str, vertices: list, parts_edges: dict[str, list], parts_faces: dict[str, list], node_index_to_id: list):
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
    init_node_id_layer = bm_verts.layers.string.new(constants.VLS_INIT_NODE_ID)
    node_id_layer = bm_verts.layers.string.new(constants.VLS_NODE_ID)
    node_origin_layer = bm_verts.layers.string.new(constants.VLS_NODE_PART_ORIGIN)
    node_is_fake_layer = bm_verts.layers.int.new(constants.VLS_NODE_IS_FAKE)

    beam_origin_layer = bm_edges.layers.string.new(constants.ELS_BEAM_PART_ORIGIN)
    beam_indices_layer = bm_edges.layers.string.new(constants.ELS_BEAM_INDICES)

    face_origin_layer = bm_faces.layers.string.new(constants.FLS_FACE_PART_ORIGIN)
    face_idx_layer = bm_faces.layers.int.new(constants.FLS_FACE_IDX)

    inv_matrix_world = obj.matrix_world.inverted()
    bytes_part = bytes(part, 'utf-8')

    if 'nodes' in vdata:
        nodes: dict[str, dict] = vdata['nodes']
        transformed_positions = {}

        for i, (pos, is_fake) in enumerate(vertices):
            node_id = node_index_to_id[i]
            if node_id not in transformed_positions:
                transformed_positions[node_id] = (inv_matrix_world @ Vector(pos)).to_tuple()
            v = bm_verts_new(transformed_positions[node_id])
            bytes_node_id = bytes(node_id, 'utf-8')
            v[init_node_id_layer] = bytes_node_id
            v[node_id_layer] = bytes_node_id
            v[node_origin_layer] = bytes(nodes[node_id]['partOrigin'], 'utf-8')
            v[node_is_fake_layer] = int(is_fake)

    bm_verts.ensure_lookup_table()

    edges = parts_edges[part] if part in parts_edges else []
    faces = parts_faces[part] if part in parts_faces else []

    added_edges = {}

    for i, edge in enumerate(edges, 1):
        if edge is not None:
            if not edge in added_edges:
                e = bm_edges_new((bm_verts[edge[0]], bm_verts[edge[1]]))
                e[beam_indices_layer] = bytes(f'{i}', 'utf-8')
                e[beam_origin_layer] = bytes_part
                added_edges[edge] = e
            else:
                e = added_edges[edge]
                last_indices = e[beam_indices_layer].decode('utf-8')
                e[beam_indices_layer] = bytes(f'{last_indices},{i}', 'utf-8')

    tri_idx_in_part, quad_idx_in_part = 1, 1
    for (num_verts, face) in faces:
        if num_verts == 3:
            if face is not None:
                f = bm_faces_new((bm_verts[face[0]], bm_verts[face[1]], bm_verts[face[2]]))
                f[face_idx_layer] = tri_idx_in_part
            tri_idx_in_part += 1
        else:
            if face is not None:
                f = bm_faces_new((bm_verts[face[0]], bm_verts[face[1]], bm_verts[face[2]], bm_verts[face[3]]))
                f[face_idx_layer] = quad_idx_in_part
            quad_idx_in_part += 1

    obj_data[constants.MESH_JBEAM_PART] = part
    obj_data[constants.MESH_JBEAM_FILE_PATH] = jbeam_filepath
    obj_data[constants.MESH_VEHICLE_MODEL] = vehicle_model
    obj_data[constants.MESH_VERTEX_COUNT] = len(bm_verts)
    obj_data[constants.MESH_EDGE_COUNT] = len(bm_edges)
    obj_data[constants.MESH_FACE_COUNT] = len(bm_faces)


def generate_meshes(vehicle_bundle: dict):
    context = bpy.context
    io_ctx = vehicle_bundle['ioCtx']
    veh_files = vehicle_bundle['vehFiles']
    vdata = vehicle_bundle['vdata']
    vehicle_model = vdata['model']
    main_part_name = vehicle_bundle['mainPartName']
    pc_filepath = vehicle_bundle['config']['partConfigFilename']
    parts = vehicle_bundle['chosenParts'].values()

    # Prevent overriding a vehicle that already exists in scene!
    if bpy.data.collections.get(vehicle_model):
        print(f'Collection named {vehicle_model} already exists! Vehicle will not be generated to avoid overriding it...', file=sys.stderr)
        return None

    vertices, parts_edges, parts_faces, node_index_to_id = get_vertices_edges_faces(vehicle_bundle)

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
        generate_part_mesh(part_obj, obj_data, bm, vehicle_bundle, part, vertices, parts_edges, parts_faces, node_index_to_id)
        bm.to_mesh(obj_data)
        obj_data.update()

        # add object to vehicle collection
        vehicle_parts_collection.objects.link(part_obj)

    # store vehicle data in collection
    vehicle_parts_collection[constants.COLLECTION_VEHICLE_BUNDLE] = pickle.dumps(vehicle_bundle)
    vehicle_parts_collection[constants.COLLECTION_IO_CTX] = io_ctx
    vehicle_parts_collection[constants.COLLECTION_VEH_FILES] = veh_files
    vehicle_parts_collection[constants.COLLECTION_PC_FILEPATH] = pc_filepath
    vehicle_parts_collection[constants.COLLECTION_VEHICLE_MODEL] = vehicle_model
    vehicle_parts_collection[constants.COLLECTION_MAIN_PART] = main_part_name

    return vehicle_parts_collection


def _reimport_vehicle(context: bpy.types.Context, veh_collection: bpy.types.Collection, vehicle_bundle: dict):
    context = bpy.context
    io_ctx = vehicle_bundle['ioCtx']
    veh_files = vehicle_bundle['vehFiles']
    vdata = vehicle_bundle['vdata']
    vehicle_model = vdata['model']
    main_part_name = vehicle_bundle['mainPartName']
    pc_filepath = vehicle_bundle['config']['partConfigFilename']
    parts = vehicle_bundle['chosenParts'].values()

    context.scene['jbeam_editor_reimporting_jbeam'] = 1 # Prevents exporting jbeam

    vertices, parts_edges, parts_faces, node_index_to_id = get_vertices_edges_faces(vehicle_bundle)

    parts_set = set()
    prev_active_obj_name = context.active_object.name
    objs = veh_collection.all_objects
    for part in parts:
        if part == '': # skip slots with empty parts
            continue

        obj: bpy.types.Object = None
        obj_data: bpy.types.Mesh = None
        bm = None
        if part in objs:
            obj = objs[part]
            obj_data = obj.data

            if obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(obj_data)
                bm.clear()
            else:
                bm = bmesh.new()
                bm.from_mesh(obj_data)
                bm.clear()
        else:
            bm = bmesh.new()
            obj_data = bpy.data.meshes.new(part)
            obj = bpy.data.objects.new(part, obj_data)
            veh_collection.objects.link(obj) # add object to scene collection

        generate_part_mesh(obj, obj_data, bm, vehicle_bundle, part, vertices, parts_edges, parts_faces, node_index_to_id)
        bm.normal_update()

        if obj.mode == 'EDIT':
            bmesh.update_edit_mesh(obj_data)
        else:
            bm.to_mesh(obj_data)
        bm.free()
        obj_data.update()
        parts_set.add(part)

    # Delete objects from vehicle collection that aren't part of parts
    obj_datas_to_remove = []
    obj: bpy.types.Object
    for obj in veh_collection.all_objects[:]:
        if obj.name not in parts_set:
            obj_datas_to_remove.append(obj.data)
            bpy.data.objects.remove(obj, do_unlink=True)

    for obj_data in obj_datas_to_remove:
        bpy.data.meshes.remove(obj_data, do_unlink=True)

    veh_collection[constants.COLLECTION_VEHICLE_BUNDLE] = pickle.dumps(vehicle_bundle)
    veh_collection[constants.COLLECTION_IO_CTX] = io_ctx
    veh_collection[constants.COLLECTION_VEH_FILES] = veh_files
    veh_collection[constants.COLLECTION_PC_FILEPATH] = pc_filepath
    veh_collection[constants.COLLECTION_VEHICLE_MODEL] = vehicle_model
    veh_collection[constants.COLLECTION_MAIN_PART] = main_part_name

    if prev_active_obj_name in context.scene.objects:
        context.view_layer.objects.active = context.scene.objects[prev_active_obj_name]


def reimport_vehicle(context: bpy.types.Context, veh_collection: bpy.types.Collection, jbeam_files: dict):
    try:
        config_path = veh_collection[constants.COLLECTION_PC_FILEPATH]

        vehicle_config = build_config(config_path, True)
        if vehicle_config is None:
            return False

        vehicle_dir = Path(config_path).parent.as_posix()
        vehicles_dir = Path(vehicle_dir).parent.as_posix()
        vehicle_directories = [vehicle_dir, Path(vehicles_dir).joinpath('common').as_posix()]

        vehicle_bundle = load_jbeam(vehicle_directories, vehicle_config, jbeam_files)
        if vehicle_bundle is None:
            return False

        # Create Blender meshes from JBeam data
        _reimport_vehicle(context, veh_collection, vehicle_bundle)

        print('Done reimporting vehicle.')
        return True
    except:
        traceback.print_exc()
        return False


def import_vehicle(context: bpy.types.Context, config_path: str):
    try:
        # Import and process JBeam data

        vehicle_dir = Path(config_path).parent.as_posix()
        vehicles_dir = Path(vehicle_dir).parent.as_posix()
        vehicle_directories = [vehicle_dir, Path(vehicles_dir).joinpath('common').as_posix()]

        jbeam_io.invalidate_cache_on_new_import(vehicle_dir)

        vehicle_config = build_config(config_path)
        if vehicle_config is None:
            return False

        vehicle_bundle = load_jbeam(vehicle_directories, vehicle_config, None)

        if vehicle_bundle is None:
            return False

        # Create Blender meshes from JBeam data
        if generate_meshes(vehicle_bundle) is None:
            return False

        text_editor.check_all_int_files_for_changes(context, False, False)

        print('Done importing vehicle.')
        return True
    except:
        traceback.print_exc()
        return False


def on_files_change(context: bpy.types.Context, files_changed: dict):
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

        reimport_vehicle(context, collection, files_changed)


class JBEAM_EDITOR_OT_import_vehicle(Operator, ImportHelper):
    bl_idname = 'jbeam_editor.import_vehicle'
    bl_label = 'Import JBeam'
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

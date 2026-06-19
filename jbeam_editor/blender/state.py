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
import bpy
from pickle import loads as pickle_loads

from ..core import constants


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

veh_render_dirty = False

rename_enabled = False

batch_node_renaming_enabled = False

part_name_to_obj = {}


def refresh_curr_vdata(force_refresh=False):
    global prev_obj_selected
    global curr_vdata
    global veh_render_dirty

    context = bpy.context
    selected_obj = None
    jbeam_part = None

    obj = context.active_object
    if obj is not None:
        obj_data = obj.data
        jbeam_part = obj_data.get(constants.MESH_JBEAM_PART)
        selected_obj = obj.name
    else:
        selected_obj = None

    if force_refresh or prev_obj_selected != selected_obj:
        if jbeam_part is not None:
            collection = obj.users_collection[0]
            veh_model = collection.get(constants.COLLECTION_VEHICLE_MODEL)

            if veh_model is not None:
                curr_vdata = pickle_loads(base64.b64decode(collection[constants.COLLECTION_VEHICLE_BUNDLE]))['vdata']
            else:
                curr_vdata = pickle_loads(base64.b64decode(obj_data[constants.MESH_SINGLE_JBEAM_PART_DATA]))
        else:
            curr_vdata = None

        veh_render_dirty = True
        prev_obj_selected = selected_obj

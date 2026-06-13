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
import blf
import bmesh
import base64
import pickle
import sys # <<< Added import
import traceback # <<< Added import
import re # <<< ADDED: Import re for variable parsing
import json # <<< Need json for parsing string values robustly
# <<< ADDED: Imports for safe expression evaluation >>>
import ast
import operator as op
import math # Ensure math is imported


from blf import position as blfpos
from blf import size as blfsize
from blf import draw as blfdraw
from blf import color as blfcolor
from blf import dimensions as blfdims

from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector, Matrix, Color # <<< ADD Color import

# Import from local modules
from . import constants
from . import globals as jb_globals # Import globals
from . import text_editor
from . import sjsonast
from . import bng_sjson
from .text_editor import SCENE_SHORT_TO_FULL_FILENAME
from .sjsonast import ASTNode # <<< Ensure ASTNode is imported
from .jbeam import io as jbeam_io # <<< ADDED: Import jbeam_io
from .utils import Metadata # <<< ADDED: Import Metadata for filtering

if not constants.UNIT_TESTING:
    import gpu
    from gpu_extras.batch import batch_for_shader

# <<< NEW: Define white color constant >>>
WHITE_COLOR = (1.0, 1.0, 1.0, 1.0)

# <<< ADDED: Global vars for node auto thresholds >>>
auto_node_weight_min = float('inf')
auto_node_weight_max = float('-inf')
auto_node_thresholds_valid = False
# <<< END ADDED >>>

# Drawing related globals
veh_render_dirty = False
# <<< ADDED: Global flag to track highlight changes >>>
_highlight_dirty = False
part_name_to_obj: dict[str, bpy.types.Object] = {}
warned_missing_nodes_this_rebuild = set()
# <<< ADDED: Set to track missing variables reported in the current rebuild cycle >>>
_reported_missing_vars_this_rebuild = set()
# <<< ADDED: Set to track unsupported operations reported in the current rebuild cycle >>>
_reported_unsupported_ops_this_rebuild = set()

# --- Visualization Batches & Coords ---
render_shader = None # Will be initialized to SMOOTH_COLOR

# Normal Beams (Static Color) - Only used when dynamic coloring is OFF
beam_render_batch = None
beam_coords = []

# Dynamic Coloring Beams <<< MODIFIED: Single list/batch for ALL types >>>
dynamic_beam_batch = None
# Store tuples: (pos1, pos2, color) - will be processed into separate lists for batching
dynamic_beam_coords_colors = []
# <<< END MODIFIED >>>

# Other Beam Types (Static Color) - Only used when dynamic coloring is OFF
anisotropic_beam_render_batch = None
anisotropic_beam_coords = []
support_beam_render_batch = None
support_beam_coords = []
hydro_beam_render_batch = None
hydro_beam_coords = []
bounded_beam_render_batch = None
bounded_beam_coords = []
lbeam_render_batch = None
lbeam_coords = []
pressured_beam_render_batch = None
pressured_beam_coords = []
# Cross-Part Beams (Static Color) - Only used when dynamic coloring is OFF
cross_part_beam_render_batch = None
cross_part_beam_coords = []

# Torsionbars, Rails (Remain separate)
torsionbar_render_batch = None
torsionbar_coords = []
torsionbar_red_render_batch = None
torsionbar_red_coords = []
rail_render_batch = None
rail_coords = []

# Node Cache (Remains the same)
all_nodes_cache: dict[str, tuple[Vector, str, str]] = {} # {node_id: (world_pos, source_filepath, part_origin)}
all_nodes_cache_dirty = True # Flag to rebuild cache

# --- Selected Beam Outline --- (Remains the same)
selected_beam_batch = None
selected_beam_coords_colors = []
selected_beam_max_original_width = 1.0

# --- Highlight on Click --- (Remain the same)
highlight_render_batch = None
highlight_coords = []
highlight_torsionbar_outer_batch = None
highlight_torsionbar_outer_coords = []
highlight_torsionbar_mid_batch = None
highlight_torsionbar_mid_coords = []
# highlighted_node_ids (set) and highlighted_element_ordered_node_ids (list) are managed in globals.py

# Update function for the cross-part beam visibility toggle.
def _update_toggle_cross_part_beams_vis(self, context):
    scene = context.scene
    # Always trigger a redraw/rebuild when the toggle changes
    scene.jbeam_editor_veh_render_dirty = True # Use scene property

# Helper function to find the line number of a beam in the AST
def find_beam_line_number(jbeam_filepath: str, target_part_origin: str, target_id1: str, target_id2: str):
    """
    Finds the 1-based line number of a specific beam definition in a JBeam file
    by matching node IDs.
    """
    # <<< ADDED CHECK >>>
    # Ignore beams involving transient TEMP_ IDs during search
    if target_id1.startswith('TEMP_') or target_id2.startswith('TEMP_'):
        return None
    # <<< END ADDED CHECK >>>

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
        sjsonast.calculate_char_positions(ast_nodes)

        stack = []
        in_dict = True
        pos_in_arr = 0
        temp_dict_key = None
        dict_key = None

        i = 0
        while i < len(ast_nodes):
            node: sjsonast.ASTNode = ast_nodes[i]
            node_type = node.data_type

            if node_type == 'wsc':
                i += 1
                continue

            # --- Determine context based on stack ---
            in_target_part = len(stack) > 0 and stack[0][0] == target_part_origin
            in_beams_section = (
                in_target_part and
                len(stack) == 2 and
                stack[1][0] == 'beams' and
                not in_dict # Ensure we are inside the beams *array*
            )
            # --- End context determination ---

            if in_dict:
                if node_type == '{':
                    if dict_key is not None:
                        stack.append((dict_key, True)) # Parent was dict
                    pos_in_arr = 0; temp_dict_key = None; dict_key = None; in_dict = True
                elif node_type == '[':
                    if dict_key is not None:
                        stack.append((dict_key, True)) # Parent was dict
                    pos_in_arr = 0; temp_dict_key = None; dict_key = None; in_dict = False
                elif node_type == '}':
                    if stack:
                        prev_key_or_idx, prev_in_dict = stack.pop()
                        in_dict = prev_in_dict
                        pos_in_arr = prev_key_or_idx + 1 if not prev_in_dict else 0
                    else: in_dict = None
                elif node_type == ']':
                     print(f"Error: Unexpected ']' while expecting dict elements near pos {node.start_pos}", file=sys.stderr); return None
                else:
                    if temp_dict_key is None and node_type == '"': temp_dict_key = node.value
                    elif node_type == ':': dict_key = temp_dict_key
                    elif dict_key is not None: temp_dict_key = None; dict_key = None
            else: # In array
                if node_type == '[': # Start of a beam definition array
                    beam_entry_start_node = node
                    stack.append((pos_in_arr, False)) # Parent was array
                    pos_in_arr = 0; in_dict = False

                    if in_beams_section: # Use derived flag
                        found_id1 = None; found_id2 = None
                        k = i + 1; ids_found = 0
                        while k < len(ast_nodes):
                            inner_node = ast_nodes[k]
                            if inner_node.data_type == ']': break
                            if inner_node.data_type == '"':
                                ids_found += 1
                                if ids_found == 1: found_id1 = inner_node.value
                                elif ids_found == 2: found_id2 = inner_node.value; break
                            k += 1

                        if found_id1 is not None and found_id2 is not None:
                            if (found_id1 == target_id1 and found_id2 == target_id2) or \
                               (found_id1 == target_id2 and found_id2 == target_id1):
                                start_char_pos = beam_entry_start_node.start_pos
                                line_number = file_content[:start_char_pos].count('\n') + 1
                                return line_number
                elif node_type == '{':
                    stack.append((pos_in_arr, False)) # Parent was array
                    pos_in_arr = 0; in_dict = True
                elif node_type == ']':
                    if stack:
                        prev_key_or_idx, prev_in_dict = stack.pop()
                        in_dict = prev_in_dict
                        pos_in_arr = prev_key_or_idx + 1 if not prev_in_dict else 0
                    else: in_dict = None
                elif node_type == '}':
                    print(f"Error: Unexpected '}}' while expecting array elements near pos {node.start_pos}", file=sys.stderr); return None
                else: # Value node within the current array
                    pos_in_arr += 1 # Increment position *after* processing the current element
            i += 1

        # Don't print a warning here, as TEMP_ nodes will naturally not be found
        # print(f"Warning: Beam {target_id1}-{target_id2} not found in part '{target_part_origin}' in file {jbeam_filepath}", file=sys.stderr)
        return None

    except Exception as e:
        print(f"Error finding beam line number: {e}", file=sys.stderr)
        traceback.print_exc()
        return None

# Helper function to find the line number of a node in the AST (REVISED APPROACH)
def find_node_line_number(jbeam_filepath: str, target_part_origin: str, target_node_id: str):
    """
    Finds the 1-based line number of a specific node definition in a JBeam file.
    (Revised approach focusing directly on the target part's nodes section)
    """
    # <<< ADDED CHECK >>>
    # Ignore transient TEMP_ IDs during search
    if target_node_id.startswith('TEMP_'):
        return None
    # <<< END ADDED CHECK >>>

    file_content = text_editor.read_int_file(jbeam_filepath)
    if not file_content:
        print(f"Error: Could not read internal file: {jbeam_filepath}", file=sys.stderr)
        return None

    try:
        # Use the robust bng_sjson parser first to get the structure
        # Add padding for safety
        padded_content = file_content + chr(127) * 2
        c, i = bng_sjson._skip_white_space(padded_content, 0, jbeam_filepath)
        parsed_data = None
        if c == 123:
            parsed_data, _ = bng_sjson._read_object(padded_content, i, jbeam_filepath)
        else:
            print(f"Error: JBeam file does not start with '{{': {jbeam_filepath}", file=sys.stderr)
            return None

        if not parsed_data or target_part_origin not in parsed_data:
            # Don't warn if the part itself isn't found (might happen during load/revert)
            # print(f"Warning: Part '{target_part_origin}' not found in parsed data for {jbeam_filepath}", file=sys.stderr)
            return None

        part_data = parsed_data[target_part_origin]
        if not isinstance(part_data, dict) or 'nodes' not in part_data:
            # print(f"Warning: 'nodes' section not found in part '{target_part_origin}' in {jbeam_filepath}", file=sys.stderr)
            return None

        nodes_section = part_data['nodes']
        if not isinstance(nodes_section, list) or len(nodes_section) <= 1:
            # print(f"Warning: 'nodes' section in part '{target_part_origin}' is not a valid list or is empty/header-only in {jbeam_filepath}", file=sys.stderr)
            return None

        # Now parse with AST to get line numbers
        ast_data = sjsonast.parse(file_content)
        if not ast_data:
            print(f"Error: Could not parse AST for: {jbeam_filepath}", file=sys.stderr)
            return None

        ast_nodes = ast_data['ast']['nodes']
        sjsonast.calculate_char_positions(ast_nodes)

        # --- Find the AST nodes corresponding to the target part's 'nodes' array ---
        nodes_array_start_node_idx = -1
        nodes_array_end_node_idx = -1
        nodes_array_stack_depth = -1 # <<< Store stack depth when '[' is found

        stack = []
        in_dict = True
        pos_in_arr = 0
        temp_dict_key = None
        dict_key = None
        in_target_part = False # <<< Track if we are inside the target part

        i = 0
        while i < len(ast_nodes):
            node: sjsonast.ASTNode = ast_nodes[i]
            node_type = node.data_type

            if node_type == 'wsc':
                i += 1
                continue

            # --- Track if inside target part ---
            current_stack_depth = len(stack)
            if current_stack_depth == 1 and stack[0][0] == target_part_origin:
                in_target_part = True
            elif current_stack_depth < 1:
                in_target_part = False
                # If we exited the target part after finding the start, stop searching
                if nodes_array_start_node_idx != -1:
                    # print(f"Warning: Exited target part '{target_part_origin}' before finding end of 'nodes' array.", file=sys.stderr)
                    break # Optimization: stop if we leave the part
            # --- End tracking ---

            if in_dict:
                if node_type == '{':
                    if dict_key is not None:
                        stack.append((dict_key, True))
                    pos_in_arr = 0; temp_dict_key = None; dict_key = None; in_dict = True
                elif node_type == '[':
                    if dict_key is not None:
                        stack.append((dict_key, True))
                        # Check if we are entering the 'nodes' array within the target part
                        if in_target_part and dict_key == 'nodes':
                            nodes_array_start_node_idx = i
                            nodes_array_stack_depth = len(stack) # <<< Record stack depth
                    pos_in_arr = 0; temp_dict_key = None; dict_key = None; in_dict = False
                elif node_type == '}':
                    if stack:
                        prev_key, prev_in_dict = stack.pop()
                        in_dict = prev_in_dict
                        pos_in_arr = prev_key + 1 if not prev_in_dict else 0
                    else: in_dict = None
                elif node_type == ']':
                     print(f"Error: Unexpected ']' while expecting dict elements near pos {node.start_pos}", file=sys.stderr); return None
                else:
                    if temp_dict_key is None and node_type == '"': temp_dict_key = node.value
                    elif node_type == ':': dict_key = temp_dict_key
                    elif dict_key is not None: temp_dict_key = None; dict_key = None
            else: # In array
                if node_type == '[':
                    stack.append((pos_in_arr, False))
                    pos_in_arr = 0; in_dict = False
                elif node_type == '{':
                    stack.append((pos_in_arr, False))
                    pos_in_arr = 0; in_dict = True
                elif node_type == ']':
                    # <<< Revised End Bracket Logic >>>
                    # Check if we found the start and if the current stack depth matches the recorded depth
                    # The stack length *before* popping should match the recorded depth.
                    if nodes_array_start_node_idx != -1 and len(stack) == nodes_array_stack_depth:
                        nodes_array_end_node_idx = i
                        break # Found the end, exit the main loop
                    # <<< End Revised Logic >>>

                    # Normal stack pop logic if it wasn't the target array's end
                    if stack:
                        prev_key_or_idx, prev_in_dict = stack.pop()
                        in_dict = prev_in_dict
                        pos_in_arr = prev_key_or_idx + 1 if not prev_in_dict else 0
                    else: in_dict = None
                elif node_type == '}':
                    print(f"Error: Unexpected '}}' while expecting array elements near pos {node.start_pos}", file=sys.stderr); return None
                else:
                    pos_in_arr += 1
            i += 1

        # --- Process the identified 'nodes' array in the AST ---
        if nodes_array_start_node_idx == -1 or nodes_array_end_node_idx == -1:
            # print(f"Warning: Could not locate AST boundaries for 'nodes' section in part '{target_part_origin}' in {jbeam_filepath}", file=sys.stderr)
            return None

        node_header = []
        node_id_column_index = -1
        row_start_node_idx = -1

        # Iterate specifically within the nodes array bounds
        k = nodes_array_start_node_idx + 1 # Start after the opening '['
        while k < nodes_array_end_node_idx:
            node = ast_nodes[k]
            node_type = node.data_type

            if node_type == 'wsc':
                k += 1
                continue

            if node_type == '[': # Start of a row
                row_start_node_idx = k
                current_col_index = 0
                is_header_row = (len(node_header) == 0) # Assume first row is header

                # Iterate within the row
                j = k + 1
                while j < nodes_array_end_node_idx:
                    inner_node = ast_nodes[j]
                    inner_node_type = inner_node.data_type

                    if inner_node_type == 'wsc':
                        j += 1
                        continue
                    if inner_node_type == ']': # End of row
                        k = j # Move outer loop index past this row
                        break

                    # Process value node within the row
                    if is_header_row:
                        if inner_node_type == '"':
                            node_header.append(inner_node.value)
                            if inner_node.value == 'id':
                                node_id_column_index = current_col_index
                    else: # Data row
                        if node_id_column_index != -1 and current_col_index == node_id_column_index:
                            if inner_node_type == '"' and inner_node.value == target_node_id:
                                # Found the target node ID in the correct column!
                                start_char_pos = ast_nodes[row_start_node_idx].start_pos
                                line_number = file_content[:start_char_pos].count('\n') + 1
                                return line_number

                    current_col_index += 1
                    j += 1
            k += 1

        # If loop finishes without finding the node
        # Don't print a warning here, as TEMP_ nodes will naturally not be found
        # print(f"Warning: Node ID '{target_node_id}' not found within 'nodes' section of part '{target_part_origin}' in file {jbeam_filepath}", file=sys.stderr)
        return None

    except Exception as e:
        print(f"Error finding node line number: {e}", file=sys.stderr)
        traceback.print_exc()
        return None

# Helper function to scroll Text Editor
def _scroll_editor_to_line(context: bpy.types.Context, filepath: str, line: int):
    """Scrolls the Text Editor to the specified file and line."""
    short_filename = text_editor._to_short_filename(filepath)
    text_obj = bpy.data.texts.get(short_filename)

    if not text_obj:
        print(f"Text object not found: {short_filename}", file=sys.stderr)
        return False

    scrolled = False
    for window in context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'TEXT_EDITOR':
                space = area.spaces[0]
                if space.text == text_obj:
                    line_index = max(0, line - 1)
                    text_obj.cursor_set(line_index)
                    with context.temp_override(window=window, area=area):
                        bpy.ops.text.jump(line=line)
                    area.tag_redraw()
                    scrolled = True
                    break
        if scrolled:
            break

    return scrolled

# Update the cache of all node positions from all loaded JBeam files
def update_all_nodes_cache(context: bpy.types.Context):
    """Scans ALL loaded JBeam text files and caches node positions and part origins."""
    global all_nodes_cache, all_nodes_cache_dirty
    print("Updating all nodes cache...")
    all_nodes_cache.clear()
    scene = context.scene
    ui_props = scene.ui_properties

    short_to_full_map = scene.get(SCENE_SHORT_TO_FULL_FILENAME, {})
    if not short_to_full_map:
        print("Scene mapping not found or empty, cannot update nodes cache yet.")
        all_nodes_cache_dirty = False
        return

    for short_name, text_obj in bpy.data.texts.items():
        full_filepath = short_to_full_map.get(short_name)
        if not full_filepath or not full_filepath.lower().endswith('.jbeam'):
            continue

        try:
            file_content = text_obj.as_string()
            if not file_content or '"nodes"' not in file_content:
                continue

            # Wrap parsing in try-except SyntaxError
            try:
                padded_content = file_content + chr(127) * 2
                c, i = bng_sjson._skip_white_space(padded_content, 0, full_filepath)
                parsed_data = None
                if c == 123:
                    parsed_data, _ = bng_sjson._read_object(padded_content, i, full_filepath)
                else:
                    continue # Skip if file doesn't start with '{' after whitespace/comments

                if not parsed_data: continue

                for part_name, part_data in parsed_data.items():
                    if isinstance(part_data, dict) and 'nodes' in part_data:
                        nodes_section = part_data['nodes']
                        if isinstance(nodes_section, list) and len(nodes_section) > 1:
                            header = nodes_section[0]
                            try:
                                id_idx = header.index("id")
                                x_idx = header.index("posX")
                                y_idx = header.index("posY")
                                z_idx = header.index("posZ")
                            except (ValueError, IndexError):
                                continue # Skip if header is malformed

                            for node_row in nodes_section[1:]:
                                if isinstance(node_row, list) and len(node_row) > max(id_idx, x_idx, y_idx, z_idx):
                                    node_id = node_row[id_idx]
                                    try:
                                        pos_x_val = node_row[x_idx]
                                        pos_y_val = node_row[y_idx]
                                        pos_z_val = node_row[z_idx]
                                        # Check if values are numbers before converting
                                        if isinstance(pos_x_val, (int, float)) and \
                                           isinstance(pos_y_val, (int, float)) and \
                                           isinstance(pos_z_val, (int, float)):
                                            pos = Vector((float(pos_x_val), float(pos_y_val), float(pos_z_val)))
                                            all_nodes_cache[node_id] = (pos, full_filepath, part_name)
                                        # else: # Skip nodes with expression-based positions silently
                                        #    pass
                                    except ValueError:
                                         pass # Skip nodes with invalid number formats silently
                                    except TypeError as e:
                                         print(f"Warning: Could not parse node position for '{node_id}' in {full_filepath} (TypeError): {e}", file=sys.stderr)
            except SyntaxError as se:
                # Print a warning instead of a full traceback for syntax errors during cache update
                print(f"Warning: Skipping node cache update for '{full_filepath}' due to syntax error: {se}", file=sys.stderr)
                continue # Continue to the next file

        except Exception as e:
            # Catch other potential errors during file processing
            print(f"Error processing file {full_filepath} for node cache: {e}", file=sys.stderr)
            traceback.print_exc() # Print traceback for unexpected errors

    print(f"All nodes cache updated with {len(all_nodes_cache)} nodes.")
    all_nodes_cache_dirty = False

# <<< MODIFIED: Function to update JBeam variables cache >>>
def update_jbeam_variables_cache(context: bpy.types.Context):
    """
    Scans ALL loaded JBeam text files and caches defined variables.
    Prioritizes '$var = val;' definitions over defaults from ["$var", ...] lists.
    """
    # Use the global cache and dirty flag from jb_globals
    # Don't clear immediately, merge results instead
    temp_variable_cache = {} # Store findings here first
    scene = context.scene

    short_to_full_map = scene.get(SCENE_SHORT_TO_FULL_FILENAME, {})
    if not short_to_full_map:
        jb_globals.jbeam_variables_cache_dirty = False
        return

    # Regex to find variable assignments: $varName = value ; (optional comment)
    variable_regex = re.compile(r'^\s*\$([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.*?)\s*;?\s*(//.*|/\*.*)?$')

    # --- Pass 1: Parse tunable variables ["$var", ...] ---
    for short_name, text_obj in bpy.data.texts.items():
        full_filepath = short_to_full_map.get(short_name)
        if not full_filepath or not full_filepath.lower().endswith('.jbeam'):
            continue

        try:
            file_content = text_obj.as_string()
            if not file_content or '"variables"' not in file_content: # Quick check
                continue

            # Use bng_sjson for structural parsing
            try:
                padded_content = file_content + chr(127) * 2
                c, i = bng_sjson._skip_white_space(padded_content, 0, full_filepath)
                parsed_data = None
                if c == 123:
                    parsed_data, _ = bng_sjson._read_object(padded_content, i, full_filepath)
                else:
                    continue # Skip if file doesn't start with '{'

                if not parsed_data: continue

                for part_name, part_data in parsed_data.items():
                    if isinstance(part_data, dict) and 'variables' in part_data:
                        variables_section = part_data['variables']
                        if isinstance(variables_section, list):
                            for var_entry in variables_section:
                                # Check format ["$varName", type, unit, category, default_value, ...]
                                if (isinstance(var_entry, list) and len(var_entry) >= 5 and
                                        isinstance(var_entry[0], str) and var_entry[0].startswith('$')):
                                    var_name = var_entry[0]
                                    default_value = var_entry[4] # 5th element is default value

                                    # Store default value if not already set by a direct assignment later
                                    if var_name not in temp_variable_cache:
                                        temp_variable_cache[var_name] = {
                                            'value': default_value, # Store raw default value
                                            'source_file': full_filepath,
                                            'line_number': None, # Line number is harder to get accurately here
                                            'source_type': 'default'
                                        }
            except SyntaxError as se:
                print(f"Warning: Skipping variable cache update (Pass 1) for '{full_filepath}' due to syntax error: {se}", file=sys.stderr)
                continue
            except Exception as e:
                print(f"Error processing file {full_filepath} for variable cache (Pass 1): {e}", file=sys.stderr)
                # traceback.print_exc() # Optional traceback

        except Exception as e:
            print(f"Error reading file {full_filepath} for variable cache (Pass 1): {e}", file=sys.stderr)

    # --- Pass 2: Parse direct assignments $var = val; (overwrites defaults) ---
    for short_name, text_obj in bpy.data.texts.items():
        full_filepath = short_to_full_map.get(short_name)
        if not full_filepath or not full_filepath.lower().endswith('.jbeam'):
            continue

        try:
            file_content = text_obj.as_string()
            if not file_content:
                continue

            lines = file_content.splitlines()
            for line_num, line in enumerate(lines, 1):
                match = variable_regex.match(line)
                if match:
                    var_name_only = match.group(1)
                    value_str = match.group(2).strip()
                    parsed_value = None

                    # Attempt to parse the value (same logic as before)
                    try:
                        if '.' in value_str or 'e' in value_str.lower(): parsed_value = float(value_str)
                        else: parsed_value = int(value_str)
                    except ValueError:
                        if value_str.lower() == 'true': parsed_value = True
                        elif value_str.lower() == 'false': parsed_value = False
                        elif len(value_str) >= 2 and value_str.startswith('"') and value_str.endswith('"'):
                            try: parsed_value = json.loads(value_str)
                            except json.JSONDecodeError: parsed_value = value_str[1:-1]
                        else: parsed_value = value_str # Store as raw string if other parsing fails

                    full_var_name = '$' + var_name_only
                    # Store or update the variable info (direct assignments overwrite defaults)
                    temp_variable_cache[full_var_name] = {
                        'value': parsed_value,
                        'source_file': full_filepath,
                        'line_number': line_num,
                        'source_type': 'assignment'
                    }

        except Exception as e:
            print(f"Error processing file {full_filepath} for variable cache (Pass 2): {e}", file=sys.stderr)
            # traceback.print_exc()

    # --- Finalize Cache ---
    jb_globals.jbeam_variables_cache.clear()
    jb_globals.jbeam_variables_cache.update(temp_variable_cache) # Update global cache

    jb_globals.jbeam_variables_cache_dirty = False
# <<< END MODIFIED FUNCTION >>>

# <<< ADDED: Safe Expression Evaluator using AST >>>
# Define allowed operators
allowed_operators = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
    ast.Div: op.truediv, ast.Pow: op.pow, ast.USub: op.neg
    # Add more operators here if needed (e.g., Modulo: ast.Mod: op.mod)
}
# Define allowed names (e.g., math functions, constants) - Start empty
allowed_names = {
    'pi': math.pi,
    'e': math.e,
    # Add math functions if desired, e.g., 'sqrt': math.sqrt
}
# Define allowed node types in the AST
allowed_node_types = {
    'Expression', 'Constant', 'Name', 'Load', 'BinOp', 'UnaryOp',
    # Add Call, Attribute etc. if functions/methods are allowed later
}

class SafeExpressionEvaluator(ast.NodeVisitor):
    """
    Safely evaluates an AST expression node, allowing only basic arithmetic
    and variable lookups from a provided cache.
    """
    def __init__(self, variable_cache):
        self.variable_cache = variable_cache
        # Limit recursion depth for variable resolution within the expression
        self._max_depth = 10
        self._current_depth = 0

    def visit(self, node):
        """Override visit to check node type and depth."""
        if self._current_depth >= self._max_depth:
            raise RecursionError("Maximum expression evaluation depth exceeded")

        node_type_name = type(node).__name__
        if node_type_name not in allowed_node_types:
            raise TypeError(f"AST node type {node_type_name} is not allowed")

        self._current_depth += 1
        try:
            result = super().visit(node)
        finally:
            self._current_depth -= 1
        return result

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        # Handles numbers, strings, True, False, None
        if isinstance(node.value, (int, float)):
            return node.value
        # Allow True/False? JBeam uses lowercase 'true'/'false' typically parsed earlier.
        # elif isinstance(node.value, bool):
        #     return node.value
        else:
            # Disallow other constants like strings within the expression itself for now
            raise TypeError(f"Constant type {type(node.value).__name__} not allowed in expression")

    # Handles variable names
    def visit_Name(self, node):
        if node.id in allowed_names:
            return allowed_names[node.id]

        # Assume it's a JBeam variable (already transformed from $var)
        jbeam_var_name = '$' + node.id.replace('jbeamvar_', '', 1) # Reconstruct original JBeam name
        cached_var = self.variable_cache.get(jbeam_var_name)

        if cached_var is not None:
            value = cached_var['value']
            # Recursively resolve if the value is another expression string or simple variable
            # Pass depth + 1 for recursion control
            resolved_value = resolve_jbeam_variable_value(value, self.variable_cache, self._current_depth + 1) # Use main resolver

            if isinstance(resolved_value, (int, float)):
                return resolved_value
            elif isinstance(resolved_value, bool):
                 return 1.0 if resolved_value else 0.0 # Example: Treat bools as 1.0/0.0
            else:
                # If nested resolution didn't return a number or bool, raise error
                raise ValueError(f"Nested variable '{jbeam_var_name}' did not resolve to a number or boolean (got {type(resolved_value).__name__})")
        else:
            raise NameError(f"Variable '{jbeam_var_name}' not found in cache")

    # Handles binary operators (+, -, *, /, **)
    def visit_BinOp(self, node):
        op_symbol = {ast.Add: '+', ast.Sub: '-', ast.Mult: '*', ast.Div: '/', ast.Pow: '**'}.get(type(node.op), '?')
        if type(node.op) not in allowed_operators:
            raise TypeError(f"Operator {type(node.op).__name__} not allowed")
        left_val = self.visit(node.left)
        right_val = self.visit(node.right)
        # Ensure both operands are numbers before operation
        if not isinstance(left_val, (int, float)) or not isinstance(right_val, (int, float)):
             raise TypeError(f"Unsupported operand types for {op_symbol}: {type(left_val).__name__}, {type(right_val).__name__}")
        try:
            result = allowed_operators[type(node.op)](left_val, right_val)
            return result
        except ZeroDivisionError:
            print(f"Warning: Division by zero encountered in expression.", file=sys.stderr)
            return float('inf') # Or return 0, or NaN, depending on desired behavior

    # Handles unary operators (e.g., - for negation)
    def visit_UnaryOp(self, node):
        op_symbol = {ast.USub: '-'}.get(type(node.op), '?')
        if type(node.op) not in allowed_operators:
            raise TypeError(f"Unary operator {type(node.op).__name__} not allowed")
        operand_val = self.visit(node.operand)
        if not isinstance(operand_val, (int, float)):
             raise TypeError(f"Unsupported operand type for {op_symbol}: {type(operand_val).__name__}")
        result = allowed_operators[type(node.op)](operand_val)
        return result

# <<< MODIFIED FUNCTION >>>
def _evaluate_jbeam_expression(expression_str: str, variable_cache: dict, depth: int = 0):
    """
    Safely evaluates a JBeam arithmetic expression string (e.g., '$var * 1.1').
    """
    # 1. Sanitize: Remove '$' and replace with a safe prefix for variable names
    # Ensure variable names are valid Python identifiers after removing '$'
    def replace_var(match):
        var_name = match.group(1)
        # Use a prefix that's unlikely to clash and is a valid identifier part
        # Python identifiers can start with underscore
        safe_var_name = 'jbeamvar_' + var_name
        if not safe_var_name.isidentifier():
            # Handle invalid chars if necessary, or raise error
            raise NameError(f"Invalid character in variable name: ${var_name}")
        return safe_var_name

    try:
        # Use regex to find $variables and replace them
        # Regex ensures we only match valid variable starts ($ followed by letter/underscore)
        python_expr = re.sub(r'\$([a-zA-Z_][a-zA-Z0-9_]*)', replace_var, expression_str)

        # 2. Parse the sanitized expression string into an AST
        tree = ast.parse(python_expr, mode='eval')

        # 3. Evaluate the AST using the safe visitor
        evaluator = SafeExpressionEvaluator(variable_cache)
        evaluator._current_depth = depth # Pass current depth for recursion control
        result = evaluator.visit(tree)

        # Ensure the final result is a number
        if isinstance(result, (int, float)):
            return result
        else:
            # This might happen if the expression was just a boolean or something unexpected
            raise TypeError(f"Expression evaluated to non-numeric type: {type(result).__name__}")

    except NameError as e:
        # Extract the original JBeam variable name if possible
        name_match = re.search(r"Variable '(\$[a-zA-Z_][a-zA-Z0-9_]*)' not found", str(e)) # Adjusted regex
        original_var = name_match.group(1) if name_match else "unknown variable"
        # <<< MODIFIED: Check if already reported this rebuild >>>
        if original_var not in _reported_missing_vars_this_rebuild:
            print(f"Evaluation Error: Variable not found - {original_var} (in expression '{expression_str}')", file=sys.stderr)
            # Add to set *after* reporting it the first time
            _reported_missing_vars_this_rebuild.add(original_var)
        # <<< END MODIFIED >>>
        return None # Indicate failure

    # <<< MODIFIED ERROR HANDLING for TypeError >>>
    except TypeError as e:
        error_str = str(e)
        # Check if it's the specific "AST node type ... not allowed" error
        if "AST node type" in error_str and "is not allowed" in error_str:
            # Use the expression string as the key to track if this specific expression's error was reported
            if expression_str not in _reported_unsupported_ops_this_rebuild:
                print(f"Evaluation Error: Could not evaluate expression '{expression_str}': {e}", file=sys.stderr)
                _reported_unsupported_ops_this_rebuild.add(expression_str)
        else:
            # For other TypeErrors, report them normally (as they might be different issues)
            print(f"Evaluation Error: Could not evaluate expression '{expression_str}': {e}", file=sys.stderr)
        return None # Indicate failure
    # <<< END MODIFIED ERROR HANDLING >>>

    except (SyntaxError, ValueError, RecursionError) as e:
        # Keep reporting other evaluation errors as they might be specific to the expression instance
        print(f"Evaluation Error: Could not evaluate expression '{expression_str}': {e}", file=sys.stderr)
        return None # Indicate failure
    except Exception as e:
        print(f"Unexpected Evaluation Error for '{expression_str}': {e}", file=sys.stderr)
        traceback.print_exc()
        return None # Indicate failure
# <<< END MODIFIED FUNCTION >>>

# <<< MODIFIED: Function for variable resolution and expression evaluation >>>
def resolve_jbeam_variable_value(value, variable_cache=None, depth=0):
    """
    Resolves JBeam variable references like '$variable', '=$variable' or evaluates
    simple arithmetic expressions like '=$variable * 1.1', '"$=$variable * 1.1"',
    "'$=$variable * 1.1'", or '$=$variable * 1.1'.

    Args:
        value: The value to resolve (can be any type).
        variable_cache: The cache to use (defaults to global cache).
        depth: Current recursion depth (internal use).

    Returns:
        The resolved/evaluated value (likely float/int/bool) or the original value
        if evaluation fails or it's not a recognized expression/variable format.
    """
    if variable_cache is None:
        variable_cache = jb_globals.jbeam_variables_cache

    # Limit overall recursion depth
    if depth >= 10:
        print(f"Warning: Maximum recursion depth reached resolving value '{value}'", file=sys.stderr)
        return value # Return original value to stop recursion

    # --- Check if it's a string that needs processing ---
    if not isinstance(value, str):
        return value # Not a string, return original value

    stripped_value = value.strip() # <<< STRIP the value here

    # --- Check for Expression Patterns ---
    is_expression = False
    expression_part = None

    # Check 1: Double quotes "$=..."
    check1_startswith = stripped_value.startswith('"$="')
    check1_endswith = stripped_value.endswith('"')
    if check1_startswith and check1_endswith:
        is_expression = True
        expression_part = stripped_value[3:-1].strip()
    else:
        # Check 2: Single quotes '$=...'
        check2_startswith = stripped_value.startswith("'$='")
        check2_endswith = stripped_value.endswith("'")
        if check2_startswith and check2_endswith:
             is_expression = True
             expression_part = stripped_value[3:-1].strip()
        else:
            # Check 3: No quotes, starts with =$
            check3_startswith = stripped_value.startswith('=$')
            if check3_startswith:
                is_expression = True
                expression_part = stripped_value[2:].strip()
            else:
                # Check 4: No quotes, starts with $=$
                check4_startswith = stripped_value.startswith('$=$')
                if check4_startswith:
                    is_expression = True
                    # Extract starting after the second '$'
                    expression_part = stripped_value[3:].strip()

    # --- Evaluate if it's an expression ---
    if is_expression and expression_part is not None:
        evaluated_result = _evaluate_jbeam_expression(expression_part, variable_cache, depth + 1)
        if evaluated_result is not None:
            return evaluated_result # Successfully evaluated
        else:
            # Evaluation failed, return the original value as a fallback
            return value # Return original UNSTRIPPED value on failure

    # --- NEW: Check for Simple Variable Reference '$varname' ---
    # Check if it starts with '$' but NOT '=$' or '$=$' (already handled above)
    # and doesn't have the quote wrappers.
    elif stripped_value.startswith('$') and not stripped_value.startswith(('=$', '$=$')):
        var_name = stripped_value # The stripped value is the variable name
        cached_var = variable_cache.get(var_name)

        if cached_var is not None:
            cached_value = cached_var['value']
            # Recursively resolve if the cached value is another expression or variable
            # Pass depth + 1 for recursion control
            return resolve_jbeam_variable_value(cached_value, variable_cache, depth + 1)
        else:
            # Variable not found in cache
            if var_name not in _reported_missing_vars_this_rebuild:
                print(f"Warning: Variable '{var_name}' not found in cache.", file=sys.stderr)
                _reported_missing_vars_this_rebuild.add(var_name)
            return value # Return original UNSTRIPPED value on failure
    # --- END NEW ---

    else:
        # Not an expression string or simple variable, return the original value
        return value # Return original UNSTRIPPED value
# <<< END MODIFIED FUNCTION >>>


# Refresh the current JBeam data based on the active object
def refresh_curr_vdata(force_refresh=False):
    # <<< MODIFIED: Add _reported_missing_vars_this_rebuild to globals >>>
    global veh_render_dirty, all_nodes_cache_dirty, warned_missing_nodes_this_rebuild, _reported_missing_vars_this_rebuild, _reported_unsupported_ops_this_rebuild
    context = bpy.context
    scene = context.scene
    ui_props = scene.ui_properties

    selected_obj_name = None
    jbeam_part = None
    is_new_jbeam_object = False

    obj = context.active_object
    if obj is not None:
        obj_data = obj.data
        if obj_data and obj_data.get(constants.MESH_JBEAM_PART) is not None:
            jbeam_part = obj_data.get(constants.MESH_JBEAM_PART)
            selected_obj_name = obj.name
            if jb_globals.prev_obj_selected != selected_obj_name:
                 is_new_jbeam_object = True
        else:
            selected_obj_name = None; jbeam_part = None
    else:
        selected_obj_name = None

    object_changed = jb_globals.prev_obj_selected != selected_obj_name

    if force_refresh or object_changed:
        if jbeam_part is not None and obj is not None:
            collection = obj.users_collection[0] if obj.users_collection else None
            veh_model = collection.get(constants.COLLECTION_VEHICLE_MODEL) if collection else None
            try:
                if veh_model is not None and collection.get(constants.COLLECTION_VEHICLE_BUNDLE):
                    jb_globals.curr_vdata = pickle.loads(base64.b64decode(collection[constants.COLLECTION_VEHICLE_BUNDLE]))['vdata']
                elif obj_data.get(constants.MESH_SINGLE_JBEAM_PART_DATA):
                    jb_globals.curr_vdata = pickle.loads(base64.b64decode(obj_data[constants.MESH_SINGLE_JBEAM_PART_DATA]))
                else:
                    jb_globals.curr_vdata = None
            except (TypeError, KeyError, EOFError, pickle.UnpicklingError, base64.binascii.Error) as e:
                 print(f"Error loading JBeam data for {selected_obj_name}: {e}", file=sys.stderr)
                 jb_globals.curr_vdata = None
        else:
            jb_globals.curr_vdata = None

        # Always mark caches dirty if object changed or forced refresh
        if is_new_jbeam_object or force_refresh:
             all_nodes_cache_dirty = True
             jb_globals.jbeam_variables_cache_dirty = True # <<< ADDED: Mark variable cache dirty >>>
             warned_missing_nodes_this_rebuild.clear() # <<< MOVED HERE: Clear missing node reports on significant refresh >>>
             _reported_missing_vars_this_rebuild.clear() # <<< MOVED HERE: Clear missing var reports on significant refresh >>>
             _reported_unsupported_ops_this_rebuild.clear() # <<< MOVED HERE: Clear unsupported ops report on significant refresh >>>

        veh_render_dirty = True

        # --- ADDED: Trigger variable cache update if dirty ---
        if jb_globals.jbeam_variables_cache_dirty:
            update_jbeam_variables_cache(context)
        # --- END ADDED ---

        # --- ADDED: Trigger highlight update on object change ---
        # If the object changed, it's a JBeam part, and highlighting is enabled,
        # re-run the highlight logic based on the current text editor state.
        if object_changed and jbeam_part is not None and ui_props.highlight_element_on_click:
            try:
                text_area = None
                # Find the first visible text editor
                if context.window_manager:
                    for window in context.window_manager.windows:
                        screen = window.screen
                        for area in screen.areas:
                            if area.type == "TEXT_EDITOR":
                                text_area = area
                                break
                        if text_area: break

                if text_area:
                    space = text_area.spaces.active
                    if space and space.text:
                        text_obj = space.text
                        current_line_index = text_obj.current_line_index
                        # Call the highlight function directly using the current text editor state
                        # This ensures the highlight reflects the newly selected object's context
                        find_and_highlight_element_for_line(context, text_obj, current_line_index)
                        # find_and_highlight_element_for_line now handles setting veh_render_dirty
                        # and tagging redraw, so no need to do it again here.
            except Exception as e:
                print(f"Error triggering highlight update on object change: {e}", file=sys.stderr)
                traceback.print_exc()
        # --- END ADDED ---

        jb_globals.prev_obj_selected = selected_obj_name


# --- START MOVED HIGHLIGHT LOGIC ---

def _tag_redraw_3d_views(context: bpy.types.Context):
    """Helper function to tag all 3D View areas for redraw."""
    if not context.window_manager: return
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

# <<< START MODIFIED FUNCTION find_and_highlight_element_for_line >>>
def find_and_highlight_element_for_line(context: bpy.types.Context, text_obj: bpy.types.Text, line_index: int):
    """
    Parses the JBeam file content around the given line index,
    identifies the *first* JBeam element (node, beam, rail, torsionbar, slidenode) definition overlapping that line,
    verifies its structural context, finds its coordinates (if applicable), and updates global highlight state.
    Returns True if an element was found and highlighted, False otherwise.
    """
    # <<< MODIFICATION: Access global highlight dirty flag >>>
    global part_name_to_obj, _highlight_dirty

    scene = context.scene
    ui_props = scene.ui_properties
    short_to_full_map = scene.get(SCENE_SHORT_TO_FULL_FILENAME, {})
    full_filepath = short_to_full_map.get(text_obj.name)

    # Clear previous highlight coordinates and node IDs first
    highlight_coords.clear()
    highlight_torsionbar_outer_coords.clear()
    highlight_torsionbar_mid_coords.clear()
    jb_globals.highlighted_node_ids.clear() # Clear the set
    jb_globals.highlighted_element_ordered_node_ids.clear() # Clear the ordered list
    # Store previous type before clearing, to check if redraw is needed on failure
    prev_highlight_type = jb_globals.highlighted_element_type
    jb_globals.highlighted_element_type = None # Assume failure until success

    # Update last info immediately, regardless of success/failure below,
    # to prevent re-processing the same line if nothing is found.
    jb_globals.last_text_area_info['name'] = text_obj.name
    jb_globals.last_text_area_info['line_index'] = line_index

    if not full_filepath:
        _tag_redraw_3d_views(context) # Always tag redraw for highlight update
        # <<< ADDED: Mark highlight dirty if it was previously active >>>
        if prev_highlight_type is not None: _highlight_dirty = True
        return False

    file_content = text_obj.as_string()
    if not file_content:
        _tag_redraw_3d_views(context) # Always tag redraw for highlight update
        # <<< ADDED: Mark highlight dirty if it was previously active >>>
        if prev_highlight_type is not None: _highlight_dirty = True
        return False # Cannot parse AST

    lines = file_content.splitlines(True) # Keep ends for accurate length
    if line_index >= len(lines):
        _tag_redraw_3d_views(context) # Always tag redraw for highlight update
        # <<< ADDED: Mark highlight dirty if it was previously active >>>
        if prev_highlight_type is not None: _highlight_dirty = True
        return False # Cursor out of bounds

    try:
        # --- Initialize variables ---
        node_ids = [] # This will store the ordered list from parsing (used for most elements)
        slidenode_node_id = None # <<< Specific storage for slidenode
        slidenode_rail_name = None # <<< Specific storage for slidenode
        element_type = None # Determined by AST context
        original_color = (1,1,1,1)
        original_mid_color = (1,0,0,1) # Default mid color
        beam_type_from_data = '|NORMAL' # Default beam type

        # --- AST Parsing and Context Check ---
        ast_data = sjsonast.parse(file_content)
        if not ast_data:
            _tag_redraw_3d_views(context) # Always tag redraw for highlight update
            # <<< ADDED: Mark highlight dirty if it was previously active >>>
            if prev_highlight_type is not None: _highlight_dirty = True
            return False # Cannot parse AST

        ast_nodes = ast_data['ast']['nodes']
        sjsonast.calculate_char_positions(ast_nodes)

        # Calculate character position range for the target line
        line_start_char_pos = sum(len(l) for l in lines[:line_index])
        line_end_char_pos = line_start_char_pos + len(lines[line_index]) # Exclusive end

        # Traverse AST to find the first element definition on the target line and check context
        stack = []
        in_dict = True
        pos_in_arr = 0
        temp_dict_key = None
        dict_key = None
        current_part_name = None
        current_section_name = None
        found_element_on_line = False
        # <<< NEW: Track rail context >>>
        in_rails_section_dict = False
        current_rail_name = None
        in_rail_links_array = False

        i = 0
        while i < len(ast_nodes):
            node: sjsonast.ASTNode = ast_nodes[i]
            node_type = node.data_type

            # Optimization: If node start is beyond the line end, stop searching
            if node.start_pos >= line_end_char_pos and not found_element_on_line:
                # Only break if we haven't already found the element start
                break

            if node_type == 'wsc':
                i += 1
                continue

            # --- Stack and Context Management ---
            # <<< MODIFIED: More detailed context tracking >>>
            if in_dict:
                if node_type == '{':
                    if dict_key is not None:
                        stack.append((dict_key, True)) # Parent was dict
                        if len(stack) == 1: current_part_name = dict_key
                        if len(stack) == 2 and dict_key == 'rails': in_rails_section_dict = True
                        if in_rails_section_dict and len(stack) == 3: current_rail_name = dict_key # Entering a specific rail's dict
                    dict_key = None; temp_dict_key = None; in_dict = True
                elif node_type == '[':
                    if dict_key is not None:
                        stack.append((dict_key, True)) # Parent was dict
                        if len(stack) == 1: current_part_name = dict_key
                        # <<< MODIFIED: Include 'slidenodes' >>>
                        if len(stack) == 2: current_section_name = dict_key # Entering nodes/beams/torsionbars/slidenodes array
                        # <<< NEW: Check if entering "links:" array >>>
                        if in_rails_section_dict and len(stack) == 4 and dict_key == 'links:':
                            in_rail_links_array = True
                    dict_key = None; temp_dict_key = None; in_dict = False
                elif node_type == '}':
                    if stack:
                        prev_key, prev_in_dict = stack.pop()
                        if len(stack) == 2 and prev_key == current_rail_name: current_rail_name = None # Exiting specific rail dict
                        if len(stack) == 1 and prev_key == 'rails': in_rails_section_dict = False
                        if len(stack) == 0: current_part_name = None
                        in_dict = prev_in_dict
                    else: in_dict = None
                elif node_type == ']':
                     pass # Should not be reached if structure is valid dict
                else:
                    if temp_dict_key is None and node_type == '"': temp_dict_key = node.value
                    elif node_type == ':': dict_key = temp_dict_key
                    elif dict_key is not None: # Value node after key:
                        # --- Check for overlap on value node ---
                        node_overlaps_line = (node.start_pos < line_end_char_pos and node.end_pos >= line_start_char_pos)
                        if node_overlaps_line and in_rails_section_dict and current_rail_name is not None and not found_element_on_line:

                             rail_dict_start_idx = -1
                             temp_k = i - 1 # Start searching backwards from the value node
                             open_brackets = 0
                             while temp_k >= 0:
                                 if ast_nodes[temp_k].data_type == '}': open_brackets += 1
                                 elif ast_nodes[temp_k].data_type == '{':
                                     if open_brackets == 0:
                                         rail_dict_start_idx = temp_k
                                         break
                                     open_brackets -= 1
                                 temp_k -= 1

                             if rail_dict_start_idx != -1:
                                 # Search forward from the rail's '{' for "links:"
                                 links_key_found = False
                                 temp_k = rail_dict_start_idx + 1
                                 while temp_k < len(ast_nodes):
                                     inner_node = ast_nodes[temp_k]
                                     if inner_node.data_type == '"' and inner_node.value == 'links:':
                                         links_key_found = True
                                     elif links_key_found and inner_node.data_type == '[':
                                         # Found the links array, parse it
                                         temp_node_ids = []
                                         l = temp_k + 1
                                         while l < len(ast_nodes):
                                             link_node = ast_nodes[l]
                                             if link_node.data_type == ']': break
                                             if link_node.data_type == '"': temp_node_ids.append(link_node.value)
                                             l += 1
                                         if len(temp_node_ids) == 2:
                                             element_type = 'rail'
                                             node_ids = temp_node_ids
                                             found_element_on_line = True
                                             break # Exit inner search loop
                                         else:
                                             break # Exit inner search loop
                                     elif inner_node.data_type == '}': # Reached end of rail dict
                                         break # Exit inner search loop
                                     temp_k += 1
                             if found_element_on_line: break # Exit outer loop if found

                        # Reset key tracking after processing value
                        dict_key = None; temp_dict_key = None
            else: # In array
                if node_type == '[': # Start of an array element (potential JBeam definition)
                    node_overlaps_line = (node.start_pos < line_end_char_pos and node.end_pos >= line_start_char_pos)

                    # <<< MODIFIED: Check array context, include 'slidenodes' >>>
                    is_element_array = (
                        (current_section_name == 'nodes' and len(stack) == 2) or
                        (current_section_name == 'beams' and len(stack) == 2) or
                        (current_section_name == 'torsionbars' and len(stack) == 2) or
                        (current_section_name == 'slidenodes' and len(stack) == 2) or # <<< ADDED: Check for slidenodes section
                        (in_rail_links_array and len(stack) == 4) # Check if it's the links array itself
                    )

                    if node_overlaps_line and is_element_array and not found_element_on_line:
                        # Parse content within this bracket pair from AST
                        temp_node_ids = []
                        temp_values_count = 0
                        k = i + 1
                        while k < len(ast_nodes):
                            inner_node = ast_nodes[k]
                            if inner_node.data_type == ']': break
                            if inner_node.data_type == '"': temp_node_ids.append(inner_node.value)
                            elif inner_node.data_type == 'number' or inner_node.data_type == 'bool': temp_values_count += 1
                            elif inner_node.data_type == '{': break # Options dict
                            k += 1

                        num_parsed_ids = len(temp_node_ids)

                        # Determine element type based on context and parsed content
                        if current_section_name == 'nodes' and num_parsed_ids == 1 and temp_values_count >= 3:
                            element_type = 'node'
                            node_ids = temp_node_ids
                            found_element_on_line = True
                        elif current_section_name == 'beams' and num_parsed_ids == 2:
                            element_type = 'beam'
                            node_ids = temp_node_ids
                            found_element_on_line = True
                        # <<< MODIFIED: Check rail context here >>>
                        elif in_rail_links_array and num_parsed_ids == 2:
                            element_type = 'rail'
                            node_ids = temp_node_ids
                            found_element_on_line = True
                        elif current_section_name == 'torsionbars' and num_parsed_ids == 4:
                            element_type = 'torsionbar'
                            node_ids = temp_node_ids
                            found_element_on_line = True
                        # <<< ADDED: Check for slidenodes >>>
                        elif current_section_name == 'slidenodes' and num_parsed_ids >= 2:
                            element_type = 'slidenode'
                            slidenode_node_id = temp_node_ids[0] # Store the node ID
                            slidenode_rail_name = temp_node_ids[1] # Store the rail name
                            found_element_on_line = True
                        # <<< END ADDED >>>

                        if found_element_on_line:
                            break # Found the first relevant element on the line
                        else:
                            pass # Keep searching

                    # Normal stack push if not the target element or context wrong
                    stack.append((pos_in_arr, False)) # Parent was array
                    pos_in_arr = 0; in_dict = False
                elif node_type == '{':
                    stack.append((pos_in_arr, False)) # Parent was array
                    pos_in_arr = 0; in_dict = True
                elif node_type == ']':
                    if stack:
                        prev_key_or_idx, prev_in_dict = stack.pop()
                        # <<< NEW: Check if exiting "links:" array >>>
                        if len(stack) == 3 and in_rail_links_array:
                            in_rail_links_array = False
                        # <<< MODIFIED: Include 'slidenodes' >>>
                        if len(stack) == 1: current_section_name = None # Exiting nodes/beams/torsionbars/slidenodes array
                        if len(stack) == 0: current_part_name = None
                        in_dict = prev_in_dict
                        pos_in_arr = prev_key_or_idx + 1 if not prev_in_dict else 0 # Restore position in parent
                    else: in_dict = None
                elif node_type == '}':
                     pass # Should not be reached if structure is valid array
                else: # Value node within array
                    pos_in_arr += 1
            i += 1
        # --- End AST Traversal ---

        # --- Further Processing & Highlighting ---
        if not found_element_on_line or element_type is None:
            _tag_redraw_3d_views(context) # Always tag redraw for highlight update
            # <<< ADDED: Mark highlight dirty if it was previously active >>>
            if prev_highlight_type is not None: _highlight_dirty = True
            return False # No valid element found on the line within correct context

        # --- Determine Color/Width based on AST-determined element_type ---
        if element_type == 'beam':
            # Determine specific beam type color/width
            if jb_globals.curr_vdata and 'beams' in jb_globals.curr_vdata:
                target_id1, target_id2 = node_ids[0], node_ids[1]
                active_obj = context.active_object
                target_part_origin = None
                if active_obj and active_obj.data:
                    target_part_origin = active_obj.data.get(constants.MESH_JBEAM_PART)

                if target_part_origin:
                    found_beam_data = None
                    for beam_data in jb_globals.curr_vdata['beams']:
                        if isinstance(beam_data, dict) and beam_data.get('partOrigin') == target_part_origin:
                            b_id1 = beam_data.get('id1:')
                            b_id2 = beam_data.get('id2:')
                            if (b_id1 == target_id1 and b_id2 == target_id2) or \
                               (b_id1 == target_id2 and b_id2 == target_id1):
                                found_beam_data = beam_data; break
                    if found_beam_data:
                        beam_type_from_data = found_beam_data.get('beamType', '|NORMAL')
                        if beam_type_from_data == '|ANISOTROPIC': original_color = ui_props.anisotropic_beam_color # <<< REMOVED width assignment
                        elif beam_type_from_data == '|SUPPORT': original_color = ui_props.support_beam_color # <<< REMOVED width assignment
                        elif beam_type_from_data == '|HYDRO': original_color = ui_props.hydro_beam_color # <<< REMOVED width assignment
                        elif beam_type_from_data == '|BOUNDED': original_color = ui_props.bounded_beam_color # <<< REMOVED width assignment
                        elif beam_type_from_data == '|LBEAM': original_color = ui_props.lbeam_beam_color # <<< REMOVED width assignment
                        elif beam_type_from_data == '|PRESSURED': original_color = ui_props.pressured_beam_color # <<< REMOVED width assignment
                        else: original_color = ui_props.beam_color # <<< REMOVED width assignment - Default normal
                    else: # Beam definition found on line, but not in curr_vdata (maybe newly added?)
                        original_color = ui_props.beam_color # <<< REMOVED width assignment - Use default normal
                else: # No target part origin found? Use default normal
                    original_color = ui_props.beam_color # <<< REMOVED width assignment
            else: # No beams in curr_vdata? Use default normal
                 original_color = ui_props.beam_color # <<< REMOVED width assignment

        elif element_type == 'rail':
             original_color = ui_props.rail_color # <<< REMOVED width assignment
        elif element_type == 'torsionbar':
             original_color = ui_props.torsionbar_color
             original_mid_color = ui_props.torsionbar_mid_color # <<< REMOVED width assignment
        # <<< ADDED: Slidenode color/width (uses rail settings) >>>
        elif element_type == 'slidenode':
             original_color = ui_props.rail_color # Use rail color for the line segment # <<< REMOVED width assignment
        # <<< END ADDED >>>

        # --- Common Logic: Find Node Positions and Set Highlight ---
        if element_type == 'node':
            # Node highlight logic
            jb_globals.highlighted_element_type = 'node'
            jb_globals.highlighted_node_ids.update(node_ids)
            jb_globals.highlighted_element_ordered_node_ids = node_ids
            _tag_redraw_3d_views(context) # Always tag redraw for highlight update
            # <<< ADDED: Mark highlight dirty >>>
            _highlight_dirty = True

            # <<< START MODIFIED LOGIC >>>
            # If exactly one node is highlighted, update the search field
            if len(node_ids) == 1:
                single_node_id = node_ids[0]
                # Check if the UI property exists and update it
                if hasattr(ui_props, 'search_node_id'):
                    # Only update if the value is different to avoid unnecessary updates
                    # and redundant flag setting.
                    if ui_props.search_node_id != single_node_id:
                        # Set the flag *before* assigning the value
                        jb_globals._populating_search_id_from_highlight = True
                        # Assign the value (this will trigger the update callback)
                        ui_props.search_node_id = single_node_id
                        # The flag will be reset within the update callback itself
            # <<< END MODIFIED LOGIC >>>

            return True

        # --- Find Node Positions (For Beams, Rails, Torsionbars, Slidenodes) ---
        active_obj = context.active_object
        active_part_name = None
        collection = None
        is_vehicle_part = False
        if active_obj and active_obj.data:
            active_part_name = active_obj.data.get(constants.MESH_JBEAM_PART)
            collection = active_obj.users_collection[0] if active_obj.users_collection else None
            is_vehicle_part = collection is not None and collection.get(constants.COLLECTION_VEHICLE_MODEL) is not None

        temp_node_map = {}
        if is_vehicle_part and collection:
            if not part_name_to_obj:
                 for obj_iter in collection.all_objects:
                    if obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART):
                        part_name_to_obj[obj_iter.data[constants.MESH_JBEAM_PART]] = obj_iter

            for obj_iter in collection.all_objects:
                 if obj_iter.visible_get() and obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART) is not None:
                    obj_iter_data = obj_iter.data
                    temp_bm = None
                    try:
                        # <<< START MODIFICATION >>>
                        if obj_iter == active_obj and active_obj.mode == 'EDIT':
                            try:
                                temp_bm = bmesh.from_edit_mesh(obj_iter_data)
                            except ValueError:
                                # Mesh not ready for edit mode access yet, skip this object for now
                                _tag_redraw_3d_views(context) # Ensure redraw if highlight was previously active
                                # <<< ADDED: Mark highlight dirty if it was previously active >>>
                                if prev_highlight_type is not None: _highlight_dirty = True
                                return False # Abort highlight attempt for this cycle
                        # <<< END MODIFICATION >>>
                        else: # Object mode or not the active object
                            temp_bm = bmesh.new(); temp_bm.from_mesh(obj_iter_data)

                        node_id_layer = temp_bm.verts.layers.string.get(constants.VL_NODE_ID)
                        is_fake_layer = temp_bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
                        if node_id_layer and is_fake_layer:
                            temp_bm.verts.ensure_lookup_table()
                            obj_matrix_copy = obj_iter.matrix_world.copy()
                            for v in temp_bm.verts:
                                if v[is_fake_layer] == 0:
                                    nid = v[node_id_layer].decode('utf-8')
                                    temp_node_map[nid] = (v.co.copy(), obj_matrix_copy)
                    finally:
                        if temp_bm and not (obj_iter == active_obj and active_obj.mode == 'EDIT'): temp_bm.free()
        elif active_obj and active_part_name: # Single part import
             temp_bm = None
             try:
                # <<< START MODIFICATION >>>
                if active_obj.mode == 'EDIT':
                    try:
                        temp_bm = bmesh.from_edit_mesh(active_obj.data)
                    except ValueError:
                        # Mesh not ready for edit mode access yet, skip highlight
                        _tag_redraw_3d_views(context) # Ensure redraw if highlight was previously active
                        # <<< ADDED: Mark highlight dirty if it was previously active >>>
                        if prev_highlight_type is not None: _highlight_dirty = True
                        return False # Abort highlight attempt for this cycle
                # <<< END MODIFICATION >>>
                else: # Object mode
                    temp_bm = bmesh.new(); temp_bm.from_mesh(active_obj.data)

                node_id_layer = temp_bm.verts.layers.string.get(constants.VL_NODE_ID)
                is_fake_layer = temp_bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
                if node_id_layer and is_fake_layer:
                    temp_bm.verts.ensure_lookup_table()
                    obj_matrix_copy = active_obj.matrix_world.copy()
                    for v in temp_bm.verts:
                        if v[is_fake_layer] == 0:
                            nid = v[node_id_layer].decode('utf-8')
                            temp_node_map[nid] = (v.co.copy(), obj_matrix_copy)
             finally:
                 if temp_bm and not (active_obj.mode == 'EDIT'): temp_bm.free()

        # --- Get World Positions and Check Origins ---
        world_positions = [] # Used for beams, rails, torsionbars
        missing_nodes = []
        node_origins = {} # Used for beams, rails, torsionbars
        highlight_set = False # <<< Initialize highlight_set >>>

        # <<< ADDED: Slidenode specific position finding >>>
        if element_type == 'slidenode':
            rail_node_id1 = None
            rail_node_id2 = None
            # Find the rail definition in curr_vdata
            if jb_globals.curr_vdata and 'rails' in jb_globals.curr_vdata:
                rails_data = jb_globals.curr_vdata['rails']
                if isinstance(rails_data, dict):
                    rail_info = rails_data.get(slidenode_rail_name)
                    if isinstance(rail_info, list) and len(rail_info) == 2:
                        rail_node_id1, rail_node_id2 = rail_info[0], rail_info[1]
                    elif isinstance(rail_info, dict):
                        links = rail_info.get('links:')
                        if isinstance(links, list) and len(links) == 2:
                            rail_node_id1, rail_node_id2 = links[0], links[1]

            if rail_node_id1 is None or rail_node_id2 is None:
                print(f"Warning: Could not find rail definition for '{slidenode_rail_name}' referenced by slidenode '{slidenode_node_id}'.") # <<< Added slidenode_node_id for context
                _tag_redraw_3d_views(context) # Always tag redraw for highlight update
                # <<< ADDED: Mark highlight dirty if it was previously active >>>
                if prev_highlight_type is not None: _highlight_dirty = True
                return False # Cannot highlight if rail nodes not found

            # <<< START: Combined check for slidenode_node_id AND rail nodes in main 'nodes' section >>>
            nodes_section_valid = jb_globals.curr_vdata and 'nodes' in jb_globals.curr_vdata
            slidenode_id_valid = nodes_section_valid and slidenode_node_id in jb_globals.curr_vdata['nodes']
            rail_node1_valid = nodes_section_valid and rail_node_id1 in jb_globals.curr_vdata['nodes']
            rail_node2_valid = nodes_section_valid and rail_node_id2 in jb_globals.curr_vdata['nodes']

            if not nodes_section_valid:
                print(f"Warning: Cannot validate slidenode '{slidenode_node_id}' - 'nodes' section missing from JBeam data.")
                _tag_redraw_3d_views(context)
                if prev_highlight_type is not None: _highlight_dirty = True
                return False
            if not slidenode_id_valid:
                print(f"Warning: Slidenode's own node ID '{slidenode_node_id}' not found in main 'nodes' section.")
                _tag_redraw_3d_views(context)
                if prev_highlight_type is not None: _highlight_dirty = True
                return False
            if not rail_node1_valid:
                print(f"Warning: Rail node '{rail_node_id1}' (for slidenode '{slidenode_node_id}') not found in main 'nodes' section.")
                _tag_redraw_3d_views(context)
                if prev_highlight_type is not None: _highlight_dirty = True
                return False
            if not rail_node2_valid:
                print(f"Warning: Rail node '{rail_node_id2}' (for slidenode '{slidenode_node_id}') not found in main 'nodes' section.")
                _tag_redraw_3d_views(context)
                if prev_highlight_type is not None: _highlight_dirty = True
                return False
            # <<< END: Combined check >>>

            # Now find positions for slidenode_node_id, rail_node_id1, rail_node_id2
            node_ids_to_find = [slidenode_node_id, rail_node_id1, rail_node_id2]
            slidenode_world_positions = {} # Use a dict for slidenode specific positions

            for node_id in node_ids_to_find:
                wp = None
                pos_data = temp_node_map.get(node_id)
                cache_data = all_nodes_cache.get(node_id)
                if pos_data:
                    wp = pos_data[1] @ pos_data[0]
                elif cache_data:
                    wp = cache_data[0]

                if wp is None:
                    # This check should ideally not be hit if the checks above passed,
                    # but keep it as a fallback for geometry/cache issues.
                    missing_nodes.append(node_id)
                slidenode_world_positions[node_id] = wp

            if missing_nodes:
                # Print a different warning if nodes were found in data but not in geometry/cache
                print(f"Warning: Could not find geometry/cache position for slidenode nodes: {missing_nodes}")
                _tag_redraw_3d_views(context) # Always tag redraw for highlight update
                # <<< ADDED: Mark highlight dirty if it was previously active >>>
                if prev_highlight_type is not None: _highlight_dirty = True
                return False

            # Populate highlight data for slidenode
            jb_globals.highlighted_element_type = 'slidenode'
            jb_globals.highlighted_node_ids.add(slidenode_node_id) # Add only the slidenode's ID for text coloring
            # Store the rail node IDs for highlight coordinate population during rebuild
            jb_globals.highlighted_element_ordered_node_ids = [rail_node_id1, rail_node_id2]
            # Add rail segment coordinates
            highlight_coords.extend([slidenode_world_positions[rail_node_id1], slidenode_world_positions[rail_node_id2]])
            jb_globals.highlighted_element_color = original_color # Use rail color set earlier
            highlight_set = True
        # <<< END ADDED: Slidenode specific position finding >>>
        else: # Logic for beams, rails, torsionbars
            for node_id in node_ids: # Use the ordered list
                wp = None
                pos_data = temp_node_map.get(node_id)
                cache_data = all_nodes_cache.get(node_id)

                if pos_data:
                    wp = pos_data[1] @ pos_data[0]
                    found_origin = None
                    if is_vehicle_part and collection:
                        for obj_iter in collection.all_objects:
                            if (obj_iter.matrix_world - pos_data[1]).is_zero(1e-5):
                                 if obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART):
                                     found_origin = obj_iter.data[constants.MESH_JBEAM_PART]; break
                    elif active_part_name: found_origin = active_part_name
                    node_origins[node_id] = found_origin if found_origin else '?'

                elif cache_data:
                    wp = cache_data[0]
                    node_origins[node_id] = cache_data[2]

                if wp is None: missing_nodes.append(node_id)
                world_positions.append(wp)

            if missing_nodes:
                _tag_redraw_3d_views(context) # Always tag redraw for highlight update
                # <<< ADDED: Mark highlight dirty if it was previously active >>>
                if prev_highlight_type is not None: _highlight_dirty = True
                return False

            # --- Populate Coordinate Lists and Finalize Highlight ---
            if element_type == 'beam':
                is_cross_part_candidate = False
                if len(node_origins) == 2:
                    origin1 = node_origins.get(node_ids[0], '?')
                    origin2 = node_origins.get(node_ids[1], '?')
                    if origin1 != origin2 and '?' not in {origin1, origin2}:
                        is_cross_part_candidate = True

                id1_in_active_geom = node_ids[0] in temp_node_map
                id2_in_active_geom = node_ids[1] in temp_node_map

                if is_cross_part_candidate and id1_in_active_geom and id2_in_active_geom:
                     # Use correct normal color based on beamType (determined earlier)
                     # The original_color/width are already set correctly for the beam type
                     highlight_coords.extend([world_positions[0], world_positions[1]])
                     highlight_set = True
                elif is_cross_part_candidate:
                    element_type = 'cross_part_beam' # Reclassify for drawing
                    original_color = ui_props.cross_part_beam_color # Use cross-part color
                    highlight_coords.extend([world_positions[0], world_positions[1]])
                    highlight_set = True
                else: # Normal beam within the same part
                    # Use the color/width determined earlier based on beamType
                    highlight_coords.extend([world_positions[0], world_positions[1]])
                    highlight_set = True

            elif element_type == 'torsionbar':
                highlight_torsionbar_outer_coords.extend([world_positions[0], world_positions[1]])
                highlight_torsionbar_mid_coords.extend([world_positions[1], world_positions[2]])
                highlight_torsionbar_outer_coords.extend([world_positions[2], world_positions[3]])
                highlight_set = True
                # Colors/width already set

            elif element_type == 'rail':
                 # Cross-part check for rails
                 is_cross_part_candidate = False
                 if len(node_origins) == 2:
                     origin1 = node_origins.get(node_ids[0], '?')
                     origin2 = node_origins.get(node_ids[1], '?')
                     if origin1 != origin2 and '?' not in {origin1, origin2}:
                         is_cross_part_candidate = True

                 id1_in_active_geom = node_ids[0] in temp_node_map
                 id2_in_active_geom = node_ids[1] in temp_node_map

                 if is_cross_part_candidate and id1_in_active_geom and id2_in_active_geom:
                     # Still a rail, just happens to use nodes from different parts' geometry
                     original_color = ui_props.rail_color # Use rail color
                     highlight_coords.extend([world_positions[0], world_positions[1]])
                     highlight_set = True
                 elif is_cross_part_candidate:
                      element_type = 'cross_part_beam' # Reclassify for drawing as cross-part
                      # original_color = ui_props.cross_part_beam_color
                      original_color = ui_props.rail_color # Use rail color
                      highlight_coords.extend([world_positions[0], world_positions[1]])
                      highlight_set = True
                 else: # Normal rail within the same part
                     # Colors/width already set
                     highlight_coords.extend([world_positions[0], world_positions[1]])
                     highlight_set = True

        # --- Finalize Highlight State ---
        if highlight_set:
            jb_globals.highlighted_element_type = element_type
            jb_globals.highlighted_element_color = original_color
            jb_globals.highlighted_element_mid_color = original_mid_color
            # Only add node IDs for non-slidenode types here, slidenode handled above
            if element_type != 'slidenode':
                jb_globals.highlighted_node_ids.update(node_ids)
                jb_globals.highlighted_element_ordered_node_ids = node_ids
            _tag_redraw_3d_views(context) # Always tag redraw for highlight update
            # <<< ADDED: Mark highlight dirty >>>
            _highlight_dirty = True
        else:
             _tag_redraw_3d_views(context) # Always tag redraw for highlight update
             # <<< ADDED: Mark highlight dirty if it was previously active >>>
             if prev_highlight_type is not None: _highlight_dirty = True

        return highlight_set

    except Exception as e:
        print(f"EXCEPTION in find_and_highlight_element_for_line: {e}", file=sys.stderr)
        traceback.print_exc()
        # Clear highlight on exception
        highlight_coords.clear()
        highlight_torsionbar_outer_coords.clear()
        highlight_torsionbar_mid_coords.clear()
        jb_globals.highlighted_node_ids.clear()
        jb_globals.highlighted_element_ordered_node_ids.clear()
        jb_globals.highlighted_element_type = None # <<< Ensure type is cleared
        _tag_redraw_3d_views(context) # Always tag redraw for highlight update
        # <<< ADDED: Mark highlight dirty if it was previously active >>>
        if prev_highlight_type is not None: _highlight_dirty = True
        return False
# <<< END MODIFIED FUNCTION find_and_highlight_element_for_line >>>

# --- END MOVED HIGHLIGHT LOGIC ---


# Draws Node IDs and tooltips
def draw_callback_px(context: bpy.types.Context):
    # ... (existing setup: scene, ui_props, font_id, active_obj checks, etc.) ...
    global part_name_to_obj
    # <<< ADDED: Access global node thresholds >>>
    global auto_node_weight_min, auto_node_weight_max, auto_node_thresholds_valid

    scene = context.scene
    ui_props = scene.ui_properties
    if not hasattr(context, 'scene') or not hasattr(scene, 'ui_properties'):
        return
    font_id = 0

    active_obj = context.active_object
    is_valid_jbeam_obj = False
    is_selected = False
    is_editing_enabled = False
    if active_obj and active_obj.data and active_obj.data.get(constants.MESH_JBEAM_PART) is not None:
        is_valid_jbeam_obj = True
        is_editing_enabled = active_obj.data.get(constants.MESH_EDITING_ENABLED, False)
        if active_obj in context.selected_objects:
            is_selected = True

    should_draw = is_valid_jbeam_obj and is_selected # Allow drawing even if editing disabled for tooltips/IDs
    if not should_draw: return
    active_obj_data = active_obj.data

    collection = active_obj.users_collection[0] if active_obj.users_collection else None
    is_vehicle_part = collection is not None and collection.get(constants.COLLECTION_VEHICLE_MODEL) is not None

    bm = None
    try:
        # Only get bmesh if editing is enabled and in edit mode
        if is_editing_enabled and active_obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(active_obj_data)
        elif not is_vehicle_part: # For single part, get bmesh even in object mode for drawing
            bm = bmesh.new(); bm.from_mesh(active_obj_data)
    except Exception as e:
        print(f"Error accessing bmesh for {active_obj.name}: {e}", file=sys.stderr)
        if not is_vehicle_part: return

    ctxRegion = context.region
    ctxRegionData = context.region_data
    lblfPosition = blfpos; lblfDraw = blfdraw; lblfDims = blfdims
    blfsize(font_id, ui_props.node_id_font_size)
    default_color = (1.0, 1.0, 1.0, 1.0)
    selected_color = (0.0, 0.85, 0.0, 1.0) # Darker-Green for highlighted nodes (text editor)
    yellow_color = (1.0, 1.0, 0.0, 1.0) # Yellow color for viewport selection
    orange_color = (1.0, 0.5, 0.0, 1.0) # Orange color for both selected and highlighted
    black_color = (0.0, 0.0, 0.0, 1.0)
    outline_size = ui_props.node_id_outline_size
    highlighted_cross_part_color = (1.0, 0.5, 1.0, 1.0)
    # <<< ADDED: Slidenode color >>>
    slidenode_color = (1.0, 0.7, 0.7, 1.0) # Light pink
    # <<< ADDED: Get text offset >>>
    text_offset = ui_props.node_id_text_offset

    # <<< START MODIFICATION: Add apply_offset parameter >>>
    def draw_text_with_outline(font_id, text, x, y, text_color, apply_offset=True):
        base_x = x
        base_y = y
        # Conditionally apply offset
        if apply_offset:
            base_x += text_offset
            base_y += text_offset
    # <<< END MODIFICATION >>>

        # ... (outline drawing logic remains the same, but uses base_x, base_y) ...
        if outline_size > 0:
            blfcolor(font_id, *black_color)
            # Use base_x, base_y for all positioning
            lblfPosition(font_id, base_x - outline_size, base_y, 0); lblfDraw(font_id, text)
            lblfPosition(font_id, base_x + outline_size, base_y, 0); lblfDraw(font_id, text)
            lblfPosition(font_id, base_x, base_y - outline_size, 0); lblfDraw(font_id, text)
            lblfPosition(font_id, base_x, base_y + outline_size, 0); lblfDraw(font_id, text)
            if outline_size > 1:
                 lblfPosition(font_id, base_x - outline_size, base_y - outline_size, 0); lblfDraw(font_id, text)
                 lblfPosition(font_id, base_x + outline_size, base_y - outline_size, 0); lblfDraw(font_id, text)
                 lblfPosition(font_id, base_x - outline_size, base_y + outline_size, 0); lblfDraw(font_id, text)
                 lblfPosition(font_id, base_x + outline_size, base_y + outline_size, 0); lblfDraw(font_id, text)
        blfcolor(font_id, *text_color)
        # Use base_x, base_y for the final text draw
        lblfPosition(font_id, base_x, base_y, 0)
        lblfDraw(font_id, text)

    active_object_defined_node_ids = set()

    # --- Node ID Drawing ---
    if ui_props.toggle_node_ids_text:
        selected_indices_set = set()
        if is_editing_enabled and active_obj.mode == 'EDIT':
            selected_indices_set = {idx for idx, _ in jb_globals.selected_nodes}

        highlighted_nodes = jb_globals.highlighted_node_ids

        # <<< ADDED: Get dynamic coloring settings once >>>
        use_dynamic_node_color = ui_props.use_dynamic_node_coloring
        use_auto_node_thresh = ui_props.use_auto_node_thresholds
        node_low_thresh = ui_props.dynamic_node_color_threshold_low
        node_high_thresh = ui_props.dynamic_node_color_threshold_high
        # <<< END ADDED >>>

        # --- Vehicle Part Iteration ---
        if is_vehicle_part:
            # ... (part_name_to_obj population) ...
            if not part_name_to_obj:
                 for obj_iter in collection.all_objects:
                    if obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART):
                        part_name_to_obj[obj_iter.data[constants.MESH_JBEAM_PART]] = obj_iter
            # ... (loop through part_name_to_obj) ...
            for part_name, obj in part_name_to_obj.items():
                # ... (visibility check, bmesh setup/cleanup) ...
                # ... (layer checks) ...
                if not obj.visible_get(): continue
                part_bm = None; obj_data = obj.data
                try:
                    # ... (bmesh acquisition) ...
                    if obj == active_obj and active_obj.mode == 'EDIT': bm = bmesh.from_edit_mesh(obj_data)
                    else: bm = bmesh.new(); bm.from_mesh(obj_data)

                    node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
                    is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
                    # <<< ADDED: Get part origin layer >>>
                    node_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN)

                    if not node_id_layer or not is_fake_layer or not node_origin_layer: # <<< Check origin layer
                        if bm != active_obj_data and bm: bm.free() # Corrected cleanup check
                        continue
                    bm.verts.ensure_lookup_table()

                    for v in bm.verts:
                        if v[is_fake_layer] == 1 or v.hide: continue
                        coord = obj.matrix_world @ v.co
                        node_id = v[node_id_layer].decode('utf-8')
                        node_origin = v[node_origin_layer].decode('utf-8') # <<< Get node origin

                        if obj == active_obj:
                            active_object_defined_node_ids.add(node_id)

                        pos_text = location_3d_to_region_2d(ctxRegion, ctxRegionData, coord)
                        if pos_text:
                            # --- Determine Color ---
                            # <<< MODIFICATION START >>>
                            should_draw_node = True # Assume we should draw unless calculation fails
                            # <<< MODIFICATION END >>>
                            text_color = default_color # Start with default
                            is_selected_in_viewport = obj == active_obj and is_editing_enabled and v.index in selected_indices_set
                            is_highlighted_by_text = node_id in highlighted_nodes
                            is_slidenode_highlight = jb_globals.highlighted_element_type == 'slidenode' and is_highlighted_by_text

                            # Apply dynamic color first if enabled and not selected/highlighted
                            if use_dynamic_node_color and not is_selected_in_viewport and not is_highlighted_by_text:
                                node_data = None
                                # Try getting node data from curr_vdata (might be slightly out of date but faster)
                                if jb_globals.curr_vdata and 'nodes' in jb_globals.curr_vdata:
                                    node_data = jb_globals.curr_vdata['nodes'].get(node_id)

                                # Fallback: Parse the specific file if not in curr_vdata (slower)
                                if node_data is None:
                                    short_to_full_map = scene.get(SCENE_SHORT_TO_FULL_FILENAME, {})
                                    node_filepath = None
                                    for short, full in short_to_full_map.items():
                                        # Find the file containing this node's origin part
                                        # This is complex, maybe skip fallback for performance?
                                        # For now, let's rely on curr_vdata
                                        pass # Placeholder

                                if node_data and isinstance(node_data, dict):
                                    node_weight_raw = node_data.get('nodeWeight')
                                    if node_weight_raw is not None:
                                        # Use auto thresholds if enabled and valid
                                        low_thresh = auto_node_weight_min if use_auto_node_thresh and auto_node_thresholds_valid else node_low_thresh
                                        high_thresh = auto_node_weight_max if use_auto_node_thresh and auto_node_thresholds_valid else node_high_thresh

                                        dynamic_color = _calculate_dynamic_color(node_weight_raw, low_thresh, high_thresh)
                                        if dynamic_color:
                                            text_color = dynamic_color # Apply dynamic color
                                        # <<< MODIFICATION START >>>
                                        else:
                                            # Calculation failed, mark this node to NOT be drawn
                                            should_draw_node = False
                                        # <<< MODIFICATION END >>>

                            # Override dynamic color with selection/highlight colors (only if drawing)
                            # <<< ADDED CHECK >>>
                            if should_draw_node:
                                if is_selected_in_viewport and is_slidenode_highlight: text_color = orange_color
                                elif is_slidenode_highlight: text_color = slidenode_color
                                elif is_selected_in_viewport and is_highlighted_by_text: text_color = orange_color
                                elif is_highlighted_by_text: text_color = selected_color
                                elif is_selected_in_viewport: text_color = yellow_color
                            # --- End Determine Color ---

                            # <<< MODIFICATION START >>>
                            # Only draw if should_draw_node is True
                            if should_draw_node:
                                draw_text_with_outline(font_id, node_id, pos_text[0], pos_text[1], text_color)
                            # <<< MODIFICATION END >>>
                except Exception as e: print(f"Error processing part {obj.name} for drawing: {e}", file=sys.stderr)
                finally:
                     if bm and not (obj == active_obj and active_obj.mode == 'EDIT'): bm.free() # Corrected cleanup check

        # --- Single Part Iteration ---
        elif bm: # bm is guaranteed to be for the active object here
            node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
            is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
            # <<< ADDED: Get part origin layer >>>
            node_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN)

            if node_id_layer and is_fake_layer and node_origin_layer: # <<< Check origin layer
                bm.verts.ensure_lookup_table()
                for v in bm.verts:
                    if v[is_fake_layer] == 1 or v.hide: continue
                    coord = active_obj.matrix_world @ v.co
                    node_id = v[node_id_layer].decode('utf-8')
                    node_origin = v[node_origin_layer].decode('utf-8') # <<< Get node origin

                    active_object_defined_node_ids.add(node_id)

                    pos_text = location_3d_to_region_2d(ctxRegion, ctxRegionData, coord)
                    if pos_text:
                        # --- Determine Color (Single Part) ---
                        # <<< MODIFICATION START >>>
                        should_draw_node = True # Assume we should draw unless calculation fails
                        # <<< MODIFICATION END >>>
                        text_color = default_color # Start with default
                        is_selected_in_viewport = is_editing_enabled and v.index in selected_indices_set
                        is_highlighted_by_text = node_id in highlighted_nodes
                        is_slidenode_highlight = jb_globals.highlighted_element_type == 'slidenode' and is_highlighted_by_text

                        # Apply dynamic color first if enabled and not selected/highlighted
                        if use_dynamic_node_color and not is_selected_in_viewport and not is_highlighted_by_text:
                            node_data = None
                            if jb_globals.curr_vdata and 'nodes' in jb_globals.curr_vdata:
                                node_data = jb_globals.curr_vdata['nodes'].get(node_id)

                            if node_data and isinstance(node_data, dict):
                                node_weight_raw = node_data.get('nodeWeight')
                                if node_weight_raw is not None:
                                    # Use auto thresholds if enabled and valid
                                    low_thresh = auto_node_weight_min if use_auto_node_thresh and auto_node_thresholds_valid else node_low_thresh
                                    high_thresh = auto_node_weight_max if use_auto_node_thresh and auto_node_thresholds_valid else node_high_thresh

                                    dynamic_color = _calculate_dynamic_color(node_weight_raw, low_thresh, high_thresh)
                                    if dynamic_color:
                                        text_color = dynamic_color # Apply dynamic color
                                    # <<< MODIFICATION START >>>
                                    else:
                                        # Calculation failed, mark this node to NOT be drawn
                                        should_draw_node = False
                                    # <<< MODIFICATION END >>>

                        # Override dynamic color with selection/highlight colors (only if drawing)
                        # <<< ADDED CHECK >>>
                        if should_draw_node:
                            if is_selected_in_viewport and is_slidenode_highlight: text_color = orange_color
                            elif is_slidenode_highlight: text_color = slidenode_color
                            elif is_selected_in_viewport and is_highlighted_by_text: text_color = orange_color
                            elif is_highlighted_by_text: text_color = selected_color
                            elif is_selected_in_viewport: text_color = yellow_color
                        # --- End Determine Color (Single Part) ---

                        # <<< MODIFICATION START >>>
                        # Only draw if should_draw_node is True
                        if should_draw_node:
                            draw_text_with_outline(font_id, node_id, pos_text[0], pos_text[1], text_color)
                        # <<< MODIFICATION END >>>

    # --- Cross-Part Node ID Drawing ---
    # ... (existing logic, no changes needed here as dynamic color doesn't apply to cross-part nodes) ...
    if ui_props.toggle_node_ids_text and all_nodes_cache:
        # ... (target_other_part_node_ids population) ...
        # ... (iterate through all_nodes_cache) ...
        cross_part_color = ui_props.cross_part_beam_color
        target_other_part_node_ids = set()
        active_part_name = active_obj_data.get(constants.MESH_JBEAM_PART)
        active_filepath = active_obj_data.get(constants.MESH_JBEAM_FILE_PATH)
        highlighted_nodes = jb_globals.highlighted_node_ids # Use the set here

        # Logic to populate target_other_part_node_ids remains the same
        if active_part_name and active_filepath:
            part_data = None
            if jb_globals.curr_vdata and active_part_name in jb_globals.curr_vdata:
                part_data = jb_globals.curr_vdata[active_part_name]
            else:
                full_file_data, _ = jbeam_io.get_jbeam(active_filepath, True, False)
                if full_file_data and active_part_name in full_file_data:
                    part_data = full_file_data[active_part_name]

            if isinstance(part_data, dict):
                # Check Beams, Torsionbars, Rails... (as before)
                if 'beams' in part_data and isinstance(part_data['beams'], list):
                    for beam in part_data['beams']:
                        id1, id2 = None, None
                        if isinstance(beam, dict): id1, id2 = beam.get('id1:'), beam.get('id2:')
                        elif isinstance(beam, list) and len(beam) >= 2: id1, id2 = beam[0], beam[1]
                        if id1 and id2:
                            node1_cache_data = all_nodes_cache.get(id1)
                            node2_cache_data = all_nodes_cache.get(id2)
                            if node1_cache_data and node1_cache_data[2] != active_part_name: target_other_part_node_ids.add(id1)
                            if node2_cache_data and node2_cache_data[2] != active_part_name: target_other_part_node_ids.add(id2)
                # ... (Check Torsionbars, Rails as before) ...
                if 'torsionbars' in part_data and isinstance(part_data['torsionbars'], list):
                    for tb in part_data['torsionbars']:
                        tb_node_ids = []
                        if isinstance(tb, dict): tb_node_ids = [tb.get(f'id{i}:') for i in range(1, 5)]
                        elif isinstance(tb, list) and len(tb) >= 4: tb_node_ids = tb[:4]
                        if len(tb_node_ids) == 4 and all(isinstance(nid, str) for nid in tb_node_ids):
                            for node_id in tb_node_ids:
                                cache_data = all_nodes_cache.get(node_id)
                                if cache_data and cache_data[2] != active_part_name: target_other_part_node_ids.add(node_id)
                if 'rails' in part_data and isinstance(part_data['rails'], dict):
                    for rail_name, rail_info in part_data['rails'].items():
                        rail_node_ids = None
                        if isinstance(rail_info, list) and len(rail_info) == 2: rail_node_ids = rail_info
                        elif isinstance(rail_info, dict): rail_node_ids = rail_info.get('links:')
                        if isinstance(rail_node_ids, list) and len(rail_node_ids) == 2:
                            for node_id in rail_node_ids:
                                cache_data = all_nodes_cache.get(node_id)
                                if cache_data and cache_data[2] != active_part_name: target_other_part_node_ids.add(node_id)

        # Iterate through cache to draw cross-part nodes
        for node_id, (world_pos, _, part_origin) in all_nodes_cache.items():
            if node_id in active_object_defined_node_ids:
                continue
            if node_id in target_other_part_node_ids:
                pos_text = location_3d_to_region_2d(ctxRegion, ctxRegionData, world_pos)
                if pos_text:
                    text_color = cross_part_color
                    if node_id in highlighted_nodes:
                        text_color = highlighted_cross_part_color
                    draw_text_with_outline(font_id, node_id, pos_text[0], pos_text[1], text_color)


    # --- Tooltip Positioning & Drawing ---
    # ... (existing tooltip logic, no changes needed here) ...
    padding_x = ui_props.tooltip_padding_x
    padding_y = 20 # Keep vertical padding hardcoded for now
    region_width = ctxRegion.width; region_height = ctxRegion.height
    line_height = lblfDims(font_id, "X")[1]; line_padding = 4
    tooltip_placement = ui_props.tooltip_placement

    # Calculate the reference X coordinate based on placement
    ref_x = 0
    if tooltip_placement == 'BOTTOM_LEFT':
        ref_x = padding_x
    elif tooltip_placement == 'BOTTOM_CENTER':
        ref_x = region_width / 2
    elif tooltip_placement == 'BOTTOM_RIGHT':
        ref_x = region_width - padding_x

    if is_editing_enabled:
        # --- Beam Tooltips ---
        beam_params_height = 0; beam_line_height_offset = 0
        if ui_props.toggle_params_tooltip and jb_globals._selected_beam_params_info is not None: # Use shared toggle
            params_list = jb_globals._selected_beam_params_info.get('params_list')
            if params_list: beam_params_height = len(params_list) * (line_height + line_padding)

        if ui_props.toggle_line_tooltip and jb_globals._selected_beam_line_info is not None: # Use shared toggle
            line_num = jb_globals._selected_beam_line_info.get('line')
            if line_num is not None:
                beam_line_y = padding_y + beam_params_height
                beam_line_height_offset = line_height + line_padding
                line_text = f"Line: {line_num}"
                line_width = lblfDims(font_id, line_text)[0]

                # Calculate draw_x based on placement and width
                draw_x = ref_x
                if tooltip_placement == 'BOTTOM_CENTER':
                    draw_x = ref_x - line_width / 2
                elif tooltip_placement == 'BOTTOM_RIGHT':
                    draw_x = ref_x - line_width

                # Pass calculated draw_x and apply_offset=False
                draw_text_with_outline(font_id, line_text, draw_x, beam_line_y, ui_props.line_tooltip_color, apply_offset=False)

        if ui_props.toggle_params_tooltip and jb_globals._selected_beam_params_info is not None: # Use shared toggle
            params_list = jb_globals._selected_beam_params_info.get('params_list')
            if params_list:
                name_color = ui_props.params_tooltip_color; value_color = ui_props.params_value_tooltip_color # Use shared colors
                start_y = padding_y + (len(params_list) - 1) * (line_height + line_padding)
                for i, (key, value_repr) in enumerate(params_list):
                    current_y = start_y - (i * (line_height + line_padding)); key_text = f"{key}: "

                    # Calculate widths and total width for alignment
                    key_width = lblfDims(font_id, key_text)[0]
                    value_width = lblfDims(font_id, value_repr)[0]
                    total_width = key_width + value_width

                    # Calculate draw_x for the key based on placement and total width
                    key_draw_x = ref_x
                    if tooltip_placement == 'BOTTOM_CENTER':
                        key_draw_x = ref_x - total_width / 2
                    elif tooltip_placement == 'BOTTOM_RIGHT':
                        key_draw_x = ref_x - total_width

                    value_draw_x = key_draw_x + key_width

                    # Pass calculated draw_x and apply_offset=False
                    draw_text_with_outline(font_id, key_text, key_draw_x, current_y, name_color, apply_offset=False)
                    draw_text_with_outline(font_id, value_repr, value_draw_x, current_y, value_color, apply_offset=False)

        # --- Node Tooltips ---
        node_params_height = 0; node_line_height_offset = 0
        total_beam_tooltip_height = beam_params_height + beam_line_height_offset
        if ui_props.toggle_params_tooltip and jb_globals._selected_node_params_info is not None: # Use shared toggle
            params_list = jb_globals._selected_node_params_info.get('params_list')
            if params_list: node_params_height = len(params_list) * (line_height + line_padding)

        if ui_props.toggle_line_tooltip and jb_globals._selected_node_line_info is not None: # Use shared toggle
            line_num = jb_globals._selected_node_line_info.get('line')
            if line_num is not None:
                node_line_y = padding_y + total_beam_tooltip_height + node_params_height
                node_line_height_offset = line_height + line_padding
                line_text = f"Line: {line_num}"
                line_width = lblfDims(font_id, line_text)[0]

                # Calculate draw_x based on placement and width
                draw_x = ref_x
                if tooltip_placement == 'BOTTOM_CENTER':
                    draw_x = ref_x - line_width / 2
                elif tooltip_placement == 'BOTTOM_RIGHT':
                    draw_x = ref_x - line_width

                # Pass calculated draw_x and apply_offset=False
                draw_text_with_outline(font_id, line_text, draw_x, node_line_y, ui_props.line_tooltip_color, apply_offset=False)

        if ui_props.toggle_params_tooltip and jb_globals._selected_node_params_info is not None: # Use shared toggle
            params_list = jb_globals._selected_node_params_info.get('params_list')
            if params_list:
                name_color = ui_props.params_tooltip_color; value_color = ui_props.params_value_tooltip_color # Use shared colors
                start_y = padding_y + total_beam_tooltip_height + (len(params_list) - 1) * (line_height + line_padding)
                for i, (key, value_repr) in enumerate(params_list):
                    current_y = start_y - (i * (line_height + line_padding)); key_text = f"{key}: "

                    # Calculate widths and total width for alignment
                    key_width = lblfDims(font_id, key_text)[0]
                    value_width = lblfDims(font_id, value_repr)[0]
                    total_width = key_width + value_width

                    # Calculate draw_x for the key based on placement and total width
                    key_draw_x = ref_x
                    if tooltip_placement == 'BOTTOM_CENTER':
                        key_draw_x = ref_x - total_width / 2
                    elif tooltip_placement == 'BOTTOM_RIGHT':
                        key_draw_x = ref_x - total_width

                    value_draw_x = key_draw_x + key_width

                    # Pass calculated draw_x and apply_offset=False
                    draw_text_with_outline(font_id, key_text, key_draw_x, current_y, name_color, apply_offset=False)
                    draw_text_with_outline(font_id, value_repr, value_draw_x, current_y, value_color, apply_offset=False)

    # Final cleanup
    if bm and not is_vehicle_part and active_obj.mode != 'EDIT':
        bm.free()

# <<< MODIFIED HELPER FUNCTION _calculate_dynamic_color >>>
def _calculate_dynamic_color(value, low_threshold, high_threshold):
    """
    Calculates a color based on a value relative to low and high thresholds,
    interpolating linearly through a Blue -> Cyan -> Green -> Yellow -> Red gradient.
    Values <= low_threshold are Blue.
    Values >= high_threshold are Red.
    Handles basic '=$variable' resolution and expression evaluation for the input value.

    Args:
        value: The raw value from the JBeam data (can be number, string, expression).
        low_threshold (float): The lower bound for the color gradient.
        high_threshold (float): The upper bound for the color gradient.

    Returns:
        A tuple (R, G, B, A) representing the calculated color, or None if the value
        could not be resolved to a finite number.
    """
    # --- MODIFIED: Resolve/Evaluate variable/expression before conversion ---
    resolved_value = resolve_jbeam_variable_value(value, jb_globals.jbeam_variables_cache)
    # --- END MODIFIED ---

    # --- Attempt conversion to float using the resolved value ---
    try:
        numeric_value = float(resolved_value)
        if not math.isfinite(numeric_value):
            # <<< MODIFIED: Return None on failure >>>
            return None # Return None for non-finite numbers
    except (ValueError, TypeError):
        # Only print warning if the original value looked like an expression or variable
        # <<< MODIFIED: Return None on failure >>>
        if isinstance(value, str) and value.startswith('$'):
             # Avoid printing warnings for simple strings that fail conversion
             # Warnings for unresolved variables/expressions are handled in resolve_jbeam_variable_value
             pass
        return None # Return None if conversion fails
    # --- END ATTEMPT ---

    # Handle invalid thresholds: return None
    if low_threshold >= high_threshold:
        # If thresholds are equal, return Green (midpoint color)
        if low_threshold == high_threshold:
             return (0.0, 1.0, 0.0, 1.0) # Green
        # <<< MODIFIED: Return None on failure >>>
        return None # Return None for invalid thresholds

    # Normalize the numeric_value within the range [low_threshold, high_threshold] to [0, 1]
    clamped_value = max(low_threshold, min(numeric_value, high_threshold))
    value_range = high_threshold - low_threshold
    normalized_value = (clamped_value - low_threshold) / value_range if value_range != 0 else 0.5

    # Interpolate color across 4 segments: Blue -> Cyan, Cyan -> Green, Green -> Yellow, Yellow -> Red
    segment_size = 0.25

    if normalized_value <= segment_size: # Segment 0: Blue to Cyan (0.0 -> 0.25)
        t = normalized_value / segment_size if segment_size != 0 else 0; red = 0.0; green = t; blue = 1.0
    elif normalized_value <= 2 * segment_size: # Segment 1: Cyan to Green (0.25 -> 0.5)
        t = (normalized_value - segment_size) / segment_size if segment_size != 0 else 0; red = 0.0; green = 1.0; blue = 1.0 - t
    elif normalized_value <= 3 * segment_size: # Segment 2: Green to Yellow (0.5 -> 0.75)
        t = (normalized_value - 2 * segment_size) / segment_size if segment_size != 0 else 0; red = t; green = 1.0; blue = 0.0
    else: # Segment 3: Yellow to Red (0.75 -> 1.0)
        t = (normalized_value - 3 * segment_size) / segment_size if segment_size != 0 else 0; red = 1.0; green = 1.0 - t; blue = 0.0

    red = max(0.0, min(red, 1.0)); green = max(0.0, min(green, 1.0)); blue = max(0.0, min(blue, 1.0))
    final_color = (red, green, blue, 1.0) # Return RGBA tuple
    return final_color
# <<< END MODIFIED HELPER FUNCTION >>>


# Draws beams, rails, torsionbars
def draw_callback_view(context: bpy.types.Context):
    # <<< MODIFICATION: Access global highlight dirty flag >>>
    global veh_render_dirty, render_shader, _highlight_dirty
    # Static colors (used when dynamic is OFF)
    global beam_render_batch, beam_coords
    global anisotropic_beam_render_batch, anisotropic_beam_coords
    global support_beam_render_batch, support_beam_coords
    global hydro_beam_render_batch, hydro_beam_coords
    global bounded_beam_render_batch, bounded_beam_coords
    global lbeam_render_batch, lbeam_coords
    global pressured_beam_render_batch, pressured_beam_coords
    global cross_part_beam_render_batch, cross_part_beam_coords
    # Dynamic colors (used when dynamic is ON)
    global dynamic_beam_batch, dynamic_beam_coords_colors
    # Torsionbars, Rails (always separate)
    global torsionbar_render_batch, torsionbar_coords
    global torsionbar_red_render_batch, torsionbar_red_coords
    global rail_render_batch, rail_coords
    # Node Cache
    global all_nodes_cache, all_nodes_cache_dirty
    # Highlight batches
    global highlight_render_batch, highlight_coords
    global highlight_torsionbar_outer_batch, highlight_torsionbar_outer_coords
    global highlight_torsionbar_mid_batch, highlight_torsionbar_mid_coords
    global warned_missing_nodes_this_rebuild
    # Selected beam outline
    global selected_beam_batch, selected_beam_coords_colors, selected_beam_max_original_width
    # <<< ADDED: Ensure global is accessible >>>
    global _reported_missing_vars_this_rebuild
    # <<< ADDED: Ensure global is accessible >>>
    global _reported_unsupported_ops_this_rebuild
    # <<< ADDED: Access global node thresholds >>>
    global auto_node_weight_min, auto_node_weight_max, auto_node_thresholds_valid

    # ... (initial checks: scene, ui_props, active_obj, should_draw) ...
    scene = context.scene
    ui_props = scene.ui_properties
    if not hasattr(context, 'scene') or not hasattr(scene, 'ui_properties'): return

    active_obj = context.active_object
    is_valid_jbeam_obj = False; is_selected = False
    if active_obj and active_obj.data and active_obj.data.get(constants.MESH_JBEAM_PART) is not None:
        is_valid_jbeam_obj = True
        if active_obj in context.selected_objects: is_selected = True

    should_draw = is_valid_jbeam_obj and is_selected
    if not should_draw:
        # ... (batch clearing logic - ensure all relevant batches are cleared) ...
        batches_were_cleared = False
        # Clear dynamic batch
        if dynamic_beam_batch: dynamic_beam_batch = None; batches_were_cleared = True
        # Clear static batches
        if beam_render_batch: beam_render_batch = None; batches_were_cleared = True
        if anisotropic_beam_render_batch: anisotropic_beam_render_batch = None; batches_were_cleared = True
        if support_beam_render_batch: support_beam_render_batch = None; batches_were_cleared = True
        if hydro_beam_render_batch: hydro_beam_render_batch = None; batches_were_cleared = True
        if bounded_beam_render_batch: bounded_beam_render_batch = None; batches_were_cleared = True
        if lbeam_render_batch: lbeam_render_batch = None; batches_were_cleared = True
        if pressured_beam_render_batch: pressured_beam_render_batch = None; batches_were_cleared = True
        if cross_part_beam_render_batch: cross_part_beam_render_batch = None; batches_were_cleared = True
        # Clear torsionbar, rail
        if torsionbar_render_batch: torsionbar_render_batch = None; batches_were_cleared = True
        if torsionbar_red_render_batch: torsionbar_red_render_batch = None; batches_were_cleared = True
        if rail_render_batch: rail_render_batch = None; batches_were_cleared = True
        # Clear highlight batches
        if highlight_render_batch: highlight_render_batch = None; batches_were_cleared = True
        if highlight_torsionbar_outer_batch: highlight_torsionbar_outer_batch = None; batches_were_cleared = True
        if highlight_torsionbar_mid_batch: highlight_torsionbar_mid_batch = None; batches_were_cleared = True
        # Clear selected beam batch
        if selected_beam_batch: selected_beam_batch = None; batches_were_cleared = True

        if batches_were_cleared:
            # Clear coordinate lists
            dynamic_beam_coords_colors.clear()
            beam_coords.clear()
            anisotropic_beam_coords.clear(); support_beam_coords.clear()
            hydro_beam_coords.clear(); bounded_beam_coords.clear(); lbeam_coords.clear()
            pressured_beam_coords.clear(); cross_part_beam_coords.clear()
            torsionbar_coords.clear(); torsionbar_red_coords.clear()
            rail_coords.clear();
            highlight_coords.clear()
            highlight_torsionbar_outer_coords.clear()
            highlight_torsionbar_mid_coords.clear()
            selected_beam_coords_colors.clear()
            veh_render_dirty = True # Mark dirty if batches were cleared
        return

    # ... (shader init, main dirty flag checks, cache updates) ...
    if render_shader is None:
        render_shader = gpu.shader.from_builtin('SMOOTH_COLOR')

    if scene.jbeam_editor_veh_render_dirty:
        veh_render_dirty = True
        scene.jbeam_editor_veh_render_dirty = False

    if all_nodes_cache_dirty:
        update_all_nodes_cache(context)
        veh_render_dirty = True # Force rebuild if cache updated

    if jb_globals.jbeam_variables_cache_dirty:
        update_jbeam_variables_cache(context)
        veh_render_dirty = True # Force rebuild if cache updated

    # --- Check if batches need rebuilding ---
    # <<< MODIFICATION: Check main batches first (excluding highlight) >>>
    batches_missing = False
    if ui_props.use_dynamic_beam_coloring:
        batches_missing = (dynamic_beam_batch is None and dynamic_beam_coords_colors)
    else:
        batches_missing = (
            (ui_props.toggle_beams_vis and beam_render_batch is None and beam_coords) or
            # ... (check other static beam batches) ...
            (ui_props.toggle_anisotropic_beams_vis and anisotropic_beam_render_batch is None and anisotropic_beam_coords) or
            (ui_props.toggle_support_beams_vis and support_beam_render_batch is None and support_beam_coords) or
            (ui_props.toggle_hydro_beams_vis and hydro_beam_render_batch is None and hydro_beam_coords) or
            (ui_props.toggle_bounded_beams_vis and bounded_beam_render_batch is None and bounded_beam_coords) or
            (ui_props.toggle_lbeam_beams_vis and lbeam_render_batch is None and lbeam_coords) or
            (ui_props.toggle_pressured_beams_vis and pressured_beam_render_batch is None and pressured_beam_coords) or
            (ui_props.toggle_cross_part_beams_vis and cross_part_beam_render_batch is None and cross_part_beam_coords and all_nodes_cache)
        )
    # Check Torsionbar, Rail, Selected (always checked, excluding highlight)
    batches_missing = batches_missing or \
        (ui_props.toggle_torsionbars_vis and torsionbar_render_batch is None and torsionbar_coords) or \
        (ui_props.toggle_torsionbars_vis and torsionbar_red_render_batch is None and torsionbar_red_coords) or \
        (ui_props.toggle_rails_vis and rail_render_batch is None and rail_coords) or \
        (selected_beam_batch is None and selected_beam_coords_colors)

    if batches_missing:
        veh_render_dirty = True
    # <<< END MODIFICATION >>>

    # <<< ADDED: Separate check for highlight batches needing rebuild >>>
    highlight_batches_missing = (
        (jb_globals.highlighted_element_type == 'torsionbar' and (highlight_torsionbar_outer_batch is None or highlight_torsionbar_mid_batch is None) and (highlight_torsionbar_outer_coords or highlight_torsionbar_mid_coords)) or
        (jb_globals.highlighted_element_type not in (None, 'node') and highlight_render_batch is None and highlight_coords)
    )
    # If highlight coords exist but batches are missing OR the explicit dirty flag is set
    if _highlight_dirty or (highlight_batches_missing and (highlight_coords or highlight_torsionbar_outer_coords or highlight_torsionbar_mid_coords)):
        # --- Rebuild Highlight Batches ONLY ---
        # Clear only highlight batches
        highlight_render_batch = None
        highlight_torsionbar_outer_batch = None
        highlight_torsionbar_mid_batch = None

        # Recreate highlight batches using current coords and colors
        if highlight_coords:
            colors = [jb_globals.highlighted_element_color] * len(highlight_coords)
            try: # Add try-except for safety during batch creation
                highlight_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": highlight_coords, "color": colors})
            except Exception as e: print(f"Error creating highlight batch: {e}", file=sys.stderr)
        if highlight_torsionbar_outer_coords:
            colors = [jb_globals.highlighted_element_color] * len(highlight_torsionbar_outer_coords)
            try:
                highlight_torsionbar_outer_batch = batch_for_shader(render_shader, 'LINES', {"pos": highlight_torsionbar_outer_coords, "color": colors})
            except Exception as e: print(f"Error creating highlight torsionbar outer batch: {e}", file=sys.stderr)
        if highlight_torsionbar_mid_coords:
            colors = [jb_globals.highlighted_element_mid_color] * len(highlight_torsionbar_mid_coords)
            try:
                highlight_torsionbar_mid_batch = batch_for_shader(render_shader, 'LINES', {"pos": highlight_torsionbar_mid_coords, "color": colors})
            except Exception as e: print(f"Error creating highlight torsionbar mid batch: {e}", file=sys.stderr)

        _highlight_dirty = False # Reset flag after rebuilding
        # --- End Rebuild Highlight Batches ---
    # <<< END ADDED >>>


    # --- Rebuild Logic (Main Beams/Nodes) ---
    if veh_render_dirty:
        # --- 1. Clear all coordinate lists and batches ---
        dynamic_beam_coords_colors.clear()
        beam_coords.clear(); anisotropic_beam_coords.clear(); support_beam_coords.clear()
        hydro_beam_coords.clear(); bounded_beam_coords.clear(); lbeam_coords.clear()
        pressured_beam_coords.clear(); cross_part_beam_coords.clear()
        torsionbar_coords.clear(); torsionbar_red_coords.clear(); rail_coords.clear();
        selected_beam_coords_colors.clear()
        highlight_coords.clear()
        highlight_torsionbar_outer_coords.clear()
        highlight_torsionbar_mid_coords.clear()
        selected_beam_max_original_width = 1.0

        # Clear all batches (will be recreated later)
        beam_render_batch = None; dynamic_beam_batch = None; anisotropic_beam_render_batch = None; support_beam_render_batch = None
        hydro_beam_render_batch = None; bounded_beam_render_batch = None; lbeam_render_batch = None; pressured_beam_render_batch = None
        cross_part_beam_render_batch = None
        torsionbar_render_batch = None; torsionbar_red_render_batch = None
        rail_render_batch = None
        selected_beam_batch = None
        highlight_render_batch = None; highlight_torsionbar_outer_batch = None; highlight_torsionbar_mid_batch = None

        # --- 2. Reset auto thresholds ---
        auto_min_val = float('inf'); auto_max_val = float('-inf'); auto_thresholds_valid = False
        auto_node_weight_min = float('inf'); auto_node_weight_max = float('-inf'); auto_node_thresholds_valid = False

        # --- Get context data ---
        active_obj_data = active_obj.data
        collection = active_obj.users_collection[0] if active_obj.users_collection else None
        is_vehicle_part = collection is not None and collection.get(constants.COLLECTION_VEHICLE_MODEL) is not None
        current_part_name = active_obj_data.get(constants.MESH_JBEAM_PART)

        # --- 3. Build node maps & Calculate Auto Node Thresholds ---
        node_id_to_hide_status: dict[str, bool] = {}
        node_id_to_pos_matrix_map: dict[str, tuple[Vector, Matrix]] = {}

        if is_vehicle_part:
            if not part_name_to_obj:
                 for obj_iter in collection.all_objects:
                    if obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART):
                        part_name_to_obj[obj_iter.data[constants.MESH_JBEAM_PART]] = obj_iter

            for obj_iter in collection.all_objects:
                if obj_iter.visible_get() and obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART) is not None:
                    obj_iter_data = obj_iter.data; part_name = obj_iter_data.get(constants.MESH_JBEAM_PART)
                    bm = None
                    try:
                        if obj_iter == active_obj and active_obj.mode == 'EDIT': bm = bmesh.from_edit_mesh(obj_iter_data)
                        else: bm = bmesh.new(); bm.from_mesh(obj_iter_data)

                        node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
                        is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)

                        if node_id_layer and is_fake_layer:
                            bm.verts.ensure_lookup_table()
                            obj_matrix_copy = obj_iter.matrix_world.copy()
                            for v in bm.verts:
                                if v[is_fake_layer] == 0:
                                    node_id = v[node_id_layer].decode('utf-8')
                                    node_id_to_hide_status[node_id] = v.hide # Store hide status
                                    node_id_to_pos_matrix_map[node_id] = (v.co.copy(), obj_matrix_copy)

                                    # Calculate Auto Node Thresholds (Check Visibility)
                                    if not v.hide:
                                        if ui_props.use_dynamic_node_coloring and ui_props.use_auto_node_thresholds:
                                            node_data = jb_globals.curr_vdata['nodes'].get(node_id) if jb_globals.curr_vdata and 'nodes' in jb_globals.curr_vdata else None
                                            if node_data and isinstance(node_data, dict):
                                                node_weight_raw = node_data.get('nodeWeight')
                                                if node_weight_raw is not None:
                                                    resolved_value = resolve_jbeam_variable_value(node_weight_raw, jb_globals.jbeam_variables_cache)
                                                    try:
                                                        numeric_value = float(resolved_value)
                                                        if math.isfinite(numeric_value):
                                                            auto_node_weight_min = min(auto_node_weight_min, numeric_value)
                                                            auto_node_weight_max = max(auto_node_weight_max, numeric_value)
                                                            auto_node_thresholds_valid = True
                                                    except (ValueError, TypeError): pass
                    except Exception as e: print(f"Error getting node geometry data from {obj_iter.name}: {e}", file=sys.stderr)
                    finally:
                        if bm and not (obj_iter == active_obj and active_obj.mode == 'EDIT'): bm.free()
        else: # Single Part
            if active_obj.visible_get():
                part_name = active_obj_data.get(constants.MESH_JBEAM_PART)
                bm = None
                try:
                    if active_obj.mode == 'EDIT': bm = bmesh.from_edit_mesh(active_obj_data)
                    else: bm = bmesh.new(); bm.from_mesh(active_obj_data)

                    node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
                    is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
                    if node_id_layer and is_fake_layer:
                        bm.verts.ensure_lookup_table()
                        obj_matrix_copy = active_obj.matrix_world.copy()
                        for v in bm.verts:
                            if v[is_fake_layer] == 0:
                                node_id = v[node_id_layer].decode('utf-8')
                                node_id_to_hide_status[node_id] = v.hide # Store hide status
                                node_id_to_pos_matrix_map[node_id] = (v.co.copy(), obj_matrix_copy)

                                # Calculate Auto Node Thresholds (Check Visibility)
                                if not v.hide:
                                    if ui_props.use_dynamic_node_coloring and ui_props.use_auto_node_thresholds:
                                        node_data = jb_globals.curr_vdata['nodes'].get(node_id) if jb_globals.curr_vdata and 'nodes' in jb_globals.curr_vdata else None
                                        if node_data and isinstance(node_data, dict):
                                            node_weight_raw = node_data.get('nodeWeight')
                                            if node_weight_raw is not None:
                                                resolved_value = resolve_jbeam_variable_value(node_weight_raw, jb_globals.jbeam_variables_cache)
                                                try:
                                                    numeric_value = float(resolved_value)
                                                    if math.isfinite(numeric_value):
                                                        auto_node_weight_min = min(auto_node_weight_min, numeric_value)
                                                        auto_node_weight_max = max(auto_node_weight_max, numeric_value)
                                                        auto_node_thresholds_valid = True
                                                except (ValueError, TypeError): pass
                except Exception as e: print(f"Error getting node geometry data from {active_obj.name}: {e}", file=sys.stderr)
                finally:
                    if bm and not (active_obj.mode == 'EDIT'): bm.free()

        # --- 4. Build edge_idx_to_beam_data_map ---
        edge_idx_to_beam_data_map: dict[tuple[str, int], dict] = {}
        if jb_globals.curr_vdata and 'beams' in jb_globals.curr_vdata:
            beam_part_counters = {}
            for global_beam_idx, beam_data in enumerate(jb_globals.curr_vdata['beams']):
                if isinstance(beam_data, dict):
                    part_origin = beam_data.get('partOrigin')
                    if part_origin:
                        current_idx_in_part = beam_part_counters.get(part_origin, 0) + 1
                        beam_part_counters[part_origin] = current_idx_in_part
                        edge_idx_to_beam_data_map[(part_origin, current_idx_in_part)] = beam_data

        # --- 5. Calculate Auto Beam Thresholds (Considering Visibility) ---
        if ui_props.use_dynamic_beam_coloring and ui_props.use_auto_thresholds:
            if jb_globals.curr_vdata and 'beams' in jb_globals.curr_vdata:
                param_name = ui_props.dynamic_coloring_parameter
                for beam_data in jb_globals.curr_vdata['beams']:
                    if isinstance(beam_data, dict):
                        id1 = beam_data.get('id1:')
                        id2 = beam_data.get('id2:')
                        if not id1 or not id2: continue

                        # Check node visibility
                        node1_hidden = node_id_to_hide_status.get(id1, False)
                        node2_hidden = node_id_to_hide_status.get(id2, False)
                        if node1_hidden or node2_hidden: continue # Skip if either node is hidden

                        # Check beam type visibility
                        beam_type = beam_data.get('beamType', '|NORMAL')
                        type_visible = False
                        if beam_type == '|NORMAL': type_visible = ui_props.toggle_beams_vis
                        elif beam_type == '|ANISOTROPIC': type_visible = ui_props.toggle_anisotropic_beams_vis
                        elif beam_type == '|SUPPORT': type_visible = ui_props.toggle_support_beams_vis
                        elif beam_type == '|HYDRO': type_visible = ui_props.toggle_hydro_beams_vis
                        elif beam_type == '|BOUNDED': type_visible = ui_props.toggle_bounded_beams_vis
                        elif beam_type == '|LBEAM': type_visible = ui_props.toggle_lbeam_beams_vis
                        elif beam_type == '|PRESSURED': type_visible = ui_props.toggle_pressured_beams_vis

                        # Check cross-part visibility separately
                        is_cross_part = False
                        cache1_data = all_nodes_cache.get(id1)
                        cache2_data = all_nodes_cache.get(id2)
                        origin1 = cache1_data[2] if cache1_data else '?'
                        origin2 = cache2_data[2] if cache2_data else '?'
                        if origin1 != origin2 and '?' not in {origin1, origin2}:
                            is_cross_part = True

                        if is_cross_part:
                            # If it's cross-part, visibility depends ONLY on the cross-part toggle
                            type_visible = ui_props.toggle_cross_part_beams_vis
                        # else: type_visible remains as determined by beam type toggle

                        if type_visible: # Only process if visible
                            param_value_raw = beam_data.get(param_name)
                            if param_value_raw is not None:
                                resolved_value = resolve_jbeam_variable_value(param_value_raw, jb_globals.jbeam_variables_cache)
                                try:
                                    numeric_value = float(resolved_value)
                                    if math.isfinite(numeric_value):
                                        auto_min_val = min(auto_min_val, numeric_value)
                                        auto_max_val = max(auto_max_val, numeric_value)
                                        auto_thresholds_valid = True
                                except (ValueError, TypeError): pass

        # <<< DEBUG PRINT START >>>
        if ui_props.use_dynamic_beam_coloring and ui_props.use_auto_thresholds:
            print(f"Beam Auto Thresholds: Valid={auto_thresholds_valid}, Min={auto_min_val}, Max={auto_max_val}")
        if ui_props.use_dynamic_node_coloring and ui_props.use_auto_node_thresholds:
            print(f"Node Auto Thresholds: Valid={auto_node_thresholds_valid}, Min={auto_node_weight_min}, Max={auto_node_weight_max}")
        # <<< DEBUG PRINT END >>>

        # --- 6. Populate Coordinate Lists (using finalized thresholds) ---
        if is_vehicle_part:
            for obj_iter in collection.all_objects:
                if obj_iter.visible_get() and obj_iter.data and obj_iter.data.get(constants.MESH_JBEAM_PART) is not None:
                    obj_iter_data = obj_iter.data; part_name = obj_iter_data.get(constants.MESH_JBEAM_PART)
                    bm = None
                    try:
                        if obj_iter == active_obj and active_obj.mode == 'EDIT': bm = bmesh.from_edit_mesh(obj_iter_data)
                        else: bm = bmesh.new(); bm.from_mesh(obj_iter_data)

                        beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
                        beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
                        if beam_indices_layer and beam_part_origin_layer:
                            bm.edges.ensure_lookup_table()
                            for e in bm.edges:
                                if e.hide or any(v.hide for v in e.verts): continue
                                beam_idx_str = e[beam_indices_layer].decode('utf-8')
                                if beam_idx_str != '' and beam_idx_str != '-1':
                                    try:
                                        first_beam_idx_in_part = int(beam_idx_str.split(',')[0])
                                        edge_part_origin = e[beam_part_origin_layer].decode('utf-8')
                                        beam_data = edge_idx_to_beam_data_map.get((edge_part_origin, first_beam_idx_in_part))
                                        beam_type = beam_data.get('beamType', '|NORMAL') if beam_data else '|NORMAL'

                                        type_visible = False
                                        if beam_type == '|NORMAL': type_visible = ui_props.toggle_beams_vis
                                        elif beam_type == '|ANISOTROPIC': type_visible = ui_props.toggle_anisotropic_beams_vis
                                        elif beam_type == '|SUPPORT': type_visible = ui_props.toggle_support_beams_vis
                                        elif beam_type == '|HYDRO': type_visible = ui_props.toggle_hydro_beams_vis
                                        elif beam_type == '|BOUNDED': type_visible = ui_props.toggle_bounded_beams_vis
                                        elif beam_type == '|LBEAM': type_visible = ui_props.toggle_lbeam_beams_vis
                                        elif beam_type == '|PRESSURED': type_visible = ui_props.toggle_pressured_beams_vis
                                        if not type_visible: continue

                                        v1, v2 = e.verts[0], e.verts[1]
                                        world_pos1 = obj_iter.matrix_world @ v1.co; world_pos2 = obj_iter.matrix_world @ v2.co
                                        original_width = ui_props.beam_width
                                        if beam_type == '|ANISOTROPIC': original_width = ui_props.anisotropic_beam_width
                                        elif beam_type == '|SUPPORT': original_width = ui_props.support_beam_width
                                        elif beam_type == '|HYDRO': original_width = ui_props.hydro_beam_width
                                        elif beam_type == '|BOUNDED': original_width = ui_props.bounded_beam_width
                                        elif beam_type == '|LBEAM': original_width = ui_props.lbeam_beam_width
                                        elif beam_type == '|PRESSURED': original_width = ui_props.pressured_beam_width

                                        if ui_props.use_dynamic_beam_coloring:
                                            color_to_use = None
                                            if beam_data:
                                                param_name = ui_props.dynamic_coloring_parameter
                                                param_value_raw = beam_data.get(param_name)
                                                if param_value_raw is not None:
                                                    # Use FINALIZED auto thresholds
                                                    low_thresh = auto_min_val if ui_props.use_auto_thresholds and auto_thresholds_valid else ui_props.dynamic_color_threshold_low
                                                    high_thresh = auto_max_val if ui_props.use_auto_thresholds and auto_thresholds_valid else ui_props.dynamic_color_threshold_high
                                                    color_to_use = _calculate_dynamic_color(param_value_raw, low_thresh, high_thresh)
                                            if color_to_use is not None:
                                                dynamic_beam_coords_colors.append((world_pos1, world_pos2, color_to_use))
                                        else:
                                            if beam_type == '|NORMAL': beam_coords.extend([world_pos1, world_pos2])
                                            elif beam_type == '|ANISOTROPIC': anisotropic_beam_coords.extend([world_pos1, world_pos2])
                                            elif beam_type == '|SUPPORT': support_beam_coords.extend([world_pos1, world_pos2])
                                            elif beam_type == '|HYDRO': hydro_beam_coords.extend([world_pos1, world_pos2])
                                            elif beam_type == '|BOUNDED': bounded_beam_coords.extend([world_pos1, world_pos2])
                                            elif beam_type == '|LBEAM': lbeam_coords.extend([world_pos1, world_pos2])
                                            elif beam_type == '|PRESSURED': pressured_beam_coords.extend([world_pos1, world_pos2])

                                        if e.index in jb_globals.selected_beam_edge_indices:
                                            selected_beam_coords_colors.append((world_pos1, world_pos2, WHITE_COLOR))
                                            selected_beam_max_original_width = max(selected_beam_max_original_width, original_width)
                                    except (ValueError, IndexError) as parse_err: pass
                    except Exception as e: print(f"Error getting beam geometry data from {obj_iter.name}: {e}", file=sys.stderr)
                    finally:
                        if bm and not (obj_iter == active_obj and active_obj.mode == 'EDIT'): bm.free()

            # Torsionbar, Rail, Cross-Part Population (Vehicle)
            if ui_props.toggle_torsionbars_vis and jb_globals.curr_vdata and 'torsionbars' in jb_globals.curr_vdata and isinstance(jb_globals.curr_vdata['torsionbars'], list):
                for tb in jb_globals.curr_vdata['torsionbars']:
                    ids = []
                    if isinstance(tb, dict): ids = [tb.get(f'id{i}:') for i in range(1, 5)]
                    elif isinstance(tb, list) and len(tb) >= 4: ids = tb[:4]
                    if len(ids) != 4 or not all(ids): continue
                    if any(node_id_to_hide_status.get(id, False) for id in ids): continue
                    world_pos = [None] * 4; all_nodes_found = True; missing_nodes = []
                    for i, node_id in enumerate(ids):
                        pos_data = node_id_to_pos_matrix_map.get(node_id)
                        cache_data = all_nodes_cache.get(node_id)
                        wp = None
                        if pos_data: wp = pos_data[1] @ pos_data[0]
                        elif cache_data: wp = cache_data[0]
                        if wp is None: all_nodes_found = False; missing_nodes.append(node_id)
                        world_pos[i] = wp
                    if not all_nodes_found:
                        if any(node_id not in warned_missing_nodes_this_rebuild for node_id in missing_nodes):
                            print(f"Warning: Could not find position data for torsionbar nodes {missing_nodes}", file=sys.stderr)
                            warned_missing_nodes_this_rebuild.update(missing_nodes)
                        continue
                    torsionbar_coords.extend([world_pos[0], world_pos[1]])
                    torsionbar_red_coords.extend([world_pos[1], world_pos[2]])
                    torsionbar_coords.extend([world_pos[2], world_pos[3]])

            if ui_props.toggle_rails_vis and jb_globals.curr_vdata and 'rails' in jb_globals.curr_vdata and isinstance(jb_globals.curr_vdata['rails'], dict):
                 for rail_name, rail_info in jb_globals.curr_vdata['rails'].items():
                    rail_nodes = None
                    if isinstance(rail_info, list) and len(rail_info) == 2: rail_nodes = rail_info
                    elif isinstance(rail_info, dict): rail_nodes = rail_info.get('links:')
                    if isinstance(rail_nodes, list) and len(rail_nodes) == 2:
                        ids = rail_nodes
                        if not all(ids): continue
                        if any(node_id_to_hide_status.get(id, False) for id in ids): continue
                        world_pos = [None] * 2; all_nodes_found = True; missing_nodes = []
                        for i, node_id in enumerate(ids):
                            pos_data = node_id_to_pos_matrix_map.get(node_id)
                            cache_data = all_nodes_cache.get(node_id)
                            wp = None
                            if pos_data: wp = pos_data[1] @ pos_data[0]
                            elif cache_data: wp = cache_data[0]
                            if wp is None: all_nodes_found = False; missing_nodes.append(node_id)
                            world_pos[i] = wp
                        if all_nodes_found: rail_coords.extend(world_pos)
                        elif ui_props.toggle_rails_vis:
                            if any(node_id not in warned_missing_nodes_this_rebuild for node_id in missing_nodes):
                                print(f"Warning: Could not find position data for rail nodes {missing_nodes}", file=sys.stderr)
                                warned_missing_nodes_this_rebuild.update(missing_nodes)

            if ui_props.toggle_cross_part_beams_vis and jb_globals.curr_vdata and 'beams' in jb_globals.curr_vdata:
                for beam_data in jb_globals.curr_vdata['beams']:
                    if not isinstance(beam_data, dict) or beam_data.get('partOrigin') != current_part_name: continue
                    id1, id2 = beam_data.get('id1:'), beam_data.get('id2:')
                    if not id1 or not id2: continue
                    id1_in_active_geom = id1 in node_id_to_pos_matrix_map
                    id2_in_active_geom = id2 in node_id_to_pos_matrix_map
                    if id1_in_active_geom and id2_in_active_geom: continue
                    if node_id_to_hide_status.get(id1, False) or node_id_to_hide_status.get(id2, False): continue

                    cache1_data = all_nodes_cache.get(id1); cache2_data = all_nodes_cache.get(id2)
                    world_pos1, world_pos2 = None, None; is_truly_cross_part = False; missing_nodes = []
                    origin1 = cache1_data[2] if cache1_data else '?'; origin2 = cache2_data[2] if cache2_data else '?'
                    if origin1 != origin2 and '?' not in {origin1, origin2}: is_truly_cross_part = True
                    elif (id1_in_active_geom and not id2_in_active_geom) or (not id1_in_active_geom and id2_in_active_geom): is_truly_cross_part = True

                    if is_truly_cross_part:
                        if id1_in_active_geom: pos1_data = node_id_to_pos_matrix_map[id1]; world_pos1 = pos1_data[1] @ pos1_data[0]
                        elif cache1_data: world_pos1 = cache1_data[0]
                        else: missing_nodes.append(id1)
                        if id2_in_active_geom: pos2_data = node_id_to_pos_matrix_map[id2]; world_pos2 = pos2_data[1] @ pos2_data[0]
                        elif cache2_data: world_pos2 = cache2_data[0]
                        else: missing_nodes.append(id2)

                        if missing_nodes:
                            if any(node_id not in warned_missing_nodes_this_rebuild for node_id in missing_nodes):
                                print(f"Warning: Could not find position data for cross-part beam nodes {missing_nodes}", file=sys.stderr)
                                warned_missing_nodes_this_rebuild.update(missing_nodes)
                            continue

                        if ui_props.use_dynamic_beam_coloring:
                            color_to_use = None
                            if beam_data:
                                param_name = ui_props.dynamic_coloring_parameter
                                param_value_raw = beam_data.get(param_name)
                                if param_value_raw is not None:
                                    # Use FINALIZED auto thresholds
                                    low_thresh = auto_min_val if ui_props.use_auto_thresholds and auto_thresholds_valid else ui_props.dynamic_color_threshold_low
                                    high_thresh = auto_max_val if ui_props.use_auto_thresholds and auto_thresholds_valid else ui_props.dynamic_color_threshold_high
                                    color_to_use = _calculate_dynamic_color(param_value_raw, low_thresh, high_thresh)
                            if color_to_use is not None:
                                dynamic_beam_coords_colors.append((world_pos1, world_pos2, color_to_use))
                        else:
                            cross_part_beam_coords.extend([world_pos1, world_pos2])
        else: # Single Part
            if active_obj.visible_get():
                part_name = active_obj_data.get(constants.MESH_JBEAM_PART)
                bm = None
                try:
                    if active_obj.mode == 'EDIT': bm = bmesh.from_edit_mesh(active_obj_data)
                    else: bm = bmesh.new(); bm.from_mesh(active_obj_data)

                    beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
                    beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)
                    if beam_indices_layer and beam_part_origin_layer:
                        bm.edges.ensure_lookup_table()
                        for e in bm.edges:
                            if e.hide or any(v.hide for v in e.verts): continue
                            beam_idx_str = e[beam_indices_layer].decode('utf-8')
                            if beam_idx_str != '' and beam_idx_str != '-1':
                                try:
                                    first_beam_idx_in_part = int(beam_idx_str.split(',')[0])
                                    edge_part_origin = e[beam_part_origin_layer].decode('utf-8')
                                    beam_data = edge_idx_to_beam_data_map.get((edge_part_origin, first_beam_idx_in_part))
                                    beam_type = beam_data.get('beamType', '|NORMAL') if beam_data else '|NORMAL'

                                    type_visible = False
                                    if beam_type == '|NORMAL': type_visible = ui_props.toggle_beams_vis
                                    elif beam_type == '|ANISOTROPIC': type_visible = ui_props.toggle_anisotropic_beams_vis
                                    elif beam_type == '|SUPPORT': type_visible = ui_props.toggle_support_beams_vis
                                    elif beam_type == '|HYDRO': type_visible = ui_props.toggle_hydro_beams_vis
                                    elif beam_type == '|BOUNDED': type_visible = ui_props.toggle_bounded_beams_vis
                                    elif beam_type == '|LBEAM': type_visible = ui_props.toggle_lbeam_beams_vis
                                    elif beam_type == '|PRESSURED': type_visible = ui_props.toggle_pressured_beams_vis
                                    if not type_visible: continue

                                    v1, v2 = e.verts[0], e.verts[1]
                                    world_pos1 = active_obj.matrix_world @ v1.co; world_pos2 = active_obj.matrix_world @ v2.co
                                    original_width = ui_props.beam_width
                                    if beam_type == '|ANISOTROPIC': original_width = ui_props.anisotropic_beam_width
                                    elif beam_type == '|SUPPORT': original_width = ui_props.support_beam_width
                                    elif beam_type == '|HYDRO': original_width = ui_props.hydro_beam_width
                                    elif beam_type == '|BOUNDED': original_width = ui_props.bounded_beam_width
                                    elif beam_type == '|LBEAM': original_width = ui_props.lbeam_beam_width
                                    elif beam_type == '|PRESSURED': original_width = ui_props.pressured_beam_width

                                    if ui_props.use_dynamic_beam_coloring:
                                        color_to_use = None
                                        if beam_data:
                                            param_name = ui_props.dynamic_coloring_parameter
                                            param_value_raw = beam_data.get(param_name)
                                            if param_value_raw is not None:
                                                # Use FINALIZED auto thresholds
                                                low_thresh = auto_min_val if ui_props.use_auto_thresholds and auto_thresholds_valid else ui_props.dynamic_color_threshold_low
                                                high_thresh = auto_max_val if ui_props.use_auto_thresholds and auto_thresholds_valid else ui_props.dynamic_color_threshold_high
                                                color_to_use = _calculate_dynamic_color(param_value_raw, low_thresh, high_thresh)
                                        if color_to_use is not None:
                                            dynamic_beam_coords_colors.append((world_pos1, world_pos2, color_to_use))
                                    else:
                                        if beam_type == '|NORMAL': beam_coords.extend([world_pos1, world_pos2])
                                        elif beam_type == '|ANISOTROPIC': anisotropic_beam_coords.extend([world_pos1, world_pos2])
                                        elif beam_type == '|SUPPORT': support_beam_coords.extend([world_pos1, world_pos2])
                                        elif beam_type == '|HYDRO': hydro_beam_coords.extend([world_pos1, world_pos2])
                                        elif beam_type == '|BOUNDED': bounded_beam_coords.extend([world_pos1, world_pos2])
                                        elif beam_type == '|LBEAM': lbeam_coords.extend([world_pos1, world_pos2])
                                        elif beam_type == '|PRESSURED': pressured_beam_coords.extend([world_pos1, world_pos2])

                                    if e.index in jb_globals.selected_beam_edge_indices:
                                        selected_beam_coords_colors.append((world_pos1, world_pos2, WHITE_COLOR))
                                        selected_beam_max_original_width = max(selected_beam_max_original_width, original_width)
                                except (ValueError, IndexError) as parse_err: pass

                    # Torsionbar, Rail, Cross-Part Population (Single Part)
                    if ui_props.toggle_torsionbars_vis and jb_globals.curr_vdata and 'torsionbars' in jb_globals.curr_vdata and isinstance(jb_globals.curr_vdata['torsionbars'], list):
                        for tb in jb_globals.curr_vdata['torsionbars']:
                            ids = []
                            if isinstance(tb, dict): ids = [tb.get(f'id{i}:') for i in range(1, 5)]
                            elif isinstance(tb, list) and len(tb) >= 4: ids = tb[:4]
                            if len(ids) != 4 or not all(ids): continue
                            if any(node_id_to_hide_status.get(id, False) for id in ids): continue
                            world_pos = [None] * 4; all_nodes_found = True; missing_nodes = []
                            for i, node_id in enumerate(ids):
                                pos_data = node_id_to_pos_matrix_map.get(node_id)
                                cache_data = all_nodes_cache.get(node_id)
                                wp = None
                                if pos_data: wp = pos_data[1] @ pos_data[0]
                                elif cache_data: wp = cache_data[0]
                                if wp is None: all_nodes_found = False; missing_nodes.append(node_id)
                                world_pos[i] = wp
                            if not all_nodes_found:
                                if any(node_id not in warned_missing_nodes_this_rebuild for node_id in missing_nodes):
                                    print(f"Warning: Could not find position data for torsionbar nodes {missing_nodes}", file=sys.stderr)
                                    warned_missing_nodes_this_rebuild.update(missing_nodes)
                                continue
                            torsionbar_coords.extend([world_pos[0], world_pos[1]])
                            torsionbar_red_coords.extend([world_pos[1], world_pos[2]])
                            torsionbar_coords.extend([world_pos[2], world_pos[3]])

                    if ui_props.toggle_rails_vis and jb_globals.curr_vdata and 'rails' in jb_globals.curr_vdata and isinstance(jb_globals.curr_vdata['rails'], dict):
                        for rail_name, rail_info in jb_globals.curr_vdata['rails'].items():
                            rail_nodes = None
                            if isinstance(rail_info, list) and len(rail_info) == 2: rail_nodes = rail_info
                            elif isinstance(rail_info, dict): rail_nodes = rail_info.get('links:')
                            if isinstance(rail_nodes, list) and len(rail_nodes) == 2:
                                ids = rail_nodes
                                if not all(ids): continue
                                if any(node_id_to_hide_status.get(id, False) for id in ids): continue
                                world_pos = [None] * 2; all_nodes_found = True; missing_nodes = []
                                for i, node_id in enumerate(ids):
                                    pos_data = node_id_to_pos_matrix_map.get(node_id)
                                    cache_data = all_nodes_cache.get(node_id)
                                    wp = None
                                    if pos_data: wp = pos_data[1] @ pos_data[0]
                                    elif cache_data: wp = cache_data[0]
                                    if wp is None: all_nodes_found = False; missing_nodes.append(node_id)
                                    world_pos[i] = wp
                                if all_nodes_found: rail_coords.extend(world_pos)
                                elif ui_props.toggle_rails_vis:
                                    if any(node_id not in warned_missing_nodes_this_rebuild for node_id in missing_nodes):
                                        print(f"Warning: Could not find position data for rail nodes {missing_nodes}", file=sys.stderr)
                                        warned_missing_nodes_this_rebuild.update(missing_nodes)

                    if ui_props.toggle_cross_part_beams_vis and jb_globals.curr_vdata and 'beams' in jb_globals.curr_vdata:
                        obj_matrix = active_obj.matrix_world
                        for beam_data in jb_globals.curr_vdata['beams']:
                            if not isinstance(beam_data, dict) or beam_data.get('partOrigin') != current_part_name: continue
                            id1, id2 = beam_data.get('id1:'), beam_data.get('id2:')
                            if not id1 or not id2: continue
                            id1_in_active_geom = id1 in node_id_to_pos_matrix_map
                            id2_in_active_geom = id2 in node_id_to_pos_matrix_map
                            if id1_in_active_geom and id2_in_active_geom: continue
                            if node_id_to_hide_status.get(id1, False) or node_id_to_hide_status.get(id2, False): continue

                            cache1_data = all_nodes_cache.get(id1); cache2_data = all_nodes_cache.get(id2)
                            world_pos1, world_pos2 = None, None; is_truly_cross_part = False; missing_nodes = []
                            origin1 = cache1_data[2] if cache1_data else '?'; origin2 = cache2_data[2] if cache2_data else '?'
                            if origin1 != origin2 and '?' not in {origin1, origin2}: is_truly_cross_part = True
                            elif (id1_in_active_geom and not id2_in_active_geom) or (not id1_in_active_geom and id2_in_active_geom): is_truly_cross_part = True

                            if is_truly_cross_part:
                                if id1_in_active_geom: pos1_data = node_id_to_pos_matrix_map[id1]; world_pos1 = obj_matrix @ pos1_data[0]
                                elif cache1_data: world_pos1 = cache1_data[0]
                                else: missing_nodes.append(id1)
                                if id2_in_active_geom: pos2_data = node_id_to_pos_matrix_map[id2]; world_pos2 = obj_matrix @ pos2_data[0]
                                elif cache2_data: world_pos2 = cache2_data[0]
                                else: missing_nodes.append(id2)

                                if missing_nodes:
                                    if any(node_id not in warned_missing_nodes_this_rebuild for node_id in missing_nodes):
                                        print(f"Warning: Could not find position data for cross-part beam nodes {missing_nodes}", file=sys.stderr)
                                        warned_missing_nodes_this_rebuild.update(missing_nodes)
                                    continue

                                if ui_props.use_dynamic_beam_coloring:
                                    color_to_use = None
                                    if beam_data:
                                        param_name = ui_props.dynamic_coloring_parameter
                                        param_value_raw = beam_data.get(param_name)
                                        if param_value_raw is not None:
                                            # Use FINALIZED auto thresholds
                                            low_thresh = auto_min_val if ui_props.use_auto_thresholds and auto_thresholds_valid else ui_props.dynamic_color_threshold_low
                                            high_thresh = auto_max_val if ui_props.use_auto_thresholds and auto_thresholds_valid else ui_props.dynamic_color_threshold_high
                                            color_to_use = _calculate_dynamic_color(param_value_raw, low_thresh, high_thresh)
                                    if color_to_use is not None:
                                        dynamic_beam_coords_colors.append((world_pos1, world_pos2, color_to_use))
                                else:
                                    cross_part_beam_coords.extend([world_pos1, world_pos2])
                except Exception as e: print(f"Error getting beam geometry data from {active_obj.name}: {e}", file=sys.stderr)
                finally:
                    if bm and not (active_obj.mode == 'EDIT'): bm.free()

        # --- 7. Populate Highlight Coordinates ---
        if jb_globals.highlighted_element_type is not None and jb_globals.highlighted_element_type != 'node':
            ordered_highlight_node_ids = jb_globals.highlighted_element_ordered_node_ids
            highlight_world_positions = []
            all_highlight_nodes_found = True
            missing_highlight_nodes = []
            for node_id in ordered_highlight_node_ids:
                wp = None
                pos_data = node_id_to_pos_matrix_map.get(node_id)
                cache_data = all_nodes_cache.get(node_id)
                if pos_data: wp = pos_data[1] @ pos_data[0]
                elif cache_data: wp = cache_data[0]
                if wp is None: all_highlight_nodes_found = False; missing_highlight_nodes.append(node_id)
                highlight_world_positions.append(wp)

            if not all_highlight_nodes_found:
                if any(node_id not in warned_missing_nodes_this_rebuild for node_id in missing_highlight_nodes):
                    print(f"Warning: Could not find position data for highlighted nodes {missing_highlight_nodes}", file=sys.stderr)
                    warned_missing_nodes_this_rebuild.update(missing_highlight_nodes)
                jb_globals.highlighted_element_type = None
                jb_globals.highlighted_node_ids.clear()
                jb_globals.highlighted_element_ordered_node_ids.clear()
                highlight_coords.clear(); highlight_torsionbar_outer_coords.clear(); highlight_torsionbar_mid_coords.clear()
            else:
                element_type = jb_globals.highlighted_element_type
                if element_type in ('beam', 'rail', 'cross_part_beam', 'slidenode'):
                    if len(highlight_world_positions) >= 2: highlight_coords.extend([highlight_world_positions[0], highlight_world_positions[1]])
                elif element_type == 'torsionbar':
                    if len(highlight_world_positions) >= 4:
                        highlight_torsionbar_outer_coords.extend([highlight_world_positions[0], highlight_world_positions[1]])
                        highlight_torsionbar_mid_coords.extend([highlight_world_positions[1], highlight_world_positions[2]])
                        highlight_torsionbar_outer_coords.extend([highlight_world_positions[2], highlight_world_positions[3]])

        # --- 8. Create Batches ---
        if ui_props.use_dynamic_beam_coloring:
            if dynamic_beam_coords_colors:
                dyn_positions = []; dyn_colors = []
                for pos1, pos2, color in dynamic_beam_coords_colors:
                    dyn_positions.extend([pos1, pos2]); dyn_colors.extend([color, color])
                try: dynamic_beam_batch = batch_for_shader(render_shader, 'LINES', {"pos": dyn_positions, "color": dyn_colors})
                except Exception as e: print(f"Error creating dynamic beam batch: {e}", file=sys.stderr)
        else:
            if beam_coords:
                static_beam_colors = [ui_props.beam_color] * len(beam_coords)
                try: beam_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": beam_coords, "color": static_beam_colors})
                except Exception as e: print(f"Error creating beam batch: {e}", file=sys.stderr)
            if anisotropic_beam_coords:
                colors = [ui_props.anisotropic_beam_color] * len(anisotropic_beam_coords)
                try: anisotropic_beam_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": anisotropic_beam_coords, "color": colors})
                except Exception as e: print(f"Error creating anisotropic beam batch: {e}", file=sys.stderr)
            if support_beam_coords:
                colors = [ui_props.support_beam_color] * len(support_beam_coords)
                try: support_beam_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": support_beam_coords, "color": colors})
                except Exception as e: print(f"Error creating support beam batch: {e}", file=sys.stderr)
            if hydro_beam_coords:
                colors = [ui_props.hydro_beam_color] * len(hydro_beam_coords)
                try: hydro_beam_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": hydro_beam_coords, "color": colors})
                except Exception as e: print(f"Error creating hydro beam batch: {e}", file=sys.stderr)
            if bounded_beam_coords:
                colors = [ui_props.bounded_beam_color] * len(bounded_beam_coords)
                try: bounded_beam_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": bounded_beam_coords, "color": colors})
                except Exception as e: print(f"Error creating bounded beam batch: {e}", file=sys.stderr)
            if lbeam_coords:
                colors = [ui_props.lbeam_beam_color] * len(lbeam_coords)
                try: lbeam_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": lbeam_coords, "color": colors})
                except Exception as e: print(f"Error creating lbeam batch: {e}", file=sys.stderr)
            if pressured_beam_coords:
                colors = [ui_props.pressured_beam_color] * len(pressured_beam_coords)
                try: pressured_beam_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": pressured_beam_coords, "color": colors})
                except Exception as e: print(f"Error creating pressured beam batch: {e}", file=sys.stderr)
            if cross_part_beam_coords:
                colors = [ui_props.cross_part_beam_color] * len(cross_part_beam_coords)
                try: cross_part_beam_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": cross_part_beam_coords, "color": colors})
                except Exception as e: print(f"Error creating cross-part beam batch: {e}", file=sys.stderr)

        if torsionbar_coords:
            colors = [ui_props.torsionbar_color] * len(torsionbar_coords)
            try: torsionbar_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": torsionbar_coords, "color": colors})
            except Exception as e: print(f"Error creating torsionbar batch: {e}", file=sys.stderr)
        if torsionbar_red_coords:
            colors = [ui_props.torsionbar_mid_color] * len(torsionbar_red_coords)
            try: torsionbar_red_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": torsionbar_red_coords, "color": colors})
            except Exception as e: print(f"Error creating torsionbar mid batch: {e}", file=sys.stderr)
        if rail_coords:
            colors = [ui_props.rail_color] * len(rail_coords)
            try: rail_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": rail_coords, "color": colors})
            except Exception as e: print(f"Error creating rail batch: {e}", file=sys.stderr)

        if selected_beam_coords_colors:
            sel_positions = []; sel_colors = []
            for pos1, pos2, color in selected_beam_coords_colors:
                sel_positions.extend([pos1, pos2]); sel_colors.extend([color, color])
            try: selected_beam_batch = batch_for_shader(render_shader, 'LINES', {"pos": sel_positions, "color": sel_colors})
            except Exception as e: print(f"Error creating selected beam batch: {e}", file=sys.stderr)

        if highlight_coords:
            colors = [jb_globals.highlighted_element_color] * len(highlight_coords)
            try: highlight_render_batch = batch_for_shader(render_shader, 'LINES', {"pos": highlight_coords, "color": colors})
            except Exception as e: print(f"Error creating highlight batch (full rebuild): {e}", file=sys.stderr)
        if highlight_torsionbar_outer_coords:
            colors = [jb_globals.highlighted_element_color] * len(highlight_torsionbar_outer_coords)
            try: highlight_torsionbar_outer_batch = batch_for_shader(render_shader, 'LINES', {"pos": highlight_torsionbar_outer_coords, "color": colors})
            except Exception as e: print(f"Error creating highlight torsionbar outer batch (full rebuild): {e}", file=sys.stderr)
        if highlight_torsionbar_mid_coords:
            colors = [jb_globals.highlighted_element_mid_color] * len(highlight_torsionbar_mid_coords)
            try: highlight_torsionbar_mid_batch = batch_for_shader(render_shader, 'LINES', {"pos": highlight_torsionbar_mid_coords, "color": colors})
            except Exception as e: print(f"Error creating highlight torsionbar mid batch (full rebuild): {e}", file=sys.stderr)

        # --- 9. Reset dirty flags ---
        veh_render_dirty = False
        _highlight_dirty = False
    # --- End Rebuild Logic ---

    # --- Drawing ---
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.blend_set('ALPHA')

    if ui_props.use_dynamic_beam_coloring:
        if dynamic_beam_batch:
            gpu.state.line_width_set(ui_props.beam_width)
            gpu.state.depth_mask_set(True); dynamic_beam_batch.draw(render_shader); gpu.state.depth_mask_set(False)
    else:
        if beam_render_batch is not None and ui_props.toggle_beams_vis:
            gpu.state.line_width_set(ui_props.beam_width)
            gpu.state.depth_mask_set(True); beam_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
        if anisotropic_beam_render_batch is not None and ui_props.toggle_anisotropic_beams_vis:
            gpu.state.line_width_set(ui_props.anisotropic_beam_width)
            gpu.state.depth_mask_set(True); anisotropic_beam_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
        if support_beam_render_batch is not None and ui_props.toggle_support_beams_vis:
            gpu.state.line_width_set(ui_props.support_beam_width)
            gpu.state.depth_mask_set(True); support_beam_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
        if hydro_beam_render_batch is not None and ui_props.toggle_hydro_beams_vis:
            gpu.state.line_width_set(ui_props.hydro_beam_width)
            gpu.state.depth_mask_set(True); hydro_beam_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
        if bounded_beam_render_batch is not None and ui_props.toggle_bounded_beams_vis:
            gpu.state.line_width_set(ui_props.bounded_beam_width)
            gpu.state.depth_mask_set(True); bounded_beam_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
        if lbeam_render_batch is not None and ui_props.toggle_lbeam_beams_vis:
            gpu.state.line_width_set(ui_props.lbeam_beam_width)
            gpu.state.depth_mask_set(True); lbeam_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
        if pressured_beam_render_batch is not None and ui_props.toggle_pressured_beams_vis:
            gpu.state.line_width_set(ui_props.pressured_beam_width)
            gpu.state.depth_mask_set(True); pressured_beam_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
        if cross_part_beam_render_batch is not None and ui_props.toggle_cross_part_beams_vis:
            gpu.state.line_width_set(ui_props.cross_part_beam_width)
            gpu.state.depth_mask_set(True); cross_part_beam_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)

    if torsionbar_render_batch is not None and ui_props.toggle_torsionbars_vis:
        gpu.state.line_width_set(ui_props.torsionbar_width)
        gpu.state.depth_mask_set(True); torsionbar_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
    if torsionbar_red_render_batch is not None and ui_props.toggle_torsionbars_vis:
        gpu.state.line_width_set(ui_props.torsionbar_width)
        gpu.state.depth_mask_set(True); torsionbar_red_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)
    if rail_render_batch is not None and ui_props.toggle_rails_vis:
        gpu.state.line_width_set(ui_props.rail_width)
        gpu.state.depth_mask_set(True); rail_render_batch.draw(render_shader); gpu.state.depth_mask_set(False)

    if ui_props.show_selected_beam_outline and selected_beam_batch:
        final_thickness = selected_beam_max_original_width * ui_props.selected_beam_thickness_multiplier
        gpu.state.line_width_set(final_thickness)
        gpu.state.depth_mask_set(True); selected_beam_batch.draw(render_shader); gpu.state.depth_mask_set(False)

    gpu.state.depth_mask_set(True)
    highlight_width = 1.0
    highlight_type = jb_globals.highlighted_element_type
    if highlight_type == 'beam':
        beam_type = '|NORMAL'
        if jb_globals.curr_vdata and 'beams' in jb_globals.curr_vdata and jb_globals.highlighted_element_ordered_node_ids:
            target_id1 = jb_globals.highlighted_element_ordered_node_ids[0]
            target_id2 = jb_globals.highlighted_element_ordered_node_ids[1]
            active_obj = context.active_object
            target_part_origin = active_obj.data.get(constants.MESH_JBEAM_PART) if active_obj and active_obj.data else None
            if target_part_origin:
                for beam_data in jb_globals.curr_vdata['beams']:
                    if isinstance(beam_data, dict) and beam_data.get('partOrigin') == target_part_origin:
                        b_id1 = beam_data.get('id1:')
                        b_id2 = beam_data.get('id2:')
                        if (b_id1 == target_id1 and b_id2 == target_id2) or \
                           (b_id1 == target_id2 and b_id2 == target_id1):
                            beam_type = beam_data.get('beamType', '|NORMAL'); break
        base_width = ui_props.beam_width
        if beam_type == '|ANISOTROPIC': base_width = ui_props.anisotropic_beam_width
        elif beam_type == '|SUPPORT': base_width = ui_props.support_beam_width
        elif beam_type == '|HYDRO': base_width = ui_props.hydro_beam_width
        elif beam_type == '|BOUNDED': base_width = ui_props.bounded_beam_width
        elif beam_type == '|LBEAM': base_width = ui_props.lbeam_beam_width
        elif beam_type == '|PRESSURED': base_width = ui_props.pressured_beam_width
        highlight_width = base_width * ui_props.highlight_thickness_multiplier
    elif highlight_type == 'rail' or highlight_type == 'slidenode':
        highlight_width = ui_props.rail_width * ui_props.highlight_thickness_multiplier
    elif highlight_type == 'torsionbar':
        highlight_width = ui_props.torsionbar_width * ui_props.highlight_thickness_multiplier
    elif highlight_type == 'cross_part_beam':
        highlight_width = ui_props.cross_part_beam_width * ui_props.highlight_thickness_multiplier
    else:
        highlight_width = 1.0 * ui_props.highlight_thickness_multiplier

    if highlight_torsionbar_outer_batch is not None:
        gpu.state.line_width_set(highlight_width)
        highlight_torsionbar_outer_batch.draw(render_shader)
    if highlight_torsionbar_mid_batch is not None:
        gpu.state.line_width_set(highlight_width)
        highlight_torsionbar_mid_batch.draw(render_shader)
    if highlight_render_batch is not None:
        gpu.state.line_width_set(highlight_width)
        highlight_render_batch.draw(render_shader)

    gpu.state.depth_mask_set(False)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')
    # --- End Drawing ---

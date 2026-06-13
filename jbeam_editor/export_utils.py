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

from mathutils import Vector
import sys
import traceback
import uuid # <<< Import uuid
import json # <<< ADDED: Import json
import re # <<< ADDED: Import re
# <<< ADDED: Import TYPE_CHECKING >>>
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .properties import UIProperties

import bpy

import bmesh

from . import constants
# <<< MODIFIED: Import ASTNode from sjsonast directly >>>
from .sjsonast import ASTNode, parse as sjsonast_parse, stringify_nodes as sjsonast_stringify_nodes
from .utils import Metadata, is_number, to_c_float, to_float_str, get_float_precision
from . import text_editor
from . import globals as jb_globals # <<< ADDED: Import globals

from .jbeam import io as jbeam_io
from .jbeam.expression_parser import add_offset_expr


INDENT = ' ' * 4
TWO_INDENT = INDENT * 2
NL_INDENT = '\n' + INDENT
NL_TWO_INDENT = '\n' + TWO_INDENT

# Tolerance for mirror check position comparison
MIRROR_CHECK_TOLERANCE = 1e-5
# Tolerance for exact position collision check
POSITION_COLLISION_TOLERANCE = 1e-6 # Use a slightly tighter tolerance for exact match

class PartNodesActions:
    def __init__(self):
        self.nodes_to_add = {}
        self.nodes_to_delete = set()
        self.nodes_to_rename = {}
        self.nodes_to_move = {}
        # <<< ADDED: Dictionary for symmetrical additions >>>
        # Format: {new_node_id: (mirrored_node_id, position_tuple)}
        self.nodes_to_add_symmetrically = {}


def print_ast_nodes(ast_nodes, start_idx, size, bidirectional, file=None):
    if file is None:
        file = sys.stdout

    if not (start_idx >= 0 and start_idx < len(ast_nodes)):
        return

    start_node = ast_nodes[start_idx]
    text = ''

    if bidirectional:
        for x in ast_nodes[max(0, start_idx - size) : max(0, start_idx)]:
            text += str(x)

        text += '*' + str(start_node) + '*'

        for x in ast_nodes[min(start_idx + 1, len(ast_nodes) - 1) : min(start_idx + size, len(ast_nodes))]:
            text += str(x)
    else:
        text += '*' + str(start_node) + '*'

        for x in ast_nodes[min(start_idx + 1, len(ast_nodes) - 1) : min(start_idx + size, len(ast_nodes))]:
            text += str(x)

    print(text, file=file)


def get_prev_node(ast_nodes, start_idx, data_types):
    i = start_idx
    while i >= 0:
        node = ast_nodes[i]
        if node.data_type in data_types:
            return i
        i -= 1
    return -1

# <<< ADDED HELPER: Get indentation string from preceding WSC node >>>
def _get_indentation_from_previous_wsc(ast_nodes: list, element_start_index: int) -> str:
    """
    Finds the whitespace node immediately preceding the given index and
    returns the indentation part (characters after the last newline).
    Defaults to NL_TWO_INDENT if not found or formatted unexpectedly.
    """
    if element_start_index > 0:
        prev_node = ast_nodes[element_start_index - 1]
        if prev_node.data_type == 'wsc':
            wsc_value = prev_node.value
            last_newline_pos = wsc_value.rfind('\n')
            if last_newline_pos != -1:
                # Return newline plus the indentation characters
                return wsc_value[last_newline_pos:]
    # Fallback to default indent if preceding WSC or newline not found
    return NL_TWO_INDENT
# <<< END ADDED HELPER >>>

def get_next_non_wsc_node(ast_nodes, start_idx):
    i = start_idx
    len_nodes = len(ast_nodes)
    while i < len_nodes:
        node = ast_nodes[i]
        if node.data_type != 'wsc':
            return i
        i += 1
    return -1


def compare_and_set_value(original_jbeam_file_data, jbeam_file_data, stack, index, node):
    old_data = original_jbeam_file_data
    data = jbeam_file_data
    for stack_entry in stack:
        old_data = old_data[stack_entry[0]]
        data = data[stack_entry[0]]

    old_data = old_data[index]
    data = data[index]

    # Only change value in AST if changed between old and new SJSON data
    if node.data_type == 'number':
        if is_number(data) and (to_c_float(old_data) != to_c_float(data) and old_data != data):
            node.value = data
            fval = float(data)
            node.precision = min(4, max(len((f'%.4g' % abs(fval - int(fval)))) - 2, 0))
            return True
    else:
        if old_data != data:
            node.value = data
            return True

    return False


def add_jbeam_setup(ast_nodes: list, jbeam_section_start_node_idx: int, jbeam_section_end_node_idx: int):
    if ast_nodes[jbeam_section_end_node_idx - 1].data_type == 'wsc':
        i = jbeam_section_end_node_idx - 1
    else:
        i = jbeam_section_end_node_idx

    node_after_entry = ast_nodes[i]
    node_2_after_entry = None

    if node_after_entry.data_type == 'wsc':
        # Split WSC node into one node for inline WSCS node entry and second node after newline character
        wscs = node_after_entry.value
        nl_found = False

        for k, char in enumerate(wscs):
            if char == '\n':
                nl_found = True
                break

        node_after_entry.value = wscs[:k] if nl_found else wscs
        node_2_after_entry = ASTNode('wsc', wscs[k:]) if nl_found else None
    else:
        node_after_entry = ASTNode('wsc', '')
        ast_nodes.insert(i, node_after_entry)
    i += 1

    #print("node_after_entry", repr(node_after_entry.value))
    #if node_2_after_entry:
    #    print("node_2_after_entry", repr(node_2_after_entry.value))

    return i, node_after_entry, node_2_after_entry


# Add jbeam nodes to end of JBeam section from list of nodes to add (this is called on node section list end character)
def add_jbeam_nodes(ast_nodes: list, jbeam_section_start_node_idx: int, jbeam_section_end_node_idx: int, nodes_to_add: dict):
    # <<< This function now only handles nodes NOT added symmetrically >>>
    if not nodes_to_add: # <<< ADDED: Early exit if nothing to add >>>
        return jbeam_section_end_node_idx

    i, node_after_entry, node_2_after_entry = add_jbeam_setup(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx)

    # <<< START ADDED COMMENT LOGIC >>>
    # Check if "//ADDED NODES BY EDITOR//" comment exists within the current node section
    comment_text = '//ADDED NODES BY EDITOR//'
    comment_already_exists_in_section = False
    # Iterate only within the bounds of the current node section
    for k in range(jbeam_section_start_node_idx, jbeam_section_end_node_idx):
        node = ast_nodes[k]
        if node.data_type == 'wsc' and comment_text in node.value:
            comment_already_exists_in_section = True
            break # Found it within the section, no need to check further

    # Add "//ADDED NODES BY EDITOR//" comment only if nodes are being added and comment doesn't already exist in this section
    if nodes_to_add and not comment_already_exists_in_section:
        # Add an extra newline before the standard indent and comment text
        comment_wsc_value = '\n' + NL_TWO_INDENT + comment_text
        if node_after_entry:
            # Append comment to the existing whitespace node before the first new node's indent
            node_after_entry.value += comment_wsc_value
            # Don't set node_after_entry to None here, the loop needs it for the first node's indent
        else:
            # Insert comment as a new whitespace node. The loop will handle the first node's indent.
            ast_nodes.insert(i, ASTNode('wsc', comment_wsc_value))
            i += 1 # Adjust insertion index because we added a node
    # <<< END ADDED COMMENT LOGIC >>>

    # Insert new nodes at bottom of nodes section
    nodes = nodes_to_add.items()

    for node_id, node_pos in nodes:
        # This logic correctly adds the NL_TWO_INDENT *after* the comment (if added),
        # or as a new node for subsequent nodes.
        if node_after_entry:
            node_after_entry.value += NL_TWO_INDENT
            node_after_entry = None
        else:
            ast_nodes.insert(i + 0, ASTNode('wsc', NL_TWO_INDENT))
            i += 1

        ast_nodes.insert(i + 0, ASTNode('['))
        ast_nodes.insert(i + 1, ASTNode('"', node_id))
        ast_nodes.insert(i + 2, ASTNode('wsc', ', '))
        ast_nodes.insert(i + 3, ASTNode('number', node_pos[0], precision=get_float_precision(node_pos[0])))
        ast_nodes.insert(i + 4, ASTNode('wsc', ', '))
        ast_nodes.insert(i + 5, ASTNode('number', node_pos[1], precision=get_float_precision(node_pos[1])))
        ast_nodes.insert(i + 6, ASTNode('wsc', ', '))
        ast_nodes.insert(i + 7, ASTNode('number', node_pos[2], precision=get_float_precision(node_pos[2])))
        ast_nodes.insert(i + 8, ASTNode(']'))
        ast_nodes.insert(i + 9, ASTNode('wsc', ','))
        i += 10

    # Add modified original last WSCS back to end of section
    if node_2_after_entry:
        # Append the original trailing whitespace after the last added comma's whitespace node
        if i > 0 and ast_nodes[i - 1].data_type == 'wsc':
             ast_nodes[i - 1].value += node_2_after_entry.value
        # Handle case where no nodes were added but whitespace was modified
        elif node_after_entry:
             node_after_entry.value += node_2_after_entry.value

    #print_ast_nodes(ast_nodes, i, 10, True)
    return i


# Add jbeam beams to end of JBeam section from list of beams to add (this is called on beam section list end character)
def add_jbeam_beams(ast_nodes: list, jbeam_section_start_node_idx: int, jbeam_section_end_node_idx: int, beams_to_add: list):
    # <<< REVERTED: Use hardcoded comment >>>
    comment_text = '//ADDED BEAMS BY EDITOR//'

    # <<< ADDED: Early exit if nothing to add >>>
    if not beams_to_add:
        return jbeam_section_end_node_idx
    i, node_after_entry, node_2_after_entry = add_jbeam_setup(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx)

    # <<< MODIFIED: Use the custom comment_text for the existence check >>>
    # Check if the custom comment exists within the current beam section
    comment_already_exists_in_section = False
    # Iterate only within the bounds of the current beam section
    for k in range(jbeam_section_start_node_idx, jbeam_section_end_node_idx):
        node = ast_nodes[k]
        if node.data_type == 'wsc' and comment_text and comment_text in node.value: # Check if comment_text is not empty before checking 'in'
            comment_already_exists_in_section = True
            break # Found it within the section, no need to check further

    # <<< MODIFIED: Use custom comment_text and check if it's not empty >>>
    # Add comment only if beams are being added, comment text is provided, and comment doesn't already exist in this section
    if beams_to_add and comment_text and not comment_already_exists_in_section:
        # Add an extra newline before the standard indent and comment text
        comment_wsc_value = '\n' + NL_TWO_INDENT + comment_text
        if node_after_entry:
            # Append comment to the existing whitespace node before the first new beam's indent
            node_after_entry.value += comment_wsc_value
            # Don't set node_after_entry to None here, the loop needs it for the first beam's indent
        else:
            # Insert comment as a new whitespace node. The loop will handle the first beam's indent.
            ast_nodes.insert(i, ASTNode('wsc', comment_wsc_value))
            i += 1 # Adjust insertion index because we added a node

    # Insert new beams at bottom of beams section (Original Loop Logic)
    for (node_id_1, node_id_2) in beams_to_add:
        # This logic correctly adds the NL_TWO_INDENT *after* the comment (if added),
        # or as a new node for subsequent beams.
        if node_after_entry:
            node_after_entry.value += NL_TWO_INDENT
            node_after_entry = None
        else:
            ast_nodes.insert(i + 0, ASTNode('wsc', NL_TWO_INDENT))
            i += 1

        ast_nodes.insert(i + 0, ASTNode('['))
        ast_nodes.insert(i + 1, ASTNode('"', node_id_1))
        ast_nodes.insert(i + 2, ASTNode('wsc', ',')) # Keep comma wsc separate for clarity
        ast_nodes.insert(i + 3, ASTNode('"', node_id_2))
        ast_nodes.insert(i + 4, ASTNode(']'))
        ast_nodes.insert(i + 5, ASTNode('wsc', ',')) # Keep comma wsc separate
        i += 6

    # Add modified original last WSCS back to end of section
    if node_2_after_entry:
        # Append the original trailing whitespace after the last added comma's whitespace node
        if i > 0 and ast_nodes[i - 1].data_type == 'wsc':
             ast_nodes[i - 1].value += node_2_after_entry.value
        # If no beams were added, but setup modified whitespace, handle it?
        # This case seems unlikely if node_2_after_entry exists and beams_to_add was empty.
        # If beams_to_add was empty and comment was not added, node_after_entry might still exist.
        elif node_after_entry:
             node_after_entry.value += node_2_after_entry.value


    #print_ast_nodes(ast_nodes, i, 10, True)
    return i


# Add jbeam triangles to end of JBeam section from list of triangles to add (this is called on triangle section list end character)
def add_jbeam_triangles(ast_nodes: list, jbeam_section_start_node_idx: int, jbeam_section_end_node_idx: int, tris_to_add: list):
    # <<< ADDED: Early exit if nothing to add >>>
    if not tris_to_add:
        return jbeam_section_end_node_idx

    i, node_after_entry, node_2_after_entry = add_jbeam_setup(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx)

    # Insert new tris at bottom of triangles section

    for (node_id_1, node_id_2, node_id_3) in tris_to_add:
        if node_after_entry:
            node_after_entry.value += NL_TWO_INDENT
            node_after_entry = None
        else:
            ast_nodes.insert(i + 0, ASTNode('wsc', NL_TWO_INDENT))
            i += 1

        ast_nodes.insert(i + 0, ASTNode('['))
        ast_nodes.insert(i + 1, ASTNode('"', node_id_1))
        ast_nodes.insert(i + 2, ASTNode('wsc', ','))
        ast_nodes.insert(i + 3, ASTNode('"', node_id_2))
        ast_nodes.insert(i + 4, ASTNode('wsc', ','))
        ast_nodes.insert(i + 5, ASTNode('"', node_id_3))
        ast_nodes.insert(i + 6, ASTNode(']'))
        ast_nodes.insert(i + 7, ASTNode('wsc', ','))
        i += 8

    # Add modified original last WSCS back to end of section
    if node_2_after_entry:
        # <<< MODIFIED: Check if last node is WSC before appending >>>
        if i > 0 and ast_nodes[i - 1].data_type == 'wsc':
            ast_nodes[i - 1].value += node_2_after_entry.value
        elif node_after_entry: # Handle case where no tris were added but whitespace was modified
            node_after_entry.value += node_2_after_entry.value

    #print_ast_nodes(ast_nodes, i, 10, True)
    return i


# Add jbeam quads to end of JBeam section from list of quads to add (this is called on triangle section list end character)
def add_jbeam_quads(ast_nodes: list, jbeam_section_start_node_idx: int, jbeam_section_end_node_idx: int, quads_to_add: list):
    # <<< ADDED: Early exit if nothing to add >>>
    if not quads_to_add:
        return jbeam_section_end_node_idx

    i, node_after_entry, node_2_after_entry = add_jbeam_setup(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx)

    # Insert new quads at bottom of quads section

    for (node_id_1, node_id_2, node_id_3, node_id_4) in quads_to_add:
        if node_after_entry:
            node_after_entry.value += NL_TWO_INDENT
            node_after_entry = None
        else:
            ast_nodes.insert(i + 0, ASTNode('wsc', NL_TWO_INDENT))
            i += 1

        ast_nodes.insert(i + 0, ASTNode('['))
        ast_nodes.insert(i + 1, ASTNode('"', node_id_1))
        ast_nodes.insert(i + 2, ASTNode('wsc', ','))
        ast_nodes.insert(i + 3, ASTNode('"', node_id_2))
        ast_nodes.insert(i + 4, ASTNode('wsc', ','))
        ast_nodes.insert(i + 5, ASTNode('"', node_id_3))
        ast_nodes.insert(i + 6, ASTNode('wsc', ','))
        ast_nodes.insert(i + 7, ASTNode('"', node_id_4))
        ast_nodes.insert(i + 8, ASTNode(']'))
        ast_nodes.insert(i + 9, ASTNode('wsc', ','))
        i += 10

    # Add modified original last WSCS back to end of section
    if node_2_after_entry:
        # <<< MODIFIED: Check if last node is WSC before appending >>>
        if i > 0 and ast_nodes[i - 1].data_type == 'wsc':
            ast_nodes[i - 1].value += node_2_after_entry.value
        elif node_after_entry: # Handle case where no quads were added but whitespace was modified
            node_after_entry.value += node_2_after_entry.value

    #print_ast_nodes(ast_nodes, i, 50, True)
    return i


# Delete jbeam entry from JBeam section (this is called on list end character of JBeam node entry)
def delete_jbeam_entry(ast_nodes: list, jbeam_section_start_node_idx: int, jbeam_entry_start_node_idx: int, jbeam_entry_end_node_idx: int):
    jbeam_entry_prev_node = ast_nodes[jbeam_entry_start_node_idx - 1]
    jbeam_entry_next_node = ast_nodes[jbeam_entry_end_node_idx + 1]

    jbeam_entry_to_left = True
    if jbeam_entry_prev_node.data_type == 'wsc':
        if '\n' in jbeam_entry_prev_node.value:
            jbeam_entry_to_left = False

    jbeam_entry_to_right, deleted_right_wsc = True, False
    if jbeam_entry_next_node.data_type == 'wsc':
        if '\n' in jbeam_entry_next_node.value:
            jbeam_entry_to_right = False

        # If node entry to left, delete right wscs before newline character
        # Else, delete up till newline character
        for k, char in enumerate(jbeam_entry_next_node.value):
            if char == '\n':
                if jbeam_entry_to_left:
                    k -= 1
                break

        if k == len(jbeam_entry_next_node.value) - 1:
            del ast_nodes[jbeam_entry_end_node_idx + 1] # next_node
            deleted_right_wsc = True
        else:
            jbeam_entry_next_node.value = jbeam_entry_next_node.value[k + 1:]

    if not jbeam_entry_to_left and not jbeam_entry_to_right:
        # Single node entry, delete left indent (not full wsc node)
        wscs = jbeam_entry_prev_node.value
        wscs_len = len(wscs)
        for k in range(wscs_len - 1, -1, -1):
            char = wscs[k]
            if char == '\n':
                break

        jbeam_entry_prev_node.value = jbeam_entry_prev_node.value[:k + 1]

    # Delete the JBeam entry
    del ast_nodes[jbeam_entry_start_node_idx:jbeam_entry_end_node_idx + 1]
    i = jbeam_entry_start_node_idx - 1
    if deleted_right_wsc:
        i -= 1

    # If current character is a WSC and previous is also, merge them into one
    curr_node = ast_nodes[i]
    jbeam_entry_next_node = ast_nodes[i + 1]

    #print(repr(curr_node.value))
    #print(repr(node_entry_next_node.value))

    if curr_node.data_type == 'wsc' and jbeam_entry_next_node.data_type == 'wsc':
        jbeam_entry_next_node.value = curr_node.value + jbeam_entry_next_node.value
        del ast_nodes[i]
        i -= 1

    #print_ast_nodes(ast_nodes, i, 10, True)

    return i


def undo_node_move_offset_and_apply_translation_to_expr(init_node_data: dict, new_pos: Vector):
    # Undo node move/offset
    pos_no_offset = Vector(init_node_data['posNoOffset'])
    init_pos = init_node_data['pos']
    metadata = init_node_data[Metadata]

    offset_from_init_pos_tup = (new_pos.x - init_pos[0], new_pos.y - init_pos[1], new_pos.z - init_pos[2])

    # Apply node translation to expression if expression exists
    pos_expr = (metadata.get('posX', 'expression'), metadata.get('posY', 'expression'), metadata.get('posZ', 'expression'))
    position = [None, None, None]
    for i in range(3):
        if pos_expr[i] is not None:
            if abs(offset_from_init_pos_tup[i]) > 0.000001:
                position[i] = add_offset_expr(pos_expr[i], to_c_float(offset_from_init_pos_tup[i]))
            else:
                position[i] = pos_expr[i]
        else:
            position[i] = to_c_float(pos_no_offset[i] + offset_from_init_pos_tup[i])

    return tuple(position)


def rec_node_ref_rename(data, node_renames: dict):
    if isinstance(data, list):
        for k, v in enumerate([*data]):
            if isinstance(v, (list, dict)):
                rec_node_ref_rename(v, node_renames)
            elif isinstance(v, str):
                if v in node_renames:
                    data[k] = node_renames[v]
    elif isinstance(data, dict):
        for k, v in [*data.items()]:
            if isinstance(v, (list, dict)):
                rec_node_ref_rename(v, node_renames)
            elif isinstance(v, str):
                if v in node_renames:
                    data[k] = node_renames[v]


def set_node_renames_positions(jbeam_file_data_modified: dict, jbeam_part: str, blender_nodes: dict, node_renames: dict, affect_node_references: bool):
    # Update current JBeam file data with blender data (only renames and moving, no additions or deletions)
    if jbeam_part not in jbeam_file_data_modified:
        return

    for section, section_data in jbeam_file_data_modified[jbeam_part].items():
        if section == 'nodes':
            for i, row_data in enumerate(section_data):
                if i == 0:
                    continue  # Ignore header row
                if isinstance(row_data, list):
                    row_node_id = row_data[0]

                    # # Ignore if node is defined in a different part.
                    # # Its possible depending on part loading order.
                    if row_node_id not in blender_nodes or blender_nodes[row_node_id]['partOrigin'] != jbeam_part:
                        continue

                    if row_node_id in node_renames:
                        row_data[0] = node_renames[row_node_id]

                    if row_node_id in blender_nodes:
                        pos = blender_nodes[row_node_id]['pos']
                        row_data[1], row_data[2], row_data[3] = pos[0], pos[1], pos[2]

        # Rename node references in all other sections
        elif affect_node_references:
            rec_node_ref_rename(section_data, node_renames)


# <<< MODIFIED HELPER FUNCTION: get_base_node_name >>>
def get_base_node_name(node_id: str, ui_props: 'UIProperties'): # Keep hint as string literal
    """
    Removes the longest matching symmetrical or middle identifier (prefix/suffix)
    from the node ID based on settings. Returns the base name, the type of
    identifier found ('left', 'right', 'middle', 'none'), and the actual
    identifier string that was removed.
    """
    if not ui_props.use_node_naming_prefixes:
        return node_id, 'none', None # Return original ID if feature is disabled

    prefix_pos = ui_props.new_node_prefix_position
    middle_id = ui_props.new_node_prefix_middle
    pairs_json = ui_props.new_node_symmetrical_pairs
    symmetrical_pairs = []
    try:
        parsed_pairs = json.loads(pairs_json)
        if isinstance(parsed_pairs, list):
            # Ensure pairs are valid lists/tuples of two strings
            symmetrical_pairs = [(p[0], p[1]) for p in parsed_pairs if isinstance(p, (list, tuple)) and len(p) == 2 and isinstance(p[0], str) and isinstance(p[1], str)]
    except (json.JSONDecodeError, TypeError, IndexError):
        print(f"Warning: Could not parse symmetrical pairs JSON: {pairs_json}. Using default [['l', 'r']].", file=sys.stderr)
        symmetrical_pairs = [("l", "r")] # Fallback

    # Sort pairs by the length of identifiers (longest first) to ensure correct matching
    symmetrical_pairs.sort(key=lambda p: max(len(p[0]), len(p[1])), reverse=True)

    # Check symmetrical pairs first (longest to shortest)
    for left_id, right_id in symmetrical_pairs:
        if prefix_pos == 'FRONT':
            if left_id and node_id.startswith(left_id):
                return node_id[len(left_id):], 'left', left_id
            elif right_id and node_id.startswith(right_id):
                return node_id[len(right_id):], 'right', right_id
        elif prefix_pos == 'BACK':
            if left_id and node_id.endswith(left_id):
                return node_id[:-len(left_id)], 'left', left_id
            elif right_id and node_id.endswith(right_id):
                return node_id[:-len(right_id)], 'right', right_id

    # If no symmetrical pair matched, check the middle identifier
    if middle_id:
        if prefix_pos == 'FRONT' and node_id.startswith(middle_id):
             return node_id[len(middle_id):], 'middle', middle_id
    elif prefix_pos == 'BACK':
         if node_id.endswith(middle_id):
             return node_id[:-len(middle_id)], 'middle', middle_id

    # No identifier found
    return node_id, 'none', None
# <<< END MODIFIED HELPER FUNCTION >>>

# <<< START ADDED HELPER FUNCTION >>>
def get_symmetrical_node_id(node_id: str, ui_props: 'UIProperties'):
    """
    Finds the symmetrical counterpart node ID based on prefix/suffix settings.
    Returns the counterpart ID string, or None if no symmetry is applicable.
    """
    if not ui_props.use_node_naming_prefixes:
        return None # Symmetry disabled

    base_name, identifier_type, matched_identifier = get_base_node_name(node_id, ui_props)

    if identifier_type not in ('left', 'right') or matched_identifier is None:
        return None

    prefix_pos = ui_props.new_node_prefix_position
    pairs_json = ui_props.new_node_symmetrical_pairs
    symmetrical_pairs = []
    try:
        parsed_pairs = json.loads(pairs_json)
        if isinstance(parsed_pairs, list):
            symmetrical_pairs = [(p[0], p[1]) for p in parsed_pairs if isinstance(p, (list, tuple)) and len(p) == 2 and isinstance(p[0], str) and isinstance(p[1], str)]
    except (json.JSONDecodeError, TypeError, IndexError):
        symmetrical_pairs = [("l", "r")] # Fallback

    counterpart_id = None
    if prefix_pos == 'FRONT':
        for left_id, right_id in symmetrical_pairs:
            if identifier_type == 'left' and matched_identifier == left_id:
                counterpart_id = right_id; break
            elif identifier_type == 'right' and matched_identifier == right_id:
                counterpart_id = left_id; break
    elif prefix_pos == 'BACK':
        for left_id, right_id in symmetrical_pairs:
            if identifier_type == 'left' and matched_identifier == left_id:
                counterpart_id = right_id; break
            elif identifier_type == 'right' and matched_identifier == right_id:
                counterpart_id = left_id; break

    if counterpart_id is not None: # Ensure counterpart exists in the pair
        if prefix_pos == 'FRONT': return f"{counterpart_id}{base_name}"
        elif prefix_pos == 'BACK': return f"{base_name}{counterpart_id}"

    return None # No symmetry found (e.g., original had middle identifier)
# <<< END ADDED HELPER FUNCTION >>>





# <<< START REPLACED FUNCTION >>>
def get_nodes_add_delete_rename(obj: bpy.types.Object, bm: bmesh.types.BMesh, jbeam_part: str, init_nodes_data: dict, affect_node_references: bool):
    context = bpy.context
    ui_props = context.scene.ui_properties # Already getting ui_props

    parts_actions = {jbeam_part: PartNodesActions()}

    init_node_id_layer = bm.verts.layers.string[constants.VL_INIT_NODE_ID]
    node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID]
    part_origin_layer = bm.verts.layers.string[constants.VL_NODE_PART_ORIGIN]
    node_is_fake_layer = bm.verts.layers.int[constants.VL_NODE_IS_FAKE]

    blender_nodes = {}
    processed_new_node_positions = set() # Store tuples (x, y, z)
    nodes_requiring_confirmation = [] # Store tuples: (node_id, display_name_for_dialog, position_tuple, existing_collided_id)

    # Ensure lookup table before iterating
    bm.verts.ensure_lookup_table()

    # Create dictionary where key is init node id and value is current blender node id and position
    for v in bm.verts:
        # Skip vertices already marked as fake (e.g., from previous collision checks)
        if v[node_is_fake_layer] == 1:
            continue

        init_node_id = v[init_node_id_layer].decode('utf-8')
        node_id = v[node_id_layer].decode('utf-8')
        node_part_origin = v[part_origin_layer].decode('utf-8')
        pos: Vector = obj.matrix_world @ v.co

        # --- Handle TEMP nodes ---
        if node_id.startswith('TEMP_'):
            # <<< ADDED: Check if prefixing is enabled >>>
            use_prefixes = ui_props.use_node_naming_prefixes
            # <<< END ADDED >>>

            # --- MIRROR CHECK ---
            mirrored_node_found = False
            base_name_from_mirror = None
            mirrored_node_id = None # <<< ADDED: Store the ID of the mirrored node
            # <<< MODIFIED: Only perform mirror check if prefixes are enabled >>>
            if use_prefixes:
                for other_node_id, other_node_data in init_nodes_data.items():
                    # Skip if the other node is marked for deletion in this cycle
                    is_marked_for_delete = False
                    for actions in parts_actions.values():
                        if other_node_id in actions.nodes_to_delete:
                            is_marked_for_delete = True; break
                    if is_marked_for_delete: continue

                    other_init_pos = other_node_data.get('pos')
                    if other_init_pos and len(other_init_pos) == 3:
                        if (abs(pos.y - other_init_pos[1]) < MIRROR_CHECK_TOLERANCE and
                            abs(pos.z - other_init_pos[2]) < MIRROR_CHECK_TOLERANCE and
                            abs(pos.x + other_init_pos[0]) < MIRROR_CHECK_TOLERANCE):
                            mirrored_node_found = True; mirrored_node_id = other_node_id
                            # <<< MODIFIED: Pass ui_props to helper >>>
                            # base_name_from_mirror = get_base_node_name(other_node_id, ui_props) # Old way
                            break
            # <<< END MODIFIED >>>

            # --- Determine Potential Final IDs ---
            uuid_base = str(uuid.uuid4())
            mirrored_name = None
            uuid_name = None # Initialize uuid_name

            if mirrored_node_found:
                # Use the NEW function to get the symmetrical name
                mirrored_name = get_symmetrical_node_id(mirrored_node_id, ui_props)

            # Determine identifier for UUID name (only if needed or no mirror)
            identifier = ""
            if use_prefixes:
                 # Find the appropriate identifier based on position using the new logic
                 # This requires parsing pairs again, maybe refactor get_base_node_name slightly?
                 # Or create a helper: find_identifier_for_position(pos_x, ui_props)
                 pairs_json = ui_props.new_node_symmetrical_pairs
                 symmetrical_pairs = []
                 try:
                     parsed_pairs = json.loads(pairs_json)
                     if isinstance(parsed_pairs, list):
                         symmetrical_pairs = [(p[0], p[1]) for p in parsed_pairs if isinstance(p, (list, tuple)) and len(p) == 2 and isinstance(p[0], str) and isinstance(p[1], str)]
                 except (json.JSONDecodeError, TypeError, IndexError):
                     symmetrical_pairs = [("l", "r")] # Fallback
                 symmetrical_pairs.sort(key=lambda p: max(len(p[0]), len(p[1])), reverse=True)
                 middle_id = ui_props.new_node_prefix_middle

                 if v.co.x < -MIRROR_CHECK_TOLERANCE:
                     # Find longest matching right identifier
                     for l_id, r_id in symmetrical_pairs:
                         if r_id: identifier = r_id; break # Found longest right
                 elif v.co.x > MIRROR_CHECK_TOLERANCE:
                     # Find longest matching left identifier
                     for l_id, r_id in symmetrical_pairs:
                         if l_id: identifier = l_id; break # Found longest left
                 else: # Middle
                     identifier = middle_id

            # Construct UUID name
            if use_prefixes and identifier:
                if ui_props.new_node_prefix_position == 'FRONT':
                    uuid_name = f"{identifier}{uuid_base}"
                else: # BACK
                    uuid_name = f"{uuid_base}{identifier}"
            else: # Prefixes disabled, just use UUID
                mirrored_name = None # No mirrored name if prefixes are off
                uuid_name = uuid_base

            # If mirrored_name couldn't be generated (e.g., mirrored node was middle), fall back to UUID name
            if mirrored_name is None:
                 # This ensures mirrored_name is always set, preferring symmetry if possible
                 mirrored_name = uuid_name # Use UUID name if symmetry fails

            # --- Collision Check ---
            collision_found = False
            collided_with_id = None
            # Check against initial nodes
            for other_node_id, other_node_data in init_nodes_data.items():
                # Skip if marked for deletion
                is_marked_for_delete = False
                for actions in parts_actions.values():
                    if other_node_id in actions.nodes_to_delete:
                        is_marked_for_delete = True; break
                if is_marked_for_delete: continue

                other_init_pos = other_node_data.get('pos')
                if other_init_pos and len(other_init_pos) == 3:
                    if (abs(pos.x - other_init_pos[0]) < POSITION_COLLISION_TOLERANCE and
                        abs(pos.y - other_init_pos[1]) < POSITION_COLLISION_TOLERANCE and
                        abs(pos.z - other_init_pos[2]) < POSITION_COLLISION_TOLERANCE):
                        collision_found = True; collided_with_id = other_node_id; break
            # Check against newly processed nodes
            if not collision_found:
                rounded_pos = tuple(round(coord, 6) for coord in pos)
                if rounded_pos in processed_new_node_positions:
                    collision_found = True; collided_with_id = "another new node"

            # --- Handle Collision or No Collision ---
            if collision_found:
                final_node_id_collision = uuid_name # Always use UUID for collision tracking internally
                display_name_for_dialog = mirrored_name # Use the potentially nicer mirrored name for the dialog
                print(f"Overlap detected: New node '{display_name_for_dialog}' at {pos.to_tuple()} overlaps with '{collided_with_id}'. Queued for deletion confirmation.", file=sys.stderr)
                final_node_id_bytes = bytes(final_node_id_collision, 'utf-8')
                v[node_id_layer] = final_node_id_bytes
                v[init_node_id_layer] = final_node_id_bytes # Update init ID as well
                # <<< MODIFIED: Store existing_collided_id >>>
                nodes_requiring_confirmation.append((final_node_id_collision, display_name_for_dialog, pos.to_tuple(), collided_with_id))
                v[node_is_fake_layer] = 1 # Mark as fake pending confirmation
                # <<< ADDED: Store overlap mapping >>>
                jb_globals.node_overlap_remap[final_node_id_collision] = collided_with_id

                # <<< *** MODIFIED PLACEMENT LOGIC *** >>>
                part_actions: PartNodesActions = parts_actions.setdefault(node_part_origin, PartNodesActions())
                if mirrored_node_found and mirrored_node_id is not None:
                    # If it HAS a mirror, add to symmetrical list even though it collided
                    part_actions.nodes_to_add_symmetrically[final_node_id_collision] = (mirrored_node_id, pos.to_tuple())
                else:
                    # If it has NO mirror, add to normal list
                    # part_actions.nodes_to_add[final_node_id_collision] = pos.to_tuple() # Don't add to list yet, handled by confirmation
                    pass # No action needed here, handled by confirmation operator
                # <<< *** END MODIFIED PLACEMENT LOGIC *** >>>
                blender_nodes[final_node_id_collision] = {'curr_node_id': final_node_id_collision, 'pos': pos.to_tuple(), 'partOrigin': node_part_origin} # Still add to blender_nodes for tracking

                continue # Go to next vertex
            else: # No collision
                final_node_id_no_collision = mirrored_name # Use mirrored name if available, else UUID name
                final_node_id_bytes = bytes(final_node_id_no_collision, 'utf-8')
                v[node_id_layer] = final_node_id_bytes
                v[init_node_id_layer] = final_node_id_bytes # Update init ID as well
                init_node_id = final_node_id_no_collision
                node_id = final_node_id_no_collision
                part_actions: PartNodesActions = parts_actions.setdefault(node_part_origin, PartNodesActions())

                # <<< MODIFIED: Add to symmetrical list if mirror found, else normal list >>>
                if mirrored_node_found and mirrored_node_id is not None:
                    part_actions.nodes_to_add_symmetrically[node_id] = (mirrored_node_id, pos.to_tuple())
                else:
                    part_actions.nodes_to_add[node_id] = pos.to_tuple() # Store tuple
                # <<< END MODIFIED >>>

                rounded_pos = tuple(round(coord, 6) for coord in pos)
                processed_new_node_positions.add(rounded_pos)
                init_node_data_placeholder = {
                    'posNoOffset': pos.to_tuple(), 'pos': pos.to_tuple(), Metadata: Metadata() # Use Metadata class as key
                }
                new_pos_tup_for_sjson = undo_node_move_offset_and_apply_translation_to_expr(init_node_data_placeholder, pos)
                blender_nodes[init_node_id] = {'curr_node_id': node_id, 'pos': new_pos_tup_for_sjson, 'partOrigin': node_part_origin}
                continue # Go to next vertex
        # --- End TEMP node handling ---

        # --- Handle existing nodes ---
        init_node_data = init_nodes_data.get(init_node_id)
        if init_node_data is None:
            # This node wasn't in the original JBeam data.
            # It could be a newly added node (TEMP_ handled above),
            # OR it could be a node kept after a cancelled deletion confirmation.

            part_actions: PartNodesActions = parts_actions.setdefault(node_part_origin, PartNodesActions())

            # <<< *** NEW LOGIC for init_node_data is None *** >>>
            # Perform mirror check based on current position to see if it *should* be symmetrical
            # This covers the case where a node was kept after cancellation.
            # We need to re-do the mirror check logic here, similar to TEMP_ nodes.

            # Assume it's not mirrored initially
            is_potentially_symmetrical = False
            mirrored_node_id_for_kept_node = None

            # <<< ADDED: Check if prefixing is enabled >>>
            use_prefixes_for_kept = ui_props.use_node_naming_prefixes
            # <<< END ADDED >>>

            # <<< MODIFIED: Only perform mirror check if prefixes are enabled >>>
            if use_prefixes_for_kept:
                for other_node_id, other_node_data in init_nodes_data.items():
                    # Skip if the other node is marked for deletion in this cycle
                    is_marked_for_delete = False
                    for actions in parts_actions.values():
                        if other_node_id in actions.nodes_to_delete:
                            is_marked_for_delete = True; break
                    if is_marked_for_delete: continue

                    other_init_pos = other_node_data.get('pos')
                    if other_init_pos and len(other_init_pos) == 3:
                        if (abs(pos.y - other_init_pos[1]) < MIRROR_CHECK_TOLERANCE and
                            abs(pos.z - other_init_pos[2]) < MIRROR_CHECK_TOLERANCE and
                            abs(pos.x + other_init_pos[0]) < MIRROR_CHECK_TOLERANCE):
                            is_potentially_symmetrical = True
                            mirrored_node_id_for_kept_node = other_node_id
                            break
            # <<< END MODIFIED >>>

            if is_potentially_symmetrical and mirrored_node_id_for_kept_node is not None:
                 # If it finds a mirror now, add it to the symmetrical list
                 # Use the current node_id (which might be the UUID from the previous cycle)
                 part_actions.nodes_to_add_symmetrically[node_id] = (mirrored_node_id_for_kept_node, pos.to_tuple())
            else:
                 # Otherwise, add to the normal list (original behavior for truly new/non-mirrored nodes)
                 part_actions.nodes_to_add[node_id] = pos.to_tuple() # Store tuple

            # Calculate position considering expressions for SJSON update (remains the same)
            init_node_data_placeholder = {
                'posNoOffset': pos.to_tuple(), 'pos': pos.to_tuple(), Metadata: Metadata()
            }
            new_pos_tup_for_sjson = undo_node_move_offset_and_apply_translation_to_expr(init_node_data_placeholder, pos)
            # Use init_node_id (UUID or original if somehow not TEMP) as the key here,
            # as that's what the export process expects for blender_nodes mapping.
            blender_nodes[init_node_id] = {'curr_node_id': node_id, 'pos': new_pos_tup_for_sjson, 'partOrigin': node_part_origin}
            # <<< *** END NEW LOGIC *** >>>
            continue # Go to next vertex

        # Check for movement
        init_pos = init_node_data['pos']
        if abs(pos.x - init_pos[0]) > 0.000001 or abs(pos.y - init_pos[1]) > 0.000001 or abs(pos.z - init_pos[2]) > 0.000001:
            part_actions: PartNodesActions = parts_actions.setdefault(node_part_origin, PartNodesActions())
            part_actions.nodes_to_move[node_id] = pos.to_tuple() # Store tuple

        # Calculate position considering expressions (for SJSON data update)
        new_pos_tup_for_sjson = undo_node_move_offset_and_apply_translation_to_expr(init_node_data, pos)

        # Check for rename
        if init_node_id != node_id:
            affected_part = True if affect_node_references else node_part_origin
            part_actions: PartNodesActions = parts_actions.setdefault(affected_part, PartNodesActions())
            part_actions.nodes_to_rename[init_node_id] = node_id

        blender_nodes[init_node_id] = {'curr_node_id': node_id, 'pos': new_pos_tup_for_sjson, 'partOrigin': node_part_origin}
        # --- End existing node handling ---

    # --- Invoke confirmation operator if needed ---
    if nodes_requiring_confirmation and not jb_globals.confirm_delete_pending:
        try:
            jb_globals.confirm_delete_pending = True
            # Pass node ID list (including existing_collided_id) to operator
            # The list now contains (node_id, display_name, position, existing_collided_id)
            nodes_json = json.dumps(nodes_requiring_confirmation)
            bpy.ops.jbeam_editor.confirm_node_deletion('INVOKE_DEFAULT', nodes_data=nodes_json)
        except Exception as e:
            print(f"Error invoking node deletion confirmation: {e}", file=sys.stderr)
            traceback.print_exc()
            jb_globals.confirm_delete_pending = False

    # --- Get nodes to delete (based on initial data vs remaining blender nodes) ---
    for init_node_id, init_node_data in init_nodes_data.items():
        if init_node_id not in blender_nodes:
            node_part_origin = init_node_data.get('partOrigin', jbeam_part)
            affected_part = True if affect_node_references else node_part_origin
            part_actions: PartNodesActions = parts_actions.setdefault(affected_part, PartNodesActions())
            part_actions.nodes_to_delete.add(init_node_id)

    return blender_nodes, parts_actions
# <<< END REPLACED FUNCTION >>>


def get_beams_add_remove(obj: bpy.types.Object, bm: bmesh.types.BMesh, init_beams_data: list, jbeam_file_data_modified: dict, jbeam_part: str, nodes_to_delete: set, affect_node_references: bool):
    beams_to_add_tuples = set() # Store (id1, id2) tuples for adding
    beams_to_delete = set()

    # <<< Get node layers needed to check if vertices are valid JBeam nodes >>>
    init_node_id_layer = bm.verts.layers.string.get(constants.VL_INIT_NODE_ID)
    node_id_layer = bm.verts.layers.string.get(constants.VL_NODE_ID)
    node_is_fake_layer = bm.verts.layers.int.get(constants.VL_NODE_IS_FAKE)
    node_part_origin_layer = bm.verts.layers.string.get(constants.VL_NODE_PART_ORIGIN) # To assign origin

    beam_indices_layer = bm.edges.layers.string.get(constants.EL_BEAM_INDICES)
    # <<< Get beam origin layer >>>
    beam_part_origin_layer = bm.edges.layers.string.get(constants.EL_BEAM_PART_ORIGIN)

    # <<< Check if all required layers exist >>>
    if not all([init_node_id_layer, node_id_layer, node_is_fake_layer, node_part_origin_layer, beam_indices_layer, beam_part_origin_layer]):
        print("Error: Required BMesh layers for beam processing are missing.", file=sys.stderr)
        return beams_to_add_tuples, beams_to_delete # Return empty sets on error

    blender_beams_indices = set() # Store indices from existing beams in Blender

    # --- Build a map of beams from the initial data for quick lookup ---
    # Key: tuple(sorted(id1, id2)), Value: set of original indices within the part
    init_beams_map = {}
    temp_beam_idx_in_part = 0
    if isinstance(init_beams_data, list):
        for beam_entry in init_beams_data:
            if isinstance(beam_entry, dict) and beam_entry.get('partOrigin') == jbeam_part:
                temp_beam_idx_in_part += 1
                id1 = beam_entry.get('id1:')
                id2 = beam_entry.get('id2:')
                if id1 and id2:
                    key = tuple(sorted((id1, id2)))
                    if key not in init_beams_map:
                        init_beams_map[key] = set()
                    init_beams_map[key].add(temp_beam_idx_in_part)
    # --- End initial beam map ---

    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table() # <<< Ensure vertex table is ready >>>
    e: bmesh.types.BMEdge
    for i, e in enumerate(bm.edges):
        beam_indices_str = e[beam_indices_layer].decode('utf-8')

        v1, v2 = e.verts[0], e.verts[1]

        # --- Robust Node Validity Check ---
        # Check if vertices are non-fake and have received final (non-TEMP) IDs
        v1_node_id_bytes = v1[node_id_layer]
        v2_node_id_bytes = v2[node_id_layer]
        v1_node_id = v1_node_id_bytes.decode('utf-8')
        v2_node_id = v2_node_id_bytes.decode('utf-8')

        v1_is_valid_jbeam = (v1[node_is_fake_layer] == 0 and
                             v1_node_id_bytes and not v1_node_id.startswith('TEMP_'))
        v2_is_valid_jbeam = (v2[node_is_fake_layer] == 0 and
                             v2_node_id_bytes and not v2_node_id.startswith('TEMP_'))

        if not v1_is_valid_jbeam or not v2_is_valid_jbeam:
            # print(f"Debug: Skipping edge {i} - Invalid nodes: v1={v1_is_valid_jbeam} ('{v1_node_id}'), v2={v2_is_valid_jbeam} ('{v2_node_id}')")
            continue # Skip edges not connecting two valid, finalized JBeam nodes
        # --- End Node Validity Check ---

        # --- Apply Node Remapping ---
        # Check if either node ID needs remapping based on the global map
        # This happens *after* getting the IDs from the vertex layers but *before* creating the tuple
        final_v1_id = jb_globals.node_overlap_remap.get(v1_node_id, v1_node_id)
        final_v2_id = jb_globals.node_overlap_remap.get(v2_node_id, v2_node_id)

        # Prevent adding beams between the *same* node after remapping
        if final_v1_id == final_v2_id:
            # print(f"Debug: Skipping beam between same node after remapping: {final_v1_id}")
            continue
        # --- End Node Remapping ---

        current_beam_tuple = tuple(sorted((final_v1_id, final_v2_id))) # <<< Use remapped IDs

        if beam_indices_str == '-1': # Standard case: Explicitly marked as new
            beams_to_add_tuples.add(current_beam_tuple)
            continue # Don't add to blender_beams_indices

        elif beam_indices_str == '': # Case: Edge exists but has no JBeam data (e.g., Symmetrize didn't copy/set layer)
            # Treat as new IF nodes are valid (already checked above)
            beams_to_add_tuples.add(current_beam_tuple)
            # Mark it so it's handled correctly if export runs again
            try:
                e[beam_indices_layer] = b'-1'
                e[beam_part_origin_layer] = bytes(jbeam_part, 'utf-8')
            except Exception as layer_err:
                print(f"Warning: Could not update layers for newly detected beam (empty index): {layer_err}", file=sys.stderr)
            continue # Don't add to blender_beams_indices

        else: # Case: Edge has existing JBeam indices (e.g., original beam or Symmetrize copied indices)
            try:
                indices_in_part = set()
                valid_indices_found = False
                for idx_str in beam_indices_str.split(','):
                    idx = int(idx_str)
                    if idx > 0: # Only consider valid positive indices
                        indices_in_part.add(idx)
                        valid_indices_found = True

                if not valid_indices_found:
                    # If indices were like "0" or otherwise invalid (e.g., non-integer),
                    # treat it like the '' case if nodes are valid.
                    print(f"Warning: Edge {i} had non-empty but invalid indices '{beam_indices_str}'. Treating as new beam {current_beam_tuple}.")
                    beams_to_add_tuples.add(current_beam_tuple)
                    try:
                        e[beam_indices_layer] = b'-1' # Mark as new
                        e[beam_part_origin_layer] = bytes(jbeam_part, 'utf-8')
                    except Exception as layer_err:
                        print(f"Warning: Could not update layers for beam with invalid indices: {layer_err}", file=sys.stderr)
                    continue # Don't add to blender_beams_indices

                # Check if this beam (defined by current node IDs) exists in the initial data
                if current_beam_tuple not in init_beams_map:
                    # Beam exists in Blender with valid indices, but doesn't match any beam in the original data using CURRENT node IDs.
                    # This is likely a new beam (e.g., from Symmetrize which copied indices, or nodes were renamed). Treat as new.
                    beams_to_add_tuples.add(current_beam_tuple)
                    # Mark it as new for future runs
                    try:
                        e[beam_indices_layer] = b'-1'
                        e[beam_part_origin_layer] = bytes(jbeam_part, 'utf-8')
                    except Exception as layer_err:
                        print(f"Warning: Could not update layers for newly detected beam (copied index): {layer_err}", file=sys.stderr)
                    # Don't add its indices to blender_beams_indices, it's being added.
                else:
                    # Beam exists in Blender AND matches an initial beam. Add its indices for deletion check.
                    blender_beams_indices.update(indices_in_part)

            except ValueError:
                print(f"Warning: Invalid beam index found: '{beam_indices_str}' on edge {i}. Skipping.", file=sys.stderr)
                # Treat as new if nodes are valid
                print(f"Info: Treating edge {i} with invalid indices '{beam_indices_str}' as new beam {current_beam_tuple}.")
                beams_to_add_tuples.add(current_beam_tuple)
                try:
                    e[beam_indices_layer] = b'-1' # Mark as new
                    e[beam_part_origin_layer] = bytes(jbeam_part, 'utf-8')
                except Exception as layer_err:
                    print(f"Warning: Could not update layers for beam with invalid indices: {layer_err}", file=sys.stderr)
                continue # Don't add to blender_beams_indices

    # --- Deletion Logic (largely the same, but uses blender_beams_indices) ---
    beams_to_delete_indices = set()
    beam_idx_in_part = 0

    # Ensure init_beams_data is a list
    if not isinstance(init_beams_data, list):
        print("Warning: Initial beams data is not a list. Cannot process deletions.", file=sys.stderr)
        init_beams_data = [] # Treat as empty list

    for i, beam in enumerate(init_beams_data):
        # Check if beam is a dictionary and has partOrigin
        if isinstance(beam, dict):
            # Only consider beams originating from the current part being processed
            if beam.get('partOrigin') != jbeam_part:
                continue

            beam_idx_in_part += 1 # Increment index *only* for beams belonging to this part

            # Check if beam is virtual or if nodes are marked for deletion
            if '__virtual' not in beam:
                id1 = beam.get('id1:')
                id2 = beam.get('id2:')
                # Ensure node IDs exist before checking deletion set
                if id1 is None or id2 is None:
                    print(f"Warning: Beam at index {i} (part index {beam_idx_in_part}) is missing node IDs. Skipping deletion check.", file=sys.stderr)
                    continue

                delete_nodes = (id1 in nodes_to_delete, id2 in nodes_to_delete)

                # Condition 1: Delete if affecting references and any node is deleted
                # Condition 2: Delete if not affecting references OR no nodes deleted, AND the beam index is NOT found in Blender
                # Check using the populated blender_beams_indices set
                if (affect_node_references and any(delete_nodes)) or \
                   (not any(delete_nodes) and beam_idx_in_part not in blender_beams_indices):
                    beams_to_delete_indices.add(beam_idx_in_part) # Add the index within the part
        else:
            # Handle cases where beam entry is not a dictionary (e.g., just the header)
            # Don't increment beam_idx_in_part for non-dict entries
            pass

    # Assign final sets
    beams_to_add = beams_to_add_tuples
    beams_to_delete = beams_to_delete_indices

    # <<< Keep the check for beams added due to triangles >>>
    # This might need adjustment if the triangle logic changes, but keep for now.
    # if 'triangles' in jbeam_file_data_modified.get(jbeam_part, {}):
    #     tris_to_add_nodes = set()
    #     # This part is tricky as tris_to_add isn't directly available here.
    #     # We might need to re-evaluate this filtering step or pass tris_to_add.
    #     # For now, let's comment it out as it might be incorrect in this context.
    #     # for tri in tris_to_add: # Assuming tris_to_add is available somehow
    #     #     tris_to_add_nodes.add(tuple(sorted(tri)))
    #     #
    #     # beams_to_add_copy = beams_to_add.copy()
    #     # for beam_nodes in beams_to_add_copy:
    #     #     # Check if this beam is part of any triangle being added
    #     #     for tri_nodes in tris_to_add_nodes:
    #     #         if set(beam_nodes).issubset(tri_nodes):
    #     #             if beam_nodes in beams_to_add:
    #     #                 beams_to_add.remove(beam_nodes)
    #     #             break # Move to next beam once removed
    #     pass

    return beams_to_add, beams_to_delete


def get_faces_add_remove(obj: bpy.types.Object, bm: bmesh.types.BMesh, init_tris_data: list, init_quads_data: list, jbeam_file_data_modified: dict, jbeam_part: str, nodes_to_delete: set, affect_node_references: bool):
    tris_to_add, tris_to_delete, tris_flipped = set(), set(), set()
    quads_to_add, quads_to_delete, quads_flipped = set(), set(), set()

    init_node_id_layer = bm.verts.layers.string[constants.VL_INIT_NODE_ID]
    node_id_layer = bm.verts.layers.string[constants.VL_NODE_ID] # <<< Get current node ID layer
    face_idx_layer = bm.faces.layers.int[constants.FL_FACE_IDX]
    face_flip_flag_layer = bm.faces.layers.int[constants.FL_FACE_FLIP_FLAG]

    blender_tris = set()
    blender_quads = set()
    # Create dictionary where key is init node id and value is current blender node id and position
    bm.faces.ensure_lookup_table()
    f: bmesh.types.BMFace
    for i, f in enumerate(bm.faces):
        num_verts = len(f.verts)
        if num_verts == 3:
            tri_idx = f[face_idx_layer]

            if tri_idx == 0: # Triangle doesn't exist in JBeam data
                continue
            if tri_idx == -1: # Newly added triangle
                v1, v2, v3 = f.verts[0], f.verts[1], f.verts[2]
                # <<< Use current node ID layer for newly added faces >>>
                v1_node_id = v1[node_id_layer].decode('utf-8')
                v2_node_id = v2[node_id_layer].decode('utf-8')
                v3_node_id = v3[node_id_layer].decode('utf-8')
                tri_tup = (v1_node_id, v2_node_id, v3_node_id)
                tris_to_add.add(tri_tup)
                continue

            # Flip face if "face flip" flag set!
            if f[face_flip_flag_layer] == 1:
                tris_jbeam_data = jbeam_file_data_modified[jbeam_part]['triangles']
                j = 0
                for tri_jbeam_data in tris_jbeam_data:
                    if isinstance(tri_jbeam_data, list):
                        if j == tri_idx:
                            tri_jbeam_data[1], tri_jbeam_data[2] = tri_jbeam_data[2], tri_jbeam_data[1]
                            tris_flipped.add(tri_idx)
                            break
                        j += 1

            blender_tris.add(tri_idx)

        elif num_verts == 4:
            quad_idx = f[face_idx_layer]

            if quad_idx == 0: # Quad doesn't exist in JBeam data
                continue
            if quad_idx == -1: # Newly added quad
                v1, v2, v3, v4 = f.verts[0], f.verts[1], f.verts[2], f.verts[3]
                # <<< Use current node ID layer for newly added faces >>>
                v1_node_id = v1[node_id_layer].decode('utf-8')
                v2_node_id = v2[node_id_layer].decode('utf-8')
                v3_node_id = v3[node_id_layer].decode('utf-8')
                v4_node_id = v4[node_id_layer].decode('utf-8')
                quad_tup = (v1_node_id, v2_node_id, v3_node_id, v4_node_id)
                quads_to_add.add(quad_tup)
                continue

            # Flip face if "face flip" flag set!
            if f[face_flip_flag_layer] == 1:
                quads_jbeam_data = jbeam_file_data_modified[jbeam_part]['quads']
                j = 0
                for quad_jbeam_data in quads_jbeam_data:
                    if isinstance(quad_jbeam_data, list):
                        if j == quad_idx:
                            quad_jbeam_data[1], quad_jbeam_data[3] = quad_jbeam_data[3], quad_jbeam_data[1]
                            quads_flipped.add(quad_idx)
                            break
                        j += 1

            blender_quads.add(quad_idx)

        else:
            print("Warning! Won't export face with 5 or more vertices!", file=sys.stderr)

    # Get tris and quads to delete
    tri_idx_in_part, quad_idx_in_part = 1, 1

    for i, tri in enumerate(init_tris_data, 1):
        if 'partOrigin' in tri and tri['partOrigin'] != jbeam_part:
            continue
        if '__virtual' not in tri:
            delete_nodes = (tri['id1:'] in nodes_to_delete, tri['id2:'] in nodes_to_delete, tri['id3:'] in nodes_to_delete)
            if (any(delete_nodes) and affect_node_references) or (not any(delete_nodes) and tri_idx_in_part not in blender_tris):
                tris_to_delete.add(tri_idx_in_part)
        tri_idx_in_part += 1

    for i, quad in enumerate(init_quads_data, 1):
        if 'partOrigin' in quad and quad['partOrigin'] != jbeam_part:
            continue
        if '__virtual' not in quad:
            delete_nodes = (quad['id1:'] in nodes_to_delete, quad['id2:'] in nodes_to_delete, quad['id3:'] in nodes_to_delete, quad['id4:'] in nodes_to_delete)
            if (any(delete_nodes) and affect_node_references) or (not any(delete_nodes) and quad_idx_in_part not in blender_quads):
                quads_to_delete.add(quad_idx_in_part)
        quad_idx_in_part += 1

    return tris_to_add, tris_to_delete, tris_flipped, quads_to_add, quads_to_delete, quads_flipped


def add_jbeam_section(ast_nodes: list, jbeam_section_end_node_idx: int):
    i = jbeam_section_end_node_idx + 1

    node_after_last_section = ast_nodes[i]
    node_2_after_last_section = None

    if node_after_last_section.data_type == 'wsc':
        # Split WSC node into one node for inline WSCS node entry and second node after newline character
        wscs = node_after_last_section.value
        nl_found = False

        for k, char in enumerate(wscs):
            if char == '\n':
                nl_found = True
                break

        node_after_last_section.value = wscs[:k]
        node_2_after_last_section = ASTNode('wsc', wscs[k:]) if nl_found else None
    else:
        node_after_last_section = ASTNode('wsc', '')
        ast_nodes.insert(i, node_after_last_section)

    i += 1

    if node_after_last_section:
        node_after_last_section.value += NL_INDENT
        node_after_last_section = None
    else:
        ast_nodes.insert(i + 0, ASTNode('wsc', NL_INDENT))
        i += 1

    return i, node_2_after_last_section


# Adds a JBeam nodes section to the JBeam part (this is called on JBeam part end character)
def add_nodes_section(ast_nodes: list, jbeam_section_end_node_idx: int):
    i, node_2_after_last_section = add_jbeam_section(ast_nodes, jbeam_section_end_node_idx)

    # "nodes":[
    #     ["id", "posX", "posY", "posZ"],
    # ],
    ast_nodes.insert(i + 0, ASTNode('"', 'nodes'))
    ast_nodes.insert(i + 1, ASTNode(':'))
    ast_nodes.insert(i + 2, ASTNode('['))
    jbeam_section_start_node_idx = i + 2
    ast_nodes.insert(i + 3, ASTNode('wsc', NL_TWO_INDENT))
    i += 4
    ast_nodes.insert(i + 0, ASTNode('['))
    ast_nodes.insert(i + 1, ASTNode('"', 'id'))
    ast_nodes.insert(i + 2, ASTNode('wsc', ', '))
    ast_nodes.insert(i + 3, ASTNode('"', 'posX'))
    ast_nodes.insert(i + 4, ASTNode('wsc', ', '))
    ast_nodes.insert(i + 5, ASTNode('"', 'posY'))
    ast_nodes.insert(i + 6, ASTNode('wsc', ', '))
    ast_nodes.insert(i + 7, ASTNode('"', 'posZ'))
    ast_nodes.insert(i + 8, ASTNode(']'))
    ast_nodes.insert(i + 9, ASTNode('wsc', ',' + NL_INDENT))
    i += 10
    ast_nodes.insert(i + 0, ASTNode(']'))
    jbeam_section_end_node_idx = i + 0
    ast_nodes.insert(i + 1, ASTNode('wsc', ','))
    i += 2

    # Add modified original last WSCS back to end of section
    if node_2_after_last_section:
        ast_nodes[i - 1].value += node_2_after_last_section.value

    return i, jbeam_section_start_node_idx, jbeam_section_end_node_idx


# Adds a JBeam beams section to the JBeam part (this is called on JBeam part end character)
def add_beams_section(ast_nodes: list, jbeam_section_end_node_idx: int):
    i, node_2_after_last_section = add_jbeam_section(ast_nodes, jbeam_section_end_node_idx)

    # "beams":[
    #     ["id1:","id2:"],
    # ],
    ast_nodes.insert(i + 0, ASTNode('"', 'beams'))
    ast_nodes.insert(i + 1, ASTNode(':'))
    ast_nodes.insert(i + 2, ASTNode('['))
    jbeam_section_start_node_idx = i + 2
    ast_nodes.insert(i + 3, ASTNode('wsc', NL_TWO_INDENT))
    i += 4
    ast_nodes.insert(i + 0, ASTNode('['))
    ast_nodes.insert(i + 1, ASTNode('"', 'id1:'))
    ast_nodes.insert(i + 2, ASTNode('wsc', ','))
    ast_nodes.insert(i + 3, ASTNode('"', 'id2:'))
    ast_nodes.insert(i + 4, ASTNode(']'))
    ast_nodes.insert(i + 5, ASTNode('wsc', ',' + NL_INDENT))
    i += 6
    ast_nodes.insert(i + 0, ASTNode(']'))
    jbeam_section_end_node_idx = i + 0
    ast_nodes.insert(i + 1, ASTNode('wsc', ','))
    i += 2

    # Add modified original last WSCS back to end of section
    if node_2_after_last_section:
        ast_nodes[i - 1].value += node_2_after_last_section.value

    return i, jbeam_section_start_node_idx, jbeam_section_end_node_idx


# Adds a JBeam triangles section to the JBeam part (this is called on JBeam part end character)
def add_triangles_section(ast_nodes: list, jbeam_section_end_node_idx: int):
    i, node_2_after_last_section = add_jbeam_section(ast_nodes, jbeam_section_end_node_idx)

    # "triangles":[
    #     ["id1:","id2:","id3:"],
    # ],
    ast_nodes.insert(i + 0, ASTNode('"', 'triangles'))
    ast_nodes.insert(i + 1, ASTNode(':'))
    ast_nodes.insert(i + 2, ASTNode('['))
    jbeam_section_start_node_idx = i + 2
    ast_nodes.insert(i + 3, ASTNode('wsc', NL_TWO_INDENT))
    i += 4
    ast_nodes.insert(i + 0, ASTNode('['))
    ast_nodes.insert(i + 1, ASTNode('"', 'id1:'))
    ast_nodes.insert(i + 2, ASTNode('wsc', ','))
    ast_nodes.insert(i + 3, ASTNode('"', 'id2:'))
    ast_nodes.insert(i + 4, ASTNode('wsc', ','))
    ast_nodes.insert(i + 5, ASTNode('"', 'id3:'))
    ast_nodes.insert(i + 6, ASTNode(']'))
    ast_nodes.insert(i + 7, ASTNode('wsc', ',' + NL_INDENT))
    i += 8
    ast_nodes.insert(i + 0, ASTNode(']'))
    jbeam_section_end_node_idx = i + 0
    ast_nodes.insert(i + 1, ASTNode('wsc', ','))
    i += 2

    # Add modified original last WSCS back to end of section
    if node_2_after_last_section:
        ast_nodes[i - 1].value += node_2_after_last_section.value

    return i, jbeam_section_start_node_idx, jbeam_section_end_node_idx


# Adds a JBeam quads section to the JBeam part (this is called on JBeam part end character)
def add_quads_section(ast_nodes: list, jbeam_section_end_node_idx: int):
    i, node_2_after_last_section = add_jbeam_section(ast_nodes, jbeam_section_end_node_idx)

    # "quads":[
    #     ["id1:","id2:","id3:","id4:"],
    # ],
    ast_nodes.insert(i + 0, ASTNode('"', 'quads'))
    ast_nodes.insert(i + 1, ASTNode(':'))
    ast_nodes.insert(i + 2, ASTNode('['))
    jbeam_section_start_node_idx = i + 2
    ast_nodes.insert(i + 3, ASTNode('wsc', NL_TWO_INDENT))
    i += 4
    ast_nodes.insert(i + 0, ASTNode('['))
    ast_nodes.insert(i + 1, ASTNode('"', 'id1:'))
    ast_nodes.insert(i + 2, ASTNode('wsc', ','))
    ast_nodes.insert(i + 3, ASTNode('"', 'id2:'))
    ast_nodes.insert(i + 4, ASTNode('wsc', ','))
    ast_nodes.insert(i + 5, ASTNode('"', 'id3:'))
    ast_nodes.insert(i + 6, ASTNode('wsc', ','))
    ast_nodes.insert(i + 7, ASTNode('"', 'id4:'))
    ast_nodes.insert(i + 8, ASTNode(']'))
    ast_nodes.insert(i + 9, ASTNode('wsc', ',' + NL_INDENT))
    i += 10
    ast_nodes.insert(i + 0, ASTNode(']'))
    jbeam_section_end_node_idx = i + 0
    ast_nodes.insert(i + 1, ASTNode('wsc', ','))
    i += 2

    # Add modified original last WSCS back to end of section
    if node_2_after_last_section:
        ast_nodes[i - 1].value += node_2_after_last_section.value

    #print_ast_nodes(ast_nodes, i, 50, True)

    return i, jbeam_section_start_node_idx, jbeam_section_end_node_idx


def comment_out_duplicate_key(ast_nodes: list, keys_visited, stack: list, curr_key: str):
    key_exists = True
    data = keys_visited[1]

    for stack_entry in stack:
        key = stack_entry[0]
        key_entry = data.get(key)
        if key_entry is None:
            key_exists = False
            break
        data = data[key][1]

    if not key_exists:
        return
    key_entry = data.pop(curr_key, None)
    if key_entry is None:
        return

    start_node_idx, end_node_idx = key_entry[0]
    if constants.DEBUG:
        print('Duplicate key!!!', [*(x[0] for x in stack), curr_key], file=sys.stderr)

    before_start_node = ast_nodes[start_node_idx - 1]
    if before_start_node.data_type == 'wsc':
        before_start_node.value += '/*'
    else:
        ast_nodes.insert(start_node_idx, ASTNode('wsc', '/*'))
        end_node_idx += 1

    after_end_node = ast_nodes[end_node_idx + 1]
    if after_end_node.data_type == 'wsc':
        after_end_node.value = '*/' + after_end_node.value
    else:
        ast_nodes.insert(end_node_idx + 1, ASTNode('wsc', '*/'))


def set_key_visited(ast_nodes: list, keys_visited, stack: list, curr_key: str, new_start_node_idx: int, new_end_node_idx: int):
    data = keys_visited[1]
    for stack_entry in stack:
        data = data.setdefault(stack_entry[0], [(None, None), {}])[1]

    if curr_key not in data:
        data[curr_key] = ((new_start_node_idx, new_end_node_idx), None)
    else:
        data[curr_key][0] = (new_start_node_idx, new_end_node_idx)

    #data[0] = (new_start_node_idx, new_end_node_idx)
    #data[curr_key][0] = (new_start_node_idx, new_end_node_idx)


def update_ast_nodes(ast_nodes: list, current_jbeam_file_data: dict, current_jbeam_file_data_modified: dict, jbeam_part: str, affect_node_references: bool,
                     nodes_to_add: dict, nodes_to_delete: set, nodes_to_add_symmetrically: dict, # <<< ADDED nodes_to_add_symmetrically
                     beams_to_add: set, beams_to_delete: set,
                     tris_to_add: set, tris_to_delete: set,
                     quads_to_add: set, quads_to_delete: set):
    # Traverse AST nodes and update them from SJSON data, add and delete jbeam definitions

    stack = []
    stack_append = stack.append
    stack_pop = stack.pop
    in_dict = True
    pos_in_arr = 0
    temp_dict_key = None
    dict_key = None

    temp_key_val_start_node_idx = None
    key_val_start_node_idx_stack = []
    keys_visited = ((None, None), {})

    jbeam_section_header = []
    jbeam_section_header_lookup = {}
    jbeam_section_def = []
    jbeam_section_row_def_idx = -1
    jbeam_entry_start_node_idx, jbeam_entry_end_node_idx = None, None
    jbeam_section_start_node_idx, jbeam_section_end_node_idx = None, None
    jbeam_part_start_node_idx, jbeam_part_end_node_idx = None, None

    add_nodes_flag = len(nodes_to_add) > 0
    add_beams_flag = len(beams_to_add) > 0
    add_tris_flag = len(tris_to_add) > 0
    add_quads_flag = len(quads_to_add) > 0

    # <<< Make a mutable copy for beams to add >>>
    beams_to_add_copy = beams_to_add.copy()

    # <<< Make a mutable copy for symmetrical nodes >>>
    nodes_to_add_symmetrically_copy = nodes_to_add_symmetrically.copy()

    i = 0
    while i < len(ast_nodes):
        node: ASTNode = ast_nodes[i]
        node_type = node.data_type
        if node_type in ('wsc', 'literal'):
            i += 1
            continue

        prev_stack_size = len(stack)
        prev_stack_head_key = stack[prev_stack_size - 1][0] if prev_stack_size > 0 else None
        prev_in_jbeam_part = prev_stack_size > 0 and stack[0][0] == jbeam_part

        if in_dict: # In dictionary object
            if node_type in ('{', '['): # Going down a level
                if dict_key is not None:
                    key_val_start_node_idx_stack.append(temp_key_val_start_node_idx)
                    stack_append((dict_key, in_dict))
                    in_dict = node_type == '{'
                else:
                    if len(stack) > 0: # Ignore outer most dictionary
                        print("{ or [ w/o key!", file=sys.stderr)

                pos_in_arr = 0
                temp_dict_key = None
                dict_key = None

            elif node_type in ('}', ']'): # Going up a level
                if prev_stack_size > 0:
                    prev_key, in_dict = stack_pop()
                else:
                    prev_key, in_dict = -1, None

                if in_dict:
                    if prev_key != -1:
                        set_key_visited(ast_nodes, keys_visited, stack, prev_key, key_val_start_node_idx_stack.pop(), i)
                else:
                    pos_in_arr = prev_key + 1

            else: # Defining key value pair
                if temp_dict_key is None:
                    if node_type == '"':
                        temp_key_val_start_node_idx = i
                        temp_dict_key = node.value
                        comment_out_duplicate_key(ast_nodes, keys_visited, stack, temp_dict_key)

                elif node_type == ':':
                    dict_key = temp_dict_key

                    if temp_dict_key is None:
                        print("key delimiter predecessor was not a key!", file=sys.stderr)

                elif dict_key is not None:
                    set_key_visited(ast_nodes, keys_visited, stack, dict_key, temp_key_val_start_node_idx, i)

                    # Ignore slots section and other parts
                    if not (prev_stack_size > 1 and stack[1][0] == 'slots') and not prev_in_jbeam_part:
                        try:
                            changed = compare_and_set_value(current_jbeam_file_data, current_jbeam_file_data_modified, stack, dict_key, node)
                            if constants.DEBUG:
                                if changed:
                                    print('value changed!', node.data_type, node.value)
                        except:
                            traceback.print_exc()
                            print_ast_nodes(ast_nodes, i, 75, True, sys.stderr)
                            #raise Exception('compare_and_set_value error!')

                    temp_dict_key = None
                    dict_key = None

        else: # In array object
            if node_type in ('{', '['): # Going down a level
                stack_append((pos_in_arr, in_dict))
                in_dict = node_type == '{'
                pos_in_arr = 0
                temp_dict_key = None
                dict_key = None

            elif node_type in ('}', ']'): # Going up a level
                if prev_stack_size > 0:
                    prev_key, in_dict = stack_pop()
                else:
                    prev_key, in_dict = -1, None

                if in_dict:
                    if prev_key != -1:
                        set_key_visited(ast_nodes, keys_visited, stack, prev_key, key_val_start_node_idx_stack.pop(), i)
                else:
                    pos_in_arr = prev_key + 1

            elif node_type not in ('}', ']'):
                # Ignore slots section
                if not (prev_stack_size > 1 and stack[1][0] == 'slots'):
                    # Value definition
                    try:
                        changed = compare_and_set_value(current_jbeam_file_data, current_jbeam_file_data_modified, stack, pos_in_arr, node)
                        if constants.DEBUG:
                            if changed:
                                print('value changed!', node.data_type, node.value)
                    except:
                        traceback.print_exc()
                        print_ast_nodes(ast_nodes, i, 75, True, sys.stderr)
                        #raise Exception('compare_and_set_value error!')

                pos_in_arr += 1

        # After traversal

        stack_size = len(stack)
        stack_size_diff = stack_size - prev_stack_size # 1 = go down level, -1 = go up level, 0 = no change
        stack_head = stack[-1] if stack_size > 0 else None
        in_jbeam_part = stack_size > 0 and stack[0][0] == jbeam_part

        # if constants.DEBUG:
        #     prev_node = ast_nodes[0]
        #     for j in range(1, len(ast_nodes)):
        #         curr_node = ast_nodes[j]
        #         if (curr_node.data_type == 'wsc' and prev_node.data_type == 'wsc'):
        #             print_ast_nodes(ast_nodes, j, 75, True, sys.stderr)
        #         prev_node = curr_node

        if stack_size_diff == 1: # Went down level { or [
            if in_jbeam_part:
                if stack_size == 1: # Start of JBeam part
                    jbeam_part_start_node_idx = i

                elif stack_size == 2: # Start of JBeam section (e.g. nodes, beams)
                    jbeam_section_start_node_idx = i

                elif stack_size == 3: # Start of JBeam entry
                    jbeam_entry_start_node_idx = i

                    if not in_dict:
                        jbeam_section_row_def_idx += 1

        elif stack_size_diff == -1: # Went up level } or ]
            if in_jbeam_part and stack_size == 2: # End of JBeam entry
                jbeam_entry_end_node_idx = i
                assert jbeam_section_start_node_idx < jbeam_entry_start_node_idx
                assert jbeam_entry_start_node_idx < jbeam_entry_end_node_idx

                jbeam_def_deleted = False

                if stack_head[0] == 'nodes':
                    # If current jbeam node is part of delete list, remove the node definition
                    if len(jbeam_section_def) > 0:
                        jbeam_node_id = jbeam_section_def[jbeam_section_header_lookup['id']]
                        if jbeam_node_id in nodes_to_delete:
                            # if constants.DEBUG:
                            #     print('Deleting node...')
                            #     print('-------------Before-------------')
                            #     print_ast_nodes(ast_nodes, i, 50, True, sys.stdout)
                            i = delete_jbeam_entry(ast_nodes, jbeam_section_start_node_idx, jbeam_entry_start_node_idx, jbeam_entry_end_node_idx)
                            # if constants.DEBUG:
                            #     print('\n-------------After-------------')
                            #     print_ast_nodes(ast_nodes, i, 50, True, sys.stdout)
                            jbeam_def_deleted = True
                        # <<< MODIFIED: Symmetrical Node Insertion Logic >>>
                        elif not jbeam_def_deleted and nodes_to_add_symmetrically_copy:
                            nodes_to_insert_here = []
                            # Find all symmetrical nodes that mirror the current node
                            for new_node_id, (mirrored_id, pos_tuple) in list(nodes_to_add_symmetrically_copy.items()): # Iterate over a copy
                                if mirrored_id == jbeam_node_id:
                                    nodes_to_insert_here.append((new_node_id, pos_tuple))
                                    del nodes_to_add_symmetrically_copy[new_node_id] # Remove from dict

                            if nodes_to_insert_here:
                                insert_idx = jbeam_entry_end_node_idx + 1
                                node_after_mirrored = ast_nodes[insert_idx] if insert_idx < len(ast_nodes) else None

                                # Defaults
                                comma_wsc_for_mirrored = ASTNode('wsc', ',')
                                # <<< MODIFIED: Get indentation from mirrored node >>>
                                mirrored_indentation = _get_indentation_from_previous_wsc(ast_nodes, jbeam_entry_start_node_idx)
                                trailing_wsc_after_insertion = ''

                                # Check the node immediately following the mirrored node's ']'
                                if node_after_mirrored and node_after_mirrored.data_type == 'wsc':
                                    wsc_value = node_after_mirrored.value
                                    nl_pos = wsc_value.find('\n')

                                    if nl_pos != -1: # Found a newline
                                        comma_wsc_for_mirrored.value = wsc_value[:nl_pos]
                                        trailing_wsc_after_insertion = wsc_value[nl_pos:]
                                    else: # No newline, just whitespace (e.g., ', ')
                                        comma_wsc_for_mirrored.value = wsc_value

                                    del ast_nodes[insert_idx]

                                # Insert the comma for the mirrored node
                                ast_nodes.insert(insert_idx, comma_wsc_for_mirrored)
                                insert_idx += 1

                                # --- Insert the new symmetrical nodes ---
                                for k, (new_node_id, node_pos) in enumerate(nodes_to_insert_here):
                                    # Insert the indentation
                                    # <<< MODIFIED: Use extracted indentation >>>
                                    ast_nodes.insert(insert_idx, ASTNode('wsc', mirrored_indentation))
                                    insert_idx += 1

                                    # --- Copy and Modify Mirrored Node Entry ---
                                    num_node_idx = 0 # Track which position number we are on
                                    id_node_found = False
                                    # Iterate through the original mirrored node's AST nodes
                                    for node_idx in range(jbeam_entry_start_node_idx, jbeam_entry_end_node_idx + 1):
                                        original_node = ast_nodes[node_idx]
                                        # Create a copy of the node
                                        copied_node = ASTNode(original_node.data_type, original_node.value,
                                                              precision=original_node.precision,
                                                              prefix_plus=original_node.prefix_plus,
                                                              add_post_fix_dot=original_node.add_post_fix_dot)

                                        # Modify the ID node
                                        if copied_node.data_type == '"' and not id_node_found:
                                            copied_node.value = new_node_id
                                            id_node_found = True

                                        # Modify the position number nodes
                                        elif copied_node.data_type == 'number':
                                            if num_node_idx < 3: # Only modify the first 3 numbers (X, Y, Z)
                                                copied_node.value = node_pos[num_node_idx]
                                                copied_node.precision = get_float_precision(node_pos[num_node_idx])
                                                # Reset flags that might not apply to the new number
                                                copied_node.prefix_plus = False
                                                copied_node.add_post_fix_dot = False
                                            num_node_idx += 1

                                        # Insert the copied/modified node
                                        ast_nodes.insert(insert_idx, copied_node)
                                        insert_idx += 1
                                    # --- End Copy and Modify ---

                                    # Insert the comma for the newly added node
                                    ast_nodes.insert(insert_idx, ASTNode('wsc', ','))
                                    insert_idx += 1
                                # --- End node insertion loop ---

                                # Insert the original trailing whitespace
                                if trailing_wsc_after_insertion:
                                    if insert_idx > 0 and ast_nodes[insert_idx - 1].data_type == 'wsc' and ast_nodes[insert_idx - 1].value == ',':
                                        ast_nodes[insert_idx - 1].value += trailing_wsc_after_insertion
                                    else:
                                        ast_nodes.insert(insert_idx, ASTNode('wsc', trailing_wsc_after_insertion))
                                        insert_idx += 1

                                i = insert_idx - 1 # Adjust the main loop index
                        # <<< END MODIFIED Symmetrical Node Insertion Logic >>>

                elif stack_head[0] == 'beams':
                    # If current jbeam beam is part of delete list, remove the beam definition
                    if len(jbeam_section_def) > 0:
                        if jbeam_section_row_def_idx in beams_to_delete:
                            i = delete_jbeam_entry(ast_nodes, jbeam_section_start_node_idx, jbeam_entry_start_node_idx, jbeam_entry_end_node_idx)
                            jbeam_def_deleted = True

                        # <<< MODIFIED: Symmetrical Beam Insertion Logic >>>
                        elif not jbeam_def_deleted and beams_to_add_copy:
                            # Get the node IDs of the current beam being processed in the AST
                            current_beam_id1 = jbeam_section_def[jbeam_section_header_lookup['id1:']]
                            current_beam_id2 = jbeam_section_def[jbeam_section_header_lookup['id2:']]

                            # Find the symmetrical counterparts of the current beam's nodes
                            ui_props = bpy.context.scene.ui_properties # Get UI props
                            sym_id1 = get_symmetrical_node_id(current_beam_id1, ui_props)
                            sym_id2 = get_symmetrical_node_id(current_beam_id2, ui_props)

                            beam_to_insert = None
                            # <<< MODIFIED: Handle cases with one center node >>>
                            if sym_id1 and sym_id2: # Case 1: Both nodes have distinct mirrors
                                potential_sym_beam = tuple(sorted((sym_id1, sym_id2)))
                                if potential_sym_beam in beams_to_add_copy:
                                    beam_to_insert = potential_sym_beam
                            elif sym_id1 is None and sym_id2 is not None: # Case 2: Node 1 is center, Node 2 has mirror
                                potential_sym_beam = tuple(sorted((current_beam_id1, sym_id2))) # Use original center node ID
                                if potential_sym_beam in beams_to_add_copy:
                                    beam_to_insert = potential_sym_beam
                            elif sym_id1 is not None and sym_id2 is None: # Case 3: Node 1 has mirror, Node 2 is center
                                potential_sym_beam = tuple(sorted((sym_id1, current_beam_id2))) # Use original center node ID
                                if potential_sym_beam in beams_to_add_copy:
                                    beam_to_insert = potential_sym_beam
                            # <<< END MODIFICATION >>>

                            if beam_to_insert:
                                # Found the symmetrical counterpart in the add list! Insert it here.
                                insert_idx = jbeam_entry_end_node_idx + 1 # Index right after current beam's ']'

                                # --- Whitespace/Comma Handling (Same as previous attempt) ---
                                # <<< MODIFIED: Get indentation from mirrored beam >>>
                                mirrored_indentation = _get_indentation_from_previous_wsc(ast_nodes, jbeam_entry_start_node_idx)
                                comma_part_for_original = ',' # Default comma for original beam
                                next_element_leading_indent = mirrored_indentation # Default indent for next element

                                node_after_current = ast_nodes[insert_idx] if insert_idx < len(ast_nodes) else None

                                if node_after_current and node_after_current.data_type == 'wsc':
                                    original_wsc_value = node_after_current.value
                                    nl_pos = original_wsc_value.find('\n')

                                    if nl_pos != -1: # Found a newline
                                        comma_part_for_original = original_wsc_value[:nl_pos] # Includes comma and maybe spaces
                                        next_element_leading_indent = original_wsc_value[nl_pos:] # Includes newline and indent
                                    else: # No newline, just whitespace (e.g., ', ')
                                        comma_part_for_original = original_wsc_value
                                        # next_element_leading_indent remains default (mirrored_indentation)

                                    # Remove the original WSC node that we just split/used
                                    del ast_nodes[insert_idx]
                                elif node_after_current and node_after_current.data_type == ']':
                                    # Current beam was the last in the section.
                                    # Original beam still needs a comma because we are adding one after it.
                                    comma_part_for_original = ','
                                    # Find the WSC node *before* the section ']' to use as the final indent
                                    section_end_wsc_idx = get_prev_node(ast_nodes, insert_idx, ['wsc']) # Find WSC before the ']'
                                    if section_end_wsc_idx != -1:
                                         next_element_leading_indent = ast_nodes[section_end_wsc_idx].value
                                         # Remove the original end WSC node
                                         del ast_nodes[section_end_wsc_idx]
                                         # Adjust insert_idx as we deleted a node before it
                                         insert_idx -= 1
                                    else: # Should not happen if formatting is standard
                                         next_element_leading_indent = NL_INDENT # Fallback indent before section ']'
                                else:
                                     # Unexpected node after current beam's ']', default to comma
                                     comma_part_for_original = ','
                                     # Assume standard indent for next line if structure is weird
                                     next_element_leading_indent = mirrored_indentation
                                # --- End Whitespace/Comma Handling ---

                                # --- Insertion ---
                                # 1. Insert trailing comma/space for the *original* beam
                                ast_nodes.insert(insert_idx, ASTNode('wsc', comma_part_for_original))
                                insert_idx += 1

                                # 2. Insert indentation for the *new* symmetrical beam
                                ast_nodes.insert(insert_idx, ASTNode('wsc', mirrored_indentation))
                                insert_idx += 1

                                # 3. <<< MODIFIED: Insert the new beam's AST nodes by copying and modifying the original >>>
                                copied_beam_nodes = []
                                id1_found = False
                                id2_found = False
                                for node_idx in range(jbeam_entry_start_node_idx, jbeam_entry_end_node_idx + 1):
                                    original_node = ast_nodes[node_idx]
                                    # Create a copy of the node
                                    copied_node = ASTNode(original_node.data_type, original_node.value,
                                                          precision=original_node.precision,
                                                          prefix_plus=original_node.prefix_plus,
                                                          add_post_fix_dot=original_node.add_post_fix_dot)
                                    copied_node.start_pos = -1 # Reset positions as they are invalid for the new copy
                                    copied_node.end_pos = -1

                                    # Modify node IDs within the copied nodes
                                    if copied_node.data_type == '"':
                                        if not id1_found:
                                            copied_node.value = beam_to_insert[0] # Use the sorted tuple from beam_to_insert
                                            id1_found = True
                                        elif not id2_found:
                                            copied_node.value = beam_to_insert[1]
                                            id2_found = True

                                    copied_beam_nodes.append(copied_node)

                                # Insert the copied and modified nodes
                                ast_nodes[insert_idx:insert_idx] = copied_beam_nodes # Efficient list insertion
                                insert_idx += len(copied_beam_nodes)
                                # <<< END MODIFIED STEP 3 >>>

                                # 4. Insert comma for the *new* symmetrical beam
                                ast_nodes.insert(insert_idx, ASTNode('wsc', ','))
                                insert_idx += 1

                                # 5. Insert the leading whitespace/indentation for the *next original* element (or section end)
                                ast_nodes.insert(insert_idx, ASTNode('wsc', next_element_leading_indent))
                                insert_idx += 1
                                # --- End Insertion ---

                                # Remove the beam from the copy set
                                beams_to_add_copy.remove(beam_to_insert)

                                # Adjust the main loop index
                                i = insert_idx - 1
                        # <<< END MODIFIED Symmetrical Beam Insertion Logic >>>

                elif stack_head[0] == 'triangles':
                    # If current jbeam tri is part of delete list, remove the tri definition
                    if len(jbeam_section_def) > 0:
                        if jbeam_section_row_def_idx in tris_to_delete:
                            i = delete_jbeam_entry(ast_nodes, jbeam_section_start_node_idx, jbeam_entry_start_node_idx, jbeam_entry_end_node_idx)
                            jbeam_def_deleted = True

                elif stack_head[0] == 'quads':
                    # If current jbeam quad is part of delete list, remove the quad definition
                    if len(jbeam_section_def) > 0:
                        if jbeam_section_row_def_idx in quads_to_delete:
                            i = delete_jbeam_entry(ast_nodes, jbeam_section_start_node_idx, jbeam_entry_start_node_idx, jbeam_entry_end_node_idx)
                            jbeam_def_deleted = True

                # Delete jbeam entries if referenced node is deleted
                if not jbeam_def_deleted and affect_node_references:
                    if len(jbeam_section_def) > 0:
                        len_row_header = len(jbeam_section_header)
                        for col_idx, col in enumerate(jbeam_section_def):
                            if col_idx < len_row_header and jbeam_section_header[col_idx].find(':') != -1:
                                if col in nodes_to_delete:
                                    i = delete_jbeam_entry(ast_nodes, jbeam_section_start_node_idx, jbeam_entry_start_node_idx, jbeam_entry_end_node_idx)
                                    jbeam_def_deleted = True
                                    break

                jbeam_entry_start_node_idx = None
                jbeam_entry_end_node_idx = None

                jbeam_section_def.clear()

            elif in_jbeam_part and stack_size == 1: # End of JBeam section (e.g. nodes, beams)
                jbeam_section_end_node_idx = i
                assert jbeam_section_start_node_idx < jbeam_section_end_node_idx

                if prev_stack_head_key == 'nodes' and nodes_to_add:
                    # Add nodes to add to end of nodes section (non-symmetrical ones)
                    # if constants.DEBUG:
                    #     print('Adding node...')
                    #     print('-------------Before-------------')
                    #     print_ast_nodes(ast_nodes, i, 50, True, sys.stdout)
                    i = add_jbeam_nodes(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx, nodes_to_add)
                    # if constants.DEBUG:
                    #     print('\n-------------After-------------')
                    #     print_ast_nodes(ast_nodes, i, 50, True, sys.stdout)
                    add_nodes_flag = False

                elif prev_stack_head_key == 'beams' and beams_to_add:
                    # <<< Use the remaining beams in beams_to_add_copy >>>
                    i = add_jbeam_beams(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx, list(beams_to_add_copy))
                    add_beams_flag = False # Mark as handled even if copy is empty now

                elif prev_stack_head_key == 'triangles' and tris_to_add:
                    i = add_jbeam_triangles(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx, tris_to_add)
                    add_tris_flag = False

                elif prev_stack_head_key == 'quads' and quads_to_add:
                    i = add_jbeam_quads(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx, quads_to_add)
                    add_quads_flag = False

                jbeam_section_header.clear()
                jbeam_section_header_lookup.clear()
                jbeam_section_row_def_idx = -1

            elif prev_in_jbeam_part and stack_size == 0: # End of JBeam part
                jbeam_part_end_node_idx = i

                assert jbeam_part_start_node_idx < jbeam_part_end_node_idx

                # Check if JBeams needing to be added haven't been added yet due to section not existing,
                # and create the sections if so
                if add_nodes_flag:
                    i, jbeam_section_start_node_idx, jbeam_section_end_node_idx = add_nodes_section(ast_nodes, jbeam_section_end_node_idx)
                    i = add_jbeam_nodes(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx, nodes_to_add)
                    i = get_next_non_wsc_node(ast_nodes, i + 1)
                    add_nodes_flag = False

                if add_beams_flag:
                    # <<< Use the remaining beams in beams_to_add_copy >>>
                    i, jbeam_section_start_node_idx, jbeam_section_end_node_idx = add_beams_section(ast_nodes, jbeam_section_end_node_idx)
                    i = add_jbeam_beams(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx, list(beams_to_add_copy))
                    i = get_next_non_wsc_node(ast_nodes, i + 1)
                    add_beams_flag = False

                if add_tris_flag:
                    i, jbeam_section_start_node_idx, jbeam_section_end_node_idx = add_triangles_section(ast_nodes, jbeam_section_end_node_idx)
                    i = add_jbeam_triangles(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx, tris_to_add)
                    i = get_next_non_wsc_node(ast_nodes, i + 1)
                    add_tris_flag = False

                if add_quads_flag:
                    i, jbeam_section_start_node_idx, jbeam_section_end_node_idx = add_quads_section(ast_nodes, jbeam_section_end_node_idx)
                    i = add_jbeam_quads(ast_nodes, jbeam_section_start_node_idx, jbeam_section_end_node_idx, quads_to_add)
                    i = get_next_non_wsc_node(ast_nodes, i + 1)
                    add_quads_flag = False

        elif stack_size_diff == 0: # Same level
            if in_jbeam_part and stack_size == 3: # JBeam entry
                if not in_dict:
                    section_row = stack[2][0]
                    if section_row == 0:
                        # Section header row
                        jbeam_section_header_lookup[node.value] = len(jbeam_section_header)
                        jbeam_section_header.append(node.value)
                    else:
                        header_len = len(jbeam_section_header)
                        if pos_in_arr - 1 < header_len:
                            jbeam_section_def.append(node.value)

        else:
            print(f'Error! AST traversal went {stack_size_diff} levels! Only 0 or 1 levels should be done per traversal!', file=sys.stderr)

        i += 1


def export_file(jbeam_filepath: str, parts: list[bpy.types.Object], data: dict, blender_nodes: dict, parts_nodes_actions: dict, affect_node_references: bool, parts_to_update: set):
    reimport_needed = False

    jbeam_file_str = text_editor.read_int_file(jbeam_filepath)
    if jbeam_file_str is None:
        print(f"File doesn't exist! {jbeam_filepath}", file=sys.stderr)
        return reimport_needed
    jbeam_file_data, cached_changed = jbeam_io.get_jbeam(jbeam_filepath, True, False)
    jbeam_file_data_modified, cached_changed = jbeam_io.get_jbeam(jbeam_filepath, True, False)
    if jbeam_file_data is None or jbeam_file_data_modified is None:
        return reimport_needed

    ast_data = sjsonast_parse(jbeam_file_str)
    if ast_data is None:
        print("SJSON AST parsing failed!", file=sys.stderr)
        return reimport_needed
    ast_nodes = ast_data['ast']['nodes']

    update_all_parts = True in parts_to_update

    # <<< Keep track if confirmation was triggered >>>
    confirmation_triggered_in_loop = False
    # <<< Store actions per part from the first pass >>>
    all_parts_actions_in_file = {}

    # === First Loop: Gather Actions & Check Confirmation ===
    for obj in parts:
        obj_data = obj.data
        jbeam_part = obj_data[constants.MESH_JBEAM_PART]

        # Skip if part doesn't need update (unless updating all)
        if not update_all_parts and jbeam_part not in parts_to_update:
            continue

        bm = None
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj_data)
        else:
            bm = bmesh.new()
            bm.from_mesh(obj_data)

        # --- Call get_nodes_add_delete_rename ---
        # This detects overlaps, assigns UUIDs, merges actions into parts_nodes_actions,
        # updates blender_nodes, and potentially invokes the confirmation dialog.
        part_blender_nodes, current_part_actions_map = get_nodes_add_delete_rename(obj, bm, jbeam_part, data.get('nodes', {}), affect_node_references)
        for part_key, actions in current_part_actions_map.items():
             global_actions = parts_nodes_actions.setdefault(part_key, PartNodesActions())
             global_actions.nodes_to_add.update(actions.nodes_to_add)
             global_actions.nodes_to_delete.update(actions.nodes_to_delete)
             global_actions.nodes_to_rename.update(actions.nodes_to_rename)
             global_actions.nodes_to_move.update(actions.nodes_to_move)
             # <<< ADDED: Merge symmetrical nodes >>>
             global_actions.nodes_to_add_symmetrically.update(actions.nodes_to_add_symmetrically)
        blender_nodes.update(part_blender_nodes)

        # --- Check if confirmation was triggered ---
        if jb_globals.confirm_delete_pending:
            confirmation_triggered_in_loop = True

        # --- Get actions specific to this part for storage ---
        part_actions: PartNodesActions | None = parts_nodes_actions.get(jbeam_part)
        nodes_to_add, nodes_to_delete, node_renames = {}, set(), {}
        # <<< ADDED: Get symmetrical nodes >>>
        nodes_to_add_symmetrically = {}
        if part_actions is not None:
            nodes_to_add, nodes_to_delete, node_renames = part_actions.nodes_to_add, part_actions.nodes_to_delete, part_actions.nodes_to_rename
            # <<< ADDED: Get symmetrical nodes >>>
            nodes_to_add_symmetrically = part_actions.nodes_to_add_symmetrically

        # Add "all parts" actions also (if applicable)
        part_nodes_actions_all: PartNodesActions | None = parts_nodes_actions.get(True)
        if part_nodes_actions_all is not None:
             nodes_to_add.update(part_nodes_actions_all.nodes_to_add)
             nodes_to_delete.update(part_nodes_actions_all.nodes_to_delete)
             node_renames.update(part_nodes_actions_all.nodes_to_rename)
             # <<< ADDED: Merge symmetrical nodes from 'all parts' >>>
             nodes_to_add_symmetrically.update(part_nodes_actions_all.nodes_to_add_symmetrically)

        # --- Get beam/face actions ---
        init_beams_data = data.get('beams')
        init_tris_data = data.get('triangles', [])
        init_quads_data = data.get('quads', [])

        if init_beams_data is not None:
            beams_to_add, beams_to_delete = get_beams_add_remove(obj, bm, init_beams_data, jbeam_file_data_modified, jbeam_part, nodes_to_delete, affect_node_references)
        else: beams_to_add, beams_to_delete = set(), set()
        tris_to_add, tris_to_delete, tris_flipped, quads_to_add, quads_to_delete, quads_flipped = get_faces_add_remove(obj, bm, init_tris_data, init_quads_data, jbeam_file_data_modified, jbeam_part, nodes_to_delete, affect_node_references)

        # Remove beams added due to triangles
        for beam in beams_to_add.copy():
            for tri in tris_to_add:
                if set(beam).issubset(tri): beams_to_add.remove(beam)

        # --- Store actions for this part ---
        all_parts_actions_in_file[jbeam_part] = {
            'nodes_to_add': nodes_to_add.copy(),
            'nodes_to_delete': nodes_to_delete.copy(),
            # <<< ADDED: Store symmetrical nodes >>>
            'nodes_to_add_symmetrically': nodes_to_add_symmetrically.copy(),
            'beams_to_add': beams_to_add.copy(),
            'beams_to_delete': beams_to_delete.copy(),
            'tris_to_add': tris_to_add.copy(),
            'tris_to_delete': tris_to_delete.copy(),
            'quads_to_add': quads_to_add.copy(),
            'quads_to_delete': quads_to_delete.copy(),
        }

        # --- Calculate reimport_needed ---
        if not reimport_needed:
            reimport_needed = (
                len(nodes_to_add) > 0 or len(nodes_to_delete) > 0 or len(node_renames) > 0 or
                # <<< ADDED: Check symmetrical nodes for reimport need >>>
                len(nodes_to_add_symmetrically) > 0 or
                len(beams_to_add) > 0 or len(beams_to_delete) > 0 or
                len(tris_to_add) > 0 or len(tris_to_delete) > 0 or len(tris_flipped) > 0 or
                len(quads_to_add) > 0 or len(quads_to_delete) > 0 or len(quads_flipped) > 0
            )

        # Free bmesh if temporary
        if obj.mode != 'EDIT':
            bm.free()
    # === End First Loop ===

    # === Confirmation Check ===
    # If the confirmation dialog was invoked, abort this export cycle.
    if confirmation_triggered_in_loop:
        print("Node deletion confirmation pending. Aborting current export cycle.")
        # Return reimport_needed calculated so far, as some changes might still require it later
        return reimport_needed

    # === If no confirmation pending, proceed with AST update ===
    # Apply node renames/positions to the Python dictionary first
    # Need to iterate through all parts involved in the file for this step
    all_node_renames = {}
    for part_key, actions_map in parts_nodes_actions.items():
        all_node_renames.update(actions_map.nodes_to_rename)
    # Apply renames/positions using the complete blender_nodes map
    # Iterate through unique parts associated with the objects passed to this function
    unique_parts_in_file = {obj.data[constants.MESH_JBEAM_PART] for obj in parts if obj.data}
    for part_name in unique_parts_in_file:
         set_node_renames_positions(jbeam_file_data_modified, part_name, blender_nodes, all_node_renames, affect_node_references)

    # --- Apply actions to AST ---
    processed_parts_in_ast = set()
    for obj in parts: # Iterate through objects again to get part names in order
        jbeam_part = obj.data[constants.MESH_JBEAM_PART]

        # Skip if part doesn't need update or already processed in AST
        if (not update_all_parts and jbeam_part not in parts_to_update) or jbeam_part in processed_parts_in_ast:
            continue

        # Retrieve stored actions for this part
        part_actions = all_parts_actions_in_file.get(jbeam_part)
        if not part_actions:
            continue # Should not happen if logic is correct

        # Call update_ast_nodes with the stored actions
        update_ast_nodes(ast_nodes, jbeam_file_data, jbeam_file_data_modified, jbeam_part, affect_node_references,
                         part_actions['nodes_to_add'], part_actions['nodes_to_delete'],
                         # <<< ADDED: Pass symmetrical nodes >>>
                         part_actions['nodes_to_add_symmetrically'],
                         part_actions['beams_to_add'], part_actions['beams_to_delete'],
                         part_actions['tris_to_add'], part_actions['tris_to_delete'],
                         part_actions['quads_to_add'], part_actions['quads_to_delete'])

        processed_parts_in_ast.add(jbeam_part)

    # --- Write the final AST string ---
    out_str_jbeam_data = sjsonast_stringify_nodes(ast_nodes)
    text_editor.write_int_file(jbeam_filepath, out_str_jbeam_data)

    if constants.DEBUG:
        print(f'Exported: {jbeam_filepath}')

    # Return the reimport_needed flag calculated in the first loop
    return reimport_needed

def end_export_cycle():
    """Clears temporary state after an export cycle."""
    jb_globals.node_overlap_remap.clear()


def export_file_to_disk(jbeam_filepath: str):
    res = text_editor.write_from_int_to_ext_file(jbeam_filepath)
    return res

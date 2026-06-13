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

# Shared global variables for the JBeam Editor addon

# Export control flags
_do_export = False
_force_do_export = False

# Selection tracking
prev_obj_selected = None
selected_nodes = [] # List of tuples: (vertex_index, init_node_id)
selected_beams = [] # List of tuples: (edge_index, beam_indices_str)
selected_tris_quads = [] # List of tuples: (BMFace, face_idx_in_part)
previous_selected_indices = set() # Set of selected vertex indices from previous update

# Current JBeam data cache
curr_vdata = None

# Tooltip data
_selected_beam_line_info = None # Dict: {'line': int, 'midpoint': Vector}
_selected_beam_params_info = None # Dict: {'params_list': list[tuple[str, str]], 'midpoint': Vector}
_selected_node_params_info = None # Dict: {'params_list': list[tuple[str, str]], 'pos': Vector}
_selected_node_line_info = None # Dict: {'line': int, 'pos': Vector}

# Operator states
batch_node_renaming_enabled = False
# _last_op is managed in handlers.py

# Highlight on Click state
highlighted_element_type = None # 'node', 'beam', 'rail', 'torsionbar', 'cross_part_beam'
# highlighted_element_coords is now managed in drawing.py
highlighted_element_color = (1.0, 1.0, 1.0, 1.0) # Default white (outer color for torsionbar)
highlighted_element_mid_color = (1.0, 0.0, 0.0, 1.0) # Default red (middle color for torsionbar)
highlighted_element_original_width = 1.0
highlighted_node_ids = set() # For quick membership checks (e.g., text coloring)
highlighted_element_ordered_node_ids = [] # <<< ADDED: Store IDs in order for drawing

# Text Editor state tracking for highlighting
last_text_area_info = {'name': None, 'line_index': -1}

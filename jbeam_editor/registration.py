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
import sys

# Import from local modules
from . import constants
# Import classes and functions from other modules
from .properties import UIProperties
from .operators import (
    JBEAM_EDITOR_OT_force_jbeam_sync,
    JBEAM_EDITOR_OT_undo,
    JBEAM_EDITOR_OT_redo,
    JBEAM_EDITOR_OT_add_beam_tri_quad,
    JBEAM_EDITOR_OT_flip_jbeam_faces,
    JBEAM_EDITOR_OT_batch_node_renaming,
    JBEAM_EDITOR_OT_find_node,
    JBEAM_EDITOR_OT_scroll_to_definition,
)
from .panels import (
    JBEAM_EDITOR_PT_transform_panel_ext,
    JBEAM_EDITOR_PT_jbeam_panel,
    JBEAM_EDITOR_PT_find_node,
    JBEAM_EDITOR_PT_jbeam_properties_panel,
    JBEAM_EDITOR_PT_batch_node_renaming,
    JBEAM_EDITOR_PT_jbeam_settings,
)
from .handlers import (
    depsgraph_update_post_handler,
    check_files_for_changes_timer,
    poll_active_operators_timer,
    on_post_register_handler,
    draw_callback_text_editor, # <<< Import the new handler
    load_post_handler, # <<< Import the new handler
    check_file_interval,
    poll_active_ops_interval,
)
# Import functions/classes needed for menu/import/export registration
from . import import_jbeam
from . import export_jbeam
from . import import_vehicle
# from . import export_vehicle # Not currently used for menu items

# List of classes to register
classes = (
    UIProperties,
    JBEAM_EDITOR_OT_force_jbeam_sync,
    JBEAM_EDITOR_OT_undo,
    JBEAM_EDITOR_OT_redo,
    JBEAM_EDITOR_OT_add_beam_tri_quad,
    JBEAM_EDITOR_OT_flip_jbeam_faces,
    JBEAM_EDITOR_OT_batch_node_renaming,
    JBEAM_EDITOR_OT_find_node,
    JBEAM_EDITOR_OT_scroll_to_definition,
    JBEAM_EDITOR_PT_transform_panel_ext,
    JBEAM_EDITOR_PT_jbeam_panel,
    JBEAM_EDITOR_PT_find_node,
    JBEAM_EDITOR_PT_jbeam_properties_panel,
    JBEAM_EDITOR_PT_batch_node_renaming,
    JBEAM_EDITOR_PT_jbeam_settings,
    import_jbeam.JBEAM_EDITOR_OT_import_jbeam,
    import_jbeam.JBEAM_EDITOR_OT_choose_jbeam,
    export_jbeam.JBEAM_EDITOR_OT_export_jbeam,
    import_vehicle.JBEAM_EDITOR_OT_import_vehicle,
    # export_vehicle.JBEAM_EDITOR_OT_export_vehicle, # If needed later
)

# Keymap storage
custom_keymaps = []

# Draw handle storage (managed here)
draw_handle = None
draw_handle2 = None
text_draw_handle = None # <<< Add handle for text editor

# Menu functions
def menu_func_import(self, context):
    self.layout.operator(import_jbeam.JBEAM_EDITOR_OT_import_jbeam.bl_idname, text="JBeam File (.jbeam)")

def menu_func_export(self, context):
    self.layout.operator(export_jbeam.JBEAM_EDITOR_OT_export_jbeam.bl_idname, text="Selected JBeam Part(s)")

def menu_func_import_vehicle(self, context):
    self.layout.operator(import_vehicle.JBEAM_EDITOR_OT_import_vehicle.bl_idname, text="Part Config File (.pc)")

# Helper for keymaps
def init_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if not kc:
        print("Warning: Addon keyconfig not found, cannot register keymaps.", file=sys.stderr)
        return None, []
    km = kc.keymaps.new(name="Window", space_type='EMPTY')
    kmi = [
        km.keymap_items.new("jbeam_editor.undo", 'LEFT_BRACKET', 'PRESS', ctrl=True),
        km.keymap_items.new("jbeam_editor.redo", 'RIGHT_BRACKET', 'PRESS', ctrl=True),
    ]
    return km, kmi

# Helper for finding layer collections (used by handlers)
def find_layer_collection_recursive(find, col):
    if col.collection == find: return col
    for c in col.children:
        found = find_layer_collection_recursive(find, c)
        if found: return found
    return None

# Main registration function
def register():
    global classes, custom_keymaps, draw_handle, draw_handle2, text_draw_handle # <<< Add text_draw_handle

    for c in classes:
        bpy.utils.register_class(c)

    if not bpy.app.background:
        km, kmi = init_keymaps()
        if km:
            for k_item in kmi:
                custom_keymaps.append((km, k_item))

    bpy.types.Scene.ui_properties = bpy.props.PointerProperty(type=UIProperties)
    bpy.types.Scene.jbeam_editor_veh_render_dirty = bpy.props.BoolProperty(default=False)
    # Add scene property for tracking file mapping
    bpy.types.Scene.jbeam_editor_text_editor_short_to_full_filename = bpy.props.CollectionProperty(type=bpy.types.PropertyGroup) # Use CollectionProperty or similar if needed, or just rely on scene dictionary
    # Add scene property for tracking previous text states
    bpy.types.Scene.jbeam_editor_text_editor_files_text = bpy.props.CollectionProperty(type=bpy.types.PropertyGroup) # Use CollectionProperty or similar if needed, or just rely on scene dictionary

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_vehicle)

    # Clear existing handlers before appending (safety measure)
    while depsgraph_update_post_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(depsgraph_update_post_handler)
    bpy.app.handlers.depsgraph_update_post.append(depsgraph_update_post_handler)

    # <<< ADDED: Register load_post handler >>>
    while load_post_handler in bpy.app.handlers.load_post:
         bpy.app.handlers.load_post.remove(load_post_handler)
    bpy.app.handlers.load_post.append(load_post_handler)
    # <<< END ADDED >>>

    try:
        # Register draw handlers via on_post_register_handler timer
        if not bpy.app.timers.is_registered(on_post_register_handler):
             bpy.app.timers.register(on_post_register_handler, first_interval=0.1, persistent=True)
        # Register other timers
        if not bpy.app.timers.is_registered(check_files_for_changes_timer):
            bpy.app.timers.register(check_files_for_changes_timer, first_interval=check_file_interval, persistent=True)
        if not bpy.app.timers.is_registered(poll_active_operators_timer):
            bpy.app.timers.register(poll_active_operators_timer, first_interval=poll_active_ops_interval, persistent=True)
    except Exception as e:
        print(f"Error registering timers: {e}", file=sys.stderr)

# Main unregistration function
def unregister():
    global classes, custom_keymaps, draw_handle, draw_handle2, text_draw_handle # <<< Add text_draw_handle

    if bpy.app.timers.is_registered(on_post_register_handler): bpy.app.timers.unregister(on_post_register_handler)
    if bpy.app.timers.is_registered(check_files_for_changes_timer): bpy.app.timers.unregister(check_files_for_changes_timer)
    if bpy.app.timers.is_registered(poll_active_operators_timer): bpy.app.timers.unregister(poll_active_operators_timer)

    # Unregister draw handlers
    if draw_handle:
        try: bpy.types.SpaceView3D.draw_handler_remove(draw_handle, 'WINDOW')
        except ValueError: pass # Handle case where it might already be removed
        draw_handle = None
    if not constants.UNIT_TESTING and draw_handle2:
        try: bpy.types.SpaceView3D.draw_handler_remove(draw_handle2, 'WINDOW')
        except ValueError: pass
        draw_handle2 = None
    # <<< Unregister text editor draw handler >>>
    if text_draw_handle:
        try: bpy.types.SpaceTextEditor.draw_handler_remove(text_draw_handle, 'WINDOW')
        except ValueError: pass
        text_draw_handle = None

    # Unregister application handlers
    if depsgraph_update_post_handler in bpy.app.handlers.depsgraph_update_post:
         bpy.app.handlers.depsgraph_update_post.remove(depsgraph_update_post_handler)

    # <<< ADDED: Unregister load_post handler >>>
    if load_post_handler in bpy.app.handlers.load_post:
         bpy.app.handlers.load_post.remove(load_post_handler)
    # <<< END ADDED >>>

    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
        bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_vehicle)
    except Exception as e: print(f"Error removing menu functions: {e}", file=sys.stderr)

    for c in reversed(classes):
        try: bpy.utils.unregister_class(c)
        except RuntimeError: print(f"Could not unregister class {c.__name__}", file=sys.stderr)

    for km, kmi in custom_keymaps:
        try: km.keymap_items.remove(kmi)
        except Exception as e: print(f"Error removing keymap item: {e}", file=sys.stderr)
    custom_keymaps.clear()

    try:
        if hasattr(bpy.types.Scene, 'ui_properties'): del bpy.types.Scene.ui_properties
        if hasattr(bpy.types.Scene, 'jbeam_editor_veh_render_dirty'): del bpy.types.Scene.jbeam_editor_veh_render_dirty
        # Clean up scene properties used for text editor tracking
        if hasattr(bpy.types.Scene, 'jbeam_editor_text_editor_short_to_full_filename'): del bpy.types.Scene.jbeam_editor_text_editor_short_to_full_filename
        if hasattr(bpy.types.Scene, 'jbeam_editor_text_editor_files_text'): del bpy.types.Scene.jbeam_editor_text_editor_files_text
    except Exception as e: print(f"Error deleting scene properties: {e}", file=sys.stderr)

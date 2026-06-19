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

from .blender.properties import UIProperties
from .blender.operators.core import (
    JBEAM_EDITOR_OT_force_jbeam_sync,
    JBEAM_EDITOR_OT_undo,
    JBEAM_EDITOR_OT_redo,
)
from .blender.operators.mesh_edit import (
    JBEAM_EDITOR_OT_add_beam_tri_quad,
    JBEAM_EDITOR_OT_flip_jbeam_faces,
)
from .blender.operators.nodes import JBEAM_EDITOR_OT_batch_node_renaming
from .blender.panels.main import (
    JBEAM_EDITOR_PT_transform_panel_ext,
    JBEAM_EDITOR_PT_jbeam_panel,
    JBEAM_EDITOR_PT_jbeam_settings,
)
from .blender.panels.properties_panel import JBEAM_EDITOR_PT_jbeam_properties_panel
from .blender.panels.tools import JBEAM_EDITOR_PT_batch_node_renaming
from . import import_jbeam
from . import export_jbeam
from . import import_vehicle
from .blender import handlers


def menu_func_import(self, context):
    self.layout.operator(import_jbeam.JBEAM_EDITOR_OT_import_jbeam.bl_idname, text="JBeam File (.jbeam)")


def menu_func_export(self, context):
    self.layout.operator(export_jbeam.JBEAM_EDITOR_OT_export_jbeam.bl_idname, text="Selected JBeam Part(s)")


def menu_func_import_vehicle(self, context):
    self.layout.operator(import_vehicle.JBEAM_EDITOR_OT_import_vehicle.bl_idname, text="Part Config File (.pc)")


classes = (
    UIProperties,
    JBEAM_EDITOR_OT_force_jbeam_sync,
    JBEAM_EDITOR_OT_undo,
    JBEAM_EDITOR_OT_redo,
    JBEAM_EDITOR_OT_add_beam_tri_quad,
    JBEAM_EDITOR_OT_flip_jbeam_faces,
    JBEAM_EDITOR_OT_batch_node_renaming,
    JBEAM_EDITOR_PT_transform_panel_ext,
    JBEAM_EDITOR_PT_jbeam_panel,
    JBEAM_EDITOR_PT_jbeam_properties_panel,
    JBEAM_EDITOR_PT_batch_node_renaming,
    JBEAM_EDITOR_PT_jbeam_settings,
    import_jbeam.JBEAM_EDITOR_OT_import_jbeam,
    import_jbeam.JBEAM_EDITOR_OT_choose_jbeam,
    export_jbeam.JBEAM_EDITOR_OT_export_jbeam,
    import_vehicle.JBEAM_EDITOR_OT_import_vehicle,
)

custom_keymaps = []


def init_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    km = kc.keymaps.new(name="Window")
    kmi = [
        km.keymap_items.new("jbeam_editor.undo", 'LEFT_BRACKET', 'PRESS', ctrl=True),
        km.keymap_items.new("jbeam_editor.redo", 'RIGHT_BRACKET', 'PRESS', ctrl=True),
    ]
    return km, kmi


def register():
    global classes, custom_keymaps

    for c in classes:
        bpy.utils.register_class(c)

    if not bpy.app.background:
        km, kmi = init_keymaps()
        for k in kmi:
            k.active = True
            custom_keymaps.append((km, k))

    bpy.types.Scene.ui_properties = bpy.props.PointerProperty(type=UIProperties)

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_vehicle)

    bpy.app.handlers.depsgraph_update_post.append(handlers.depsgraph_callback)

    # Delayed function call to prevent "restrictcontext" error
    bpy.app.timers.register(handlers.on_post_register, first_interval=0.1, persistent=True)

    bpy.app.timers.register(handlers.check_files_for_changes, first_interval=handlers.state.check_file_interval, persistent=True)
    bpy.app.timers.register(handlers.poll_active_operators, first_interval=handlers.state.poll_active_ops_interval, persistent=True)


def unregister():
    global classes, custom_keymaps

    for c in reversed(classes):
        bpy.utils.unregister_class(c)

    for km, kmi in custom_keymaps:
        km.keymap_items.remove(kmi)
    custom_keymaps.clear()

    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_vehicle)

    bpy.app.handlers.depsgraph_update_post.remove(handlers.depsgraph_callback)

    if handlers.state.draw_handle:
        bpy.types.SpaceView3D.draw_handler_remove(handlers.state.draw_handle, 'WINDOW')

    from .core import constants
    if not constants.UNIT_TESTING:
        if handlers.state.draw_handle2:
            bpy.types.SpaceView3D.draw_handler_remove(handlers.state.draw_handle2, 'WINDOW')

    bpy.app.timers.unregister(handlers.check_files_for_changes)
    bpy.app.timers.unregister(handlers.poll_active_operators)

    del bpy.types.Scene.ui_properties

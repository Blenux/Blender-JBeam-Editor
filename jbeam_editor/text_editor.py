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

import hashlib
import re
from pathlib import Path # <<< ADDED: Import Path
import traceback # Ensure traceback is imported
import sys # <<< Import sys

from . import constants
from . import import_vehicle
from . import import_jbeam
from . import utils
# <<< Import drawing module >>>
from . import drawing
# <<< Import globals >>>
from . import globals as jb_globals

SCENE_PREV_TEXTS = 'jbeam_editor_text_editor_files_text'
SCENE_SHORT_TO_FULL_FILENAME = 'jbeam_editor_text_editor_short_to_full_filename'

HISTORY_STACK_SIZE = 250

regex = re.compile(r'^.*/vehicles/(.*)$')

history_stack = []
history_stack_idx = -1

def _get_short_jbeam_path(path: str):
    match = re.match(regex, path)
    if match is not None:
        return match.group(1)
    return None


def _to_short_filename(filepath: str):
    """Generates an MD5 hash for the given filepath."""
    return hashlib.md5(filepath.encode('UTF-8')).hexdigest()

    # new_filename = _get_short_jbeam_path(filename)
    # if new_filename is None:
    #     new_filename = filename
    # return new_filename[max(0, len(new_filename) - 60):] # roughly 60 character limit

# <<< ADDED: New helper function for internal naming >>>
def _get_internal_filename(filepath: str) -> str:
    """
    Determines the internal Blender text document name for a given file path.
    Uses the base name for .pc files and an MD5 hash for others.
    """
    path_obj = Path(filepath)
    if path_obj.suffix.lower() == '.pc':
        return path_obj.stem # Use base name without extension
    else:
        return _to_short_filename(filepath) # Use MD5 hash for other types


def write_from_ext_to_int_file(filepath: str):
    filetext = utils.read_file(filepath)
    if filetext is None:
        return None
    write_int_file(filepath, filetext)
    return filetext


def write_from_int_to_ext_file(filepath: str):
    internal_name = _get_internal_filename(filepath) # <<< Use new helper
    text: bpy.types.Text | None = bpy.data.texts.get(internal_name)
    if text is None:
        return False

    res = utils.write_file(filepath, text.as_string())
    return res


def write_int_file(filepath: str, text: str):
    context = bpy.context
    scene = context.scene

    # this full to short filename BS because of 63 character key limit...

    internal_name = _get_internal_filename(filepath) # <<< Use new helper
    if SCENE_SHORT_TO_FULL_FILENAME not in scene:
        scene[SCENE_SHORT_TO_FULL_FILENAME] = {}

    scene[SCENE_SHORT_TO_FULL_FILENAME][internal_name] = filepath # Map internal_name -> full_filepath

    if internal_name not in bpy.data.texts:
        bpy.data.texts.new(internal_name)
    file = bpy.data.texts[internal_name]
    curr_line, curr_char = file.current_line_index, file.current_character
    file.clear()
    file.write(text)
    file.cursor_set(curr_line, character=curr_char)

    if SCENE_PREV_TEXTS not in scene:
        scene[SCENE_PREV_TEXTS] = {}
    if internal_name not in scene[SCENE_PREV_TEXTS]:
        scene[SCENE_PREV_TEXTS][internal_name] = None

    #check_files_for_changes(context)


def read_int_file(filename: str) -> str | None:
    internal_name = _get_internal_filename(filename) # <<< Use new helper
    text: bpy.types.Text | None = bpy.data.texts.get(internal_name)
    if text is None:
        return None
    return text.as_string()


def delete_int_file(filename: str):
    ui_props = bpy.context.scene.ui_properties
    internal_name = _get_internal_filename(filename) # <<< Use new helper
    text: bpy.types.Text | None = bpy.data.texts.get(internal_name)
    if text is None:
        return
    bpy.data.texts.remove(text)


def show_int_file(filename: str):
    internal_name = _get_internal_filename(filename) # <<< Use new helper
    text: bpy.types.Text | None = bpy.data.texts.get(internal_name)
    if text is None:
        return

    text_area = None
    for area in bpy.context.screen.areas:
        if area.type == "TEXT_EDITOR":
            text_area = area
            break

    if text_area is None:
        return

    if text_area.spaces[0].text != text:
        text_area.spaces[0].text = text


def check_open_int_file_for_changes(context: bpy.types.Context, undoing_redoing=False):
    scene = context.scene

    if SCENE_PREV_TEXTS not in scene:
        return False

    # If active file changed, reimport jbeam file/vehicle
    text = None
    text_area = None
    for area in context.screen.areas:
        if area.type == "TEXT_EDITOR":
            text_area = area
            break

    if text_area is not None:
        text = text_area.spaces[0].text
    if text is None:
        return False

    internal_name, curr_file_text = text.name, text.as_string() # text.name is the internal_name

    # --- Revised logic for last_file_text_for_history ---
    _stored_last_text = scene[SCENE_PREV_TEXTS].get(internal_name) # Get raw value, could be None
    if _stored_last_text is None or internal_name not in scene[SCENE_PREV_TEXTS]:
        # This means it's the first check after load/init or prev state was None.
        # The "state before change" for history is the current content.
        last_file_text_for_history = curr_file_text
        # For the *next* comparison, the "previous" will be this current text.
        scene[SCENE_PREV_TEXTS][internal_name] = curr_file_text
    else:
        # A valid previous state exists.
        last_file_text_for_history = _stored_last_text
    # --- End Revised logic ---

    filename_full = scene[SCENE_SHORT_TO_FULL_FILENAME].get(internal_name) # Use internal_name as key
    if filename_full is None:
        return False

    file_changed = False
    # Compare current text with the determined "last state for history"
    if curr_file_text != last_file_text_for_history:
        # File changed!
        if constants.DEBUG:
            print('file changed!', filename_full)

        # Store the state *before* the change for potential initial history push

        # Update the scene's "previous text" store to the new current text *before* reimport/history.
        # This is crucial so that if reimport itself triggers another check, it sees the correct "previous".
        scene[SCENE_PREV_TEXTS][internal_name] = curr_file_text

        # <<< MOVED: Reimport BEFORE highlighting >>>
        import_vehicle.on_files_change(context, {filename_full: curr_file_text}, True)
        import_jbeam.on_file_change(context, filename_full, True)
        file_changed = True
        # <<< END MOVE >>>

        # <<< Trigger highlight update AFTER text change and reimport >>>
        ui_props = scene.ui_properties
        if ui_props.highlight_element_on_click:
            try:
                # <<< ADD EXPLICIT REFRESH >>>
                drawing.refresh_curr_vdata(True)
                # <<< END ADD >>>
                # Trigger highlight update based on current cursor position after text change
                current_line_index = text.current_line_index # Get current line
                # <<< Use drawing. prefix >>>
                drawing.find_and_highlight_element_for_line(context, text, current_line_index)
            except Exception as e:
                print(f"Error during highlight update on text change: {e}", file=sys.stderr)
                traceback.print_exc()
        # <<< End highlight update trigger >>>

        # <<< MODIFIED History Push Logic >>>
        if not undoing_redoing:
            global history_stack, history_stack_idx

            if not history_stack:
                # Push the state *before* this change (last_file_text_for_history)
                history_stack.insert(0, {internal_name: last_file_text_for_history})
                history_stack_idx = 0

            # Now, push the *current* state (after the change)
            history_stack_idx += 1
            history_stack.insert(history_stack_idx, {internal_name: curr_file_text}) # Use internal_name as key
            history_stack = history_stack[:history_stack_idx + 1] # Truncate if needed

            # Limit history stack size
            if len(history_stack) > HISTORY_STACK_SIZE:
                history_stack.pop(0)
                history_stack_idx -= 1

            if constants.DEBUG:
                print('len(history_stack)', len(history_stack))
                print('history_stack_idx', history_stack_idx)
        # <<< END MODIFIED History Push Logic >>>

    # <<< REMOVED Original History Push Block >>>
    # if not undoing_redoing and file_changed:
    #     # Insert new history into history stack
    #     global history_stack, history_stack_idx
    #
    #     # If history is empty, push the initial state (state before the first change)
    #     if not history_stack:
    #          history_stack.insert(0, {short_filename: state_before_change}) # Push initial state at index 0
    #          history_stack_idx = 0 # Index now points to initial state
    #
    #     history_stack_idx += 1
    #     # Push the state *after* the change onto the stack
    #     history_stack.insert(history_stack_idx, {short_filename: curr_file_text})
    #     history_stack = history_stack[:history_stack_idx + 1] # Truncate if needed
    #
    #     # Limit history stack size
    #     if len(history_stack) > HISTORY_STACK_SIZE:
    #         history_stack.pop(0)
    #         history_stack_idx -= 1 # Adjust index if we removed the first element
    #
    #     if constants.DEBUG:
    #         print('len(history_stack)', len(history_stack))
    #         print('history_stack_idx', history_stack_idx)

    return file_changed


def check_int_files_for_changes(context: bpy.types.Context, filenames: list, undoing_redoing=False, reimport=True, regenerate_mesh=True):
    scene = context.scene

    if SCENE_PREV_TEXTS not in scene or SCENE_SHORT_TO_FULL_FILENAME not in scene:
        return

    files_changed_short_names = None # Initialize as None
    files_changed = None
    any_file_changed = False # Flag to track if any relevant file changed
    states_before_change = {} # Collect states before change for this step
    active_text_to_highlight = None # Store active text if it changed

    for filename in filenames:
        internal_name = _get_internal_filename(filename) # <<< Use new helper
        text = bpy.data.texts.get(internal_name)
        if not text:
            continue

        curr_file_text = text.as_string()
        # --- Revised logic for last_file_text_for_history ---
        _stored_last_text = scene[SCENE_PREV_TEXTS].get(internal_name)
        if _stored_last_text is None or internal_name not in scene[SCENE_PREV_TEXTS]:
            last_file_text_for_history = curr_file_text
            scene[SCENE_PREV_TEXTS][internal_name] = curr_file_text # Update for next cycle
        else:
            last_file_text_for_history = _stored_last_text
        # --- End Revised logic ---

        filename_full = scene[SCENE_SHORT_TO_FULL_FILENAME].get(internal_name) # <<< Use internal_name
        if filename_full is None:
            continue
        if curr_file_text != last_file_text_for_history:
            if not any_file_changed: # First change detected in this run
                any_file_changed = True # Mark change
                # <<< FIX: Initialize files_changed_short_names here >>>
                files_changed_short_names = {}
                if reimport:
                    files_changed = {}
                # <<< END FIX >>>

            # File changed!
            if constants.DEBUG:
                print('file changed!', filename_full)

            # Store the state *before* the change for history
            states_before_change[internal_name] = last_file_text_for_history # Use the determined "before" state

            # Update the scene's "previous text" store to the new current text
            scene[SCENE_PREV_TEXTS][internal_name] = curr_file_text

            # Check if this is the active file *before* reimporting
            active_text = None
            for area in context.screen.areas:
                if area.type == "TEXT_EDITOR":
                    if area.spaces.active and area.spaces.active.text == text:
                        active_text = text
                        active_text_to_highlight = active_text # Mark for highlighting later
                        break

            # <<< MOVED: Reimport logic moved down, but collect changes here >>>
            # Store the current text for reimport map and history map
            # files_changed_short_names is guaranteed to be a dict here if any_file_changed is True
            files_changed_short_names[internal_name] = curr_file_text # <<< Use internal_name
            if reimport and files_changed is not None: # Check files_changed as well
                files_changed[filename_full] = curr_file_text


    # <<< MOVED: Perform reimport AFTER checking all files for changes >>>
    if reimport and files_changed is not None:
        # Check if it's the active file before calling single part reimport
        active_obj = context.active_object
        active_filepath = None
        if active_obj and active_obj.data and active_obj.data.get(constants.MESH_JBEAM_FILE_PATH):
             active_filepath = active_obj.data.get(constants.MESH_JBEAM_FILE_PATH)

        # Perform single part reimport if active file changed
        if active_filepath in files_changed:
            import_jbeam.on_file_change(context, active_filepath, regenerate_mesh)

        # Vehicle reimport handles multiple file changes
        import_vehicle.on_files_change(context, files_changed, regenerate_mesh)
    # <<< END MOVE >>>

    # <<< MOVED: Trigger highlight update AFTER reimport >>>
    if active_text_to_highlight:
        ui_props = scene.ui_properties
        if ui_props.highlight_element_on_click:
            try:
                # <<< ADD EXPLICIT REFRESH >>>
                drawing.refresh_curr_vdata(True)
                # <<< END ADD >>>
                current_line_index = active_text_to_highlight.current_line_index
                # <<< Use drawing. prefix >>>
                drawing.find_and_highlight_element_for_line(context, active_text_to_highlight, current_line_index)
            except Exception as e:
                print(f"Error during highlight update on text change (multi-file check): {e}", file=sys.stderr)
                traceback.print_exc()
    # <<< End highlight update trigger >>>

    # <<< MODIFIED History Push Logic >>>
    if not undoing_redoing and any_file_changed: # Check if any file actually changed
        global history_stack, history_stack_idx

        # If history is empty, push the collected states *before* this change batch.
        if not history_stack:
            # states_before_change was collected earlier in the loop
            history_stack.insert(0, states_before_change.copy())
            history_stack_idx = 0

        # Now, push the *current* states (after the changes in this batch)
        history_stack_idx += 1
        # files_changed_short_names contains the current states
        # files_changed_short_names is guaranteed to be a dict here
        history_stack.insert(history_stack_idx, files_changed_short_names.copy()) # Push a copy
        history_stack = history_stack[:history_stack_idx + 1] # Truncate if needed

        # Limit history stack size
        if len(history_stack) > HISTORY_STACK_SIZE:
            history_stack.pop(0)
            history_stack_idx -= 1 # Adjust index if we removed the first element

        if constants.DEBUG:
            print('len(history_stack)', len(history_stack))
            print('history_stack_idx', history_stack_idx)
    # <<< END MODIFIED History Push Logic >>>

    # <<< REMOVED Original History Push Block >>>
    # if not undoing_redoing and any_file_changed: # Check if any file actually changed
    #     # Insert new history into history stack
    #     global history_stack, history_stack_idx
    #
    #     # If history is empty, push the initial state(s) (state before the first change)
    #     if not history_stack:
    #          history_stack.insert(0, states_before_change.copy()) # Push initial state(s) at index 0
    #          history_stack_idx = 0 # Index now points to initial state
    #
    #     history_stack_idx += 1
    #     # Push the dictionary of states *after* the changes onto the stack
    #     # files_changed_short_names contains the current states
    #     # files_changed_short_names is guaranteed to be a dict here
    #     history_stack.insert(history_stack_idx, files_changed_short_names.copy()) # Push a copy
    #     history_stack = history_stack[:history_stack_idx + 1] # Truncate if needed
    #
    #     # Limit history stack size
    #     if len(history_stack) > HISTORY_STACK_SIZE:
    #         history_stack.pop(0)
    #         history_stack_idx -= 1 # Adjust index if we removed the first element
    #
    #     if constants.DEBUG:
    #         print('len(history_stack)', len(history_stack))
    #         print('history_stack_idx', history_stack_idx)


def check_all_int_files_for_changes(context: bpy.types.Context, undoing_redoing=False, reimport=True):
    scene = context.scene
    if SCENE_SHORT_TO_FULL_FILENAME not in scene:
        return
    # Pass a list copy of values to avoid issues if the dictionary changes during iteration
    check_int_files_for_changes(context, list(scene[SCENE_SHORT_TO_FULL_FILENAME].values()), undoing_redoing, reimport)


def on_undo_redo(context: bpy.types.Context, undoing: bool):
    scene = context.scene

    if SCENE_SHORT_TO_FULL_FILENAME not in scene or SCENE_PREV_TEXTS not in scene:
        return

    global history_stack_idx, history_stack # Need history_stack too

    if not history_stack: # Cannot undo/redo if history is empty
        print("History stack is empty.")
        return

    target_idx = -1

    if undoing:
        if history_stack_idx > 0: # Can only undo if not at the initial state (index 0)
            target_idx = history_stack_idx - 1
        else:
            print("Cannot undo further.")
            return # Cannot undo initial state
    else: # Redoing
        if history_stack_idx < len(history_stack) - 1: # Can only redo if not at the last state
            target_idx = history_stack_idx + 1
        else:
            print("Cannot redo further.")
            return # Cannot redo last state

    if constants.DEBUG:
        print(f"{'Undoing' if undoing else 'Redoing'} to index: {target_idx}")
        print('len(history_stack)', len(history_stack))
        print('history_stack_idx before', history_stack_idx)

    # Update the global index *after* determining the target
    history_stack_idx = target_idx

    if constants.DEBUG:
        print('history_stack_idx after', history_stack_idx)

    try: # Add try-except block for safety during state application
        entry = history_stack[history_stack_idx] # Retrieve state at the new index
        filepaths = []
        files_changed_for_reimport = {} # Collect changes for reimport call
        active_text_restored = None # Track if the active text editor file was restored

        for internal_name, text_content in entry.items(): # Key is internal_name
            if internal_name not in bpy.data.texts:
                print(f"Warning: Text object {internal_name} not found during undo/redo.")
                continue

            file = bpy.data.texts[internal_name]

            # Check if the content actually needs changing to avoid unnecessary updates
            if file.as_string() != text_content:
                curr_line, curr_char = file.current_line_index, file.current_character
                file.clear()
                file.write(text_content if text_content is not None else "") # Ensure text_content is a string
                file.cursor_set(curr_line, character=curr_char) # Restore cursor

            # Update the 'previous' text state to match the state we just restored
            # This prevents the next check_..._for_changes from thinking a change occurred
            scene[SCENE_PREV_TEXTS][internal_name] = text_content if text_content is not None else "" # Ensure string

            full_filepath = scene[SCENE_SHORT_TO_FULL_FILENAME].get(internal_name) # <<< Use internal_name
            if full_filepath:
                filepaths.append(full_filepath) # Use full path for reimport list
                files_changed_for_reimport[full_filepath] = text_content # Use full path for reimport

            # Check if this is the active text editor file
            for area in context.screen.areas:
                if area.type == "TEXT_EDITOR":
                    if area.spaces.active and area.spaces.active.text == file:
                        active_text_restored = file
                        break
            if active_text_restored: break # Found the active one

        # <<< MOVED: Reimport BEFORE highlighting >>>
        # Reimport based on the restored state, but don't trigger history push
        # Pass regenerate_mesh=True because the mesh should reflect the restored text state
        # Pass reimport=True to actually trigger the reimport logic
        # Pass undoing_redoing=True to prevent history push
        if filepaths:
            # Single part reimport (if active object matches one of the changed files)
            active_obj = context.active_object
            active_filepath = None
            if active_obj and active_obj.data and active_obj.data.get(constants.MESH_JBEAM_FILE_PATH):
                 active_filepath = active_obj.data.get(constants.MESH_JBEAM_FILE_PATH)

            if active_filepath in files_changed_for_reimport:
                # Call reimport directly, regenerate mesh
                import_jbeam.reimport_jbeam(context, None, active_obj, active_filepath, True)

            # Vehicle reimport (handles multiple files)
            # Find the relevant vehicle collection based on the changed files
            veh_collection_to_reimport = None
            for coll in bpy.data.collections:
                if coll.get(constants.COLLECTION_VEHICLE_MODEL):
                    coll_files = coll.get(constants.COLLECTION_VEH_FILES, [])
                    # Check if any of the changed files belong to this vehicle collection
                    if any(fp in coll_files for fp in files_changed_for_reimport):
                        veh_collection_to_reimport = coll
                        break # Assume a file belongs to only one vehicle collection

            if veh_collection_to_reimport:
                 # Call reimport directly, regenerate mesh
                 import_vehicle.reimport_vehicle(context, veh_collection_to_reimport, files_changed_for_reimport, True)
        # <<< END MOVE >>>

        # <<< Trigger highlight update AFTER reimport >>>
        if active_text_restored:
            ui_props = scene.ui_properties
            if ui_props.highlight_element_on_click:
                try:
                    # <<< ADD EXPLICIT REFRESH >>>
                    drawing.refresh_curr_vdata(True)
                    # <<< END ADD >>>
                    current_line_index = active_text_restored.current_line_index
                    # <<< Use drawing. prefix >>>
                    drawing.find_and_highlight_element_for_line(context, active_text_restored, current_line_index)
                except Exception as e:
                    print(f"Error during highlight update on undo/redo: {e}", file=sys.stderr)
                    traceback.print_exc()
        # <<< End highlight update trigger >>>


        if constants.DEBUG:
            print(f"Applied state from index {history_stack_idx}")

    except IndexError:
        print(f"Error: History index {history_stack_idx} out of bounds.", file=sys.stderr)
        # Attempt to recover the index? Maybe clamp it?
        history_stack_idx = utils.clamp(history_stack_idx, 0, len(history_stack) - 1)
        print(f"History index clamped to: {history_stack_idx}")
    except Exception as e:
        print(f"Error applying undo/redo state: {e}", file=sys.stderr)
        traceback.print_exc()

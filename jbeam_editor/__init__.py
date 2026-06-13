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

bl_info = {
    "name": "Blender JBeam Editor (unofficial)",
    "description": "Modify BeamNG JBeam files in a 3D editor!",
    "author": "BeamNG + Tomsteel", # <<< Keep author info
    "version": (0, 2, 66), # Keep incremented version
    "blender": (4, 3, 0), # Keep blender version info
    "location": "File > Import > JBeam File / File > Export > JBeam File",
    "warning": "",
    "doc_url": "https://github.com/BeamNG/Blender-JBeam-Editor/blob/vehicle_importer/docs/user/user_docs.md",
    "tracker_url": "https://github.com/BeamNG/Blender-JBeam-Editor/issues",
    "support": "COMMUNITY",
    "category": "Development",
}

# Import only the registration module
from . import registration

def register():
    """Registers the addon."""
    registration.register()

def unregister():
    """Unregisters the addon."""
    registration.unregister()

# This allows you to run the script directly from Blender's Text editor
# to test the add-on without having to install it.
if __name__ == "__main__":
    # Clean up previous registration if run multiple times
    try:
        unregister()
    except Exception as e:
        pass # Ignore errors during cleanup before registration
    register()

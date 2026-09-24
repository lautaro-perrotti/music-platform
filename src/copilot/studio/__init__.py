"""Local Music Studio vertical slice.

The Studio is an application layer around the existing provider-neutral music
generation contracts.  It does not own Ableton writes; generated audio is
managed as an immutable artifact until a separate, explicitly authorized
import path exists.
"""

from copilot.studio.service import StudioService

__all__ = ["StudioService"]

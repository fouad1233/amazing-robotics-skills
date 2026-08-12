# SPDX-License-Identifier: Apache-2.0
"""Views over the event stream.

Nothing in here simulates an agent. Each view subscribes to
``roboagents.events`` (live bus or a recorded JSONL transcript) and draws what
it is told; an empty stream means an empty screen.

Submodules are deliberately not imported here — the web world needs aiohttp,
the terminal needs rich, and the desktop pets need GTK on the *system* Python.
Importing this package must not require any of them.
"""

from __future__ import annotations

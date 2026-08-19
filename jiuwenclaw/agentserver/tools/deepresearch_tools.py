"""Compatibility module alias for DeepResearch tools.

The implementation lives in :mod:`jiuwenclaw.agentserver.tools.deepresearch.tools`.
Alias the old module path to the implementation module so existing imports,
private test hooks, monkeypatches, and ``__file__`` checks keep their behavior.
"""

import sys

from jiuwenclaw.agentserver.tools.deepresearch import tools as _tools

sys.modules[__name__] = _tools

"""Vendor packages bundled with jiuwenclaw.

This directory contains embedded dependencies to avoid git dependencies
in wheel packages for downstream users.

When using --vendor build option, openjiuwen and openjiuwen_deepsearch
are embedded here and made available via sys.modules redirection.
"""

import sys


def _setup_vendor_imports():
    """Set up sys.modules redirection for vendor packages.

    This allows code to use `import openjiuwen` while the actual
    package resides in `jiuwenclaw.vendor.openjiuwen`.
    """
    # Only setup if vendor packages exist
    try:
        from jiuwenclaw.vendor import openjiuwen
        sys.modules['openjiuwen'] = openjiuwen
    except ImportError:
        pass

    try:
        from jiuwenclaw.vendor import openjiuwen_deepsearch
        sys.modules['openjiuwen_deepsearch'] = openjiuwen_deepsearch
    except ImportError:
        pass


# Auto-setup when this package is imported
_setup_vendor_imports()
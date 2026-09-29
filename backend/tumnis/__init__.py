"""Tumnis Guide backend."""

import os

# The release this code is: the image's VERSION build arg (a tag such as v0.1.0, or
# sha-<commit> for main), baked in as TUMNIS_BUILD_VERSION (deploy/Dockerfile, P0-30).
__version__ = os.environ.get("TUMNIS_BUILD_VERSION") or "0.0.0+dev"

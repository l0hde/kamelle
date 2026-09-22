"""Keep the test run away from the real ~/.kamelle.

kamelle.state resolves its paths at import time, so KAMELLE_HOME has to be set
before any kamelle module is imported — which is exactly what conftest is for.
"""
from __future__ import annotations

import os
import tempfile

os.environ["KAMELLE_HOME"] = tempfile.mkdtemp(prefix="kamelle-tests-")

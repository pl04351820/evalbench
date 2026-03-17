"""
Root conftest — stubs GCP/cloud SDK modules that initialize at import time,
so MongoDB unit tests run locally without GCP credentials.

Runs before any test collection, which is the key requirement.
"""
import sys
import os
from unittest.mock import MagicMock

# ── 1. Add evalbench/ to sys.path so internal bare imports work ──────────────
_evalbench_src = os.path.join(os.path.dirname(__file__), "evalbench")
if _evalbench_src not in sys.path:
    sys.path.insert(0, _evalbench_src)


# ── 2. Helper: register a MagicMock for a dotted module name ─────────────────
def _stub(name):
    mod = MagicMock()
    mod.__name__ = name
    mod.__path__ = []          # makes Python treat it as a package
    mod.__package__ = name
    mod.__spec__ = None
    sys.modules[name] = mod
    # Also register all parent segments so dotted lookups resolve
    parts = name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            p = MagicMock()
            p.__name__ = parent
            p.__path__ = []
            p.__package__ = parent
            p.__spec__ = None
            sys.modules[parent] = p
    return mod


# ── 3. GCP / cloud stubs ─────────────────────────────────────────────────────

# SecretManager — databases/util.py calls SecretManagerServiceClient() at module level
_stub("google.cloud.secretmanager_v1")

# Cloud SQL Connector — databases/postgres.py: CONNECTOR = Connector()
_stub("google.cloud.sql.connector")

# AlloyDB Connector — databases/alloydb.py: CONNECTOR = AlloyDBConnector()
_stub("google.cloud.alloydb.connector")

# BigQuery — reporting/bqstore.py
_stub("google.cloud.bigquery")
_stub("google.api_core.exceptions")

# Bigtable — databases/bigtable.py deep import chain
_stub("google.cloud.bigtable")
_stub("google.cloud.bigtable.data")
_stub("google.cloud.bigtable.data.execute_query")
_stub("google.cloud.bigtable.row_filters")

# Spanner
_stub("google.cloud.spanner_v1")
_stub("sqlalchemy_spanner")

# Vertex AI / genai — stub all sub-modules to prevent real google.adk from
# importing google.genai.errors (which isn't installed)
_stub("google.genai")
_stub("google.genai.types")
_stub("google.genai.errors")
_stub("vertexai")

# ADK — stub fully so the installed real package is never executed
_stub("google.adk")
_stub("google.adk.sessions")
_stub("google.adk.agents")
_stub("google.adk.runners")
_stub("google.adk.events")
_stub("google.adk.models")

# Anthropic
_stub("anthropic")

# pandas-gbq — reporting/bqstore.py
_stub("pandas_gbq")

# ── 4. Stub databases sub-modules with GCP init or Python 3.12 syntax ────────
# databases/spanner.py uses f-string syntax only valid in Python 3.12+.
# Pre-populating sys.modules prevents Python from ever reading the file.
for _sub in ["spanner", "alloydb", "alloydb_omni"]:
    _key = f"databases.{_sub}"
    if _key not in sys.modules:
        sys.modules[_key] = MagicMock()

"""Fixture for T-P1-03-04, copied as `lintpkg/modules/planning/_bad.py` into a throwaway
tree: planning reaching for the Generation slot, which `generation-callers` forbids."""

from tumnis.modules.decisions import generation_api

PLACEHOLDER = generation_api.placeholder_first_action

"""Read-only recommendation view of PR #10's canonical classification.

No duplicated code ranges or labels. Importing definitions does not migrate a DB.
"""
from teum.catalog.categories import CODES, GROUPS as CATALOG_GROUPS, GROUP_NAMES

TAXONOMY_VERSION = "teum-c21-v1"
GROUPS = {group: frozenset(codes) for group, codes in CATALOG_GROUPS.items()}
CATEGORY_CODES = frozenset(CODES)

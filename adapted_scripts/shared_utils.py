"""
Shared utilities for adapted scripts working with filtered_complete_chains_cleaned.jsonl

This module provides common functions for:
- Loading and parsing the new JSONL format
- Extracting licenses from metadata and scancode
- License categorization and normalization
- Chain building and upstream tracing
- Copyright text handling
- File type categorization
"""

import json
import re
from typing import Dict, List, Set, Tuple, Optional, Any
from collections import defaultdict


# ============================================================================
# File Loading
# ============================================================================

def load_jsonl_file(filepath: str) -> Dict[str, Dict]:
    """
    Load JSONL file with case-insensitive ID indexing.

    Note: There are ~161 ID collisions where the same ID is used for different entity types.
    This function keeps the last entity loaded for each ID. For type-safe lookup, use get_entity().

    Args:
        filepath: Path to the JSONL file

    Returns:
        Dictionary mapping lowercase IDs to entity data
    """
    entities = {}
    entities_by_type = {'dataset': {}, 'model': {}, 'application': {}}

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                entity = json.loads(line)
                entity_id = entity['id'].lower()
                entity_type = entity.get('type', 'unknown')

                # Store in both simple dict (for backward compatibility) and typed dict (for safety)
                entities[entity_id] = entity
                if entity_type in entities_by_type:
                    entities_by_type[entity_type][entity_id] = entity

    # Add the typed dicts as metadata
    entities['__by_type__'] = entities_by_type
    return entities


def get_entity(entities: Dict[str, Dict], entity_id: str, entity_type: str) -> Optional[Dict]:
    """
    Look up entity by ID and type (type-safe lookup that handles ID collisions).

    Args:
        entities: Dictionary from load_jsonl_file()
        entity_id: Entity ID (will be lowercased)
        entity_type: Entity type ('model', 'dataset', 'application')

    Returns:
        Entity dict if found, None otherwise
    """
    by_type = entities.get('__by_type__', {})
    if entity_type in by_type:
        return by_type[entity_type].get(entity_id.lower())
    return None


# ============================================================================
# License Extraction
# ============================================================================

def extract_licenses_metadata(entity: Dict) -> List[str]:
    """
    Extract licenses from metadata 'licenses' field.

    Args:
        entity: Entity dictionary with 'licenses' field

    Returns:
        List of license identifiers (may be empty)
    """
    return entity.get('licenses', [])


def extract_licenses_scancode(entity: Dict) -> List[str]:
    """
    Extract licenses from 'scancode' field.

    Args:
        entity: Entity dictionary with 'scancode' field

    Returns:
        List of unique license SPDX expressions
    """
    scancode = entity.get('scancode', [])
    if scancode is None:
        return []
    licenses = []
    for item in scancode:
        lic = item.get('license_expression_spdx')
        if lic and lic not in licenses:
            licenses.append(lic)
    return licenses


def has_target_license(licenses: List[str], targets: Set[str] = None) -> bool:
    """
    Check if entity has any of the target licenses (MIT/Apache/BSD).

    Args:
        licenses: List of license identifiers
        targets: Set of target licenses (default: MIT, Apache-2.0, BSD-3-Clause)

    Returns:
        True if any target license is present
    """
    if targets is None:
        targets = {'MIT', 'Apache-2.0', 'BSD-3-Clause'}

    # Normalize both to lowercase for comparison
    targets_lower = {t.lower() for t in targets}
    return any(lic.lower() in targets_lower for lic in licenses)


# ============================================================================
# License Categorization
# ============================================================================

# Standard license category mappings
LICENSE_CATEGORIES = {
    # Permissive
    'MIT': 'Permissive',
    'Apache-2.0': 'Permissive',
    'BSD-3-Clause': 'Permissive',
    'BSD-2-Clause': 'Permissive',
    'ISC': 'Permissive',
    '0BSD': 'Permissive',
    'Unlicense': 'Permissive',
    'WTFPL': 'Permissive',
    'Zlib': 'Permissive',

    # Copyleft - Strong
    'GPL-3.0': 'Copyleft-Strong',
    'GPL-2.0': 'Copyleft-Strong',
    'GPL-3.0-or-later': 'Copyleft-Strong',
    'GPL-2.0-or-later': 'Copyleft-Strong',
    'AGPL-3.0': 'Copyleft-Strong',
    'AGPL-3.0-or-later': 'Copyleft-Strong',

    # Copyleft - Weak
    'LGPL-3.0': 'Copyleft-Weak',
    'LGPL-2.1': 'Copyleft-Weak',
    'LGPL-3.0-or-later': 'Copyleft-Weak',
    'LGPL-2.1-or-later': 'Copyleft-Weak',
    'MPL-2.0': 'Copyleft-Weak',
    'EPL-1.0': 'Copyleft-Weak',
    'EPL-2.0': 'Copyleft-Weak',

    # ML-Specific
    'BigScience-RAIL-1.0': 'ML-Specific',
    'BigScience-OpenRAIL-M': 'ML-Specific',
    'CreativeML-OpenRAIL-M': 'ML-Specific',
    'bigscience-bloom-rail-1.0': 'ML-Specific',
    'bigcode-openrail-m': 'ML-Specific',
    'llama2': 'ML-Specific',
    'llama3': 'ML-Specific',
    'llama3.1': 'ML-Specific',
    'llama3.2': 'ML-Specific',
    'other-open-rail': 'ML-Specific',

    # Creative Commons
    'CC-BY-4.0': 'Creative-Commons',
    'CC-BY-SA-4.0': 'Creative-Commons',
    'CC-BY-3.0': 'Creative-Commons',
    'CC-BY-SA-3.0': 'Creative-Commons',
    'CC0-1.0': 'Creative-Commons',

    # Proprietary/Other
    'UNKNOWN': 'Other',
    'other': 'Other',
}


def categorize_license(license_id: str) -> str:
    """
    Categorize a license into standard groups.

    Args:
        license_id: SPDX license identifier

    Returns:
        Category string (e.g., 'Permissive', 'Copyleft-Strong', etc.)
    """
    # Direct lookup
    if license_id in LICENSE_CATEGORIES:
        return LICENSE_CATEGORIES[license_id]

    # Pattern matching for variations
    license_lower = license_id.lower()

    if 'gpl' in license_lower and 'lgpl' not in license_lower:
        return 'Copyleft-Strong'
    if 'lgpl' in license_lower or 'mpl' in license_lower or 'epl' in license_lower:
        return 'Copyleft-Weak'
    if any(x in license_lower for x in ['rail', 'llama', 'openai', 'bigscience', 'bigcode']):
        return 'ML-Specific'
    if 'cc-by' in license_lower or 'cc0' in license_lower:
        return 'Creative-Commons'
    if any(x in license_lower for x in ['mit', 'apache', 'bsd', 'isc']):
        return 'Permissive'

    return 'Other'


def categorize_licenses(licenses: List[str]) -> str:
    """
    Categorize a list of licenses, handling multiple licenses.

    Args:
        licenses: List of license identifiers

    Returns:
        Single category string (most restrictive wins)
    """
    if not licenses:
        return 'Empty'

    categories = [categorize_license(lic) for lic in licenses]

    # Priority order (most restrictive to least)
    priority = [
        'Copyleft-Strong',
        'Copyleft-Weak',
        'ML-Specific',
        'Creative-Commons',
        'Permissive',
        'Other',
        'Empty'
    ]

    for cat in priority:
        if cat in categories:
            return cat

    return 'Other'


# ============================================================================
# Chain Building and Tracing
# ============================================================================

def build_chains(entities: Dict[str, Dict], target_licenses: Set[str] = None) -> List[Tuple[str, str]]:
    """
    Build all application → model chains.

    Args:
        entities: Dictionary of all entities (lowercase IDs)
        target_licenses: Optional set of target licenses to filter by

    Returns:
        List of (app_id, model_id) tuples
    """
    chains = []

    for entity_id, entity in entities.items():
        if entity.get('type') != 'application':
            continue

        # Filter by licenses if specified
        if target_licenses is not None:
            licenses = extract_licenses_metadata(entity)
            if not has_target_license(licenses, target_licenses):
                continue

        # Get linked models
        models = entity.get('models', [])
        for model_id in models:
            chains.append((entity_id, model_id.lower()))

    return chains


def trace_upstream_models(model_id: str, entities: Dict[str, Dict],
                          visited: Set[str] = None) -> List[str]:
    """
    Recursively trace upstream base models with cycle detection.

    Args:
        model_id: Starting model ID (lowercase)
        entities: Dictionary of all entities
        visited: Set of already visited model IDs (for cycle detection)

    Returns:
        List of all upstream model IDs (including starting model)
    """
    if visited is None:
        visited = set()

    # Avoid cycles
    if model_id in visited:
        return []

    visited.add(model_id)
    upstream = [model_id]

    # Get the model entity (using type-safe lookup to avoid ID collisions)
    model = get_entity(entities, model_id, 'model')
    if not model:
        return upstream

    # Recursively trace base models
    base_models = model.get('base_models', [])
    for base_id in base_models:
        base_id_lower = base_id.lower()
        upstream.extend(trace_upstream_models(base_id_lower, entities, visited))

    return upstream


def get_training_datasets(model_id: str, entities: Dict[str, Dict]) -> List[str]:
    """
    Get training datasets for a model.

    Args:
        model_id: Model ID (lowercase)
        entities: Dictionary of all entities

    Returns:
        List of dataset IDs (lowercase)
    """
    # Use type-safe lookup to avoid ID collisions
    model = get_entity(entities, model_id, 'model')
    if not model:
        return []

    # New format uses 'datasets' field
    datasets = model.get('datasets', [])
    return [d.lower() for d in datasets]


# ============================================================================
# Copyright Handling
# ============================================================================

def normalize_copyright(text: str) -> str:
    """
    Normalize copyright text for matching.

    Args:
        text: Copyright statement text

    Returns:
        Normalized text (lowercase, whitespace collapsed, punctuation removed)
    """
    # Lowercase
    text = text.lower()

    # Remove common variations
    text = re.sub(r'copyright\s*(\(c\)|©|&copy;)?\s*', '', text)

    # Remove years (single or ranges)
    text = re.sub(r'\b\d{4}(\s*-\s*\d{4})?\b', '', text)

    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)

    # Remove punctuation
    text = re.sub(r'[^\w\s]', '', text)

    return text.strip()


def extract_copyrights(entity: Dict) -> List[str]:
    """
    Extract copyright statements from entity.

    Args:
        entity: Entity dictionary with 'copyrights' field

    Returns:
        List of copyright statement texts
    """
    copyrights = entity.get('copyrights', [])
    statements = []

    for item in copyrights:
        stmt = item.get('copyright', '')
        if stmt and stmt not in statements:
            statements.append(stmt)

    return statements


def extract_holders(entity: Dict) -> List[str]:
    """
    Extract copyright holders from entity.

    Args:
        entity: Entity dictionary with 'holders' field

    Returns:
        List of holder names
    """
    holders = entity.get('holders', [])
    names = []

    for item in holders:
        holder = item.get('holder', '')
        if holder and holder not in names:
            names.append(holder)

    return names


# ============================================================================
# File Type Categorization
# ============================================================================

def categorize_file_type(file_path: str) -> str:
    """
    Categorize file type based on path.

    Args:
        file_path: File path from scancode origins

    Returns:
        Category: 'LICENSE', 'README', 'NOTICE', 'SOURCE', or 'OTHER'
    """
    path_lower = file_path.lower()
    filename = path_lower.split('/')[-1]

    # LICENSE files
    if 'license' in path_lower or 'copying' in path_lower:
        return 'LICENSE'

    # README files
    if 'readme' in path_lower:
        return 'README'

    # NOTICE files
    if 'notice' in path_lower:
        return 'NOTICE'

    # Source code (common extensions)
    source_exts = ['.py', '.js', '.java', '.cpp', '.c', '.h', '.rs', '.go', '.ts', '.jsx', '.tsx']
    if any(filename.endswith(ext) for ext in source_exts):
        return 'SOURCE'

    return 'OTHER'


def has_full_license_text(entity: Dict, threshold: float = 90.0) -> bool:
    """
    Check if entity has full license text (match_coverage >= threshold).

    Args:
        entity: Entity dictionary with 'scancode' field
        threshold: Minimum match coverage percentage (default: 90.0)

    Returns:
        True if any license has match_coverage >= threshold
    """
    scancode = entity.get('scancode', [])
    if scancode is None:
        return False

    for item in scancode:
        origins = item.get('origins', [])
        for origin in origins:
            coverage = origin.get('match_coverage', 0.0)
            # Handle None case (different pattern found but no full match) - treat as 0
            if coverage is None:
                coverage = 0.0
            if coverage >= threshold:
                return True

    return False


# ============================================================================
# License Matrix Loading
# ============================================================================

def load_license_matrix(filepath: str = 'matrix.json') -> Dict:
    """
    Load license category transition matrix.

    Args:
        filepath: Path to matrix.json file

    Returns:
        Dictionary with license category mappings
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        # Return empty dict if file doesn't exist
        return {}


# ============================================================================
# Statistics Helpers
# ============================================================================

def count_by_type(entities: Dict[str, Dict]) -> Dict[str, int]:
    """
    Count entities by type.

    Args:
        entities: Dictionary of all entities

    Returns:
        Dictionary mapping type to count
    """
    # Use type-indexed data if available (preserves all entities without ID collisions)
    by_type = entities.get('__by_type__', {})
    if by_type:
        return {
            entity_type: len(entity_dict)
            for entity_type, entity_dict in by_type.items()
        }

    # Fallback to counting from main dict
    counts = defaultdict(int)
    for eid, entity in entities.items():
        if eid == '__by_type__':
            continue
        entity_type = entity.get('type', 'unknown')
        counts[entity_type] += 1
    return dict(counts)


def filter_by_type(entities: Dict[str, Dict], entity_type: str) -> Dict[str, Dict]:
    """
    Filter entities by type.

    Args:
        entities: Dictionary of all entities
        entity_type: Type to filter by ('dataset', 'model', 'application')

    Returns:
        Dictionary of entities of specified type
    """
    # Use type-indexed data if available for efficiency
    by_type = entities.get('__by_type__', {})
    if entity_type in by_type:
        return by_type[entity_type]

    # Fallback to filtering
    return {
        eid: entity
        for eid, entity in entities.items()
        if eid != '__by_type__' and entity.get('type') == entity_type
    }


def iter_all_entities(entities: Dict[str, Dict]):
    """
    Iterate through all entities without ID collisions.

    Yields all entities from the collision-free __by_type__ storage.

    Args:
        entities: Dictionary from load_jsonl_file()

    Yields:
        Tuple of (entity_id, entity_dict) for each entity
    """
    by_type = entities.get('__by_type__', {})
    if by_type:
        # Iterate through type-indexed storage (no collisions)
        for entity_type, entity_dict in by_type.items():
            for entity_id, entity in entity_dict.items():
                yield entity_id, entity
    else:
        # Fallback to main dict
        for entity_id, entity in entities.items():
            if entity_id != '__by_type__':
                yield entity_id, entity


def get_popular_entities(entities: Dict[str, Dict], n: int = 10,
                         entity_type: str = None) -> List[Tuple[str, int]]:
    """
    Get most popular entities by likes.

    Args:
        entities: Dictionary of all entities
        n: Number of top entities to return
        entity_type: Optional type filter

    Returns:
        List of (entity_id, likes) tuples sorted by likes descending
    """
    filtered = entities
    if entity_type:
        filtered = filter_by_type(entities, entity_type)

    # Sort by likes
    sorted_entities = sorted(
        [(eid, entity.get('likes', 0)) for eid, entity in filtered.items() if eid != '__by_type__'],
        key=lambda x: x[1],
        reverse=True
    )

    return sorted_entities[:n]


# ============================================================================
# Output Formatting
# ============================================================================

def format_percentage(numerator: int, denominator: int, decimals: int = 2) -> str:
    """
    Format a percentage with specified decimal places.

    Args:
        numerator: Numerator value
        denominator: Denominator value
        decimals: Number of decimal places

    Returns:
        Formatted percentage string (e.g., "12.34%")
    """
    if denominator == 0:
        return "0.00%"

    percentage = (numerator / denominator) * 100
    return f"{percentage:.{decimals}f}%"


def format_count_with_percentage(count: int, total: int, decimals: int = 2) -> str:
    """
    Format count with percentage.

    Args:
        count: Count value
        total: Total value
        decimals: Number of decimal places for percentage

    Returns:
        Formatted string (e.g., "123 (12.34%)")
    """
    pct = format_percentage(count, total, decimals)
    return f"{count:,} ({pct})"

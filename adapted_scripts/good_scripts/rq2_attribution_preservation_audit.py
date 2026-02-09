#!/usr/bin/env python3
"""
Copyright Attribution Audit: Dataset → Model → Application

For every permissive (MIT, BSD-3-Clause, Apache-2.0) dataset that has a copyright notice:
1. Check if the copyright is preserved downstream in any model linked via the model's `datasets` field
2. For each such model, check if any application (linked via app's `models` field) preserves the dataset copyright
3. Report preservation rates at dataset-level, link-level, and full-chain-level

Uses shared_utils.normalize_copyright + substring matching.


"""

import sys
from pathlib import Path
import os
import json
from collections import defaultdict
from io import StringIO

# Add parent directory to path for shared_utils import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shared_utils

# ============================================================
# CONFIGURATION
# ============================================================
# Auto-detect base directory (works on any system)
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent  # adapted_scripts/
DATA_DIR = BASE_DIR.parent  # main directory with JSONL file
DATA_FILE = DATA_DIR / 'filtered_complete_chains_cleaned.jsonl'
PERMISSIVE_LICENSES = {'MIT', 'Apache-2.0', 'BSD-3-Clause'}
FULL_TEXT_THRESHOLD = 90.0
OUTPUT_FILE = DATA_DIR / 'outputs_new' / 'good_scripts' / 'copyright_attribution_audit.md'


# ============================================================
# COPYRIGHT HELPERS
# ============================================================

def has_copyright_notice(entity):
    """Check if entity has valid copyright notice (matches integrity audit logic)."""
    copyrights = entity.get('copyrights', [])
    return len(copyrights) > 0


def get_copyright_statements(entity):
    """
    Extract all copyright statements from an entity.

    Returns:
        list: List of normalized copyright statements
    """
    statements = []
    copyrights = entity.get('copyrights', [])

    for copyright_obj in copyrights:
        statement = copyright_obj.get('copyright', '')
        if statement:
            normalized = shared_utils.normalize_copyright(statement)
            if normalized:
                statements.append(normalized)

    return statements


def check_copyright_preserved(upstream_statements, downstream_statements):
    """
    Check if ANY upstream copyright appears in ANY downstream copyright.

    Args:
        upstream_statements: List of normalized upstream copyright statements
        downstream_statements: List of normalized downstream copyright statements

    Returns:
        bool: True if any upstream copyright is found in downstream
    """
    if not upstream_statements or not downstream_statements:
        return False

    for upstream in upstream_statements:
        for downstream in downstream_statements:
            if upstream in downstream or downstream in upstream:
                return True

    return False


def has_permissive_license(entity):
    """Check if entity has at least one permissive license in its licenses metadata."""
    licenses = shared_utils.extract_licenses_metadata(entity)
    return shared_utils.has_target_license(licenses, PERMISSIVE_LICENSES)


def get_all_downstream_apps(model_id_lower, model_to_apps, base_model_to_children, visited=None):
    """
    Get all apps reachable from a model, including through child models
    (models that use this as a base_model).
    
    Args:
        model_id_lower: lowercased model ID
        model_to_apps: direct model→app reverse index
        base_model_to_children: base_model→child_model reverse index
        visited: cycle detection set
    
    Returns:
        list of app entities
    """
    if visited is None:
        visited = set()
    if model_id_lower in visited:
        return []
    visited.add(model_id_lower)
    
    apps = list(model_to_apps.get(model_id_lower, []))
    
    # Also get apps from child models (models derived from this one)
    for child_entity in base_model_to_children.get(model_id_lower, []):
        child_id = child_entity['id'].lower()
        apps.extend(get_all_downstream_apps(child_id, model_to_apps, base_model_to_children, visited))
    
    return apps


def has_full_permissive_license_text(entity, threshold=90.0):
    """
    Check if entity has match_coverage >= threshold on a scancode entry
    that contains a permissive license (MIT, Apache-2.0, BSD-3-Clause).

    Careful: match_coverage can be null or 0.

    Returns:
        bool: True if any permissive scancode entry has coverage >= threshold
    """
    permissive_lower = {p.lower() for p in PERMISSIVE_LICENSES}
    scancode = entity.get('scancode', [])
    if scancode is None:
        return False

    for item in scancode:
        spdx = item.get('license_expression_spdx', '')
        if not spdx:
            continue

        # Check if this scancode entry references a permissive license
        # Handle compound expressions like "MIT AND Apache-2.0"
        spdx_lower = spdx.lower()
        is_permissive = any(p in spdx_lower for p in permissive_lower)
        if not is_permissive:
            continue

        # Check if any origin has sufficient coverage
        for origin in item.get('origins', []):
            coverage = origin.get('match_coverage')
            if coverage is None:
                continue
            if coverage >= threshold:
                return True

    return False


# ============================================================
# MAIN AUDIT
# ============================================================

def main():
    print("=" * 80)
    print("COPYRIGHT ATTRIBUTION AUDIT: Dataset → Model → Application")
    print("=" * 80)
    print()

    # ----------------------------------------------------------
    # 1. Load data and build indexes
    # ----------------------------------------------------------
    print("Loading unified dataset...")
    entities = shared_utils.load_jsonl_file(DATA_FILE)

    by_type = entities.get('__by_type__', {})
    datasets_dict = by_type.get('dataset', {})
    models_dict = by_type.get('model', {})
    apps_dict = by_type.get('application', {})

    print(f"Loaded: {len(datasets_dict):,} datasets, "
          f"{len(models_dict):,} models, {len(apps_dict):,} applications")
    print()

    # Build reverse indexes for supply chain links
    # dataset_id (lower) → [model entities]
    dataset_to_models = defaultdict(list)
    for model_id, model_entity in models_dict.items():
        for ds_id in model_entity.get('datasets', []):
            dataset_to_models[ds_id.lower()].append(model_entity)

    # model_id (lower) → [app entities]
    model_to_apps = defaultdict(list)
    for app_id, app_entity in apps_dict.items():
        for m_id in app_entity.get('models', []):
            model_to_apps[m_id.lower()].append(app_entity)

    # base_model_id (lower) → [child model entities] (models derived from this base)
    base_model_to_children = defaultdict(list)
    for model_id, model_entity in models_dict.items():
        for bm_id in model_entity.get('base_models', []):
            base_model_to_children[bm_id.lower()].append(model_entity)

    # ----------------------------------------------------------
    # 2. Identify fully compliant permissive datasets
    #    (permissive license + copyright + match_coverage >= 90%)
    # ----------------------------------------------------------
    permissive_datasets_total = 0
    permissive_ds_with_copyright = 0
    permissive_ds_with_full_text = 0
    permissive_ds_fully_compliant = {}  # id -> {'entity': ..., 'copyrights': [...]}

    for ds_id, ds_entity in datasets_dict.items():
        if not has_permissive_license(ds_entity):
            continue
        permissive_datasets_total += 1
        has_cr = has_copyright_notice(ds_entity)
        has_ft = shared_utils.has_full_license_text(ds_entity, FULL_TEXT_THRESHOLD)
        if has_cr:
            permissive_ds_with_copyright += 1
        if has_ft:
            permissive_ds_with_full_text += 1
        if has_cr and has_ft:
            stmts = get_copyright_statements(ds_entity)
            permissive_ds_fully_compliant[ds_id] = {
                'entity': ds_entity,
                'copyrights': stmts
            }

    print(f"Permissive datasets (MIT/BSD-3/Apache-2.0): {permissive_datasets_total:,}")
    print(f"  With copyright notices:                  {permissive_ds_with_copyright:,}")
    print(f"  With full license text (≥90%): {permissive_ds_with_full_text:,}")
    print(f"  Fully compliant (copyright + full text):  {len(permissive_ds_fully_compliant):,}")
    print()

    # ----------------------------------------------------------
    # 3. Trace downstream: dataset → model → application
    # ----------------------------------------------------------
    # Dataset-level counters
    ds_with_downstream_model = 0
    ds_preserved_in_any_model = 0
    ds_preserved_in_any_app = 0

    # Link-level counters
    total_ds_model_links = 0
    total_ds_model_preserved = 0

    # Chain-level counters (dataset → model → app)
    total_ds_app_chains = 0
    total_ds_app_preserved = 0

    # Track unique entities involved in comparison
    unique_datasets_in_comparison = set()
    unique_models_in_comparison = set()
    unique_apps_in_comparison = set()

    # Track apps where dataset copyright was preserved
    apps_preserving_dataset_copyright = set()

    # For detailed output
    per_dataset_details = []

    for ds_id, ds_info in permissive_ds_fully_compliant.items():
        ds_copyrights = ds_info['copyrights']
        ds_entity = ds_info['entity']

        linked_models = dataset_to_models.get(ds_id, [])
        if not linked_models:
            continue

        ds_with_downstream_model += 1
        unique_datasets_in_comparison.add(ds_entity['id'])

        any_model_preserved = False
        any_app_preserved = False
        n_model_preserved = 0
        n_model_not = 0
        n_app_preserved = 0
        n_app_not = 0

        for model_entity in linked_models:
            total_ds_model_links += 1
            unique_models_in_comparison.add(model_entity['id'])
            model_copyrights = get_copyright_statements(model_entity)
            m_preserved = check_copyright_preserved(ds_copyrights, model_copyrights)

            if m_preserved:
                total_ds_model_preserved += 1
                n_model_preserved += 1
                any_model_preserved = True
            else:
                n_model_not += 1

            # Trace to applications (direct + through child models)
            linked_apps_all = get_all_downstream_apps(model_entity['id'].lower(), model_to_apps, base_model_to_children)
            # Deduplicate by app ID
            seen_app_ids = set()
            linked_apps = []
            for app in linked_apps_all:
                aid = app['id'].lower()
                if aid not in seen_app_ids:
                    seen_app_ids.add(aid)
                    linked_apps.append(app)

            for app_entity in linked_apps:
                total_ds_app_chains += 1
                unique_apps_in_comparison.add(app_entity['id'])
                app_copyrights = get_copyright_statements(app_entity)
                a_preserved = check_copyright_preserved(ds_copyrights, app_copyrights)

                if a_preserved:
                    total_ds_app_preserved += 1
                    n_app_preserved += 1
                    any_app_preserved = True
                    apps_preserving_dataset_copyright.add(app_entity['id'])
                else:
                    n_app_not += 1

        if any_model_preserved:
            ds_preserved_in_any_model += 1
        if any_app_preserved:
            ds_preserved_in_any_app += 1

        per_dataset_details.append({
            'dataset_id': ds_entity['id'],
            'licenses': ds_entity.get('licenses', []),
            'copyrights_raw': [c.get('copyright', '') for c in ds_entity.get('copyrights', [])],
            'copyrights_normalized': ds_copyrights,
            'n_linked_models': len(linked_models),
            'n_model_preserved': n_model_preserved,
            'n_model_not': n_model_not,
            'n_linked_apps': n_app_preserved + n_app_not,
            'n_app_preserved': n_app_preserved,
            'n_app_not': n_app_not,
            'any_model_preserved': any_model_preserved,
            'any_app_preserved': any_app_preserved,
        })

    # ----------------------------------------------------------
    # 4. SEPARATE ANALYSIS: Model → Application copyright preservation
    # ----------------------------------------------------------
    # Starting from fully compliant permissive models
    # (permissive license + copyright + match_coverage >= 90%)
    permissive_models_total = 0
    permissive_models_with_copyright = 0
    permissive_models_with_full_text = 0
    permissive_models_fully_compliant = {}  # id -> {'entity': ..., 'copyrights': [...]}

    for m_id, m_entity in models_dict.items():
        if not has_permissive_license(m_entity):
            continue
        permissive_models_total += 1
        has_cr = has_copyright_notice(m_entity)
        has_ft = shared_utils.has_full_license_text(m_entity, FULL_TEXT_THRESHOLD)
        if has_cr:
            permissive_models_with_copyright += 1
        if has_ft:
            permissive_models_with_full_text += 1
        if has_cr and has_ft:
            stmts = get_copyright_statements(m_entity)
            permissive_models_fully_compliant[m_id] = {
                'entity': m_entity,
                'copyrights': stmts
            }

    # Trace downstream to applications
    m2a_with_downstream_app = 0
    m2a_preserved_in_any_app = 0

    m2a_total_links = 0
    m2a_total_preserved = 0

    m2a_unique_models = set()
    m2a_unique_apps = set()
    m2a_unique_apps_permissive = set()

    # Track apps where model copyright was preserved
    apps_preserving_model_copyright = set()

    m2a_per_model_details = []
    m2a_direct_only = 0
    m2a_via_base_model = 0  # models with no direct apps but connected via children

    for m_id, m_info in permissive_models_fully_compliant.items():
        m_copyrights = m_info['copyrights']
        m_entity = m_info['entity']

        # Get apps via direct connection AND through child models (base_model chain)
        linked_apps_direct = model_to_apps.get(m_id, [])
        linked_apps_all = get_all_downstream_apps(m_id, model_to_apps, base_model_to_children)
        
        # Deduplicate by app ID
        seen_app_ids = set()
        linked_apps = []
        for app in linked_apps_all:
            aid = app['id'].lower()
            if aid not in seen_app_ids:
                seen_app_ids.add(aid)
                linked_apps.append(app)

        if not linked_apps:
            continue

        m2a_with_downstream_app += 1
        if linked_apps_direct:
            m2a_direct_only += 1
        else:
            m2a_via_base_model += 1
        m2a_unique_models.add(m_entity['id'])

        any_app_preserved = False
        n_app_preserved = 0
        n_app_not = 0

        for app_entity in linked_apps:
            m2a_total_links += 1
            m2a_unique_apps.add(app_entity['id'])
            app_copyrights = get_copyright_statements(app_entity)
            a_preserved = check_copyright_preserved(m_copyrights, app_copyrights)

            if a_preserved:
                m2a_total_preserved += 1
                n_app_preserved += 1
                any_app_preserved = True
                apps_preserving_model_copyright.add(app_entity['id'])
            else:
                n_app_not += 1

        if any_app_preserved:
            m2a_preserved_in_any_app += 1

        m2a_per_model_details.append({
            'model_id': m_entity['id'],
            'licenses': m_entity.get('licenses', []),
            'copyrights_raw': [c.get('copyright', '') for c in m_entity.get('copyrights', [])],
            'copyrights_normalized': m_copyrights,
            'n_linked_apps': len(linked_apps),
            'n_app_preserved': n_app_preserved,
            'n_app_not': n_app_not,
            'any_app_preserved': any_app_preserved,
        })

    # Permissive breakdown for model→app unique apps
    for aid in m2a_unique_apps:
        a_entity = shared_utils.get_entity(entities, aid, 'application')
        if a_entity and has_permissive_license(a_entity):
            m2a_unique_apps_permissive.add(aid)

    # ----------------------------------------------------------
    # 5. Print results
    # ----------------------------------------------------------
    def pct(num, denom):
        return f"{num/max(denom,1)*100:.2f}%"

    print("=" * 80)
    print("RESULTS: Dataset → Model Copyright Preservation")
    print("=" * 80)
    print()
    print("Population flow:")
    print(f"  Total datasets:                          {len(datasets_dict):>6,}")
    print(f"  → Permissive (MIT/BSD-3/Apache-2.0):     {permissive_datasets_total:>6,}")
    print(f"  → With copyright notices:                {permissive_ds_with_copyright:>6,}")
    print(f"  → With full license text:     {permissive_ds_with_full_text:>6,}")
    print(f"  → Fully compliant (copyright+full text): {len(permissive_ds_fully_compliant):>6,}")
    print(f"  → With ≥1 downstream model:              {ds_with_downstream_model:>6,}")
    print()
    print("Dataset-level (does ANY linked model preserve the copyright?):")
    print(f"  Preserved in ≥1 model:  {ds_preserved_in_any_model:>6,} ({pct(ds_preserved_in_any_model, ds_with_downstream_model)})")
    print(f"  Not preserved anywhere: {ds_with_downstream_model - ds_preserved_in_any_model:>6,} ({pct(ds_with_downstream_model - ds_preserved_in_any_model, ds_with_downstream_model)})")
    print()
    print("Link-level (each dataset→model pair):")
    print(f"  Total dataset→model links: {total_ds_model_links:,}")
    print(f"  Copyright preserved:       {total_ds_model_preserved:>8,} ({pct(total_ds_model_preserved, total_ds_model_links)})")
    print(f"  Copyright NOT preserved:   {total_ds_model_links - total_ds_model_preserved:>8,} ({pct(total_ds_model_links - total_ds_model_preserved, total_ds_model_links)})")
    print()
    print(f"Unique datasets in this comparison: {len(unique_datasets_in_comparison):,}")
    print(f"Unique models in this comparison: {len(unique_models_in_comparison):,}")
    print()

    print("=" * 80)
    print("RESULTS: Dataset → Model → Application Copyright Preservation")
    print("=" * 80)
    print()
    print("Population flow (same starting datasets, tracing through to apps):")
    print(f"  Fully compliant datasets:                {len(permissive_ds_fully_compliant):>6,}")
    print(f"  → With ≥1 downstream model:              {ds_with_downstream_model:>6,}")
    print(f"  → Unique models reached:                 {len(unique_models_in_comparison):>6,}")
    print(f"  → Unique applications reached:           {len(unique_apps_in_comparison):>6,}")
    print()
    print("Dataset-level (does ANY linked app preserve the dataset copyright?):")
    print(f"  Preserved in ≥1 app:    {ds_preserved_in_any_app:>6,} ({pct(ds_preserved_in_any_app, ds_with_downstream_model)})")
    print(f"  Not preserved anywhere: {ds_with_downstream_model - ds_preserved_in_any_app:>6,} ({pct(ds_with_downstream_model - ds_preserved_in_any_app, ds_with_downstream_model)})")
    print()
    print("Chain-level (each dataset→model→app chain):")
    print(f"  Total dataset→model→app chains: {total_ds_app_chains:,}")
    print(f"  Copyright preserved:            {total_ds_app_preserved:>8,} ({pct(total_ds_app_preserved, total_ds_app_chains)})")
    print(f"  Copyright NOT preserved:        {total_ds_app_chains - total_ds_app_preserved:>8,} ({pct(total_ds_app_chains - total_ds_app_preserved, total_ds_app_chains)})")
    print(f"  Unique apps preserving:         {len(apps_preserving_dataset_copyright):>8,}")
    print()

    # ----------------------------------------------------------
    # Model → Application results (separate analysis)
    # ----------------------------------------------------------
    print("=" * 80)
    print("RESULTS: Model → Application Copyright Preservation (SEPARATE ANALYSIS)")
    print("=" * 80)
    print()
    print("Population flow:")
    print(f"  Total models:                            {len(models_dict):>6,}")
    print(f"  → Permissive (MIT/BSD-3/Apache-2.0):     {permissive_models_total:>6,}")
    print(f"  → With copyright notices:                {permissive_models_with_copyright:>6,}")
    print(f"  → With full license text:     {permissive_models_with_full_text:>6,}")
    print(f"  → Fully compliant (copyright+full text): {len(permissive_models_fully_compliant):>6,}")
    print(f"  → With ≥1 downstream app:                {m2a_with_downstream_app:>6,} (direct: {m2a_direct_only:,}, via base_model chain: {m2a_via_base_model:,})")
    print()
    print("Model-level (does ANY linked app preserve the copyright?):")
    print(f"  Preserved in ≥1 app:    {m2a_preserved_in_any_app:>6,} ({pct(m2a_preserved_in_any_app, m2a_with_downstream_app)})")
    print(f"  Not preserved anywhere: {m2a_with_downstream_app - m2a_preserved_in_any_app:>6,} ({pct(m2a_with_downstream_app - m2a_preserved_in_any_app, m2a_with_downstream_app)})")
    print()
    print("Link-level (each model→app pair):")
    print(f"  Total model→app links:   {m2a_total_links:,}")
    print(f"  Copyright preserved:     {m2a_total_preserved:>8,} ({pct(m2a_total_preserved, m2a_total_links)})")
    print(f"  Copyright NOT preserved: {m2a_total_links - m2a_total_preserved:>8,} ({pct(m2a_total_links - m2a_total_preserved, m2a_total_links)})")
    print(f"  Unique apps preserving:  {len(apps_preserving_model_copyright):>8,}")
    print()
    print("Unique entities in this comparison:")
    print(f"  Models:       {len(m2a_unique_models):>6,}")
    print(f"  Applications: {len(m2a_unique_apps):>6,} (permissive: {len(m2a_unique_apps_permissive):,} / {len(m2a_unique_apps):,}, {pct(len(m2a_unique_apps_permissive), len(m2a_unique_apps))})")
    print()

    # ----------------------------------------------------------
    # 5. End-to-end summary
    # ----------------------------------------------------------
    print("=" * 80)
    print("END-TO-END SUMMARY")
    print("=" * 80)
    print()
    print(f"Starting population: {permissive_datasets_total:,} permissive datasets")
    print(f"  → With copyright notices:                {permissive_ds_with_copyright:,}")
    print(f"  → With full license text:      {permissive_ds_with_full_text:,}")
    print(f"  → Fully compliant (copyright + full text): {len(permissive_ds_fully_compliant):,}")
    print(f"  → With ≥1 downstream model:               {ds_with_downstream_model:,}")
    print(f"  → Copyright preserved in ≥1 model:        {ds_preserved_in_any_model:,} ({pct(ds_preserved_in_any_model, ds_with_downstream_model)})")
    print(f"  → Copyright preserved in ≥1 app:          {ds_preserved_in_any_app:,} ({pct(ds_preserved_in_any_app, ds_with_downstream_model)})")
    print()

    print("Unique entities involved in comparison:")
    print(f"  Datasets:     {len(unique_datasets_in_comparison):>6,}")

    # Count permissive among unique models
    unique_models_permissive = set()
    for mid in unique_models_in_comparison:
        m_entity = shared_utils.get_entity(entities, mid, 'model')
        if m_entity and has_permissive_license(m_entity):
            unique_models_permissive.add(mid)

    # Count permissive among unique apps
    unique_apps_permissive = set()
    for aid in unique_apps_in_comparison:
        a_entity = shared_utils.get_entity(entities, aid, 'application')
        if a_entity and has_permissive_license(a_entity):
            unique_apps_permissive.add(aid)

    print(f"  Models:       {len(unique_models_in_comparison):>6,} (permissive: {len(unique_models_permissive):,} / {len(unique_models_in_comparison):,}, {pct(len(unique_models_permissive), len(unique_models_in_comparison))})")
    print(f"  Applications: {len(unique_apps_in_comparison):>6,} (permissive: {len(unique_apps_permissive):,} / {len(unique_apps_in_comparison):,}, {pct(len(unique_apps_permissive), len(unique_apps_in_comparison))})")
    print()

    # ----------------------------------------------------------
    # Combined: Fully compliant dataset → Fully compliant model → App
    # ----------------------------------------------------------
    # Find chains where a fully compliant dataset connects to a fully compliant model
    # Then check which apps preserve copyright from BOTH the dataset AND the model

    combo_datasets = set()
    combo_models = set()
    combo_apps = set()
    combo_apps_preserves_ds = set()
    combo_apps_preserves_model = set()
    combo_apps_preserves_both = set()
    combo_total_chains = 0
    combo_chains_both_preserved = 0
    combo_chains_ds_preserved = 0
    combo_chains_m_preserved = 0
    combo_chains_either_preserved = 0

    for ds_id, ds_info in permissive_ds_fully_compliant.items():
        ds_copyrights = ds_info['copyrights']
        ds_entity = ds_info['entity']

        linked_models = dataset_to_models.get(ds_id, [])
        for model_entity in linked_models:
            m_id = model_entity['id'].lower()

            # Only follow if the model is ALSO fully compliant
            if m_id not in permissive_models_fully_compliant:
                continue

            m_info = permissive_models_fully_compliant[m_id]
            m_copyrights = m_info['copyrights']

            combo_datasets.add(ds_entity['id'])
            combo_models.add(model_entity['id'])

            # Get all downstream apps (direct + via base_model chain)
            linked_apps_all = get_all_downstream_apps(m_id, model_to_apps, base_model_to_children)
            seen_app_ids = set()
            linked_apps = []
            for app in linked_apps_all:
                aid = app['id'].lower()
                if aid not in seen_app_ids:
                    seen_app_ids.add(aid)
                    linked_apps.append(app)

            for app_entity in linked_apps:
                combo_total_chains += 1
                combo_apps.add(app_entity['id'])
                app_copyrights = get_copyright_statements(app_entity)

                ds_preserved = check_copyright_preserved(ds_copyrights, app_copyrights)
                m_preserved = check_copyright_preserved(m_copyrights, app_copyrights)

                if ds_preserved:
                    combo_apps_preserves_ds.add(app_entity['id'])
                    combo_chains_ds_preserved += 1
                if m_preserved:
                    combo_apps_preserves_model.add(app_entity['id'])
                    combo_chains_m_preserved += 1
                if ds_preserved or m_preserved:
                    combo_chains_either_preserved += 1
                if ds_preserved and m_preserved:
                    combo_apps_preserves_both.add(app_entity['id'])
                    combo_chains_both_preserved += 1

    # Also keep the simple OR/AND from the two independent analyses
    apps_preserving_either = apps_preserving_dataset_copyright | apps_preserving_model_copyright
    apps_preserving_both_independent = apps_preserving_dataset_copyright & apps_preserving_model_copyright
    all_apps_in_both_analyses = unique_apps_in_comparison | m2a_unique_apps

    print("=" * 80)
    print("COMBINED: Fully Compliant Dataset → Fully Compliant Model → Application")
    print("=" * 80)
    print()
    print("Population flow:")
    print(f"  Fully compliant datasets:                {len(permissive_ds_fully_compliant):>6,}")
    print(f"  Fully compliant models:                  {len(permissive_models_fully_compliant):>6,}")
    print(f"  Connected ds→model pairs (both compliant): {len(combo_datasets):>4,} datasets → {len(combo_models):,} models")
    print(f"  Unique applications reached:             {len(combo_apps):>6,}")
    print(f"  Total chains (ds→model→app):             {combo_total_chains:>6,}")
    print()
    print(f"Apps preserving dataset copyright:          {len(combo_apps_preserves_ds):>6,} / {len(combo_apps):,} ({pct(len(combo_apps_preserves_ds), len(combo_apps))})")
    print(f"Apps preserving model copyright:            {len(combo_apps_preserves_model):>6,} / {len(combo_apps):,} ({pct(len(combo_apps_preserves_model), len(combo_apps))})")
    print(f"Apps preserving BOTH (ds AND model):        {len(combo_apps_preserves_both):>6,} / {len(combo_apps):,} ({pct(len(combo_apps_preserves_both), len(combo_apps))})")
    print(f"Apps preserving EITHER (ds OR model):       {len(combo_apps_preserves_ds | combo_apps_preserves_model):>6,} / {len(combo_apps):,} ({pct(len(combo_apps_preserves_ds | combo_apps_preserves_model), len(combo_apps))})")
    print()
    print("Chain-level (each ds→model→app triple):")
    print(f"  Total chains:              {combo_total_chains:>6,}")
    print(f"  DS copyright preserved:    {combo_chains_ds_preserved:>6,} ({pct(combo_chains_ds_preserved, combo_total_chains)})")
    print(f"  Model copyright preserved: {combo_chains_m_preserved:>6,} ({pct(combo_chains_m_preserved, combo_total_chains)})")
    print(f"  BOTH preserved:            {combo_chains_both_preserved:>6,} ({pct(combo_chains_both_preserved, combo_total_chains)})")
    print(f"  EITHER preserved:          {combo_chains_either_preserved:>6,} ({pct(combo_chains_either_preserved, combo_total_chains)})")
    print(f"  NEITHER preserved:         {combo_total_chains - combo_chains_either_preserved:>6,} ({pct(combo_total_chains - combo_chains_either_preserved, combo_total_chains)})")
    print()

    # ----------------------------------------------------------
    # Abstracted: Upstream → App link-level analysis
    # Both dataset→app and model→app as comparable pairs
    # ----------------------------------------------------------

    # Dataset→App links (deduplicated: each unique ds,app pair counted once)
    ds_app_links = {}  # (ds_id, app_id) -> preserved bool
    for ds_id, ds_info in permissive_ds_fully_compliant.items():
        ds_copyrights = ds_info['copyrights']
        ds_entity = ds_info['entity']

        linked_models = dataset_to_models.get(ds_id, [])
        for model_entity in linked_models:
            linked_apps_all = get_all_downstream_apps(model_entity['id'].lower(), model_to_apps, base_model_to_children)
            seen_app_ids = set()
            for app in linked_apps_all:
                aid = app['id'].lower()
                if aid in seen_app_ids:
                    continue
                seen_app_ids.add(aid)

                pair_key = (ds_entity['id'], app['id'])
                if pair_key not in ds_app_links:
                    app_copyrights = get_copyright_statements(app)
                    preserved = check_copyright_preserved(ds_copyrights, app_copyrights)
                    ds_app_links[pair_key] = preserved

    ds_app_total = len(ds_app_links)
    ds_app_preserved = sum(1 for v in ds_app_links.values() if v)

    # Model→App links (deduplicated: each unique model,app pair counted once)
    m_app_links = {}  # (model_id, app_id) -> preserved bool
    for m_id, m_info in permissive_models_fully_compliant.items():
        m_copyrights = m_info['copyrights']
        m_entity = m_info['entity']

        linked_apps_all = get_all_downstream_apps(m_id, model_to_apps, base_model_to_children)
        seen_app_ids = set()
        for app in linked_apps_all:
            aid = app['id'].lower()
            if aid in seen_app_ids:
                continue
            seen_app_ids.add(aid)

            pair_key = (m_entity['id'], app['id'])
            if pair_key not in m_app_links:
                app_copyrights = get_copyright_statements(app)
                preserved = check_copyright_preserved(m_copyrights, app_copyrights)
                m_app_links[pair_key] = preserved

    m_app_total = len(m_app_links)
    m_app_preserved = sum(1 for v in m_app_links.values() if v)

    # Combined upstream→app links
    total_upstream_app_links = ds_app_total + m_app_total
    total_upstream_app_preserved = ds_app_preserved + m_app_preserved

    # Unique apps across both
    ds_app_unique_apps = set(pair[1] for pair in ds_app_links.keys())
    m_app_unique_apps = set(pair[1] for pair in m_app_links.keys())
    all_upstream_apps = ds_app_unique_apps | m_app_unique_apps

    # Per-app: preserved from dataset, from model, from both
    ds_preserving_apps = set(pair[1] for pair, v in ds_app_links.items() if v)
    m_preserving_apps = set(pair[1] for pair, v in m_app_links.items() if v)
    upstream_either = ds_preserving_apps | m_preserving_apps
    upstream_both = ds_preserving_apps & m_preserving_apps

    print("=" * 80)
    print("UPSTREAM → APP LINK ANALYSIS (Dataset→App + Model→App as comparable pairs)")
    print("=" * 80)
    print()
    print("Dataset→App links (deduplicated dataset,app pairs):")
    print(f"  Total links:       {ds_app_total:>6,}")
    print(f"  Preserved:         {ds_app_preserved:>6,} ({pct(ds_app_preserved, ds_app_total)})")
    print(f"  Not preserved:     {ds_app_total - ds_app_preserved:>6,} ({pct(ds_app_total - ds_app_preserved, ds_app_total)})")
    print(f"  Unique apps:       {len(ds_app_unique_apps):>6,}")
    print()
    print("Model→App links (deduplicated model,app pairs):")
    print(f"  Total links:       {m_app_total:>6,}")
    print(f"  Preserved:         {m_app_preserved:>6,} ({pct(m_app_preserved, m_app_total)})")
    print(f"  Not preserved:     {m_app_total - m_app_preserved:>6,} ({pct(m_app_total - m_app_preserved, m_app_total)})")
    print(f"  Unique apps:       {len(m_app_unique_apps):>6,}")
    print()
    print("Combined upstream→app links:")
    print(f"  Total links (ds→app + model→app): {total_upstream_app_links:>6,}")
    print(f"  Preserved:                        {total_upstream_app_preserved:>6,} ({pct(total_upstream_app_preserved, total_upstream_app_links)})")
    print(f"  Not preserved:                    {total_upstream_app_links - total_upstream_app_preserved:>6,} ({pct(total_upstream_app_links - total_upstream_app_preserved, total_upstream_app_links)})")
    print()
    print(f"Unique apps across both:             {len(all_upstream_apps):>6,}")
    print(f"Apps preserving dataset copyright:   {len(ds_preserving_apps):>6,} / {len(all_upstream_apps):,} ({pct(len(ds_preserving_apps), len(all_upstream_apps))})")
    print(f"Apps preserving model copyright:     {len(m_preserving_apps):>6,} / {len(all_upstream_apps):,} ({pct(len(m_preserving_apps), len(all_upstream_apps))})")
    print(f"Apps preserving EITHER (union):      {len(upstream_either):>6,} / {len(all_upstream_apps):,} ({pct(len(upstream_either), len(all_upstream_apps))})")
    print(f"Apps preserving BOTH (intersection): {len(upstream_both):>6,} / {len(all_upstream_apps):,} ({pct(len(upstream_both), len(all_upstream_apps))})")
    print()

    print("=" * 80)
    print("INDEPENDENT ANALYSES: Application-Level Summary")
    print("=" * 80)
    print()
    print(f"Total applications:                        {len(apps_dict):>6,}")
    print(f"Unique apps in ds→model→app analysis:      {len(unique_apps_in_comparison):>6,}")
    print(f"Unique apps in model→app analysis:         {len(m2a_unique_apps):>6,}")
    print(f"Union (total unique across both):           {len(all_apps_in_both_analyses):>6,}")
    print()
    print(f"Apps preserving dataset copyright:          {len(apps_preserving_dataset_copyright):>6,} / {len(unique_apps_in_comparison):,} ({pct(len(apps_preserving_dataset_copyright), len(unique_apps_in_comparison))})")
    print(f"Apps preserving model copyright:            {len(apps_preserving_model_copyright):>6,} / {len(m2a_unique_apps):,} ({pct(len(apps_preserving_model_copyright), len(m2a_unique_apps))})")
    print()
    print(f"Apps preserving dataset OR model (union):   {len(apps_preserving_either):>6,} / {len(all_apps_in_both_analyses):,} ({pct(len(apps_preserving_either), len(all_apps_in_both_analyses))})")
    print(f"Apps preserving dataset AND model (intersect): {len(apps_preserving_both_independent):>4,} / {len(all_apps_in_both_analyses):,} ({pct(len(apps_preserving_both_independent), len(all_apps_in_both_analyses))})")
    print()

    # ----------------------------------------------------------
    # 6. Write detailed markdown output
    # ----------------------------------------------------------
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as out:
        out.write("# Copyright Attribution Audit: Dataset → Model → Application\n\n")
        out.write(f"Generated: {os.popen('date').read().strip()}\n\n")

        out.write("## Configuration\n\n")
        out.write(f"- Permissive licenses: {', '.join(sorted(PERMISSIVE_LICENSES))}\n")
        out.write(f"- Starting population: fully compliant entities (copyright notice + full license text match_coverage ≥ 90%, consistent with RQ1 integrity audit)\n")
        out.write(f"- Copyright matching: normalized substring matching (shared_utils.normalize_copyright)\n")
        out.write(f"- Normalization: strips 'copyright/©/(c)', removes years, removes punctuation, lowercases\n\n")

        out.write("## Population\n\n")
        out.write(f"| Metric | Count |\n")
        out.write(f"|--------|-------|\n")
        out.write(f"| Permissive datasets (total) | {permissive_datasets_total:,} |\n")
        out.write(f"| With copyright notices | {permissive_ds_with_copyright:,} |\n")
        out.write(f"| With full license text (≥90%) | {permissive_ds_with_full_text:,} |\n")
        out.write(f"| Fully compliant (copyright + full text) | {len(permissive_ds_fully_compliant):,} |\n")
        out.write(f"| With ≥1 downstream model | {ds_with_downstream_model:,} |\n\n")

        out.write("## Dataset → Model Preservation\n\n")
        out.write(f"| Level | Preserved | Total | Rate |\n")
        out.write(f"|-------|-----------|-------|------|\n")
        out.write(f"| Dataset-level (≥1 model preserves) | {ds_preserved_in_any_model:,} | {ds_with_downstream_model:,} | {pct(ds_preserved_in_any_model, ds_with_downstream_model)} |\n")
        out.write(f"| Link-level (each ds→model pair) | {total_ds_model_preserved:,} | {total_ds_model_links:,} | {pct(total_ds_model_preserved, total_ds_model_links)} |\n\n")

        out.write("## Dataset → Model → Application Preservation\n\n")
        out.write(f"| Level | Preserved | Total | Rate |\n")
        out.write(f"|-------|-----------|-------|------|\n")
        out.write(f"| Dataset-level (≥1 app preserves) | {ds_preserved_in_any_app:,} | {ds_with_downstream_model:,} | {pct(ds_preserved_in_any_app, ds_with_downstream_model)} |\n")
        out.write(f"| Chain-level (each ds→model→app) | {total_ds_app_preserved:,} | {total_ds_app_chains:,} | {pct(total_ds_app_preserved, total_ds_app_chains)} |\n\n")

        # Unique entities involved
        out.write("## Unique Entities in Comparison\n\n")
        out.write(f"| Entity Type | Total | Permissive | % Permissive |\n")
        out.write(f"|-------------|-------|------------|-------------|\n")
        out.write(f"| Datasets | {len(unique_datasets_in_comparison):,} | {len(unique_datasets_in_comparison):,} | 100.00% (by definition) |\n")
        out.write(f"| Models | {len(unique_models_in_comparison):,} | {len(unique_models_permissive):,} | {pct(len(unique_models_permissive), len(unique_models_in_comparison))} |\n")
        out.write(f"| Applications | {len(unique_apps_in_comparison):,} | {len(unique_apps_permissive):,} | {pct(len(unique_apps_permissive), len(unique_apps_in_comparison))} |\n\n")

        # =============================================================
        # MODEL → APPLICATION (SEPARATE ANALYSIS)
        # =============================================================
        out.write("---\n\n")
        out.write("# Model → Application Copyright Preservation (Separate Analysis)\n\n")
        out.write("Starting from fully compliant permissive models (copyright + full license text ≥90%), checking preservation in downstream applications.\n\n")

        out.write("## Population\n\n")
        out.write(f"| Metric | Count |\n")
        out.write(f"|--------|-------|\n")
        out.write(f"| Permissive models (total) | {permissive_models_total:,} |\n")
        out.write(f"| With copyright notices | {permissive_models_with_copyright:,} |\n")
        out.write(f"| With full license text (≥90%) | {permissive_models_with_full_text:,} |\n")
        out.write(f"| Fully compliant (copyright + full text) | {len(permissive_models_fully_compliant):,} |\n")
        out.write(f"| With ≥1 downstream app | {m2a_with_downstream_app:,} |\n\n")

        out.write("## Model → Application Preservation\n\n")
        out.write(f"| Level | Preserved | Total | Rate |\n")
        out.write(f"|-------|-----------|-------|------|\n")
        out.write(f"| Model-level (≥1 app preserves) | {m2a_preserved_in_any_app:,} | {m2a_with_downstream_app:,} | {pct(m2a_preserved_in_any_app, m2a_with_downstream_app)} |\n")
        out.write(f"| Link-level (each model→app pair) | {m2a_total_preserved:,} | {m2a_total_links:,} | {pct(m2a_total_preserved, m2a_total_links)} |\n\n")

        out.write("## Unique Entities in This Comparison\n\n")
        out.write(f"| Entity Type | Total | Permissive | % Permissive |\n")
        out.write(f"|-------------|-------|------------|-------------|\n")
        out.write(f"| Models | {len(m2a_unique_models):,} | {len(m2a_unique_models):,} | 100.00% (by definition) |\n")
        out.write(f"| Applications | {len(m2a_unique_apps):,} | {len(m2a_unique_apps_permissive):,} | {pct(len(m2a_unique_apps_permissive), len(m2a_unique_apps))} |\n\n")

        out.write("### Model IDs\n\n")
        for mid in sorted(m2a_unique_models):
            out.write(f"- {mid}\n")
        out.write("\n")

        out.write("### Application IDs\n\n")
        for aid in sorted(m2a_unique_apps):
            out.write(f"- {aid}\n")
        out.write("\n")

        # Per-model details
        out.write("## Per-Model Details\n\n")
        out.write("Models with ≥1 downstream app, sorted by number of linked apps (descending).\n\n")

        sorted_m2a = sorted(m2a_per_model_details, key=lambda x: x['n_linked_apps'], reverse=True)

        out.write(f"| Model | License | Copyright (normalized) | Apps | Apps Preserved |\n")
        out.write(f"|-------|---------|----------------------|------|---------------|\n")

        for d in sorted_m2a:
            mid = d['model_id']
            lic = ', '.join(d['licenses'])
            cr = '; '.join(d['copyrights_normalized'])
            if len(cr) > 60:
                cr = cr[:57] + '...'
            cr = cr.replace('|', '\\|')
            n_a = d['n_linked_apps']
            ap = d['n_app_preserved']
            out.write(f"| {mid} | {lic} | {cr} | {n_a} | {ap}/{n_a} |\n")

        out.write(f"\n\nTotal models in table: {len(sorted_m2a):,}\n\n")
        out.write("---\n\n")

        # =============================================================
        # COMBINED ANALYSIS: Fully Compliant DS → Fully Compliant Model → App
        # =============================================================
        out.write("# Combined: Fully Compliant Dataset → Fully Compliant Model → Application\n\n")
        out.write("Chains where BOTH the dataset and model are fully compliant (permissive + copyright + full text ≥90%).\n")
        out.write("For each app reached, checks if it preserves copyright from the dataset, the model, or both.\n\n")

        out.write("## Population\n\n")
        out.write(f"| Metric | Count |\n")
        out.write(f"|--------|-------|\n")
        out.write(f"| Fully compliant datasets | {len(permissive_ds_fully_compliant):,} |\n")
        out.write(f"| Fully compliant models | {len(permissive_models_fully_compliant):,} |\n")
        out.write(f"| Connected datasets (with ≥1 compliant model) | {len(combo_datasets):,} |\n")
        out.write(f"| Connected models (downstream of compliant ds) | {len(combo_models):,} |\n")
        out.write(f"| Unique applications reached | {len(combo_apps):,} |\n")
        out.write(f"| Total chains (ds→model→app) | {combo_total_chains:,} |\n\n")

        out.write("## Copyright Preservation (unique apps)\n\n")
        out.write(f"| Metric | Count | % of {len(combo_apps):,} apps |\n")
        out.write(f"|--------|-------|------|\n")
        out.write(f"| Preserves **dataset** copyright | {len(combo_apps_preserves_ds):,} | {pct(len(combo_apps_preserves_ds), len(combo_apps))} |\n")
        out.write(f"| Preserves **model** copyright | {len(combo_apps_preserves_model):,} | {pct(len(combo_apps_preserves_model), len(combo_apps))} |\n")
        out.write(f"| Preserves **BOTH** (ds AND model) | {len(combo_apps_preserves_both):,} | {pct(len(combo_apps_preserves_both), len(combo_apps))} |\n")
        out.write(f"| Preserves **EITHER** (ds OR model) | {len(combo_apps_preserves_ds | combo_apps_preserves_model):,} | {pct(len(combo_apps_preserves_ds | combo_apps_preserves_model), len(combo_apps))} |\n\n")

        combo_only_ds = combo_apps_preserves_ds - combo_apps_preserves_model
        combo_only_model = combo_apps_preserves_model - combo_apps_preserves_ds
        combo_neither = combo_apps - (combo_apps_preserves_ds | combo_apps_preserves_model)

        out.write("### Breakdown\n\n")
        out.write(f"- Only dataset copyright preserved: {len(combo_only_ds):,}\n")
        out.write(f"- Only model copyright preserved: {len(combo_only_model):,}\n")
        out.write(f"- Both preserved: {len(combo_apps_preserves_both):,}\n")
        out.write(f"- Neither preserved: {len(combo_neither):,}\n\n")

        out.write("### App IDs preserving BOTH dataset AND model copyright\n\n")
        for aid in sorted(combo_apps_preserves_both):
            out.write(f"- {aid}\n")
        out.write("\n")

        out.write("---\n\n")

        # Independent analyses summary
        out.write("# Independent Analyses: Application-Level Summary\n\n")
        out.write(f"Total unique apps across both analyses: {len(all_apps_in_both_analyses):,}\n\n")
        out.write(f"| Metric | Count | Denominator | Rate |\n")
        out.write(f"|--------|-------|-------------|------|\n")
        out.write(f"| Apps preserving dataset copyright (ds→model→app) | {len(apps_preserving_dataset_copyright):,} | {len(unique_apps_in_comparison):,} | {pct(len(apps_preserving_dataset_copyright), len(unique_apps_in_comparison))} |\n")
        out.write(f"| Apps preserving model copyright (model→app) | {len(apps_preserving_model_copyright):,} | {len(m2a_unique_apps):,} | {pct(len(apps_preserving_model_copyright), len(m2a_unique_apps))} |\n")
        out.write(f"| Apps preserving **dataset OR model** (union) | {len(apps_preserving_either):,} | {len(all_apps_in_both_analyses):,} | {pct(len(apps_preserving_either), len(all_apps_in_both_analyses))} |\n")
        out.write(f"| Apps preserving **dataset AND model** (intersection) | {len(apps_preserving_both_independent):,} | {len(all_apps_in_both_analyses):,} | {pct(len(apps_preserving_both_independent), len(all_apps_in_both_analyses))} |\n\n")

        out.write("---\n\n")

        # Dataset→Model→App entity ID lists
        out.write("# Entity ID Lists (Dataset → Model → App Analysis)\n\n")

        out.write("### Dataset IDs\n\n")
        for did in sorted(unique_datasets_in_comparison):
            out.write(f"- {did}\n")
        out.write("\n")

        out.write("### Model IDs\n\n")
        for mid in sorted(unique_models_in_comparison):
            out.write(f"- {mid}\n")
        out.write("\n")

        out.write("### Application IDs\n\n")
        for aid in sorted(unique_apps_in_comparison):
            out.write(f"- {aid}\n")
        out.write("\n")

        # Per-dataset details (sorted by number of linked models descending)
        out.write("## Per-Dataset Details\n\n")
        out.write("Datasets with ≥1 downstream model, sorted by number of linked models (descending).\n\n")

        sorted_details = sorted(per_dataset_details, key=lambda x: x['n_linked_models'], reverse=True)

        out.write(f"| Dataset | License | Copyright (normalized) | Models | Models Preserved | Apps | Apps Preserved |\n")
        out.write(f"|---------|---------|----------------------|--------|-----------------|------|---------------|\n")

        for d in sorted_details:
            ds_id = d['dataset_id']
            lic = ', '.join(d['licenses'])
            # Truncate copyright for table readability
            cr = '; '.join(d['copyrights_normalized'])
            if len(cr) > 60:
                cr = cr[:57] + '...'
            # Escape pipes in copyright text for markdown table
            cr = cr.replace('|', '\\|')
            n_m = d['n_linked_models']
            mp = d['n_model_preserved']
            n_a = d['n_linked_apps']
            ap = d['n_app_preserved']
            out.write(f"| {ds_id} | {lic} | {cr} | {n_m} | {mp}/{n_m} | {n_a} | {ap}/{n_a} |\n")

        out.write(f"\n\nTotal datasets in table: {len(sorted_details):,}\n")

    print(f"Results written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Chain Analysis (Adapted for New Format)

This script builds and analyzes supply chains from applications to models to datasets.

ADAPTATIONS FROM ORIGINAL:
- Uses single file: filtered_complete_chains_cleaned.jsonl
- Uses shared_utils for common operations
- New field names: model_links→models, training_datasets→datasets
- Simplified loading since data is already unified
- Outputs chains to outputs_new directory


"""

import sys
from pathlib import Path
import os
import json

# Add parent directory to path for shared_utils import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shared_utils


# Configuration
# Auto-detect base directory (works on any system)
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent  # adapted_scripts/
DATA_DIR = BASE_DIR.parent  # main directory with JSONL file
DATA_FILE = DATA_DIR / 'filtered_complete_chains_cleaned.jsonl'
OUTPUT_DIR = DATA_DIR / 'outputs_new' / 'good_scripts'
MATRIX_FILE = DATA_DIR / 'matrix.json'

# Target licenses for subset analysis
TARGET_LICENSES = {'MIT', 'Apache-2.0', 'BSD-3-Clause'}


def main():
    """Main function."""
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load data
    print("Loading unified dataset...")
    entities = shared_utils.load_jsonl_file(DATA_FILE)
    print(f"Loaded {len(entities):,} entities")

    # Split by type
    datasets_dict = shared_utils.filter_by_type(entities, 'dataset')
    models_dict = shared_utils.filter_by_type(entities, 'model')
    apps_dict = shared_utils.filter_by_type(entities, 'application')

    print("=" * 80)
    print("2.5 CHAIN ANALYSIS - Complete Chains Only")
    print("=" * 80)
    print(f"\nTotal entities: {len(entities):,}")
    print(f"  Datasets: {len(datasets_dict):,}")
    print(f"  Models: {len(models_dict):,}")
    print(f"  Applications: {len(apps_dict):,}")

    # Build all chains
    print("\n" + "=" * 80)
    print("BUILDING CHAINS (Application, Direct Model) pairs")
    print("=" * 80)

    all_chains = []
    mit_apache_bsd_chains = []

    for app in apps_dict.values():
        app_id = app['id'].lower()

        # New format uses 'models' field instead of 'model_links'
        for direct_model_id in app.get('models', []):
            direct_model_id_lower = direct_model_id.lower()

            if direct_model_id_lower not in models_dict:
                continue

            # Trace all upstream models via base_models
            all_models_in_chain = shared_utils.trace_upstream_models(
                direct_model_id_lower, entities
            )

            # Get all datasets from all models
            all_datasets_in_chain = set()
            for model_id in all_models_in_chain:
                datasets = shared_utils.get_training_datasets(model_id, entities)
                for ds_id in datasets:
                    if ds_id in datasets_dict:
                        all_datasets_in_chain.add(ds_id)

            # Build chain object
            chain = {
                'app_id': app_id,
                'direct_model_id': direct_model_id_lower,
                'all_models': all_models_in_chain,
                'all_datasets': all_datasets_in_chain
            }

            all_chains.append(chain)

            # Check if any dataset has MIT/Apache/BSD
            has_target = False
            for ds_id in all_datasets_in_chain:
                dataset = datasets_dict[ds_id]
                ds_licenses = shared_utils.extract_licenses_metadata(dataset)
                if shared_utils.has_target_license(ds_licenses, TARGET_LICENSES):
                    has_target = True
                    break

            if has_target:
                mit_apache_bsd_chains.append(chain)

    print(f"\nTotal chains (Application, Direct Model pairs): {len(all_chains):,}")
    print(f"  Chains with at least one MIT/Apache/BSD dataset: {len(mit_apache_bsd_chains):,}")

    # Statistics on chain sizes
    print("\n" + "=" * 80)
    print("CHAIN SIZE STATISTICS - ALL CHAINS")
    print("=" * 80)

    models_per_chain = [len(chain['all_models']) for chain in all_chains]
    datasets_per_chain = [len(chain['all_datasets']) for chain in all_chains]

    print(f"\nModels per chain:")
    print(f"  Min: {min(models_per_chain) if models_per_chain else 0}")
    print(f"  Max: {max(models_per_chain) if models_per_chain else 0}")
    print(f"  Average: {sum(models_per_chain) / len(models_per_chain):.2f}")

    print(f"\nDatasets per chain:")
    print(f"  Min: {min(datasets_per_chain) if datasets_per_chain else 0}")
    print(f"  Max: {max(datasets_per_chain) if datasets_per_chain else 0}")
    print(f"  Average: {sum(datasets_per_chain) / len(datasets_per_chain):.2f}")

    chains_no_datasets = sum(1 for chain in all_chains if len(chain['all_datasets']) == 0)
    chains_with_datasets = len(all_chains) - chains_no_datasets
    print(f"\nChains with datasets: {chains_with_datasets:,} ({chains_with_datasets/len(all_chains)*100:.1f}%)")
    print(f"Chains without datasets: {chains_no_datasets:,} ({chains_no_datasets/len(all_chains)*100:.1f}%)")

    print("\n" + "=" * 80)
    print("CHAIN SIZE STATISTICS - MIT/APACHE/BSD SUBSET")
    print("=" * 80)

    if mit_apache_bsd_chains:
        models_per_chain_subset = [len(chain['all_models']) for chain in mit_apache_bsd_chains]
        datasets_per_chain_subset = [len(chain['all_datasets']) for chain in mit_apache_bsd_chains]

        print(f"\nModels per chain:")
        print(f"  Min: {min(models_per_chain_subset)}")
        print(f"  Max: {max(models_per_chain_subset)}")
        print(f"  Average: {sum(models_per_chain_subset) / len(models_per_chain_subset):.2f}")

        print(f"\nDatasets per chain:")
        print(f"  Min: {min(datasets_per_chain_subset)}")
        print(f"  Max: {max(datasets_per_chain_subset)}")
        print(f"  Average: {sum(datasets_per_chain_subset) / len(datasets_per_chain_subset):.2f}")

    # Count unique entities across all chains
    print("\n" + "=" * 80)
    print("UNIQUE ENTITIES - ALL CHAINS")
    print("=" * 80)

    unique_apps = set(chain['app_id'] for chain in all_chains)
    unique_models = set()
    unique_datasets = set()
    for chain in all_chains:
        unique_models.update(chain['all_models'])
        unique_datasets.update(chain['all_datasets'])

    print(f"\nUnique applications: {len(unique_apps):,} / {len(apps_dict):,} ({len(unique_apps)/len(apps_dict)*100:.1f}%)")
    print(f"Unique models: {len(unique_models):,} / {len(models_dict):,} ({len(unique_models)/len(models_dict)*100:.1f}%)")
    print(f"Unique datasets: {len(unique_datasets):,} / {len(datasets_dict):,} ({len(unique_datasets)/len(datasets_dict)*100:.1f}%)")

    print("\n" + "=" * 80)
    print("UNIQUE ENTITIES - MIT/APACHE/BSD SUBSET")
    print("=" * 80)

    unique_apps_subset = set(chain['app_id'] for chain in mit_apache_bsd_chains)
    unique_models_subset = set()
    unique_datasets_subset = set()
    for chain in mit_apache_bsd_chains:
        unique_models_subset.update(chain['all_models'])
        unique_datasets_subset.update(chain['all_datasets'])

    print(f"\nUnique applications: {len(unique_apps_subset):,}")
    print(f"Unique models: {len(unique_models_subset):,}")
    print(f"Unique datasets: {len(unique_datasets_subset):,}")

    # Save chains for future analyses
    CHAINS_FILE = os.path.join(OUTPUT_DIR, "all_chains_v6.json")
    SUBSET_CHAINS_FILE = os.path.join(OUTPUT_DIR, "mit_apache_bsd_chains_v6.json")

    chains_serializable = []
    for chain in all_chains:
        chains_serializable.append({
            'app_id': chain['app_id'],
            'direct_model_id': chain['direct_model_id'],
            'all_models': list(chain['all_models']),
            'all_datasets': list(chain['all_datasets'])
        })

    subset_chains_serializable = []
    for chain in mit_apache_bsd_chains:
        subset_chains_serializable.append({
            'app_id': chain['app_id'],
            'direct_model_id': chain['direct_model_id'],
            'all_models': list(chain['all_models']),
            'all_datasets': list(chain['all_datasets'])
        })

    with open(CHAINS_FILE, 'w') as f:
        json.dump(chains_serializable, f)

    with open(SUBSET_CHAINS_FILE, 'w') as f:
        json.dump(subset_chains_serializable, f)

    # Write summary statistics to markdown file
    SUMMARY_FILE = os.path.join(OUTPUT_DIR, "chain_analysis_summary.md")
    with open(SUMMARY_FILE, 'w') as f:
        f.write("# Supply Chain Analysis Summary (Q2.5)\n\n")
        f.write("Complete analysis of AI supply chain relationships.\n\n")
        f.write("---\n\n")

        f.write("## Entity Counts\n\n")
        f.write(f"**Total entities analyzed:** {len(entities):,}\n\n")
        f.write(f"- **Datasets:** {len(datasets_dict):,}\n")
        f.write(f"- **Models:** {len(models_dict):,}\n")
        f.write(f"- **Applications:** {len(apps_dict):,}\n\n")

        f.write("---\n\n")
        f.write("## Complete Supply Chains\n\n")
        f.write(f"**Total chains (Application → Model pairs):** {len(all_chains):,}\n\n")

        chains_with_pct = chains_with_datasets / len(all_chains) * 100 if all_chains else 0
        chains_without_pct = chains_no_datasets / len(all_chains) * 100 if all_chains else 0

        f.write(f"- **Chains with at least one dataset:** {chains_with_datasets:,} ({chains_with_pct:.1f}%)\n")
        f.write(f"- **Chains without datasets:** {chains_no_datasets:,} ({chains_without_pct:.1f}%)\n\n")

        f.write("---\n\n")
        f.write("## MIT/Apache-2.0/BSD-3-Clause Subset\n\n")
        f.write(f"**Subset chains (with target licenses):** {len(mit_apache_bsd_chains):,}\n\n")
        f.write(f"**Unique entities in subset:**\n")
        f.write(f"- **Applications:** {len(unique_apps_subset):,}\n")
        f.write(f"- **Models:** {len(unique_models_subset):,}\n")
        f.write(f"- **Datasets:** {len(unique_datasets_subset):,}\n\n")

        f.write("---\n\n")
        f.write("## Output Files\n\n")
        f.write(f"- **All chains:** `{os.path.basename(CHAINS_FILE)}`\n")
        f.write(f"- **Target license subset:** `{os.path.basename(SUBSET_CHAINS_FILE)}`\n")
        f.write(f"- **This summary:** `{os.path.basename(SUMMARY_FILE)}`\n")

    print("\n" + "=" * 80)
    print("SUMMARY FOR PAPER (2.5)")
    print("=" * 80)
    print(f"\nTotal entities analyzed: {len(entities):,}")
    print(f"  - Datasets: {len(datasets_dict):,}")
    print(f"  - Models: {len(models_dict):,}")
    print(f"  - Applications: {len(apps_dict):,}")
    print(f"\nTotal chains (Application, Model pairs): {len(all_chains):,}")
    print(f"  - Chains with at least one dataset: {chains_with_datasets:,} ({chains_with_datasets/len(all_chains)*100:.1f}%)")
    print(f"  - Chains without datasets: {chains_no_datasets:,} ({chains_no_datasets/len(all_chains)*100:.1f}%)")
    print(f"\nMIT/Apache/BSD subset chains: {len(mit_apache_bsd_chains):,}")
    print(f"  - Unique entities: {len(unique_apps_subset):,} apps, {len(unique_models_subset):,} models, {len(unique_datasets_subset):,} datasets")

    print("\n" + "=" * 80)
    print("FILES SAVED")
    print("=" * 80)
    print(f"All chains: {CHAINS_FILE}")
    print(f"MIT/Apache/BSD subset: {SUBSET_CHAINS_FILE}")
    print(f"Summary statistics: {SUMMARY_FILE}")
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()

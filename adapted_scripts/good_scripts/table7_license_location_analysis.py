#!/usr/bin/env python3
"""
License and Copyright Location Analysis

Generates table data showing where license text and copyright notices
are located across the AI supply chain (LICENSE files, README files, Source Code).

Counts how many ENTITIES have matches in each file type category
(not total file counts - an entity can appear in multiple columns).


"""

import sys
from pathlib import Path
import os
from collections import defaultdict

# Add parent directory to path for shared_utils import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shared_utils

# Configuration
# Auto-detect base directory (works on any system)
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent  # adapted_scripts/
DATA_DIR = BASE_DIR.parent  # main directory with JSONL file
DATA_FILE = DATA_DIR / 'filtered_complete_chains_cleaned.jsonl'
TARGET_LICENSES = {'MIT', 'Apache-2.0', 'BSD-3-Clause'}
THRESHOLD = 90.0
OUTPUT_FILE = DATA_DIR / 'outputs_new' / 'good_scripts' / 'license_location_table.md'


def analyze_license_locations(entities, filter_func=None):
    """
    Analyze where license text is located (entities with matches in each file type).

    Returns:
        dict: Results by entity type
    """
    results = {
        'dataset': {'total': 0, 'LICENSE': 0, 'README': 0},
        'model': {'total': 0, 'LICENSE': 0, 'README': 0},
        'application': {'total': 0, 'LICENSE': 0, 'README': 0}
    }

    for entity_id, entity in shared_utils.iter_all_entities(entities):
        entry_type = entity['type']

        # Apply filter if provided (e.g., MIT/Apache/BSD subset)
        if filter_func is not None:
            licenses = shared_utils.extract_licenses_metadata(entity)
            if not filter_func(licenses):
                continue

        results[entry_type]['total'] += 1

        # Find the BEST location for license reference (any mention, prefer LICENSE over README)
        best_location = None
        best_coverage = -1.0  # Start at -1 to accept any coverage including 0

        scancode = entity.get('scancode', [])
        if scancode is None:
            scancode = []

        for item in scancode:
            origins = item.get('origins', [])
            for origin in origins:
                # Accept ANY mention (including null/0 coverage)
                coverage = origin.get('match_coverage', 0.0)
                if coverage is None:
                    coverage = 0.0

                file_path = origin.get('file_path', '')
                file_type = shared_utils.categorize_file_type(file_path)

                if file_type not in ['LICENSE', 'README']:
                    continue

                # Update best location if:
                # 1. Higher coverage, OR
                # 2. Same coverage but LICENSE file (preferred)
                if (coverage > best_coverage or
                    (coverage == best_coverage and file_type == 'LICENSE' and best_location != 'LICENSE')):
                    best_coverage = coverage
                    best_location = file_type

        # Count entity in the SINGLE best location category
        if best_location:
            results[entry_type][best_location] += 1
        else:
            # Debug: Track entities with no location
            if 'no_location' not in results[entry_type]:
                results[entry_type]['no_location'] = []
            results[entry_type]['no_location'].append(entity.get('id'))

    return results


def analyze_copyright_locations(entities, filter_func=None):
    """
    Analyze where copyright notices are located (entities with matches in each file type).

    Returns:
        dict: Results by entity type
    """
    results = {
        'dataset': {'total': 0, 'LICENSE': 0, 'README': 0},
        'model': {'total': 0, 'LICENSE': 0, 'README': 0},
        'application': {'total': 0, 'LICENSE': 0, 'README': 0}
    }

    for entity_id, entity in shared_utils.iter_all_entities(entities):
        entry_type = entity['type']

        # Apply filter if provided
        if filter_func is not None:
            licenses = shared_utils.extract_licenses_metadata(entity)
            if not filter_func(licenses):
                continue

        results[entry_type]['total'] += 1

        # Find the BEST location for copyright (prefer LICENSE over README)
        # Count how many copyrights in each file type, then pick best
        location_counts = {'LICENSE': 0, 'README': 0}

        copyrights = entity.get('copyrights', [])
        if copyrights is None:
            copyrights = []

        for item in copyrights:
            origins = item.get('origins', [])
            for origin in origins:
                file_path = origin.get('file_path', '')
                file_type = shared_utils.categorize_file_type(file_path)

                if file_type in location_counts:
                    location_counts[file_type] += 1

        # Pick best location: LICENSE preferred, otherwise README
        best_location = None
        if location_counts['LICENSE'] > 0:
            best_location = 'LICENSE'
        elif location_counts['README'] > 0:
            best_location = 'README'

        # Count entity in the SINGLE best location category
        if best_location:
            results[entry_type][best_location] += 1
        else:
            # Debug: Track entities with no location
            if 'no_location' not in results[entry_type]:
                results[entry_type]['no_location'] = []
            results[entry_type]['no_location'].append(entity.get('id'))

    return results


def print_table(license_results, copyright_results, title, output_lines):
    """Print table in markdown format."""
    output_lines.append(f"## {title}\n\n")

    # License reference table (any mention, not just full text)
    output_lines.append("### (a) License Reference Locations (any mention)\n\n")
    output_lines.append("| Artifact Type | Total | LICENSE Files | README Files |\n")
    output_lines.append("|---------------|-------|---------------|-------------|\n")

    for entry_type, name in [('dataset', 'Datasets'), ('model', 'Models'), ('application', 'Applications')]:
        stats = license_results[entry_type]
        total = stats['total']

        if total == 0:
            output_lines.append(f"| {name} | 0 | --- | --- | --- |\n")
            continue

        license_count = stats['LICENSE']
        readme_count = stats['README']

        license_pct = (license_count / total * 100) if total > 0 else 0
        readme_pct = (readme_count / total * 100) if total > 0 else 0

        output_lines.append(
            f"| {name} | {total:,} | "
            f"{license_count:,} ({license_pct:.2f}%) | "
            f"{readme_count:,} ({readme_pct:.2f}%) |\n"
        )

    # Totals
    total_all = sum(license_results[t]['total'] for t in ['dataset', 'model', 'application'])
    total_license = sum(license_results[t]['LICENSE'] for t in ['dataset', 'model', 'application'])
    total_readme = sum(license_results[t]['README'] for t in ['dataset', 'model', 'application'])

    license_pct = (total_license / total_all * 100) if total_all > 0 else 0
    readme_pct = (total_readme / total_all * 100) if total_all > 0 else 0

    output_lines.append(
        f"| **Total** | **{total_all:,}** | "
        f"**{total_license:,} ({license_pct:.2f}%)** | "
        f"**{total_readme:,} ({readme_pct:.2f}%)** |\n"
    )

    output_lines.append("\n")

    # Show missing entities (no LICENSE or README reference)
    for entry_type, name in [('dataset', 'Datasets'), ('model', 'Models'), ('application', 'Applications')]:
        no_loc = license_results[entry_type].get('no_location', [])
        if no_loc:
            output_lines.append(f"**{name} with no LICENSE/README reference ({len(no_loc)}):**\n")
            for entity_id in no_loc[:10]:  # Limit to first 10
                output_lines.append(f"- {entity_id}\n")
            if len(no_loc) > 10:
                output_lines.append(f"- ... and {len(no_loc) - 10} more\n")
            output_lines.append("\n")

    # Copyright table
    output_lines.append("### (b) Copyright Notice Locations\n\n")
    output_lines.append("| Artifact Type | Total | LICENSE Files | README Files |\n")
    output_lines.append("|---------------|-------|---------------|-------------|\n")

    for entry_type, name in [('dataset', 'Datasets'), ('model', 'Models'), ('application', 'Applications')]:
        stats = copyright_results[entry_type]
        total = stats['total']

        if total == 0:
            output_lines.append(f"| {name} | 0 | --- | --- | --- |\n")
            continue

        license_count = stats['LICENSE']
        readme_count = stats['README']

        license_pct = (license_count / total * 100) if total > 0 else 0
        readme_pct = (readme_count / total * 100) if total > 0 else 0

        output_lines.append(
            f"| {name} | {total:,} | "
            f"{license_count:,} ({license_pct:.2f}%) | "
            f"{readme_count:,} ({readme_pct:.2f}%) |\n"
        )

    # Totals
    total_all = sum(copyright_results[t]['total'] for t in ['dataset', 'model', 'application'])
    total_license = sum(copyright_results[t]['LICENSE'] for t in ['dataset', 'model', 'application'])
    total_readme = sum(copyright_results[t]['README'] for t in ['dataset', 'model', 'application'])

    license_pct = (total_license / total_all * 100) if total_all > 0 else 0
    readme_pct = (total_readme / total_all * 100) if total_all > 0 else 0

    output_lines.append(
        f"| **Total** | **{total_all:,}** | "
        f"**{total_license:,} ({license_pct:.2f}%)** | "
        f"**{total_readme:,} ({readme_pct:.2f}%)** |\n"
    )

    output_lines.append("\n")

    # Show missing entities (no LICENSE or README copyright)
    for entry_type, name in [('dataset', 'Datasets'), ('model', 'Models'), ('application', 'Applications')]:
        no_loc = copyright_results[entry_type].get('no_location', [])
        if no_loc:
            output_lines.append(f"**{name} with no LICENSE/README copyright ({len(no_loc)}):**\n")
            for entity_id in no_loc[:10]:  # Limit to first 10
                output_lines.append(f"- {entity_id}\n")
            if len(no_loc) > 10:
                output_lines.append(f"- ... and {len(no_loc) - 10} more\n")
            output_lines.append("\n")

    output_lines.append("\n")


def main():
    """Main function."""
    print("=" * 100)
    print("License and Copyright Location Analysis")
    print("=" * 100)
    print()

    # Load data
    print(f"Loading {DATA_FILE}...")
    entities = shared_utils.load_jsonl_file(DATA_FILE)
    print(f"Loaded {len(entities):,} entities\n")

    output_lines = []
    output_lines.append("# License and Copyright Location Analysis\n\n")
    output_lines.append(f"Generated: {os.popen('date').read().strip()}\n\n")

    # Analysis 1: ALL entities
    print("Analyzing all entities...")
    license_all = analyze_license_locations(entities)
    copyright_all = analyze_copyright_locations(entities)

    # Show missing entities
    for entry_type in ['dataset', 'model', 'application']:
        no_loc = license_all[entry_type].get('no_location', [])
        if no_loc:
            print(f"  - {entry_type.capitalize()}s with no LICENSE/README reference: {len(no_loc)}")
            for entity_id in no_loc[:5]:
                print(f"    - {entity_id}")

    print_table(license_all, copyright_all, "All Entities", output_lines)

    # Analysis 2: MIT/Apache/BSD subset
    print("Analyzing MIT/Apache/BSD subset...")
    def has_target_license(licenses):
        return shared_utils.has_target_license(licenses, TARGET_LICENSES)

    license_subset = analyze_license_locations(entities, has_target_license)
    copyright_subset = analyze_copyright_locations(entities, has_target_license)

    # Show missing entities
    for entry_type in ['dataset', 'model', 'application']:
        no_loc = license_subset[entry_type].get('no_location', [])
        if no_loc:
            print(f"  - {entry_type.capitalize()}s with no LICENSE/README reference: {len(no_loc)}")
            for entity_id in no_loc[:5]:
                print(f"    - {entity_id}")

    print_table(license_subset, copyright_subset, "MIT/Apache-2.0/BSD-3-Clause Subset", output_lines)

    # Write to file
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.writelines(output_lines)

    print(f"\nResults written to: {OUTPUT_FILE}")

    # Export missing entities to separate files
    missing_dir = os.path.dirname(OUTPUT_FILE)

    # Missing license references
    missing_license_file = os.path.join(missing_dir, 'missing_license_references.txt')
    with open(missing_license_file, 'w', encoding='utf-8') as f:
        f.write("# Entities with No LICENSE/README License Reference\n\n")
        f.write("## MIT/Apache-2.0/BSD-3-Clause Subset\n\n")

        for entry_type, name in [('dataset', 'Datasets'), ('model', 'Models'), ('application', 'Applications')]:
            no_loc = license_subset[entry_type].get('no_location', [])
            if no_loc:
                f.write(f"\n### {name} ({len(no_loc):,})\n\n")
                for entity_id in no_loc:
                    f.write(f"{entity_id}\n")

    print(f"Missing license references: {missing_license_file}")

    # Missing copyrights
    missing_copyright_file = os.path.join(missing_dir, 'missing_copyright_notices.txt')
    with open(missing_copyright_file, 'w', encoding='utf-8') as f:
        f.write("# Entities with No LICENSE/README Copyright Notice\n\n")
        f.write("## MIT/Apache-2.0/BSD-3-Clause Subset\n\n")

        for entry_type, name in [('dataset', 'Datasets'), ('model', 'Models'), ('application', 'Applications')]:
            no_loc = copyright_subset[entry_type].get('no_location', [])
            if no_loc:
                f.write(f"\n### {name} ({len(no_loc):,})\n\n")
                for entity_id in no_loc:
                    f.write(f"{entity_id}\n")

    print(f"Missing copyright notices: {missing_copyright_file}")
    print()


if __name__ == '__main__':
    main()

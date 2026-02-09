#!/usr/bin/env python3
"""
Question 3: Missing README and LICENSE Files Analysis (Adapted for New Format)

Analyzes entities missing README or LICENSE files in their repositories.

Separate analysis for:
1. ALL entities (regardless of license)
2. MIT/Apache-2.0/BSD-3-Clause subset (among those missing files)

ADAPTATIONS FROM ORIGINAL:
- Uses single file: filtered_complete_chains_cleaned.jsonl
- Uses shared_utils for common operations
- New scancode structure: origins[] instead of detections[]
- File paths in origins[].file_path


"""

import sys
from pathlib import Path
import os
from io import StringIO

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
OUTPUT_FILE = DATA_DIR / 'outputs_new' / 'good_scripts' / 'missing_license_readme_results.md'


def has_readme_file(entity):
    """
    Check if scancode detected any README file.

    Args:
        entity: Entity dictionary with 'scancode' field

    Returns:
        True if README file found
    """
    scancode = entity.get('scancode', [])
    if not scancode:
        return False

    # Check file paths in scancode origins
    for item in scancode:
        origins = item.get('origins', [])
        for origin in origins:
            file_path = origin.get('file_path', '').lower()
            # Check for README files (README, README.md, README.txt, etc.)
            if 'readme' in file_path:
                return True

    # Also check copyrights for README files
    copyrights = entity.get('copyrights', [])
    for item in copyrights:
        origins = item.get('origins', [])
        for origin in origins:
            file_path = origin.get('file_path', '').lower()
            if 'readme' in file_path:
                return True

    return False


def has_license_file(entity):
    """
    Check if scancode detected any LICENSE file.

    Args:
        entity: Entity dictionary with 'scancode' field

    Returns:
        True if LICENSE file found
    """
    scancode = entity.get('scancode', [])
    if not scancode:
        return False

    # Check file paths in scancode origins
    for item in scancode:
        origins = item.get('origins', [])
        for origin in origins:
            file_path = origin.get('file_path', '').lower()
            # Check for LICENSE files (LICENSE, LICENSE.txt, LICENSE.md, COPYING, etc.)
            if any(keyword in file_path for keyword in ['license', 'copying', 'licence']):
                return True

    # Also check copyrights for LICENSE files
    copyrights = entity.get('copyrights', [])
    for item in copyrights:
        origins = item.get('origins', [])
        for origin in origins:
            file_path = origin.get('file_path', '').lower()
            if any(keyword in file_path for keyword in ['license', 'copying', 'licence']):
                return True

    return False


def analyze_missing_files(entities, filter_func=None):
    """
    Analyze entities missing README or LICENSE files.

    Args:
        entities: Dictionary of entities
        filter_func: Optional function to filter entities by license (None = all entities)

    Returns:
        Dictionary with counts by type
    """
    results = {
        'dataset': {
            'total': 0,
            'missing_readme': 0,
            'missing_license': 0,
            'missing_either': 0
        },
        'model': {
            'total': 0,
            'missing_readme': 0,
            'missing_license': 0,
            'missing_either': 0
        },
        'application': {
            'total': 0,
            'missing_readme': 0,
            'missing_license': 0,
            'missing_either': 0
        }
    }

    for entity_id, entity in shared_utils.iter_all_entities(entities):
        entry_type = entity['type']

        # If filter function provided, check if entry passes filter
        if filter_func is not None:
            licenses = shared_utils.extract_licenses_metadata(entity)
            if not filter_func(licenses):
                continue

        results[entry_type]['total'] += 1

        has_readme = has_readme_file(entity)
        has_license = has_license_file(entity)

        if not has_readme:
            results[entry_type]['missing_readme'] += 1

        if not has_license:
            results[entry_type]['missing_license'] += 1

        # Missing either = missing at least one of README or LICENSE
        if not has_readme or not has_license:
            results[entry_type]['missing_either'] += 1

    return results


def print_results(results, title):
    """Print results in formatted table."""
    print("=" * 100)
    print(title)
    print("=" * 100)
    print()

    types = ['dataset', 'model', 'application']
    type_names = {'dataset': 'Datasets', 'model': 'Models', 'application': 'Applications'}

    # Calculate totals
    totals = {
        'total': sum(results[t]['total'] for t in types),
        'missing_readme': sum(results[t]['missing_readme'] for t in types),
        'missing_license': sum(results[t]['missing_license'] for t in types),
        'missing_either': sum(results[t]['missing_either'] for t in types)
    }

    for entry_type in types:
        stats = results[entry_type]
        n = stats['total']

        if n == 0:
            print(f"{type_names[entry_type]} (n=0): No entities with these licenses")
            print()
            continue

        print(f"{type_names[entry_type]} (n={n:,}):")
        print(f"  - Missing README:        {stats['missing_readme']:>8,} ({stats['missing_readme']/n*100:>5.2f}%)")
        print(f"  - Missing LICENSE:       {stats['missing_license']:>8,} ({stats['missing_license']/n*100:>5.2f}%)")
        print(f"  - Missing EITHER:        {stats['missing_either']:>8,} ({stats['missing_either']/n*100:>5.2f}%)")
        print()

    # Print totals
    print("-" * 80)
    n_total = totals['total']
    print(f"TOTAL (n={n_total:,}):")
    print(f"  - Missing README:        {totals['missing_readme']:>8,} ({totals['missing_readme']/n_total*100:>5.2f}%)")
    print(f"  - Missing LICENSE:       {totals['missing_license']:>8,} ({totals['missing_license']/n_total*100:>5.2f}%)")
    print(f"  - Missing EITHER:        {totals['missing_either']:>8,} ({totals['missing_either']/n_total*100:>5.2f}%)")
    print("=" * 100)
    print()


def capture_output(func, *args, **kwargs):
    """Capture output from a function."""
    old_stdout = sys.stdout
    sys.stdout = buffer = StringIO()
    try:
        func(*args, **kwargs)
        output = buffer.getvalue()
    finally:
        sys.stdout = old_stdout
    return output


def write_to_file(entities):
    """Write all analysis output to markdown file."""
    output_lines = []

    # Header
    output_lines.append("# Missing README and LICENSE Files Analysis\n")
    output_lines.append(f"Generated: {os.popen('date').read().strip()}\n")
    output_lines.append("\n")

    # Analysis 1: ALL entities
    output_lines.append("\n" + "=" * 100 + "\n")
    output_lines.append("ANALYZING: ALL ENTITIES\n")
    output_lines.append("=" * 100 + "\n")

    all_results = analyze_missing_files(entities, filter_func=None)
    all_output = capture_output(print_results, all_results, "3a. ALL entities missing README/LICENSE files")
    output_lines.append(all_output)

    # Analysis 2: MIT/Apache-2.0/BSD-3-Clause subset
    output_lines.append("\n" + "=" * 100 + "\n")
    output_lines.append("ANALYZING: MIT/APACHE-2.0/BSD-3-CLAUSE SUBSET\n")
    output_lines.append("=" * 100 + "\n")

    target_filter = lambda licenses: shared_utils.has_target_license(licenses, TARGET_LICENSES)
    target_results = analyze_missing_files(entities, filter_func=target_filter)
    target_output = capture_output(print_results, target_results, "3b. MIT/APACHE-2.0/BSD-3-CLAUSE entities missing README/LICENSE files")
    output_lines.append(target_output)

    # Summary
    output_lines.append("\n" + "=" * 100 + "\n")
    output_lines.append("SUMMARY FOR PAPER (Question 3)\n")
    output_lines.append("=" * 100 + "\n")

    output_lines.append("\nALL ENTITIES:\n")
    output_lines.append(f"  Total entities: {sum(all_results[t]['total'] for t in ['dataset', 'model', 'application']):,}\n")
    output_lines.append(f"  Missing README: {sum(all_results[t]['missing_readme'] for t in ['dataset', 'model', 'application']):,}\n")
    output_lines.append(f"  Missing LICENSE: {sum(all_results[t]['missing_license'] for t in ['dataset', 'model', 'application']):,}\n")
    output_lines.append(f"  Missing EITHER: {sum(all_results[t]['missing_either'] for t in ['dataset', 'model', 'application']):,}\n")

    output_lines.append("\nMIT/APACHE-2.0/BSD-3-CLAUSE SUBSET:\n")
    output_lines.append(f"  Total entities with target licenses: {sum(target_results[t]['total'] for t in ['dataset', 'model', 'application']):,}\n")
    output_lines.append(f"  Missing README: {sum(target_results[t]['missing_readme'] for t in ['dataset', 'model', 'application']):,}\n")
    output_lines.append(f"  Missing LICENSE: {sum(target_results[t]['missing_license'] for t in ['dataset', 'model', 'application']):,}\n")
    output_lines.append(f"  Missing EITHER: {sum(target_results[t]['missing_either'] for t in ['dataset', 'model', 'application']):,}\n")

    # Write to file
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        f.write(''.join(output_lines))


def main():
    """Main function."""
    print("Loading unified dataset...")
    entities = shared_utils.load_jsonl_file(DATA_FILE)
    print(f"Loaded {len(entities):,} entities")
    print()

    # Analysis 1: ALL entities (regardless of license)
    print("\n" + "=" * 100)
    print("ANALYZING: ALL ENTITIES")
    print("=" * 100)

    all_results = analyze_missing_files(entities, filter_func=None)

    print_results(all_results,
                  "3a. ALL entities missing README/LICENSE files")

    # Analysis 2: MIT/Apache-2.0/BSD-3-Clause subset
    print("\n" + "=" * 100)
    print("ANALYZING: MIT/APACHE-2.0/BSD-3-CLAUSE SUBSET")
    print("=" * 100)

    target_filter = lambda licenses: shared_utils.has_target_license(licenses, TARGET_LICENSES)
    target_results = analyze_missing_files(entities, filter_func=target_filter)

    print_results(target_results,
                  "3b. MIT/APACHE-2.0/BSD-3-CLAUSE entities missing README/LICENSE files")

    # Summary
    print("\n" + "=" * 100)
    print("SUMMARY FOR PAPER (Question 3)")
    print("=" * 100)

    print("\nALL ENTITIES:")
    print(f"  Total entities: {sum(all_results[t]['total'] for t in ['dataset', 'model', 'application']):,}")
    print(f"  Missing README: {sum(all_results[t]['missing_readme'] for t in ['dataset', 'model', 'application']):,}")
    print(f"  Missing LICENSE: {sum(all_results[t]['missing_license'] for t in ['dataset', 'model', 'application']):,}")
    print(f"  Missing EITHER: {sum(all_results[t]['missing_either'] for t in ['dataset', 'model', 'application']):,}")

    print("\nMIT/APACHE-2.0/BSD-3-CLAUSE SUBSET:")
    print(f"  Total entities with target licenses: {sum(target_results[t]['total'] for t in ['dataset', 'model', 'application']):,}")
    print(f"  Missing README: {sum(target_results[t]['missing_readme'] for t in ['dataset', 'model', 'application']):,}")
    print(f"  Missing LICENSE: {sum(target_results[t]['missing_license'] for t in ['dataset', 'model', 'application']):,}")
    print(f"  Missing EITHER: {sum(target_results[t]['missing_either'] for t in ['dataset', 'model', 'application']):,}")

    # Write to markdown file
    write_to_file(entities)
    print(f"\nResults written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
AI Supply Chain Dataset Statistics for Paper (Adapted for New Format)

This script generates statistics for two research questions:
1. Total number of datasets, models, and applications considered
2. License distribution across MIT, APACHE-2.0, and BSD-3-CLAUSE

ADAPTATIONS FROM ORIGINAL:
- Uses single file: filtered_complete_chains_cleaned.jsonl
- Uses shared_utils for common operations
- New field names: scancode[] instead of licenses[]
- License extraction from scancode.license_expression_spdx
- Simplified since data is already unified and validated

"""

import sys
import os
from pathlib import Path
from collections import defaultdict
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
OUTPUT_FILE = DATA_DIR / 'outputs_new' / 'good_scripts' / 'paper_statistics_results.md'


def categorize_licenses(licenses):
    """
    Categorize a license list.

    Args:
        licenses: List of license strings (from metadata or scancode)

    Returns:
        'empty': No licenses
        'target': Contains at least one target license (MIT/Apache/BSD)
        'other': Has licenses but none are in target set
    """
    if not licenses:
        return 'empty'

    # Check for exact match
    license_set = {lic for lic in licenses}
    if license_set & TARGET_LICENSES:
        return 'target'

    # Check for substring match (for compound expressions or variations)
    for lic in licenses:
        for target in TARGET_LICENSES:
            if target.lower() in lic.lower():
                return 'target'

    return 'other'


def analyze_licenses_metadata(entities):
    """
    Analyze license distribution by type using metadata 'licenses' field.

    Args:
        entities: Dictionary of entities

    Returns:
        Dictionary with license counts by type
    """
    results = defaultdict(lambda: {'empty': 0, 'target': 0, 'other': 0, 'total': 0})

    # Use type-aware iteration to avoid ID collision issues
    for eid, entity in shared_utils.iter_all_entities(entities):
        entry_type = entity['type']
        licenses = shared_utils.extract_licenses_metadata(entity)
        category = categorize_licenses(licenses)

        results[entry_type][category] += 1
        results[entry_type]['total'] += 1

    return dict(results)


def analyze_licenses_scancode(entities):
    """
    Analyze license distribution by type using scancode field.

    Args:
        entities: Dictionary of entities

    Returns:
        Dictionary with license counts by type
    """
    results = defaultdict(lambda: {'empty': 0, 'target': 0, 'other': 0, 'total': 0})

    # Use type-aware iteration to avoid ID collision issues
    for eid, entity in shared_utils.iter_all_entities(entities):
        entry_type = entity['type']
        licenses = shared_utils.extract_licenses_scancode(entity)
        category = categorize_licenses(licenses)

        results[entry_type][category] += 1
        results[entry_type]['total'] += 1

    return dict(results)


def print_question_1(entities):
    """Print Question 1: Total counts by type."""
    print("=" * 100)
    print("1. Total number of datasets, models, and applications considered.")
    print("=" * 100)
    print()

    # Count by type
    counts = shared_utils.count_by_type(entities)

    print("Total datasets, models, and applications in unified dataset:")
    print(f"  - Datasets:     {counts.get('dataset', 0):>8,}")
    print(f"  - Models:       {counts.get('model', 0):>8,}")
    print(f"  - Applications: {counts.get('application', 0):>8,}")
    print(f"  - Total:        {len(entities):>8,}")
    print()
    print("Note: All entries have validated scancode data and complete chain information.")
    print()


def print_question_2(entities):
    """Print Question 2: License distribution with metadata and scancode comparison."""
    print("=" * 100)
    print("2. Number of datasets, models, and applications with MIT, Apache-2.0, BSD-3-Clause")
    print("=" * 100)
    print()

    # Analyze licenses
    metadata_licenses = analyze_licenses_metadata(entities)
    scancode_licenses = analyze_licenses_scancode(entities)

    types = ['dataset', 'model', 'application']
    type_names = {'dataset': 'Datasets', 'model': 'Models', 'application': 'Applications'}

    # Accumulators for totals
    total_meta = {'empty': 0, 'other': 0, 'target': 0, 'total': 0}
    total_scan = {'empty': 0, 'other': 0, 'target': 0, 'total': 0}

    for entry_type in types:
        meta = metadata_licenses.get(entry_type, {'empty': 0, 'target': 0, 'other': 0, 'total': 0})
        scan = scancode_licenses.get(entry_type, {'empty': 0, 'target': 0, 'other': 0, 'total': 0})

        n = meta['total']
        print(f"{type_names[entry_type]} (n={n:,}):")
        print(f"  Metadata:")
        print(f"    - Empty:         {meta['empty']:>8,} ({meta['empty']/n*100:>5.2f}%)")
        print(f"    - NOT in target: {meta['other']:>8,} ({meta['other']/n*100:>5.2f}%)")
        print(f"    - IN target:     {meta['target']:>8,} ({meta['target']/n*100:>5.2f}%)")
        print(f"  Scancode:")
        print(f"    - Empty:         {scan['empty']:>8,} ({scan['empty']/n*100:>5.2f}%)")
        print(f"    - NOT in target: {scan['other']:>8,} ({scan['other']/n*100:>5.2f}%)")
        print(f"    - IN target:     {scan['target']:>8,} ({scan['target']/n*100:>5.2f}%)")
        print()

        # Accumulate totals
        for key in ['empty', 'other', 'target', 'total']:
            total_meta[key] += meta[key]
            total_scan[key] += scan[key]

    # Print totals
    print("-" * 80)
    n_total = total_meta['total']
    print(f"TOTAL (n={n_total:,}):")
    print(f"  Metadata:")
    print(f"    - Empty:         {total_meta['empty']:>8,} ({total_meta['empty']/n_total*100:>5.2f}%)")
    print(f"    - NOT in target: {total_meta['other']:>8,} ({total_meta['other']/n_total*100:>5.2f}%)")
    print(f"    - IN target:     {total_meta['target']:>8,} ({total_meta['target']/n_total*100:>5.2f}%)")
    print(f"  Scancode:")
    print(f"    - Empty:         {total_scan['empty']:>8,} ({total_scan['empty']/n_total*100:>5.2f}%)")
    print(f"    - NOT in target: {total_scan['other']:>8,} ({total_scan['other']/n_total*100:>5.2f}%)")
    print(f"    - IN target:     {total_scan['target']:>8,} ({total_scan['target']/n_total*100:>5.2f}%)")
    print("=" * 100)


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
    output_lines.append("# AI Supply Chain Dataset Statistics for Paper\n")
    output_lines.append(f"Generated: {os.popen('date').read().strip()}\n")
    output_lines.append("\n")

    # Capture Question 1
    q1_output = capture_output(print_question_1, entities)
    output_lines.append(q1_output)
    output_lines.append("\n")

    # Capture Question 2
    q2_output = capture_output(print_question_2, entities)
    output_lines.append(q2_output)

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

    # Answer questions
    print_question_1(entities)
    print()
    print_question_2(entities)

    # Write to markdown file
    write_to_file(entities)
    print(f"\nResults written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

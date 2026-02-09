#!/usr/bin/env python3
"""
RQ1: Integrity Audit Analysis (Adapted for New Format)

Questions:
6. Entities lacking full license text (< 90%)
7. Entities with full license text that have valid copyright notice
8. Entities with BOTH full license text AND valid copyright notice
9. Where matches were identified (source code, license files, readme, notice)

ADAPTATIONS FROM ORIGINAL:
- Uses single file: filtered_complete_chains_cleaned.jsonl
- Uses shared_utils for common operations
- New scancode structure: match_coverage in origins[] (was percentage_of_license_text in detections[])
- File paths in origins[].file_path


"""

import sys
from pathlib import Path
import os
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
THRESHOLD = 90.0
OUTPUT_FILE = DATA_DIR / 'outputs_new' / 'good_scripts' / 'integrity_audit_results.md'


def has_copyright_notice(entity):
    """Check if entity has valid copyright notice."""
    copyrights = entity.get('copyrights', [])
    return len(copyrights) > 0


def analyze_file_types_for_matches(entity, threshold=90.0):
    """
    Analyze where license matches were found (file types).

    Returns:
        dict: Counts by file type category
    """
    file_types = defaultdict(int)

    scancode = entity.get('scancode', [])
    for item in scancode:
        origins = item.get('origins', [])
        for origin in origins:
            coverage = origin.get('match_coverage', 0.0)
            # Handle None case (different pattern found but no full match) - treat as 0
            if coverage is None:
                coverage = 0.0
            if coverage >= threshold:
                file_path = origin.get('file_path', '')
                file_type = shared_utils.categorize_file_type(file_path)
                file_types[file_type] += 1

    return dict(file_types)


def analyze_copyright_file_types(entity):
    """
    Analyze where copyright notices were found (file types).

    Returns:
        dict: Counts by file type category
    """
    file_types = defaultdict(int)

    copyrights = entity.get('copyrights', [])
    for item in copyrights:
        origins = item.get('origins', [])
        for origin in origins:
            file_path = origin.get('file_path', '')
            file_type = shared_utils.categorize_file_type(file_path)
            file_types[file_type] += 1

    return dict(file_types)


def analyze_integrity(entities, filter_func=None):
    """
    Analyze integrity for Q6-Q9.
    
    Returns:
        dict: Results by entity type
    """
    results = {
        'dataset': {
            'total': 0,
            'lacking_full_text': 0,
            'has_full_text': 0,
            'has_copyright_total': 0,  # Total with copyright (regardless of full text)
            'full_text_with_copyright': 0,
            'both_full_text_and_copyright': 0,
            'file_types': defaultdict(int),
            'copyright_file_types': defaultdict(int)
        },
        'model': {
            'total': 0,
            'lacking_full_text': 0,
            'has_full_text': 0,
            'has_copyright_total': 0,  # Total with copyright (regardless of full text)
            'full_text_with_copyright': 0,
            'both_full_text_and_copyright': 0,
            'file_types': defaultdict(int),
            'copyright_file_types': defaultdict(int)
        },
        'application': {
            'total': 0,
            'lacking_full_text': 0,
            'has_full_text': 0,
            'has_copyright_total': 0,  # Total with copyright (regardless of full text)
            'full_text_with_copyright': 0,
            'both_full_text_and_copyright': 0,
            'file_types': defaultdict(int),
            'copyright_file_types': defaultdict(int)
        }
    }
    
    for entity_id, entity in shared_utils.iter_all_entities(entities):
        entry_type = entity['type']

        # Apply filter if provided
        if filter_func is not None:
            licenses = shared_utils.extract_licenses_metadata(entity)
            if not filter_func(licenses):
                continue
        
        results[entry_type]['total'] += 1
        
        # Check for full license text
        has_full_text = shared_utils.has_full_license_text(entity, THRESHOLD)
        has_copyright = has_copyright_notice(entity)

        # Count total entities with copyright (regardless of full text)
        if has_copyright:
            results[entry_type]['has_copyright_total'] += 1

        if not has_full_text:
            results[entry_type]['lacking_full_text'] += 1
        else:
            results[entry_type]['has_full_text'] += 1

            # Q7: Full text with copyright
            if has_copyright:
                results[entry_type]['full_text_with_copyright'] += 1

        # Q8: Both full text AND copyright
        if has_full_text and has_copyright:
            results[entry_type]['both_full_text_and_copyright'] += 1
        
        # Q9: License file types
        if has_full_text:
            file_types = analyze_file_types_for_matches(entity, THRESHOLD)
            for ft, count in file_types.items():
                results[entry_type]['file_types'][ft] += count

        # Q9: Copyright file types
        if has_copyright:
            copyright_file_types = analyze_copyright_file_types(entity)
            for ft, count in copyright_file_types.items():
                results[entry_type]['copyright_file_types'][ft] += count
    
    return results


def print_results(results, title):
    """Print results in formatted table."""
    print("=" * 100)
    print(title)
    print("=" * 100)
    print()
    
    types = ['dataset', 'model', 'application']
    type_names = {'dataset': 'Datasets', 'model': 'Models', 'application': 'Applications'}
    
    # Q6: Lacking full license text
    print("Q6. Entities LACKING full license text (< 90% match coverage):")
    print()
    for entry_type in types:
        stats = results[entry_type]
        n = stats['total']
        if n == 0:
            continue
        print(f"{type_names[entry_type]} (n={n:,}):")
        print(f"  - Lacking full text: {stats['lacking_full_text']:>8,} ({stats['lacking_full_text']/n*100:>5.2f}%)")
        print(f"  - Has full text:     {stats['has_full_text']:>8,} ({stats['has_full_text']/n*100:>5.2f}%)")
        print()
    
    total = sum(results[t]['total'] for t in types)
    total_lacking = sum(results[t]['lacking_full_text'] for t in types)
    total_has = sum(results[t]['has_full_text'] for t in types)
    print(f"TOTAL (n={total:,}):")
    print(f"  - Lacking full text: {total_lacking:>8,} ({total_lacking/total*100:>5.2f}%)")
    print(f"  - Has full text:     {total_has:>8,} ({total_has/total*100:>5.2f}%)")
    print()
    print()
    
    # Total entities with copyright (regardless of full text)
    print("TOTAL ENTITIES WITH COPYRIGHT NOTICE (all entities):")
    print()

    grand_total_copyright = sum(results[t]['has_copyright_total'] for t in types)
    for entry_type in types:
        stats = results[entry_type]
        n = stats['total']
        if n == 0:
            continue
        has_copyright = stats['has_copyright_total']
        print(f"{type_names[entry_type]} (n={n:,}):")
        print(f"  - With copyright notice: {has_copyright:>8,} ({has_copyright/n*100:>5.2f}%)")
        print()

    print(f"TOTAL (n={total:,}):")
    print(f"  - With copyright notice: {grand_total_copyright:>8,} ({grand_total_copyright/total*100:>5.2f}%)")
    print()
    print()

    # Q7: Full text with copyright
    print("Q7. Entities with FULL license text that have valid copyright notice:")
    print()
    for entry_type in types:
        stats = results[entry_type]
        has_full = stats['has_full_text']
        if has_full == 0:
            continue
        with_copyright = stats['full_text_with_copyright']
        print(f"{type_names[entry_type]} (n={has_full:,} with full text):")
        print(f"  - With copyright notice:    {with_copyright:>8,} ({with_copyright/has_full*100:>5.2f}%)")
        print(f"  - Without copyright notice: {has_full - with_copyright:>8,} ({(has_full - with_copyright)/has_full*100:>5.2f}%)")
        print()

    total_with_copyright = sum(results[t]['full_text_with_copyright'] for t in types)
    print(f"TOTAL (n={total_has:,} with full text):")
    print(f"  - With copyright notice:    {total_with_copyright:>8,} ({total_with_copyright/total_has*100:>5.2f}%)")
    print(f"  - Without copyright notice: {total_has - total_with_copyright:>8,} ({(total_has - total_with_copyright)/total_has*100:>5.2f}%)")
    print()
    print()
    
    # Q8: Both full text AND copyright
    print("Q8. Entities with BOTH full license text AND valid copyright notice:")
    print()
    for entry_type in types:
        stats = results[entry_type]
        n = stats['total']
        if n == 0:
            continue
        both = stats['both_full_text_and_copyright']
        print(f"{type_names[entry_type]} (n={n:,}):")
        print(f"  - Has both: {both:>8,} ({both/n*100:>5.2f}%)")
        print()
    
    total_both = sum(results[t]['both_full_text_and_copyright'] for t in types)
    print(f"TOTAL (n={total:,}):")
    print(f"  - Has both: {total_both:>8,} ({total_both/total*100:>5.2f}%)")
    print()
    print()
    
    # Q9: File types for license text
    print("Q9. Where matches were identified (file types):")
    print()
    print("LICENSE TEXT (full text, >= 90% coverage):")
    print()
    for entry_type in types:
        stats = results[entry_type]
        file_types = stats['file_types']
        if not file_types:
            continue
        total_files = sum(file_types.values())
        num_entities = stats['has_full_text']
        print(f"{type_names[entry_type]} ({num_entities:,} entities, {total_files:,} files):")
        for ft in ['LICENSE', 'README', 'NOTICE', 'SOURCE']:
            count = file_types.get(ft, 0)
            if count > 0:
                print(f"  - {ft:10s}: {count:>8,} ({count/total_files*100:>5.2f}%)")
        print()

    print()
    print("COPYRIGHT NOTICES:")
    print()
    for entry_type in types:
        stats = results[entry_type]
        copyright_file_types = stats['copyright_file_types']
        if not copyright_file_types:
            continue
        total_files = sum(copyright_file_types.values())
        num_entities = stats['has_copyright_total']
        print(f"{type_names[entry_type]} ({num_entities:,} entities, {total_files:,} files):")
        for ft in ['LICENSE', 'README', 'NOTICE', 'SOURCE']:
            count = copyright_file_types.get(ft, 0)
            if count > 0:
                print(f"  - {ft:10s}: {count:>8,} ({count/total_files*100:>5.2f}%)")
        print()

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
    output_lines.append("# RQ1: Integrity Audit Analysis\n")
    output_lines.append(f"Generated: {os.popen('date').read().strip()}\n")
    output_lines.append("\n")

    # Analysis 1: ALL entities
    output_lines.append("\n" + "=" * 100 + "\n")
    output_lines.append("ANALYZING: ALL ENTITIES\n")
    output_lines.append("=" * 100 + "\n")

    all_results = analyze_integrity(entities, filter_func=None)
    all_output = capture_output(print_results, all_results, "RQ1: Integrity Audit (ALL ENTITIES)")
    output_lines.append(all_output)

    # Analysis 2: MIT/Apache-2.0/BSD-3-Clause subset
    output_lines.append("\n" + "=" * 100 + "\n")
    output_lines.append("ANALYZING: MIT/APACHE-2.0/BSD-3-CLAUSE SUBSET\n")
    output_lines.append("=" * 100 + "\n")

    target_filter = lambda licenses: shared_utils.has_target_license(licenses, TARGET_LICENSES)
    target_results = analyze_integrity(entities, filter_func=target_filter)
    target_output = capture_output(print_results, target_results, "RQ1: Integrity Audit (MIT/APACHE/BSD SUBSET)")
    output_lines.append(target_output)

    # Summary
    output_lines.append("\n" + "=" * 100 + "\n")
    output_lines.append("SUMMARY FOR PAPER (RQ1)\n")
    output_lines.append("=" * 100 + "\n")

    output_lines.append("\nALL ENTITIES:\n")
    total_all = sum(all_results[t]['total'] for t in ['dataset', 'model', 'application'])
    lacking_all = sum(all_results[t]['lacking_full_text'] for t in ['dataset', 'model', 'application'])
    both_all = sum(all_results[t]['both_full_text_and_copyright'] for t in ['dataset', 'model', 'application'])
    output_lines.append(f"  Total entities: {total_all:,}\n")
    output_lines.append(f"  Lacking full license text: {lacking_all:,} ({lacking_all/total_all*100:.2f}%)\n")
    output_lines.append(f"  Has both full text AND copyright: {both_all:,} ({both_all/total_all*100:.2f}%)\n")

    output_lines.append("\nMIT/APACHE-2.0/BSD-3-CLAUSE SUBSET:\n")
    total_target = sum(target_results[t]['total'] for t in ['dataset', 'model', 'application'])
    lacking_target = sum(target_results[t]['lacking_full_text'] for t in ['dataset', 'model', 'application'])
    both_target = sum(target_results[t]['both_full_text_and_copyright'] for t in ['dataset', 'model', 'application'])
    output_lines.append(f"  Total entities: {total_target:,}\n")
    output_lines.append(f"  Lacking full license text: {lacking_target:,} ({lacking_target/total_target*100:.2f}%)\n")
    output_lines.append(f"  Has both full text AND copyright: {both_target:,} ({both_target/total_target*100:.2f}%)\n")

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

    # Analysis 1: ALL entities
    print("\n" + "=" * 100)
    print("ANALYZING: ALL ENTITIES")
    print("=" * 100)

    all_results = analyze_integrity(entities, filter_func=None)
    print_results(all_results, "RQ1: Integrity Audit (ALL ENTITIES)")

    # Analysis 2: MIT/Apache-2.0/BSD-3-Clause subset
    print("\n" + "=" * 100)
    print("ANALYZING: MIT/APACHE-2.0/BSD-3-CLAUSE SUBSET")
    print("=" * 100)

    target_filter = lambda licenses: shared_utils.has_target_license(licenses, TARGET_LICENSES)
    target_results = analyze_integrity(entities, filter_func=target_filter)
    print_results(target_results, "RQ1: Integrity Audit (MIT/APACHE/BSD SUBSET)")

    # Summary
    print("\n" + "=" * 100)
    print("SUMMARY FOR PAPER (RQ1)")
    print("=" * 100)

    print("\nALL ENTITIES:")
    total_all = sum(all_results[t]['total'] for t in ['dataset', 'model', 'application'])
    lacking_all = sum(all_results[t]['lacking_full_text'] for t in ['dataset', 'model', 'application'])
    both_all = sum(all_results[t]['both_full_text_and_copyright'] for t in ['dataset', 'model', 'application'])
    print(f"  Total entities: {total_all:,}")
    print(f"  Lacking full license text: {lacking_all:,} ({lacking_all/total_all*100:.2f}%)")
    print(f"  Has both full text AND copyright: {both_all:,} ({both_all/total_all*100:.2f}%)")

    print("\nMIT/APACHE-2.0/BSD-3-CLAUSE SUBSET:")
    total_target = sum(target_results[t]['total'] for t in ['dataset', 'model', 'application'])
    lacking_target = sum(target_results[t]['lacking_full_text'] for t in ['dataset', 'model', 'application'])
    both_target = sum(target_results[t]['both_full_text_and_copyright'] for t in ['dataset', 'model', 'application'])
    print(f"  Total entities: {total_target:,}")
    print(f"  Lacking full license text: {lacking_target:,} ({lacking_target/total_target*100:.2f}%)")
    print(f"  Has both full text AND copyright: {both_target:,} ({both_target/total_target*100:.2f}%)")

    # Write to markdown file
    write_to_file(entities)
    print(f"\nResults written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

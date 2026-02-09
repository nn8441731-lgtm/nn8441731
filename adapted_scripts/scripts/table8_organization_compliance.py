#!/usr/bin/env python3
"""
Organization Compliance Analysis

Analyzes major AI organizations to identify assets failing both license text
and copyright notice requirements. Generates LaTeX table showing non-compliant
assets per organization.

Filters to MIT/Apache-2.0/BSD-3-Clause licensed assets only.
Orders organizations by follower count.


"""

import sys
import json
from pathlib import Path
from collections import defaultdict

# Import shared utilities
sys.path.insert(0, str(Path(__file__).parent.parent))
import shared_utils

# Configuration
# Auto-detect base directory (works on any system)
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent  # adapted_scripts/
DATA_DIR = BASE_DIR.parent  # main directory with JSONL file
DATA_FILE = DATA_DIR / 'filtered_complete_chains_cleaned.jsonl'
ORG_FILE = SCRIPT_DIR / 'hf_orgs_top_20260201_135507.json'
OUTPUT_FILE = DATA_DIR / 'outputs_new' / 'scripts' / 'organization_compliance_analysis.md'
THRESHOLD = 90.0  # License text coverage threshold
TOP_N_ORGS = 100  # Analyze top N organizations by followers
TARGET_LICENSES = {'MIT', 'Apache-2.0', 'BSD-3-Clause'}


def extract_organization(entity_id):
    """Extract organization name from entity ID (owner/repo format)."""
    if '/' in entity_id:
        return entity_id.split('/')[0].lower()
    return None


def has_full_license_text(entity):
    """Check if entity has full license text (>= 90% coverage)."""
    scancode = entity.get('scancode', [])
    if not scancode:
        return False

    for item in scancode:
        origins = item.get('origins', [])
        if not origins:
            continue

        for origin in origins:
            coverage = origin.get('match_coverage', 0.0)
            if coverage is None:
                coverage = 0.0

            if coverage >= THRESHOLD:
                return True

    return False


def has_copyright_notice(entity):
    """Check if entity has copyright notice."""
    copyrights = entity.get('copyrights', [])
    if not copyrights:
        return False

    # Check if any copyright has origins (file locations)
    for item in copyrights:
        origins = item.get('origins', [])
        if origins:
            return True

    return False


def analyze_organizations(entities, org_list):
    """Analyze compliance for each organization (all assets + MIT/Apache/BSD subset)."""

    # Track assets per organization
    org_assets = defaultdict(lambda: {
        # All assets (regardless of license)
        'total_all': 0,
        'all_no_license_text': 0,
        'all_no_copyright': 0,
        'all_no_both': 0,
        'all_has_license_text': 0,
        'all_has_copyright': 0,
        'all_has_both': 0,

        # MIT/Apache/BSD assets only
        'total': 0,
        'no_license_text': 0,
        'no_copyright': 0,
        'no_both': 0,
        'has_license_text': 0,
        'has_copyright': 0,
        'has_both': 0,
        'non_compliant_assets': []  # List of non-compliant MIT/Apache/BSD assets
    })

    # Process all entities
    for entity_id, entity in shared_utils.iter_all_entities(entities):
        org = extract_organization(entity_id)
        if not org:
            continue

        # Only track organizations in our top list
        if org not in org_list:
            continue

        # Filter to HuggingFace only (datasets and models, NOT applications/GitHub)
        entity_type = entity.get('type', '')
        if entity_type not in ['dataset', 'model']:
            continue

        # Analyze ALL HuggingFace assets first
        org_assets[org]['total_all'] += 1

        has_license = has_full_license_text(entity)
        has_copyright = has_copyright_notice(entity)

        if has_license:
            org_assets[org]['all_has_license_text'] += 1
        else:
            org_assets[org]['all_no_license_text'] += 1

        if has_copyright:
            org_assets[org]['all_has_copyright'] += 1
        else:
            org_assets[org]['all_no_copyright'] += 1

        if not has_license and not has_copyright:
            org_assets[org]['all_no_both'] += 1

        # Track all assets with BOTH
        if has_license and has_copyright:
            org_assets[org]['all_has_both'] += 1

        # Now check if it's MIT/Apache/BSD for permissive subset analysis
        licenses = shared_utils.extract_licenses_metadata(entity)
        if not shared_utils.has_target_license(licenses, TARGET_LICENSES):
            continue

        # Track MIT/Apache/BSD subset
        org_assets[org]['total'] += 1

        if has_license:
            org_assets[org]['has_license_text'] += 1
        else:
            org_assets[org]['no_license_text'] += 1

        if has_copyright:
            org_assets[org]['has_copyright'] += 1
        else:
            org_assets[org]['no_copyright'] += 1

        # Track MIT/Apache/BSD assets failing BOTH requirements
        if not has_license and not has_copyright:
            org_assets[org]['no_both'] += 1

        # Track MIT/Apache/BSD assets with BOTH
        if has_license and has_copyright:
            org_assets[org]['has_both'] += 1
            likes = entity.get('likes', 0) or 0
            entity_type = entity.get('type', 'unknown')
            org_assets[org]['non_compliant_assets'].append({
                'id': entity_id,
                'likes': likes,
                'type': entity_type
            })

    return org_assets


def generate_latex_table(org_assets, org_info_map):
    """Generate LaTeX table format (sorted by follower count)."""

    # Filter organizations with MIT/Apache/BSD assets (even if compliant)
    orgs_with_assets = {
        org: data for org, data in org_assets.items()
        if data['total'] > 0
    }

    if not orgs_with_assets:
        print("No organizations found with MIT/Apache/BSD assets")
        return []

    # Sort by follower count (descending)
    sorted_orgs = sorted(
        orgs_with_assets.items(),
        key=lambda x: org_info_map.get(x[0], {}).get('follower_count', 0),
        reverse=True
    )

    lines = []
    lines.append("# Organization Compliance Analysis\n\n")
    lines.append("## Major AI Organizations - MIT/Apache-2.0/BSD-3-Clause Assets\n\n")
    lines.append("Analyzing compliance for MIT/Apache-2.0/BSD-3-Clause licensed assets only.\n\n")
    lines.append("Organizations ordered by **follower count** (popularity).\n\n")

    # Summary statistics
    total_assets = sum(data['total'] for _, data in sorted_orgs)
    total_non_compliant = sum(data['no_both'] for _, data in sorted_orgs)
    lines.append(f"**Total MIT/Apache/BSD assets analyzed**: {total_assets:,}\n")
    lines.append(f"**Assets failing both license text AND copyright**: {total_non_compliant:,} ({total_non_compliant/total_assets*100:.1f}%)\n\n")

    # LaTeX table
    lines.append("### LaTeX Table Format\n\n")
    lines.append("```latex\n")
    lines.append("\\begin{table}[t]\n")
    lines.append("\\centering\n")
    lines.append("\\caption{Major AI organizations (ordered by follower count) with MIT/Apache-2.0/BSD-3-Clause assets failing both license text and copyright notice requirements. Top Asset shows the most popular non-compliant asset from each organization.}\n")
    lines.append("\\label{tab:org_compliance}\n")
    lines.append("\\begin{tabular}{@{}lrrll@{}}\n")
    lines.append("\\toprule\n")
    lines.append("\\textbf{Organization} & \\textbf{Followers} & \\textbf{Failing Both} & \\textbf{Top Asset} & \\textbf{Likes} \\\\\n")
    lines.append("\\midrule\n")

    # Top 10 organizations by followers
    for org, data in sorted_orgs[:10]:
        # Get organization display name and followers
        org_title = org_info_map.get(org, {}).get('title', org.title())
        followers = org_info_map.get(org, {}).get('follower_count', 0)

        # Get top non-compliant asset by likes
        if data['non_compliant_assets']:
            top_asset = max(data['non_compliant_assets'], key=lambda x: x['likes'])
            asset_name = top_asset['id'].split('/')[-1] if '/' in top_asset['id'] else top_asset['id']
            asset_likes = top_asset['likes']
        else:
            asset_name = "---"
            asset_likes = 0

        # Format followers (e.g., 116K, 80K)
        if followers >= 1000:
            followers_str = f"{followers/1000:.0f}K"
        else:
            followers_str = str(followers)

        lines.append(f"{org_title} & {followers_str} & {data['no_both']} & {asset_name} & {asset_likes:,} \\\\\n")

    lines.append("\\midrule\n")
    lines.append(f"\\textbf{{Total}} & & \\textbf{{{total_non_compliant:,}}} & & \\\\\n")
    lines.append("\\bottomrule\n")
    lines.append("\\end{tabular}\n")
    lines.append("\\end{table}\n")
    lines.append("```\n\n")

    # Detailed breakdown - All assets
    lines.append("### Detailed Breakdown - All Assets (All Licenses)\n\n")
    lines.append("| Organization | Followers | Total Assets | No License Text | No Copyright | Failing Both | Has Both | License Rate | Copyright Rate |\n")
    lines.append("|--------------|-----------|--------------|-----------------|--------------|--------------|----------|--------------|----------------|\n")

    for org, data in sorted_orgs[:20]:
        org_title = org_info_map.get(org, {}).get('title', org.title())
        followers = org_info_map.get(org, {}).get('follower_count', 0)
        total_all = data['total_all']

        all_license_rate = (data['all_has_license_text'] / total_all * 100) if total_all > 0 else 0
        all_copyright_rate = (data['all_has_copyright'] / total_all * 100) if total_all > 0 else 0

        lines.append(
            f"| {org_title} | {followers:,} | {total_all:,} | "
            f"{data['all_no_license_text']:,} | {data['all_no_copyright']:,} | {data['all_no_both']:,} | "
            f"{data['all_has_both']:,} | "
            f"{all_license_rate:.1f}% | {all_copyright_rate:.1f}% |\n"
        )

    lines.append("\n")

    # Detailed breakdown - MIT/Apache/BSD subset
    lines.append("### Detailed Breakdown - MIT/Apache-2.0/BSD-3-Clause Subset\n\n")
    lines.append("| Organization | Followers | MIT/Apache/BSD | No License Text | No Copyright | Failing Both | Has Both | License Rate | Copyright Rate |\n")
    lines.append("|--------------|-----------|----------------|-----------------|--------------|--------------|----------|--------------|----------------|\n")

    for org, data in sorted_orgs[:20]:
        org_title = org_info_map.get(org, {}).get('title', org.title())
        followers = org_info_map.get(org, {}).get('follower_count', 0)
        total_permissive = data['total']

        if total_permissive == 0:
            # Skip organizations with no MIT/Apache/BSD assets
            continue

        license_rate = (data['has_license_text'] / total_permissive * 100) if total_permissive > 0 else 0
        copyright_rate = (data['has_copyright'] / total_permissive * 100) if total_permissive > 0 else 0

        lines.append(
            f"| {org_title} | {followers:,} | {total_permissive:,} | "
            f"{data['no_license_text']:,} | {data['no_copyright']:,} | {data['no_both']:,} | "
            f"{data['has_both']:,} | "
            f"{license_rate:.1f}% | {copyright_rate:.1f}% |\n"
        )

    lines.append("\n")

    # Top non-compliant assets per organization
    lines.append("### Top Non-Compliant Assets by Organization\n\n")

    for org, data in sorted_orgs[:10]:
        org_title = org_info_map.get(org, {}).get('title', org.title())
        lines.append(f"#### {org_title} ({data['no_both']} non-compliant assets)\n\n")

        # Sort assets by likes
        top_assets = sorted(data['non_compliant_assets'], key=lambda x: x['likes'], reverse=True)[:5]

        lines.append("| Asset | Type | Likes |\n")
        lines.append("|-------|------|-------|\n")

        for asset in top_assets:
            asset_name = asset['id'].split('/')[-1] if '/' in asset['id'] else asset['id']
            lines.append(f"| [{asset_name}](https://huggingface.co/{asset['id']}) | {asset['type']} | {asset['likes']:,} |\n")

        lines.append("\n")

    return lines


def main():
    """Main function."""
    print("=" * 100)
    print("Organization Compliance Analysis")
    print("=" * 100)
    print()

    # Load organization data
    print(f"Loading {ORG_FILE}...")
    with open(ORG_FILE, 'r', encoding='utf-8') as f:
        org_data = json.load(f)

    # Filter to top N orgs with models
    orgs = [
        org for org in org_data['organizations']
        if org.get('model_count', 0) > 0
    ][:TOP_N_ORGS]

    print(f"Analyzing top {len(orgs)} organizations with models")

    # Create org lookup map (name -> info)
    org_info_map = {org['name'].lower(): org for org in orgs}
    org_list = set(org_info_map.keys())

    # Load entity data
    print(f"Loading {DATA_FILE}...")
    entities = shared_utils.load_jsonl_file(DATA_FILE)
    print(f"Loaded {len(entities):,} entities\n")

    # Analyze compliance
    print("Analyzing organization compliance...")
    org_assets = analyze_organizations(entities, org_list)

    print(f"Found {len(org_assets)} organizations with assets in dataset")

    # Generate output
    output_lines = generate_latex_table(org_assets, org_info_map)

    # Write to file
    Path(OUTPUT_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.writelines(output_lines)

    print(f"\nResults written to: {OUTPUT_FILE}")
    print()


if __name__ == '__main__':
    main()

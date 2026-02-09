#!/usr/bin/env python3
"""
Master Analysis Script - Runs All Paper Analyses

This script runs all 7 analysis scripts in sequence and generates:
- All paper tables (1, 2, 7, 8, 10)
- Basic statistics
- Supply chain analysis
- Comprehensive summary report

Usage:
    python run_full_analysis.py

Output:
    All results saved to ./outputs_new/
    Summary report: ./outputs_new/ANALYSIS_SUMMARY.md

"""

import sys
import subprocess
import time
from pathlib import Path
from datetime import datetime

# Configuration
SCRIPT_DIR = Path(__file__).parent  # Root directory (where this script and JSONL are)
ADAPTED_SCRIPTS_DIR = SCRIPT_DIR / 'adapted_scripts'
OUTPUT_DIR = SCRIPT_DIR / 'outputs_new'

# Scripts to run in order
SCRIPTS = [
    {
        'name': 'Basic Counts',
        'script': ADAPTED_SCRIPTS_DIR / 'good_scripts' / 'basic_dataset_model_app_counts.py',
        'output': OUTPUT_DIR / 'good_scripts' / 'paper_statistics_results.md',
        'table': None,
        'description': 'Entity counts and license distributions'
    },
    {
        'name': 'Build Supply Chains',
        'script': ADAPTED_SCRIPTS_DIR / 'good_scripts' / 'build_supply_chains.py',
        'output': OUTPUT_DIR / 'good_scripts' / 'chain_analysis_summary.md',
        'table': None,
        'description': 'Constructs dataset→model→application chains'
    },
    {
        'name': 'Table 1: License Integrity',
        'script': ADAPTED_SCRIPTS_DIR / 'good_scripts' / 'table1_license_integrity_audit.py',
        'output': OUTPUT_DIR / 'good_scripts' / 'integrity_audit_results.md',
        'table': 1,
        'description': 'License text and copyright notice presence'
    },
    {
        'name': 'Table 2: Attribution Preservation (RQ2)',
        'script': ADAPTED_SCRIPTS_DIR / 'good_scripts' / 'rq2_attribution_preservation_audit.py',
        'output': OUTPUT_DIR / 'good_scripts' / 'copyright_attribution_audit.md',
        'table': 2,
        'description': 'Copyright attribution preservation across supply chain'
    },
    {
        'name': 'Table 7: License Locations',
        'script': ADAPTED_SCRIPTS_DIR / 'good_scripts' / 'table7_license_location_analysis.py',
        'output': OUTPUT_DIR / 'good_scripts' / 'license_location_table.md',
        'table': 7,
        'description': 'Where license text and copyright notices are located'
    },
    {
        'name': 'Table 10: File Availability',
        'script': ADAPTED_SCRIPTS_DIR / 'good_scripts' / 'table10_file_availability_analysis.py',
        'output': OUTPUT_DIR / 'good_scripts' / 'missing_license_readme_results.md',
        'table': 10,
        'description': 'LICENSE and README file presence'
    },
    {
        'name': 'Table 8: Organization Compliance',
        'script': ADAPTED_SCRIPTS_DIR / 'scripts' / 'table8_organization_compliance.py',
        'output': OUTPUT_DIR / 'scripts' / 'organization_compliance_analysis.md',
        'table': 8,
        'description': 'Compliance rates for top organizations'
    }
]


def print_header():
    """Print script header."""
    print("=" * 100)
    print("MASTER ANALYSIS SCRIPT - Full Paper Analysis Pipeline")
    print("=" * 100)
    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Running {len(SCRIPTS)} analysis scripts...")
    print("=" * 100)
    print()


def run_script(script_info, index):
    """Run a single analysis script."""
    print(f"[{index}/{len(SCRIPTS)}] Running: {script_info['name']}")
    print(f"    Description: {script_info['description']}")

    start_time = time.time()

    try:
        # Run the script
        result = subprocess.run(
            [sys.executable, str(script_info['script'])],
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        elapsed = time.time() - start_time

        if result.returncode == 0:
            print(f"    ✅ Completed in {elapsed:.1f}s")
            if script_info['output'].exists():
                print(f"    Output: {script_info['output']}")
            return True, elapsed, None
        else:
            print(f"    ❌ Failed after {elapsed:.1f}s")
            print(f"    Error: {result.stderr[:200]}")
            return False, elapsed, result.stderr

    except subprocess.TimeoutExpired:
        print(f"    ❌ Timeout after 10 minutes")
        return False, 600, "Timeout"
    except Exception as e:
        print(f"    ❌ Exception: {str(e)}")
        return False, 0, str(e)
    finally:
        print()


def extract_key_metrics(output_files):
    """Extract key metrics from output files."""
    metrics = {}

    # Extract from Table 1 (Integrity Audit)
    table1_file = OUTPUT_DIR / 'good_scripts' / 'integrity_audit_results.md'
    if table1_file.exists():
        content = table1_file.read_text()
        # Extract total entities
        for line in content.split('\n'):
            if 'TOTAL (n=' in line:
                # Extract number
                import re
                match = re.search(r'TOTAL \(n=([\d,]+)\):', line)
                if match:
                    metrics['total_entities'] = match.group(1)
                    break

    # Extract from Table 2 (Attribution)
    table2_file = OUTPUT_DIR / 'good_scripts' / 'copyright_attribution_audit.md'
    if table2_file.exists():
        content = table2_file.read_text()
        for line in content.split('\n'):
            if 'Apps preserving EITHER (union):' in line:
                metrics['apps_preserving_any'] = line.split()[-1].strip('()')

    return metrics


def generate_summary_report(results, total_time, metrics):
    """Generate comprehensive summary report."""
    summary_file = OUTPUT_DIR / 'ANALYSIS_SUMMARY.md'
    summary_file.parent.mkdir(parents=True, exist_ok=True)

    with open(summary_file, 'w') as f:
        f.write("# Analysis Summary Report\n\n")
        f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Total Runtime**: {total_time:.1f} seconds ({total_time/60:.1f} minutes)\n")
        f.write(f"**Input Data**: filtered_complete_chains_cleaned.jsonl\n\n")

        # Overall status
        successful = sum(1 for r in results if r['success'])
        f.write(f"## Overall Status\n\n")
        f.write(f"- **Scripts Run**: {len(results)}\n")
        f.write(f"- **Successful**: {successful}\n")
        f.write(f"- **Failed**: {len(results) - successful}\n\n")

        if successful == len(results):
            f.write("✅ **All analyses completed successfully!**\n\n")
        else:
            f.write("⚠️ **Some analyses failed - check details below**\n\n")

        # Key metrics
        if metrics:
            f.write("## Key Metrics\n\n")
            if 'total_entities' in metrics:
                f.write(f"- **Total Entities Analyzed**: {metrics['total_entities']}\n")
            if 'apps_preserving_any' in metrics:
                f.write(f"- **Apps Preserving Attribution**: {metrics['apps_preserving_any']}\n")
            f.write("\n")

        # Script results
        f.write("## Script Results\n\n")
        f.write("| # | Script | Status | Time (s) | Output File |\n")
        f.write("|---|--------|--------|----------|-------------|\n")

        for i, result in enumerate(results, 1):
            status = "✅" if result['success'] else "❌"
            output = result['output'].name if result['output'].exists() else "Not found"
            f.write(f"| {i} | {result['name']} | {status} | {result['time']:.1f} | {output} |\n")

        f.write("\n")

        # Paper tables generated
        f.write("## Paper Tables Generated\n\n")
        f.write("| Table | Status | Output File |\n")
        f.write("|-------|--------|-------------|\n")

        for result in results:
            if result['table']:
                status = "✅" if result['success'] else "❌"
                output = result['output'].name if result['output'].exists() else "Not generated"
                f.write(f"| Table {result['table']} | {status} | {output} |\n")

        f.write("\n")

        # Output locations
        f.write("## Output Locations\n\n")
        f.write(f"All results are saved to: `{OUTPUT_DIR}/`\n\n")
        f.write("**good_scripts/**\n")
        for result in results:
            if 'good_scripts' in str(result['output']):
                marker = "✅" if result['output'].exists() else "❌"
                f.write(f"- {marker} `{result['output'].name}`\n")

        f.write("\n**scripts/**\n")
        for result in results:
            if 'scripts/' in str(result['output']) and 'good_scripts' not in str(result['output']):
                marker = "✅" if result['output'].exists() else "❌"
                f.write(f"- {marker} `{result['output'].name}`\n")

        f.write("\n")

        # Errors
        if any(not r['success'] for r in results):
            f.write("## Errors\n\n")
            for i, result in enumerate(results, 1):
                if not result['success']:
                    f.write(f"### {i}. {result['name']}\n\n")
                    f.write(f"```\n{result['error'][:500]}\n```\n\n")

        # Next steps
        f.write("## Next Steps\n\n")
        if successful == len(results):
            f.write("1. Review the generated tables in the output files\n")
            f.write("2. Verify the metrics match your paper's reported values\n")
            f.write("3. Copy results to your paper manuscript\n")
        else:
            f.write("1. Check the errors above\n")
            f.write("2. Fix any issues\n")
            f.write("3. Re-run this script\n")

    return summary_file


def main():
    """Main function."""
    print_header()

    overall_start = time.time()
    results = []

    # Run all scripts
    for i, script_info in enumerate(SCRIPTS, 1):
        success, elapsed, error = run_script(script_info, i)

        results.append({
            'name': script_info['name'],
            'success': success,
            'time': elapsed,
            'output': script_info['output'],
            'table': script_info['table'],
            'error': error
        })

    total_time = time.time() - overall_start

    # Extract key metrics
    print("Extracting key metrics...")
    metrics = extract_key_metrics([r['output'] for r in results])
    print()

    # Generate summary report
    print("Generating summary report...")
    summary_file = generate_summary_report(results, total_time, metrics)
    print(f"Summary report: {summary_file}")
    print()

    # Final status
    print("=" * 100)
    successful = sum(1 for r in results if r['success'])

    if successful == len(results):
        print(f"✅ SUCCESS: All {len(results)} analyses completed in {total_time:.1f}s")
    else:
        print(f"⚠️  PARTIAL: {successful}/{len(results)} analyses completed in {total_time:.1f}s")
        print(f"   Failed: {', '.join(r['name'] for r in results if not r['success'])}")

    print("=" * 100)
    print()

    return 0 if successful == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())

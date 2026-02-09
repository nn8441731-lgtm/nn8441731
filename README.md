# Replication Package: License Integrity Audit

---

## 📋 Overview

This replication package contains all code and data necessary to reproduce the results in our paper analyzing license compliance in AI supply chains.

**Complete replication in 2 steps:**
1. Run mining script to collect data (optional - data provided)
2. Run analysis script to generate all paper tables (~9 seconds)

---

## 📦 Package Contents

```
replication/
├── README.md                                  # This file
├── requirements.txt                           # Python dependencies
│
├── mine.py                                    # Data mining script
├── api.env                                    # API configuration (add your keys)
├── scancode_rule_lookup.json                 # License detection rules
├── llm_yes_calls_filtered_frequencies_open.json  # LLM validation data
│
├── filtered_complete_chains_cleaned.jsonl    # Output data (38,518 entities)
│
├── run_full_analysis.py                      # Analysis master script
└── adapted_scripts/                          # Analysis pipeline
    ├── good_scripts/                         # Main analysis scripts (6)
    ├── scripts/                              # Supporting scripts (1)
    └── shared_utils.py                       # Common utilities
```

---

## 🚀 Quick Start (Using Provided Data)

### Prerequisites
```bash
# Python 3.12.9
conda create -n paper-replication python=3.12.9
conda activate paper-replication

# Install dependencies
pip install -r requirements.txt
```

### Run Analysis
```bash
# Generate all paper tables
python run_full_analysis.py

# View results
cat outputs_new/ANALYSIS_SUMMARY.md
```

**Done!** All tables generated in ~9 seconds.

---

## 🔄 Full Replication (From Scratch)

### Step 1: Data Collection (Optional)

**Note**: Pre-collected data is included (`filtered_complete_chains_cleaned.jsonl`). Only run this if you want to collect fresh data.

```bash
# Configure API keys
cp api.env.example api.env
# Edit api.env and add your HuggingFace API token

# Run mining script
python mine.py --type all

# Output: filtered_complete_chains_cleaned.jsonl
```


### Step 2: Analysis

```bash
# Run complete analysis pipeline
python run_full_analysis.py

# Results in outputs_new/
```

---

## 📊 Expected Outputs

### Paper Tables Generated:

| Table | File | Key Finding |
|-------|------|-------------|
| Table 1 | `integrity_audit_results.md` | 55.28% lack full license text |
| Table 2 | `copyright_attribution_audit.md` | 6.48% preserve upstream copyright |
| Table 7 | `license_location_table.md` | 97.0% datasets use README not LICENSE |
| Table 8 | `organization_compliance_analysis.md` | 1.5% of top orgs are compliant |
| Table 10 | `missing_license_readme_results.md` | 96.5% datasets missing LICENSE file |

### Summary Report:
- `outputs_new/ANALYSIS_SUMMARY.md` - Complete metrics & verification

---

## 🔍 System Requirements

### Software:
- **Python**: 3.12.9
- **OS**: macOS, Linux, or Windows

### For Analysis Only (using provided data):
- **RAM**: 8GB minimum
- **Disk**: 2GB free space

### For Full Data Mining (optional):
- **RAM**: 16GB minimum (32GB recommended)
- **Disk**: 2TB free space (for downloading repos, processing, and intermediate files)

### Python Dependencies:
```bash
# Install all dependencies:
pip install -r requirements.txt

# Mining script (mine.py):
- huggingface-hub>=0.20.0
- python-dotenv>=1.0.0
- pyyaml>=6.0
- requests>=2.31.0
- tqdm>=4.65.0
- scancode-toolkit>=32.0.0  # License detection engine

# Analysis scripts:
- No external dependencies (standard library only)
```

### System Requirements (for mining only):
```bash
# Required system tools (usually pre-installed):
- tar (for archiving)
- gzip (for compression)

# Optional but recommended:
- pigz (parallel gzip for faster compression)
  Install: apt-get install pigz  # Ubuntu/Debian
           brew install pigz      # macOS

# ScanCode Toolkit (installed via pip above)
# Note: ScanCode installation may require:
# - C compiler (gcc/clang)
# - Additional disk space (~500MB for ScanCode + plugins)
```

---

## 📈 Data Description

### Input: `filtered_complete_chains_cleaned.jsonl`

**Format**: JSON Lines (one entity per line)

**Schema**:
```json
{
  "id": "string",                    // Entity identifier
  "type": "dataset|model|application",
  "licenses": ["license-id", ...],   // Metadata licenses
  "likes": int,                      // Community engagement
  "base_models": ["id", ...],        // For models: base models
  "datasets": ["id", ...],           // For models: training datasets
  "models": ["id", ...],             // For apps: used models
  "scancode": [                      // Detected licenses
    {
      "license_expression_spdx": "license-id",
      "origins": [
        {
          "file_path": "path/to/file",
          "match_coverage": float  // 0-100%
        }
      ]
    }
  ],
  "copyrights": [                    // Detected copyrights
    {
      "copyright": "Copyright text",
      "origins": [{"file_path": "path"}]
    }
  ],
  "holders": [                       // Copyright holders
    {
      "holder": "Holder name",
      "start_line": int,
      "end_line": int
    }
  ]
}
```

### Reproducing Paper Results:

**Option 1: Using Provided Data (Fast)**
```bash
# Install dependencies
pip install -r requirements.txt

# Run analysis
python run_full_analysis.py

# Verify results match paper
# - Table 1: 55.28% lack full license text
# - Table 2: 6.48% preserve copyright
# - Table 8: 1.5% orgs compliant
# - Table 10: 96.5% datasets missing LICENSE
```

**Option 2: Full Replication (Slow)**
```bash
# 1. Add API tokens to api.env:
#    - HuggingFace API tokens (HUGGINGFACE_TOKEN_1, etc.)
#    - GitHub personal access tokens (api_key1/GITHUB_TOKEN_1, etc.)
# 2. Run mining script 
python mine.py --type all

# 3. Run analysis
python run_full_analysis.py
```


## 🐛 Troubleshooting

### Issue: Mining script fails with API error
**Solution**: Ensure valid HuggingFace API token in `api.env`

### Issue: "ModuleNotFoundError"
**Solution**: Install dependencies: `pip install -r requirements.txt`

### Issue: Slow mining
**Solution**: Expected - full dataset can take weeks to mine

---

## 📄 License

This replication package is released under CC-BY-4.0.

The paper analyzes open-source licensing practices; code follows best practices.

---



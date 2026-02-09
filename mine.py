"""
HUGGINGFACE SUPER SCRAPER (v3.0 - With Dataset Link Resolution)
----------------------------------------------------------------
1. Generates Master List
2. Fetches Accurate File Sizes
3. Fetches Total Downloads
4. Fetches Readmes (Max 300MB, Reports Truncation)
5. Saves to JSONL
6. NEW: Resolves ambiguous/missing dataset references via API
"""

import os
import json
import time
import threading
import logging
import argparse
import random
import gc
import requests
import yaml
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import load_dotenv
from huggingface_hub import HfApi, hf_hub_url
from huggingface_hub.utils import RepositoryNotFoundError, GatedRepoError
from tqdm import tqdm
from collections import defaultdict, Counter
import warnings  # ← ADD THIS LINE


# --- CONFIGURATION ---
NUM_WORKERS = 16
BATCH_SIZE = 50       
MAX_TEXT_SIZE = 300_000_000 # 300 MB Limit
OUTPUT_DIR = 'metadata_results'
LIST_DIR = 'outputs'
RESOLUTION_DIR = 'resolution_results'

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LIST_DIR, exist_ok=True)
os.makedirs(RESOLUTION_DIR, exist_ok=True)
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    filename=f'logs/super_fetch_{datetime.now().strftime("%Y%m%d")}.log',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
console_logger = logging.getLogger("console")
console_logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
console_logger.addHandler(ch)

load_dotenv(dotenv_path='api.env')

# ------------------------------------------------------------------
# PART 1: TOKEN MANAGER
# ------------------------------------------------------------------
class SmartTokenManager:
    def __init__(self, tokens):
        self.tokens = tokens
        self.token_status = {t: 0 for t in tokens} 
        self.lock = threading.Lock()
        self.base_cooldown = 60 

    def get_token(self):
        while True:
            current_time = time.time()
            with self.lock:
                for token in self.tokens:
                    if self.token_status[token] <= current_time:
                        return token
                wait_times = [t - current_time for t in self.token_status.values()]
                min_wait = min(wait_times)
            if min_wait > 0: time.sleep(min(min_wait + 1, 5))

    def report_rate_limit(self, token):
        with self.lock:
            self.token_status[token] = time.time() + self.base_cooldown
            return self.base_cooldown

def load_tokens():
    tokens = []
    i = 1
    while True:
        token = os.getenv(f"HUGGINGFACE_TOKEN_{i}")
        if not token: break
        tokens.append(token)
        i += 1
    return tokens

# ------------------------------------------------------------------
# PART 2: MASTER LIST GENERATOR
# ------------------------------------------------------------------
def fetch_ids_manual(asset_type, token_manager):
    filename = os.path.join(LIST_DIR, f"{asset_type}s.json")
    if os.path.exists(filename) and os.path.getsize(filename) > 100:
        console_logger.info(f"✅ Master list found: {filename}")
        return filename

    console_logger.info(f"🚀 Fetching full {asset_type} list (Manual Pagination)...")
    url = f"https://huggingface.co/api/{asset_type}s"
    params = {'limit': 1000, 'full': 'false', 'config': 'false'}
    count = 0
    temp_file = filename + ".tmp"
    
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write("[\n")
        first_item = True
        
        while url:
            token = token_manager.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=20)
                if resp.status_code == 429:
                    token_manager.report_rate_limit(token)
                    continue 
                if resp.status_code != 200: break

                data = resp.json()
                if not data: break

                for item in data:
                    if not first_item: f.write(",\n")
                    json.dump(item['id'], f)
                    first_item = False
                    count += 1
                
                print(f"   Fetched {count:,} {asset_type}s...", end='\r')

                link_header = resp.headers.get('Link')
                if link_header and 'rel="next"' in link_header:
                    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
                    if match:
                        url = match.group(1)
                        params = {} 
                    else: url = None
                else: url = None
                    
            except Exception: time.sleep(5)
                
        f.write("\n]")
    
    os.replace(temp_file, filename)
    console_logger.info(f"\n✅ Saved {count:,} IDs to {filename}")
    return filename

# ------------------------------------------------------------------
# PART 3: SUPER WORKER (Smart Streaming + 300MB Cap)
# ------------------------------------------------------------------
def download_readme_smart(repo_id, repo_type, token, siblings):
    target_file_obj = None
    if siblings:
        for s in siblings:
            if s.rfilename.lower() == 'readme.md':
                target_file_obj = s
                break
    
    if not target_file_obj: return None, None

    # --- CHECK 1: LFS DETECTION ---
    if getattr(target_file_obj, 'lfs', None):
        return "[SKIPPED] README is an LFS Binary Blob (Not text)", None

    # --- CHECK 2: DOWNLOAD WITH CAP ---
    url = hf_hub_url(repo_id=repo_id, filename=target_file_obj.rfilename, repo_type=repo_type)
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers, timeout=60, stream=True)
        
        if response.status_code == 200:
            content_chunks = []
            current_size = 0
            truncated = False
            
            for chunk in response.iter_content(chunk_size=10240, decode_unicode=True):
                if chunk:
                    content_chunks.append(chunk)
                    current_size += len(chunk)
                    
                    if current_size > MAX_TEXT_SIZE:
                        truncated = True
                        break
            
            full_content = "".join(content_chunks)
            
            if truncated:
                size_mb = MAX_TEXT_SIZE / (1024 * 1024)
                console_logger.warning(f"⚠️ [TRUNCATED] {repo_id} Readme exceeded {size_mb:.2f}MB")
                full_content += f"\n... [TRUNCATED: EXCEEDED {size_mb:.2f}MB LIMIT]"

            card_data = None
            if full_content.startswith('---'):
                try:
                    header_slice = full_content[:50000] 
                    parts = header_slice.split('---', 2)
                    if len(parts) >= 3:
                        card_data = yaml.safe_load(parts[1])
                except: pass
                
            return full_content, card_data
            
    except Exception: pass
    return None, None

def super_worker(repo_id, repo_type, token_manager, writer):
    api = HfApi()
    attempts = 0
    
    while attempts < 5:
        token = token_manager.get_token()
        try:
            if repo_type == 'model':
                info = api.model_info(repo_id, token=token, files_metadata=True)
            else:
                info = api.dataset_info(repo_id, token=token, files_metadata=True)

            try:
                if repo_type == 'model':
                    stats = api.model_info(repo_id, token=token, expand=['downloadsAllTime'])
                else:
                    stats = api.dataset_info(repo_id, token=token, expand=['downloadsAllTime'])
                total_downloads = getattr(stats, 'downloads_all_time', 0)
            except: total_downloads = 0

            readme_text, card_data = download_readme_smart(repo_id, repo_type, token, info.siblings)

            data = {
                'id': info.id,
                'sha': getattr(info, 'sha', None),
                'last_modified': str(info.last_modified) if getattr(info, 'last_modified', None) else None,
                'created_at': str(info.created_at) if getattr(info, 'created_at', None) else None,
                'private': getattr(info, 'private', False),
                'gated': getattr(info, 'gated', False),
                'downloads_30d': getattr(info, 'downloads', 0),
                'downloads_all_time': total_downloads,
                'likes': getattr(info, 'likes', 0),
                'tags': getattr(info, 'tags', []),
                'readme_content': readme_text, 
                'card_data': card_data,
            }

            if repo_type == 'model':
                data['pipeline_tag'] = getattr(info, 'pipeline_tag', None)
                data['library_name'] = getattr(info, 'library_name', None)

            files = []
            total_size = 0
            siblings = getattr(info, 'siblings', []) or []
            for sibling in siblings:
                f_size = getattr(sibling, 'size', 0) or 0
                file_entry = {'filename': sibling.rfilename, 'size': f_size}
                if getattr(sibling, 'lfs', None):
                    file_entry['lfs'] = {'size': sibling.lfs.size, 'sha256': sibling.lfs.sha256}
                    if f_size == 0: f_size = sibling.lfs.size
                files.append(file_entry)
                total_size += f_size

            data['files'] = files
            data['file_count'] = len(files)
            data['total_size'] = total_size

            writer.write(data)
            del info, stats, readme_text, data
            return True

        except Exception as e:
            err = str(e)
            if '429' in err:
                token_manager.report_rate_limit(token)
                attempts += 1
                continue 
            
            if isinstance(e, (RepositoryNotFoundError, GatedRepoError)) or '404' in err or '401' in err:
                return False
            
            logging.error(f"Error {repo_id}: {err}")
            attempts += 1
            time.sleep(1)
    return False

# ------------------------------------------------------------------
# PART 4: ORCHESTRATOR
# ------------------------------------------------------------------
class JsonlWriter:
    def __init__(self, filepath):
        self.filepath = filepath
        self.lock = threading.Lock()
    def write(self, data):
        with self.lock:
            with open(self.filepath, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data) + '\n')
    def get_existing_ids(self):
        if not os.path.exists(self.filepath): return set()
        ids = set()
        console_logger.info(f"Scanning {self.filepath} for resume...")
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        if 'id' in data: ids.add(data['id'])
                    except: continue
        except: pass
        return ids

def load_source_ids(filepath):
    console_logger.info(f"Loading source: {filepath}...")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list): ids = [x['id'] if isinstance(x, dict) else x for x in data]
        elif isinstance(data, dict): ids = list(data.keys())
        else: ids = []
        del data
        gc.collect()
        console_logger.info(f"✅ Loaded {len(ids):,} IDs.")
        return ids
    except Exception as e:
        console_logger.error(f"Error loading source: {e}")
        return []

def run_pipeline(repo_type, token_manager, test_mode=False):
    if not test_mode:
        list_file = fetch_ids_manual(repo_type, token_manager)
        if not list_file: return
    else: list_file = "TEST_MODE"

    output_file = f"{OUTPUT_DIR}/{repo_type}s.jsonl"
    if test_mode: 
        output_file = f"{OUTPUT_DIR}/TEST_{repo_type}s.jsonl"
        if os.path.exists(output_file): os.remove(output_file)

    writer = JsonlWriter(output_file)
    
    if test_mode:
        if repo_type == 'model':
            target_ids = [
                # Dataset reference diversity (Phase 5 testing)
                "salakash/SamKash-Tolstoy",
                "MCG-NJU/SteadyDancer-14B",
                "fal/FLUX.2-Tiny-AutoEncoder",
                "opendatalab/MinerU-HTML",
                "allenai/Olmo-3-32B-Think",
                "nvidia/multitalker-parakeet-streaming-0.6b-v1",
                "google-bert/bert-base-uncased",
                "Genius-Society/MiVOLO",
                "Genius-Society/svhn",
                "sentence-transformers/all-MiniLM-L6-v2",
                "joeddav/xlm-roberta-large-xnli",
                "nvidia/canary-qwen-2.5b",
                "nvidia/diar_streaming_sortformer_4spk-v2.1",
                "google-t5/t5-small",
                "facebook/sam3",
                
                # Base model chain testing (Phase 6 filtering)
                "black-forest-labs/FLUX.2-dev",
                "Tongyi-MAI/Z-Image-Turbo",
                "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union",
                "Comfy-Org/z_image_turbo",
                "nvidia/Orchestrator-8B",
                "deepseek-ai/DeepSeek-V3.2",
                "AIDC-AI/Ovis-Image-7B",
                "PrimeIntellect/INTELLECT-3",
                "deepseek-ai/DeepSeek-V3.2-Speciale",
                "microsoft/Fara-7B",
                "jayn7/Z-Image-Turbo-GGUF",
                "apple/starflow",
                "tencent/HunyuanOCR",
                "mistralai/Mistral-Large-3-675B-Instruct-2512",
                "deepseek-ai/DeepSeek-Math-V2",
                
                # File mining variety (Phase 7)
                "T5B/Z-Image-Turbo-FP8",
                "mistralai/Ministral-3-14B-Instruct-2512",
                "Supertone/supertonic",
                "stepfun-ai/Step-Audio-R1",
                "deepseek-ai/DeepSeek-V3.2-Exp",
                "tencent/HunyuanVideo-1.5",
                "moonshotai/Kimi-K2-Thinking",
                "deepseek-ai/DeepSeek-OCR",
                "Qwen/Qwen-Image-Edit-2509",
                "apple/CLaRa-7B-Instruct",
                
                # Edge cases
                "unsloth/Qwen3-Next-80B-A3B-Instruct-GGUF",
                "mistralai/Ministral-3-3B-Instruct-2512",
                "stepfun-ai/GELab-Zero-4B-preview",
                "arcee-ai/Trinity-Mini",
                "ostris/zimage_turbo_training_adapter",
                "mistralai/Ministral-3-14B-Reasoning-2512",
                "meta-llama/Llama-3.1-8B-Instruct",
                "ArliAI/gpt-oss-20b-Derestricted",
                "Comfy-Org/flux2-dev",
                "city96/FLUX.2-dev-gguf"
            ]
        else:  # datasets
            target_ids = [
                # Popular datasets referenced by models
                "fka/awesome-chatgpt-prompts",
                "nex-agi/agent-sft",
                "openai/gsm8k",
                "ytz20/LMSYS-Chat-GPT-5-Chat-Response",
                "nvidia/ToolScale",
                "openai/gdpval",
                "opendatalab/AICC",
                "PleIAs/SYNTH",
                "nick007x/arxiv-papers",
                "ccmusic-database/music_genre",
                
                # Canonical versions for resolution testing
                "nvidia/PhysicalAI-Autonomous-Vehicles",
                "Anthropic/hh-rlhf",
                "Idavidrein/gpqa",
                "tatsu-lab/alpaca",
                "linxy/LaTeX_OCR",
                "llm-jp/AnswerCarefully",
                "TuringEnterprises/Turing-Open-Reasoning",
                "Anthropic/AnthropicInterviewer",
                "opendatalab-raiser/Envision",
                "natolambert/GeneralThought-430K-filtered",
                
                # Edge cases and variety
                "perplexity-ai/browsesafe-bench",
                "ytu-ce-cosmos/Cosmos-Turkish-Corpus-v1.0",
                "ccmusic-database/pianos",
                "ccmusic-database/bel_canto",
                "ccmusic-database/chest_falsetto",
                "Genius-Society/Pima",
                "ASLP-lab/WSC-Train",
                "ccmusic-database/timbre_range",
                "Genius-Society/aal_stats_vol",
                "Genius-Society/emo163"
            ]
        console_logger.info(f"🧪 TEST MODE: Processing {len(target_ids)} items.")
    else:
        all_ids = load_source_ids(list_file)
        done_ids = writer.get_existing_ids()
        target_ids = [mid for mid in all_ids if mid not in done_ids]
        console_logger.info(f"Processing: {len(target_ids):,} (Skipped {len(done_ids):,})")

    if not target_ids:
        console_logger.info("Nothing to process.")
        return

    total_items = len(target_ids)
    with tqdm(total=total_items, desc=f"{repo_type.capitalize()}s", unit="item") as pbar:
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            for i in range(0, total_items, BATCH_SIZE):
                batch = target_ids[i : i + BATCH_SIZE]
                futures = [
                    executor.submit(super_worker, mid, repo_type, token_manager, writer) 
                    for mid in batch
                ]
                for future in as_completed(futures):
                    future.result()
                    pbar.update(1)
                del futures
                gc.collect()
                if test_mode: break

    console_logger.info(f"Finished {repo_type}s")

# ------------------------------------------------------------------
# PART 5: DATASET REFERENCE EXTRACTION AND RESOLUTION
# ------------------------------------------------------------------

def stream_jsonl(filepath):
    """Memory-efficient JSONL streaming."""
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

def extract_dataset_refs_from_model(model):
    """Extract all dataset references from a model's tags and card_data."""
    refs = set()
    
    # From tags (dataset:xxx pattern)
    tags = model.get('tags', [])
    if tags:
        for tag in tags:
            if isinstance(tag, str) and tag.startswith('dataset:'):
                refs.add(tag[8:])
    
    # From card_data.datasets field
    card_data = model.get('card_data')
    if card_data and isinstance(card_data, dict):
        datasets_field = card_data.get('datasets', [])
        if isinstance(datasets_field, list):
            for ds in datasets_field:
                if isinstance(ds, str):
                    refs.add(ds)
                elif isinstance(ds, dict) and 'name' in ds:
                    refs.add(ds['name'])
        elif isinstance(datasets_field, str):
            refs.add(datasets_field)
    
    return refs

def build_dataset_id_set(datasets_file):
    """Build set of all known dataset IDs from datasets.jsonl."""
    console_logger.info("Building dataset ID set...")
    dataset_ids = set()
    short_to_full = defaultdict(list)
    
    for ds in stream_jsonl(datasets_file):
        ds_id = ds.get('id', '')
        dataset_ids.add(ds_id)
        
        # Build short name mapping
        if '/' in ds_id:
            short_name = ds_id.split('/', 1)[1]
            short_to_full[short_name].append(ds_id)
        else:
            short_to_full[ds_id].append(ds_id)
    
    console_logger.info(f"   Found {len(dataset_ids):,} dataset IDs")
    return dataset_ids, dict(short_to_full)

def extract_all_dataset_refs(models_file):
    """Extract all unique dataset references from models."""
    console_logger.info("Extracting dataset references from models...")
    all_refs = Counter()
    
    for model in stream_jsonl(models_file):
        refs = extract_dataset_refs_from_model(model)
        for ref in refs:
            all_refs[ref] += 1
    
    console_logger.info(f"   Found {len(all_refs):,} unique dataset references")
    return all_refs

def categorize_refs(all_refs, dataset_ids, short_to_full):
    """Categorize refs into exact matches, resolvable, ambiguous, unresolved."""
    exact_matches = {}
    resolvable_shorts = {}
    ambiguous_shorts = {}
    unresolved_refs = {}
    
    # Known loader/format names to skip
    loader_names = {
        'imagefolder', 'generator', 'arrow', 'json', 'csv', 'text', 'parquet',
        'webdataset', 'audiofolder', 'videofolder', 'grouped_dataset'
    }
    
    for ref, count in all_refs.items():
        # Skip loader names
        if ref.lower() in loader_names:
            continue
        
        if ref in dataset_ids:
            # Exact match
            exact_matches[ref] = {'resolved': ref, 'count': count, 'type': 'exact'}
        elif '/' not in ref:
            # Short name
            candidates = short_to_full.get(ref, [])
            if len(candidates) == 1:
                resolvable_shorts[ref] = {'resolved': candidates[0], 'count': count, 'type': 'short_unique'}
            elif len(candidates) > 1:
                ambiguous_shorts[ref] = {'candidates': candidates, 'count': count, 'type': 'ambiguous'}
            else:
                unresolved_refs[ref] = {'count': count, 'type': 'short_not_found'}
        else:
            # Full path but not in dataset_ids
            unresolved_refs[ref] = {'count': count, 'type': 'full_not_found'}
    
    return exact_matches, resolvable_shorts, ambiguous_shorts, unresolved_refs

def resolve_single_ref(ref, token_manager, api=None):
    """Try to resolve a single reference via HuggingFace API."""
    if api is None:
        api = HfApi()
    
    attempts = 0
    while attempts < 5:
        token = token_manager.get_token()
        try:
            # Try direct resolution - HuggingFace resolves short names internally
            info = api.dataset_info(ref, token=token)
            return {
                'resolved': info.id,
                'downloads': getattr(info, 'downloads', 0),
                'likes': getattr(info, 'likes', 0),
                'method': 'direct_api'
            }
        except RepositoryNotFoundError:
            return None
        except GatedRepoError:
            # Gated but exists
            return {'resolved': ref, 'method': 'gated'}
        except Exception as e:
            err = str(e)
            if '429' in err:
                token_manager.report_rate_limit(token)
                attempts += 1
                continue
            if '404' in err or '401' in err:
                return None
            attempts += 1
            time.sleep(1)
    return None

def resolve_ambiguous_by_popularity(ref, candidates, token_manager):
    """Resolve ambiguous short name by checking candidate popularity."""
    api = HfApi()
    best = None
    best_score = -1
    
    for candidate in candidates:
        attempts = 0
        while attempts < 3:
            token = token_manager.get_token()
            try:
                info = api.dataset_info(candidate, token=token)
                score = (getattr(info, 'likes', 0) * 10) + getattr(info, 'downloads', 0)
                if score > best_score:
                    best_score = score
                    best = {
                        'resolved': info.id,
                        'downloads': getattr(info, 'downloads', 0),
                        'likes': getattr(info, 'likes', 0),
                        'method': 'popularity'
                    }
                break
            except Exception as e:
                if '429' in str(e):
                    token_manager.report_rate_limit(token)
                    attempts += 1
                    continue
                break
    
    return best

def resolve_single_ref_worker(args):
    """Worker function for parallel resolution."""
    ref, ref_type, data, token_manager = args
    api = HfApi()
    
    result = None
    
    if ref_type == 'ambiguous':
        # First try direct API resolution
        result = resolve_single_ref(ref, token_manager, api)
        
        if not result and 'candidates' in data:
            # Fall back to popularity check
            result = resolve_ambiguous_by_popularity(ref, data['candidates'], token_manager)
    else:
        # Unresolved - try direct API
        result = resolve_single_ref(ref, token_manager, api)
    
    if result:
        result['original_count'] = data.get('count', 0)
        result['original_type'] = ref_type
        return (ref, 'resolved', result)
    else:
        return (ref, 'failed', {
            'count': data.get('count', 0),
            'type': ref_type,
            'candidates': data.get('candidates', [])
        })


def resolve_references_via_api_parallel(ambiguous_shorts, unresolved_refs, token_manager):
    """OPTIMIZED: Resolve references using parallel workers like Phases 1-4."""
    console_logger.info("\n" + "="*60)
    console_logger.info("RESOLVING DATASET REFERENCES VIA API (PARALLEL)")
    console_logger.info("="*60)
    
    resolved = {}
    failed = {}
    
    # Combine all refs to resolve
    to_resolve = []
    
    for ref, data in ambiguous_shorts.items():
        to_resolve.append((ref, 'ambiguous', data, token_manager))
    
    for ref, data in unresolved_refs.items():
        to_resolve.append((ref, 'unresolved', data, token_manager))
    
    console_logger.info(f"Resolving {len(to_resolve):,} references with {NUM_WORKERS} workers...")
    
    # Sort by usage count (most used first)
    to_resolve.sort(key=lambda x: x[2].get('count', 0), reverse=True)
    
    with tqdm(total=len(to_resolve), desc="Resolving", unit="ref") as pbar:
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            # Process in batches like the other phases
            for i in range(0, len(to_resolve), BATCH_SIZE):
                batch = to_resolve[i:i + BATCH_SIZE]
                futures = [executor.submit(resolve_single_ref_worker, args) for args in batch]
                
                for future in as_completed(futures):
                    try:
                        ref, status, data = future.result()
                        if status == 'resolved':
                            resolved[ref] = data
                        else:
                            failed[ref] = data
                    except Exception as e:
                        logging.error(f"Resolution worker error: {e}")
                    pbar.update(1)
                
                del futures
                gc.collect()
    
    console_logger.info(f"\n✅ Resolved: {len(resolved):,}")
    console_logger.info(f"❌ Failed: {len(failed):,}")
    
    return resolved, failed

def run_resolution_pipeline(token_manager):
    """Run the complete dataset reference resolution pipeline."""
    console_logger.info("\n" + "="*60)
    console_logger.info("DATASET REFERENCE RESOLUTION PIPELINE")
    console_logger.info("="*60)
    
    models_file = f"{OUTPUT_DIR}/models.jsonl"
    datasets_file = f"{OUTPUT_DIR}/datasets.jsonl"
    
    if not os.path.exists(models_file):
        console_logger.error(f"❌ {models_file} not found. Run with --type models first.")
        return
    if not os.path.exists(datasets_file):
        console_logger.error(f"❌ {datasets_file} not found. Run with --type datasets first.")
        return
    
    # Step 1: Build dataset ID set
    dataset_ids, short_to_full = build_dataset_id_set(datasets_file)
    
    # Step 2: Extract all refs from models
    all_refs = extract_all_dataset_refs(models_file)
    
    # Step 3: Categorize refs
    console_logger.info("\nCategorizing references...")
    exact, resolvable, ambiguous, unresolved = categorize_refs(
        all_refs, dataset_ids, short_to_full
    )
    
    console_logger.info(f"   Exact matches: {len(exact):,}")
    console_logger.info(f"   Resolvable (unique short): {len(resolvable):,}")
    console_logger.info(f"   Ambiguous (multiple candidates): {len(ambiguous):,}")
    console_logger.info(f"   Unresolved: {len(unresolved):,}")
    
    # Step 4: Resolve via API
    api_resolved, api_failed = resolve_references_via_api_parallel(
        ambiguous, unresolved, token_manager
    )
    
    # Step 5: Build final mapping
    console_logger.info("\nBuilding final mapping...")
    
    final_mapping = {}
    
    # Add exact matches
    for ref, data in exact.items():
        final_mapping[ref] = data['resolved']
    
    # Add resolvable shorts
    for ref, data in resolvable.items():
        final_mapping[ref] = data['resolved']
    
    # Add API resolved
    for ref, data in api_resolved.items():
        final_mapping[ref] = data['resolved']
    
    console_logger.info(f"   Total mappings: {len(final_mapping):,}")
    
    # Step 6: Save outputs
    console_logger.info("\n💾 Saving resolution results...")
    
    # Main mapping file
    with open(f"{RESOLUTION_DIR}/dataset_reference_mapping.json", 'w') as f:
        json.dump(final_mapping, f, indent=2)
    console_logger.info(f"   ✅ {RESOLUTION_DIR}/dataset_reference_mapping.json")
    
    # Detailed resolution info
    detailed = {
        'exact_matches': exact,
        'resolvable_shorts': resolvable,
        'api_resolved': api_resolved,
        'failed': api_failed
    }
    with open(f"{RESOLUTION_DIR}/resolution_details.json", 'w') as f:
        json.dump(detailed, f, indent=2)
    console_logger.info(f"   ✅ {RESOLUTION_DIR}/resolution_details.json")
    
    # Stats summary
    stats = {
        'total_unique_refs': len(all_refs),
        'exact_matches': len(exact),
        'resolvable_shorts': len(resolvable),
        'ambiguous_resolved': len([r for r in api_resolved if api_resolved[r].get('original_type') == 'ambiguous']),
        'unresolved_resolved': len([r for r in api_resolved if api_resolved[r].get('original_type') == 'unresolved']),
        'failed': len(api_failed),
        'total_mapped': len(final_mapping),
        'top_referenced': all_refs.most_common(100)
    }
    with open(f"{RESOLUTION_DIR}/resolution_stats.json", 'w') as f:
        json.dump(stats, f, indent=2)
    console_logger.info(f"   ✅ {RESOLUTION_DIR}/resolution_stats.json")
    
    # Failed refs for manual review
    with open(f"{RESOLUTION_DIR}/failed_refs.json", 'w') as f:
        json.dump(api_failed, f, indent=2)
    console_logger.info(f"   ✅ {RESOLUTION_DIR}/failed_refs.json")
    
    console_logger.info("\n✅ Resolution pipeline complete!")
    
    # Print summary
    console_logger.info("\n" + "="*60)
    console_logger.info("RESOLUTION SUMMARY")
    console_logger.info("="*60)
    console_logger.info(f"Total unique dataset refs: {len(all_refs):,}")
    console_logger.info(f"Successfully mapped: {len(final_mapping):,} ({100*len(final_mapping)/len(all_refs):.1f}%)")
    console_logger.info(f"Failed to resolve: {len(api_failed):,}")
    
    if api_failed:
        console_logger.info("\nTop 20 unresolved (by usage):")
        sorted_failed = sorted(api_failed.items(), key=lambda x: x[1]['count'], reverse=True)[:20]
        for ref, data in sorted_failed:
            console_logger.info(f"   {data['count']:>5}x  {ref}")

# ------------------------------------------------------------------
# PART 6: FILTERED DATASET CREATION (Likes + Base Model Chain + Dataset Links)
# ------------------------------------------------------------------

FILTER_OUTPUT_DIR = 'filtered_data'
os.makedirs(FILTER_OUTPUT_DIR, exist_ok=True)

def extract_base_model_from_model(model):
    """Extract base_model reference from tags and card_data."""
    # Check tags first (base_model:xxx pattern)
    tags = model.get('tags', [])
    if tags:
        for tag in tags:
            if isinstance(tag, str) and tag.startswith('base_model:'):
                return tag[11:]  # Remove 'base_model:' prefix
    
    # Check card_data
    card_data = model.get('card_data')
    if card_data and isinstance(card_data, dict):
        base = card_data.get('base_model')
        if isinstance(base, str):
            return base
        elif isinstance(base, list) and base:
            return base[0] if isinstance(base[0], str) else None
    
    return None

def build_model_indices(models_file):
    """
    Build memory-efficient indices for model graph.
    Returns: model_likes, model_to_base, model_dataset_refs, model_ids_set
    """
    console_logger.info("Building model indices (streaming)...")
    
    model_likes = {}
    model_to_base = {}
    model_dataset_refs = {}
    model_ids_set = set()
    
    count = 0
    for model in stream_jsonl(models_file):
        model_id = model.get('id', '')
        if not model_id:
            continue
            
        model_ids_set.add(model_id)
        likes = model.get('likes', 0) or 0
        model_likes[model_id] = likes
        
        # Extract base model
        base_model = extract_base_model_from_model(model)
        if base_model:
            model_to_base[model_id] = base_model
        
        # Extract dataset refs
        refs = extract_dataset_refs_from_model(model)
        if refs:
            model_dataset_refs[model_id] = refs
        
        count += 1
        if count % 100000 == 0:
            console_logger.info(f"   Processed {count:,} models...")
    
    console_logger.info(f"   Total models: {len(model_ids_set):,}")
    console_logger.info(f"   Models with base_model: {len(model_to_base):,}")
    console_logger.info(f"   Models with dataset refs: {len(model_dataset_refs):,}")
    
    return model_likes, model_to_base, model_dataset_refs, model_ids_set

def get_ancestor_chain(model_id, model_to_base, max_depth=100):
    """
    Get full ancestor chain (base models) for a model.
    Returns list of ancestors from immediate parent to root.
    """
    chain = []
    current = model_id
    seen = set([model_id])
    
    depth = 0
    while current in model_to_base and depth < max_depth:
        base = model_to_base[current]
        if base in seen:  # Cycle detection
            break
        chain.append(base)
        seen.add(base)
        current = base
        depth += 1
    
    return chain

def get_descendant_models(model_id, base_to_children):
    """Get all descendants of a model (models that use it as base)."""
    descendants = set()
    queue = [model_id]
    
    while queue:
        current = queue.pop(0)
        children = base_to_children.get(current, [])
        for child in children:
            if child not in descendants:
                descendants.add(child)
                queue.append(child)
    
    return descendants

def resolve_model_datasets(model_dataset_refs, ref_mapping):
    """Resolve all dataset references using the mapping."""
    resolved = {}
    for model_id, refs in model_dataset_refs.items():
        resolved_refs = set()
        for ref in refs:
            if ref in ref_mapping:
                resolved_refs.add(ref_mapping[ref])
            else:
                resolved_refs.add(ref)
        resolved[model_id] = resolved_refs
    return resolved

def chain_has_datasets(model_id, model_to_base, model_resolved_datasets):
    """Check if model or any ancestor in its chain has datasets."""
    # Check self
    if model_id in model_resolved_datasets and model_resolved_datasets[model_id]:
        return True
    
    # Check ancestors
    for ancestor in get_ancestor_chain(model_id, model_to_base):
        if ancestor in model_resolved_datasets and model_resolved_datasets[ancestor]:
            return True
    
    return False

def run_filter_pipeline(token_manager):
    """
    Filter models by:
    1. >0 likes OR is base model of a liked model
    2. Has datasets somewhere in its ancestor chain
    
    Output: filtered_models.jsonl, filtered_datasets.jsonl with chain metadata
    """
    console_logger.info("\n" + "="*60)
    console_logger.info("PART 6: FILTERED DATASET CREATION")
    console_logger.info("="*60)
    
    models_file = f"{OUTPUT_DIR}/models.jsonl"
    datasets_file = f"{OUTPUT_DIR}/datasets.jsonl"
    mapping_file = f"{RESOLUTION_DIR}/dataset_reference_mapping.json"
    
    # Verify inputs exist
    for f, desc in [(models_file, "models"), (datasets_file, "datasets"), (mapping_file, "mapping")]:
        if not os.path.exists(f):
            console_logger.error(f"❌ {f} not found. Run previous stages first.")
            return
    
    # Step 1: Load resolution mapping
    console_logger.info("\n📂 Loading dataset reference mapping...")
    with open(mapping_file, 'r') as f:
        ref_mapping = json.load(f)
    console_logger.info(f"   Loaded {len(ref_mapping):,} mappings")
    
    # Step 2: Build model indices
    console_logger.info("\n📂 Building model indices...")
    model_likes, model_to_base, model_dataset_refs, model_ids_set = build_model_indices(models_file)
    
    # Step 3: Resolve dataset refs
    console_logger.info("\n🔗 Resolving dataset references...")
    model_resolved_datasets = resolve_model_datasets(model_dataset_refs, ref_mapping)
    
    # Step 4: Build reverse mapping (base -> children) for ancestor discovery
    console_logger.info("\n🔗 Building base->children mapping...")
    base_to_children = defaultdict(list)
    for model_id, base_model in model_to_base.items():
        base_to_children[base_model].append(model_id)
    console_logger.info(f"   {len(base_to_children):,} unique base models")
    
    # Step 5: Find models with >0 likes
    liked_models = {m for m, likes in model_likes.items() if likes > 0}
    console_logger.info(f"\n📊 Models with >0 likes: {len(liked_models):,}")
    
    # Step 6: Find all ancestors of liked models (backpropagate)
    console_logger.info("🔍 Backpropagating through base model chains...")
    all_ancestors = set()
    for model_id in tqdm(liked_models, desc="Finding ancestors"):
        chain = get_ancestor_chain(model_id, model_to_base)
        all_ancestors.update(chain)
    
    # Filter to only ancestors that exist in our dataset
    all_ancestors = all_ancestors & model_ids_set
    console_logger.info(f"   Unique ancestors (in dataset): {len(all_ancestors):,}")
    
    # Step 7: Candidate set = liked models + their ancestors
    candidate_models = liked_models | all_ancestors
    console_logger.info(f"   Total candidates: {len(candidate_models):,}")
    
    # Step 8: Filter - keep only models where chain has datasets
    console_logger.info("\n🔍 Filtering models without datasets in chain...")
    kept_models = set()
    orphaned_count = 0
    
    for model_id in tqdm(candidate_models, desc="Checking dataset chains"):
        if chain_has_datasets(model_id, model_to_base, model_resolved_datasets):
            kept_models.add(model_id)
        else:
            orphaned_count += 1
    
    console_logger.info(f"   ✅ Models with datasets in chain: {len(kept_models):,}")
    console_logger.info(f"   ❌ Orphaned (no datasets): {orphaned_count:,}")
    
    # Step 9: Collect all datasets referenced by kept models
    console_logger.info("\n📦 Collecting referenced datasets...")
    all_datasets = set()
    for model_id in kept_models:
        # Direct datasets
        if model_id in model_resolved_datasets:
            all_datasets.update(model_resolved_datasets[model_id])
        # Inherited from ancestors
        for ancestor in get_ancestor_chain(model_id, model_to_base):
            if ancestor in model_resolved_datasets:
                all_datasets.update(model_resolved_datasets[ancestor])
    
    console_logger.info(f"   Unique datasets referenced: {len(all_datasets):,}")
    
    # Step 10: Write filtered models with chain metadata
    console_logger.info("\n💾 Writing filtered models...")
    filtered_models_file = f"{FILTER_OUTPUT_DIR}/filtered_models.jsonl"
    
    model_count = 0
    with open(filtered_models_file, 'w', encoding='utf-8') as out:
        for model in tqdm(stream_jsonl(models_file), desc="Writing models"):
            model_id = model.get('id', '')
            if model_id not in kept_models:
                continue
            
            # Add chain metadata
            base_model = model_to_base.get(model_id)
            ancestor_chain = get_ancestor_chain(model_id, model_to_base)
            
            # Direct datasets (this model's own refs)
            direct_datasets = list(model_resolved_datasets.get(model_id, set()))
            
            # Inherited datasets (from ancestors)
            inherited_datasets = set()
            for ancestor in ancestor_chain:
                inherited_datasets.update(model_resolved_datasets.get(ancestor, set()))
            inherited_datasets = list(inherited_datasets)
            
            # Combined
            all_model_datasets = list(set(direct_datasets) | set(inherited_datasets))
            
            # Add metadata fields (prefixed with _ to indicate computed)
            model['_base_model'] = base_model
            model['_ancestor_chain'] = ancestor_chain
            model['_direct_datasets'] = direct_datasets
            model['_inherited_datasets'] = inherited_datasets
            model['_all_datasets'] = all_model_datasets
            model['_has_direct_datasets'] = len(direct_datasets) > 0
            model['_chain_depth'] = len(ancestor_chain)
            
            out.write(json.dumps(model) + '\n')
            model_count += 1
    
    console_logger.info(f"   ✅ Wrote {model_count:,} models to {filtered_models_file}")
    
    # Step 11: Write filtered datasets
    console_logger.info("\n💾 Writing filtered datasets...")
    filtered_datasets_file = f"{FILTER_OUTPUT_DIR}/filtered_datasets.jsonl"
    
    dataset_count = 0
    with open(filtered_datasets_file, 'w', encoding='utf-8') as out:
        for ds in tqdm(stream_jsonl(datasets_file), desc="Writing datasets"):
            ds_id = ds.get('id', '')
            if ds_id in all_datasets:
                out.write(json.dumps(ds) + '\n')
                dataset_count += 1
    
    console_logger.info(f"   ✅ Wrote {dataset_count:,} datasets to {filtered_datasets_file}")
    
    # Step 12: Write summary statistics
    console_logger.info("\n💾 Writing summary...")
    summary = {
        'timestamp': datetime.now().isoformat(),
        'input': {
            'total_models': len(model_ids_set),
            'total_datasets_in_mapping': len(ref_mapping),
            'models_with_base_model': len(model_to_base),
            'models_with_dataset_refs': len(model_dataset_refs),
        },
        'filtering': {
            'models_with_likes': len(liked_models),
            'ancestors_found': len(all_ancestors),
            'candidates_total': len(candidate_models),
            'orphaned_no_datasets': orphaned_count,
        },
        'output': {
            'filtered_models': model_count,
            'filtered_datasets': dataset_count,
            'unique_datasets_referenced': len(all_datasets),
        }
    }
    
    with open(f"{FILTER_OUTPUT_DIR}/filter_summary.json", 'w') as f:
        json.dump(summary, f, indent=2)
    
    console_logger.info(f"\n" + "="*60)
    console_logger.info("FILTERING COMPLETE")
    console_logger.info("="*60)
    console_logger.info(f"Models: {len(model_ids_set):,} → {model_count:,}")
    console_logger.info(f"Datasets: {dataset_count:,}")
    console_logger.info(f"Output: {FILTER_OUTPUT_DIR}/")


# ------------------------------------------------------------------
# PART 7: LICENSE/CONFIG FILE MINING FROM FILTERED REPOS
# ------------------------------------------------------------------

# --- File Filtering Configuration ---
LICENSE_KEYWORDS = [
    'license', 'licence', 'copying', 'unlicense', 'patents', 'notice', 
    'copyright', 'legal', 'disclaimer', 'authors', 'terms', 'attribution', 'citation'
]
README_PATTERNS = ['readme', 'read_me', 'read-me', 'modelcard', 'model_card', 'datasheet']
LICENSE_DIRECTORIES_LOWER = ['legal', 'license', 'licenses', 'licensing']

LICENSE_REGEX = re.compile(r".*(" + "|".join(LICENSE_KEYWORDS) + r").*", re.IGNORECASE)
README_REGEX = re.compile(r".*(" + "|".join(README_PATTERNS) + r").*", re.IGNORECASE)

MODEL_WEIGHT_EXTENSIONS = frozenset([
    '.bin', '.pth', '.pt', '.safetensors', '.gguf', '.onnx', '.h5', 
    '.tf', '.ckpt', '.params', '.weights', '.data', '.index'
])
SOURCE_CODE_EXTENSIONS = frozenset([
    '.py', '.pyw', '.ipynb', '.c', '.cpp', '.h', '.hpp', '.cxx', '.hxx',
    '.java', '.kt', '.kts', '.scala', '.js', '.mjs', '.ts', '.jsx', '.tsx', 
    '.html', '.css', '.sh', '.bash', '.ps1', '.pl', '.rb', '.go', '.rs', 
    '.swift', '.r', '.php', '.cs', '.lua'
])
CONFIG_FILENAMES_LOWER = frozenset([
    'config.json', 'configuration.json', 'model_card.json', 'generation_config.json',
    'training_args.json', 'training_arguments.json', 'requirements.txt', 'setup.py', 
    'pyproject.toml', 'pipfile', 'makefile', 'dockerfile', 'conda.yaml', 'environment.yml'
])
CONFIG_EXTENSIONS = frozenset(['.yaml', '.yml', '.toml', '.ini', '.conf', '.cfg', '.json5'])

RELEVANT_EXTENSIONS = SOURCE_CODE_EXTENSIONS | CONFIG_EXTENSIONS

FILES_OUTPUT_DIR = 'mined_files'
os.makedirs(FILES_OUTPUT_DIR, exist_ok=True)


def should_download_file(filepath):
    """Determine if a file should be downloaded based on filtering rules."""
    filename = os.path.basename(filepath).lower()
    filepath_lower = filepath.lower()
    
    # SKIP: Model weight files
    for ext in MODEL_WEIGHT_EXTENSIONS:
        if filename.endswith(ext):
            return False, 'weight_file'
    
    # KEEP: Files in license directories
    path_parts = filepath_lower.split('/')
    for part in path_parts[:-1]:  # Exclude filename itself
        if part in LICENSE_DIRECTORIES_LOWER:
            return True, 'license_dir'
    
    # KEEP: License keyword match
    if LICENSE_REGEX.match(filename):
        return True, 'license_keyword'
    
    # KEEP: README pattern match
    if README_REGEX.match(filename):
        return True, 'readme_pattern'
    
    # KEEP: Exact config filename match
    if filename in CONFIG_FILENAMES_LOWER:
        return True, 'config_filename'
    
    # KEEP: Relevant extensions (source code, config)
    for ext in RELEVANT_EXTENSIONS:
        if filename.endswith(ext):
            return True, 'relevant_extension'
    
    return False, 'not_relevant'


def sanitize_path_component(name):
    """Sanitize a path component to be filesystem-safe."""
    # Replace problematic characters but preserve case
    forbidden = '<>:"|?*'
    for char in forbidden:
        name = name.replace(char, '_')
    return name


def get_repo_output_dir(repo_id, repo_type):
    """
    Get the output directory for a repo, preserving structure.
    Structure: mined_files/{models|datasets}/{org}/{repo}/
    """
    # repo_id is like "org/repo" or just "repo"
    if '/' in repo_id:
        org, repo = repo_id.split('/', 1)
    else:
        org = '_root'
        repo = repo_id
    
    org = sanitize_path_component(org)
    repo = sanitize_path_component(repo)
    
    base_dir = os.path.join(FILES_OUTPUT_DIR, f"{repo_type}s", org, repo)
    return base_dir


def download_and_save_file(repo_id, repo_type, filepath, token, output_dir, max_size=MAX_TEXT_SIZE):
    """
    Download file and save to disk, preserving original path structure.
    Returns (local_path, error, truncated, size_bytes).
    """
    url = hf_hub_url(repo_id=repo_id, filename=filepath, repo_type=repo_type)
    
    # Preserve the original filepath structure (including case)
    local_path = os.path.join(output_dir, filepath)
    local_dir = os.path.dirname(local_path)
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers, timeout=120, stream=True)
        
        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}", False, 0
        
        # Create directory structure BEFORE opening file
        os.makedirs(local_dir, exist_ok=True)
        
        # Stream to file with size limit
        current_size = 0
        truncated = False
        
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    if current_size + len(chunk) > max_size:
                        # Write only what fits
                        bytes_to_write = max_size - current_size
                        if bytes_to_write > 0:
                            f.write(chunk[:bytes_to_write])
                        current_size = max_size
                        truncated = True
                        break
                    
                    f.write(chunk)
                    current_size += len(chunk)
        
        return local_path, None, truncated, current_size
                
    except requests.Timeout:
        return None, "timeout", False, 0
    except Exception as e:
        return None, str(e)[:100], False, 0


def mine_single_repo(repo_id, repo_type, token_manager, writer):
    """Mine relevant files from a single repository and save to disk."""
    api = HfApi()
    attempts = 0
    
    # Get output directory for this repo
    output_dir = get_repo_output_dir(repo_id, repo_type)
    
    while attempts < 5:
        token = token_manager.get_token()
        try:
            # Get repo info with file metadata
            if repo_type == 'model':
                info = api.model_info(repo_id, token=token, files_metadata=True)
            else:
                info = api.dataset_info(repo_id, token=token, files_metadata=True)
            
            siblings = getattr(info, 'siblings', []) or []
            
            # Identify files to download
            files_to_process = []
            for sibling in siblings:
                # Use original filename (preserves case like README.md vs readme.md)
                filepath = sibling.rfilename
                should_dl, reason = should_download_file(filepath)
                
                if not should_dl:
                    continue
                
                # Get file size
                file_size = getattr(sibling, 'size', 0) or 0
                is_lfs = bool(getattr(sibling, 'lfs', None))
                if is_lfs and sibling.lfs:
                    file_size = sibling.lfs.size
                
                # Skip files > 300MB
                if file_size > MAX_TEXT_SIZE:
                    files_to_process.append({
                        'path': filepath,
                        'size': file_size,
                        'match_reason': reason,
                        'local_path': None,
                        'status': 'skipped_too_large',
                        'is_lfs': is_lfs
                    })
                    continue
                
                files_to_process.append({
                    'path': filepath,
                    'size': file_size,
                    'match_reason': reason,
                    'is_lfs': is_lfs,
                    'local_path': None,
                    'status': 'pending'
                })
            
            # Download each relevant file to disk
            downloaded_count = 0
            total_bytes = 0
            
            for file_info in files_to_process:
                if file_info['status'] != 'pending':
                    continue
                
                # Skip LFS files (usually binary blobs)
                if file_info['is_lfs']:
                    file_info['status'] = 'skipped_lfs'
                    continue
                
                local_path, error, truncated, size_bytes = download_and_save_file(
                    repo_id, repo_type, file_info['path'], token, output_dir
                )
                
                if local_path is not None:
                    file_info['local_path'] = local_path
                    file_info['status'] = 'truncated' if truncated else 'ok'
                    file_info['downloaded_size'] = size_bytes
                    downloaded_count += 1
                    total_bytes += size_bytes
                else:
                    file_info['local_path'] = None
                    file_info['status'] = f'error:{error}'
            
            # Write manifest (no content, just metadata and paths)
            result = {
                'id': repo_id,
                'type': repo_type,
                'output_dir': output_dir,
                'total_files_in_repo': len(siblings),
                'relevant_files_found': len(files_to_process),
                'files_downloaded': downloaded_count,
                'total_bytes_downloaded': total_bytes,
                'files': files_to_process
            }
            
            writer.write(result)
            return True
            
        except Exception as e:
            err = str(e)
            if '429' in err:
                token_manager.report_rate_limit(token)
                attempts += 1
                continue
            if isinstance(e, (RepositoryNotFoundError, GatedRepoError)) or '404' in err or '401' in err:
                # Write error record
                writer.write({
                    'id': repo_id,
                    'type': repo_type,
                    'error': 'not_found_or_gated',
                    'files': []
                })
                return False
            attempts += 1
            time.sleep(2)
    
    return False


def run_file_mining_pipeline(token_manager):
    """Mine license/readme/config/source files from filtered repos and save to disk."""
    console_logger.info("\n" + "="*60)
    console_logger.info("PART 7: LICENSE/CONFIG FILE MINING (DOWNLOAD TO DISK)")
    console_logger.info("="*60)
    
    filtered_models = f"{FILTER_OUTPUT_DIR}/filtered_models.jsonl"
    filtered_datasets = f"{FILTER_OUTPUT_DIR}/filtered_datasets.jsonl"
    
    for f in [filtered_models, filtered_datasets]:
        if not os.path.exists(f):
            console_logger.error(f"❌ {f} not found. Run --type filter first.")
            return
    
    # Create output directories
    os.makedirs(f"{FILES_OUTPUT_DIR}/models", exist_ok=True)
    os.makedirs(f"{FILES_OUTPUT_DIR}/datasets", exist_ok=True)
    
    # Load filtered IDs
    console_logger.info("\n📂 Loading filtered repo IDs...")
    model_ids = [m['id'] for m in stream_jsonl(filtered_models)]
    dataset_ids = [d['id'] for d in stream_jsonl(filtered_datasets)]
    console_logger.info(f"   Models to mine: {len(model_ids):,}")
    console_logger.info(f"   Datasets to mine: {len(dataset_ids):,}")
    
    # --- Mine Models ---
    console_logger.info("\n" + "-"*40)
    console_logger.info("Mining MODEL files...")
    console_logger.info("-"*40)
    
    models_manifest = f"{FILES_OUTPUT_DIR}/model_manifest.jsonl"
    writer = JsonlWriter(models_manifest)
    done_ids = writer.get_existing_ids()
    remaining_models = [m for m in model_ids if m not in done_ids]
    
    console_logger.info(f"   Already done: {len(done_ids):,}")
    console_logger.info(f"   Remaining: {len(remaining_models):,}")
    
    if remaining_models:
        with tqdm(total=len(remaining_models), desc="Models", unit="repo") as pbar:
            with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
                for i in range(0, len(remaining_models), BATCH_SIZE):
                    batch = remaining_models[i:i+BATCH_SIZE]
                    futures = [
                        executor.submit(mine_single_repo, mid, 'model', token_manager, writer)
                        for mid in batch
                    ]
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except Exception as e:
                            logging.error(f"Worker error: {e}")
                        pbar.update(1)
                    del futures
                    gc.collect()
    
    console_logger.info(f"   ✅ Model files saved to {FILES_OUTPUT_DIR}/models/")
    console_logger.info(f"   ✅ Manifest: {models_manifest}")
    
    # --- Mine Datasets ---
    console_logger.info("\n" + "-"*40)
    console_logger.info("Mining DATASET files...")
    console_logger.info("-"*40)
    
    datasets_manifest = f"{FILES_OUTPUT_DIR}/dataset_manifest.jsonl"
    writer = JsonlWriter(datasets_manifest)
    done_ids = writer.get_existing_ids()
    remaining_datasets = [d for d in dataset_ids if d not in done_ids]
    
    console_logger.info(f"   Already done: {len(done_ids):,}")
    console_logger.info(f"   Remaining: {len(remaining_datasets):,}")
    
    if remaining_datasets:
        with tqdm(total=len(remaining_datasets), desc="Datasets", unit="repo") as pbar:
            with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
                for i in range(0, len(remaining_datasets), BATCH_SIZE):
                    batch = remaining_datasets[i:i+BATCH_SIZE]
                    futures = [
                        executor.submit(mine_single_repo, did, 'dataset', token_manager, writer)
                        for did in batch
                    ]
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except Exception as e:
                            logging.error(f"Worker error: {e}")
                        pbar.update(1)
                    del futures
                    gc.collect()
    
    console_logger.info(f"   ✅ Dataset files saved to {FILES_OUTPUT_DIR}/datasets/")
    console_logger.info(f"   ✅ Manifest: {datasets_manifest}")
    
    # --- Summary ---
    console_logger.info("\n" + "="*60)
    console_logger.info("FILE MINING COMPLETE")
    console_logger.info("="*60)
    console_logger.info(f"Output structure:")
    console_logger.info(f"   {FILES_OUTPUT_DIR}/")
    console_logger.info(f"   ├── models/")
    console_logger.info(f"   │   └── {{org}}/{{repo}}/{{files...}}")
    console_logger.info(f"   ├── datasets/")
    console_logger.info(f"   │   └── {{org}}/{{repo}}/{{files...}}")
    console_logger.info(f"   ├── model_manifest.jsonl")
    console_logger.info(f"   └── dataset_manifest.jsonl")


def prepare_test_files_for_pipeline():
    """Copy TEST_ prefixed files to regular names so Parts 5-7 can run unchanged."""
    import shutil
    
    copies = [
        (f"{OUTPUT_DIR}/TEST_models.jsonl", f"{OUTPUT_DIR}/models.jsonl"),
        (f"{OUTPUT_DIR}/TEST_datasets.jsonl", f"{OUTPUT_DIR}/datasets.jsonl"),
    ]
    
    for src, dst in copies:
        if os.path.exists(src):
            console_logger.info(f"   Copying {src} → {dst}")
            shutil.copy2(src, dst)
        else:
            console_logger.warning(f"   ⚠️ {src} not found")


# ------------------------------------------------------------------
# PART 8: GITHUB APPLICATION SEARCH AND EXTRACTION
# ------------------------------------------------------------------

import zipfile
import shutil
from urllib.parse import quote

APPS_OUTPUT_DIR = 'application_data'
APPS_SEARCH_DIR = os.path.join(APPS_OUTPUT_DIR, 'search_results')
APPS_REPOS_DIR = os.path.join(APPS_OUTPUT_DIR, 'repositories')
APPS_STAGING_DIR = os.path.join(APPS_OUTPUT_DIR, 'staging')

os.makedirs(APPS_OUTPUT_DIR, exist_ok=True)
os.makedirs(APPS_SEARCH_DIR, exist_ok=True)
os.makedirs(APPS_REPOS_DIR, exist_ok=True)
os.makedirs(APPS_STAGING_DIR, exist_ok=True)

# GitHub rate limits
GITHUB_SEARCH_PER_MINUTE = 30
GITHUB_CORE_PER_HOUR = 5000


class GitHubTokenManager:
    """Manages GitHub API tokens with rate limiting."""
    
    def __init__(self, tokens):
        self.tokens = tokens
        self.token_status = {t: {'remaining': GITHUB_CORE_PER_HOUR, 'reset': 0} for t in tokens}
        self.search_status = {t: {'remaining': GITHUB_SEARCH_PER_MINUTE, 'reset': 0} for t in tokens}
        self.lock = threading.Lock()
        self.current_index = 0
    
    def get_token(self, request_type='core'):
        """Get best available token for request type."""
        while True:
            current_time = time.time()
            with self.lock:
                for i in range(len(self.tokens)):
                    idx = (self.current_index + i) % len(self.tokens)
                    token = self.tokens[idx]
                    
                    status = self.search_status if request_type == 'search' else self.token_status
                    
                    # Reset if past reset time
                    if current_time > status[token]['reset']:
                        limit = GITHUB_SEARCH_PER_MINUTE if request_type == 'search' else GITHUB_CORE_PER_HOUR
                        status[token]['remaining'] = limit
                        status[token]['reset'] = current_time + (60 if request_type == 'search' else 3600)
                    
                    if status[token]['remaining'] > 2:
                        self.current_index = (idx + 1) % len(self.tokens)
                        return token
                
                # All tokens exhausted, wait
                min_reset = min(s['reset'] for s in status.values())
                wait_time = max(0, min_reset - current_time) + 5
                
            console_logger.warning(f"All GitHub tokens rate-limited for '{request_type}'. Waiting {wait_time:.0f}s")
            time.sleep(min(wait_time, 60))
    
    def update_from_headers(self, token, headers, request_type='core'):
        """Update token status from response headers."""
        with self.lock:
            status = self.search_status if request_type == 'search' else self.token_status
            
            if 'X-RateLimit-Remaining' in headers:
                status[token]['remaining'] = int(headers['X-RateLimit-Remaining'])
            if 'X-RateLimit-Reset' in headers:
                status[token]['reset'] = int(headers['X-RateLimit-Reset'])
    
    def report_rate_limit(self, token, request_type='core'):
        """Mark token as rate limited."""
        with self.lock:
            status = self.search_status if request_type == 'search' else self.token_status
            status[token]['remaining'] = 0
            status[token]['reset'] = time.time() + (60 if request_type == 'search' else 3600)


def load_github_tokens():
    """Load GitHub API tokens from environment."""
    tokens = []
    # Try numbered tokens first
    i = 1
    while True:
        token = os.getenv(f"GITHUB_TOKEN_{i}") or os.getenv(f"api_key{i}")
        if not token:
            break
        tokens.append(token)
        i += 1
    
    # Try single token
    if not tokens:
        token = os.getenv("GITHUB_TOKEN")
        if token:
            tokens.append(token)
    
    return tokens


def github_api_request(url, token_manager, params=None, request_type='core', max_retries=10):
    """Make GitHub API request with retry logic."""
    attempts = 0
    
    while attempts < max_retries:
        token = token_manager.get_token(request_type)
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            
            if response.headers:
                token_manager.update_from_headers(token, response.headers, request_type)
            
            if response.ok:
                return response.json()
            
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                console_logger.warning(f"GitHub 429, waiting {retry_after}s")
                token_manager.report_rate_limit(token, request_type)
                time.sleep(min(retry_after, 120))
                attempts += 1
                continue
            
            if response.status_code == 403 and 'rate limit' in response.text.lower():
                token_manager.report_rate_limit(token, request_type)
                attempts += 1
                continue
            
            if response.status_code >= 500:
                wait_time = (2 ** attempts) + random.uniform(0, 1)
                console_logger.warning(f"GitHub {response.status_code}, waiting {wait_time:.1f}s")
                time.sleep(wait_time)
                attempts += 1
                continue
            
            # Client error
            logging.error(f"GitHub API error {response.status_code}: {url}")
            return None
            
        except requests.RequestException as e:
            wait_time = (2 ** attempts) + random.uniform(0, 1)
            console_logger.warning(f"GitHub request error: {e}, waiting {wait_time:.1f}s")
            time.sleep(wait_time)
            attempts += 1
    
    return None


def search_github_for_model(model_id, github_token_manager):
    """
    Search GitHub code for references to a model ID.
    Uses query splitting for >1000 results (GitHub limit bypass).
    """
    base_query = f'"{model_id}" in:file'
    
    results = {
        'model_id': model_id,
        'total_count': 0,
        'query_strategy': 'direct',
        'split_details': [],
        'items': [],
        'repos': {}
    }
    
    # Get initial count
    count_response = github_api_request(
        "https://api.github.com/search/code",
        github_token_manager,
        params={'q': base_query, 'per_page': 1},
        request_type='search'
    )
    
    if not count_response:
        return results
    
    total_count = count_response.get('total_count', 0)
    results['total_count'] = total_count
    
    if total_count == 0:
        return results
    
    all_items = []
    
    if total_count <= 1000:
        # Direct fetch - simple case
        results['query_strategy'] = 'direct'
        all_items = fetch_all_search_pages(base_query, github_token_manager, max_pages=10)
    
    else:
        # >1000 results: Split by file size ranges
        results['query_strategy'] = 'size_split'
        console_logger.info(f"   {model_id}: {total_count} results, using size-split strategy")
        
        # More granular size ranges for better coverage
        size_ranges = [
            (0, 500),
            (501, 1000),
            (1001, 2000),
            (2001, 5000),
            (5001, 10000),
            (10001, 20000),
            (20001, 50000),
            (50001, 100000),
            (100001, 200000),
            (200001, 500000),
            (500001, 1000000),
            (1000001, None),
        ]
        
        for min_size, max_size in size_ranges:
            if max_size:
                size_query = f'{base_query} size:{min_size}..{max_size}'
            else:
                size_query = f'{base_query} size:>={min_size}'
            
            # Check count for this range
            range_count_resp = github_api_request(
                "https://api.github.com/search/code",
                github_token_manager,
                params={'q': size_query, 'per_page': 1},
                request_type='search'
            )
            
            if not range_count_resp:
                continue
            
            range_count = range_count_resp.get('total_count', 0)
            
            if range_count == 0:
                continue
            
            results['split_details'].append({
                'range': f'{min_size}-{max_size}',
                'count': range_count
            })
            
            if range_count <= 1000:
                # Fetch all from this range
                range_items = fetch_all_search_pages(size_query, github_token_manager, max_pages=10)
                all_items.extend(range_items)
            else:
                # Still >1000 - try date sub-splitting
                console_logger.info(f"      Size {min_size}-{max_size}: {range_count} results, adding date splits...")
                date_items = fetch_with_date_splits(size_query, github_token_manager)
                all_items.extend(date_items)
            
            time.sleep(0.3)
    
    # Deduplicate by URL
    unique_items = list({item['html_url']: item for item in all_items}.values())
    results['items'] = unique_items
    results['items_fetched'] = len(unique_items)
    
    # Extract unique repos
    for item in unique_items:
        repo = item.get('repository', {})
        repo_name = repo.get('full_name')
        if repo_name and repo_name not in results['repos']:
            results['repos'][repo_name] = {
                'full_name': repo_name,
                'html_url': repo.get('html_url'),
                'description': repo.get('description'),
                'files': []
            }
        if repo_name:
            results['repos'][repo_name]['files'].append(item.get('path'))
    
    return results

def fetch_with_date_splits(base_query, github_token_manager):
    """Fetch results by splitting on creation date when size split isn't enough."""
    all_items = []
    
    # Year ranges
    date_ranges = [
        (None, '2019-12-31'),      # Ancient
        ('2020-01-01', '2020-12-31'),
        ('2021-01-01', '2021-12-31'),
        ('2022-01-01', '2022-06-30'),
        ('2022-07-01', '2022-12-31'),
        ('2023-01-01', '2023-06-30'),
        ('2023-07-01', '2023-12-31'),
        ('2024-01-01', '2024-06-30'),
        ('2024-07-01', '2024-12-31'),
        ('2025-01-01', None),      # Recent
    ]
    
    for start_date, end_date in date_ranges:
        if start_date and end_date:
            date_filter = f'created:{start_date}..{end_date}'
        elif start_date:
            date_filter = f'created:>={start_date}'
        else:
            date_filter = f'created:<={end_date}'
        
        date_query = f'{base_query} {date_filter}'
        
        # Check count
        count_resp = github_api_request(
            "https://api.github.com/search/code",
            github_token_manager,
            params={'q': date_query, 'per_page': 1},
            request_type='search'
        )
        
        if not count_resp:
            continue
        
        range_count = count_resp.get('total_count', 0)
        
        if range_count == 0:
            continue
        
        # Fetch up to 1000 from this date range
        items = fetch_all_search_pages(date_query, github_token_manager, max_pages=10)
        all_items.extend(items)
        
        if range_count > 1000:
            console_logger.warning(f"         Date range {start_date or 'start'}-{end_date or 'now'}: {range_count} results, capped at 1000")
        
        time.sleep(0.2)
    
    return all_items

def fetch_all_search_pages(query, github_token_manager, max_pages=10):
    """Fetch all pages of search results (up to max_pages * 100 = 1000 items)."""
    all_items = []
    page = 1
    
    while page <= max_pages:
        response = github_api_request(
            "https://api.github.com/search/code",
            github_token_manager,
            params={'q': query, 'per_page': 100, 'page': page},
            request_type='search'
        )
        
        if not response:
            break
        
        items = response.get('items', [])
        if not items:
            break
        
        all_items.extend(items)
        
        if len(items) < 100:
            break
        
        page += 1
        time.sleep(0.2)  # Rate limit courtesy
    
    return all_items


def fetch_repo_metadata(repo_name, token_manager):
    """Fetch additional metadata for a repository."""
    response = github_api_request(
        f"https://api.github.com/repos/{repo_name}",
        token_manager,
        request_type='core'
    )
    
    if response:
        return {
            'stars': response.get('stargazers_count', 0),
            'forks': response.get('forks_count', 0),
            'open_issues': response.get('open_issues_count', 0),
            'license': response.get('license', {}).get('spdx_id') if response.get('license') else None,
            'default_branch': response.get('default_branch', 'main'),
            'created_at': response.get('created_at'),
            'updated_at': response.get('updated_at'),
            'language': response.get('language'),
            'topics': response.get('topics', [])
        }
    return {}


def should_download_app_file(filepath):
    """Determine if a file should be downloaded from an application repo."""
    filename = os.path.basename(filepath).lower()
    filepath_lower = filepath.lower()
    
    # SKIP: Model weight files
    for ext in MODEL_WEIGHT_EXTENSIONS:
        if filename.endswith(ext):
            return False, 'weight_file'
    
    # KEEP: Files in license directories
    path_parts = filepath_lower.split('/')
    for part in path_parts[:-1]:
        if part in LICENSE_DIRECTORIES_LOWER:
            return True, 'license_dir'
    
    # KEEP: License keyword match
    if LICENSE_REGEX.match(filename):
        return True, 'license_keyword'
    
    # KEEP: README pattern match
    if README_REGEX.match(filename):
        return True, 'readme_pattern'
    
    # KEEP: Exact config filename match
    if filename in CONFIG_FILENAMES_LOWER:
        return True, 'config_filename'
    
    # KEEP: Relevant extensions (source code, config)
    for ext in RELEVANT_EXTENSIONS:
        if filename.endswith(ext):
            return True, 'relevant_extension'
    
    return False, 'not_relevant'


def download_and_extract_github_repo(repo_name, token_manager, output_dir, staging_dir, referenced_files=None):
    """
    Download a GitHub repository and extract relevant files.
    Similar to mine_single_repo but for GitHub repos.
    """
    owner, repo = repo_name.split('/', 1)
    repo_folder = f"{sanitize_path_component(owner)}_{sanitize_path_component(repo)}"
    staging_repo_dir = os.path.join(staging_dir, repo_folder)
    final_repo_dir = os.path.join(output_dir, repo_folder)
    
    # Skip if already exists
    if os.path.exists(os.path.join(final_repo_dir, 'extraction_manifest.json')):
        return repo_name, True, {'skipped': True}
    
    # Clean staging
    if os.path.exists(staging_repo_dir):
        shutil.rmtree(staging_repo_dir)
    os.makedirs(staging_repo_dir, exist_ok=True)
    
    try:
        manifest = {
            'repo_name': repo_name,
            'extraction_timestamp': datetime.now().isoformat(),
            'referenced_files': list(referenced_files) if referenced_files else [],
            'files_extracted': [],
            'stats': {
                'total_files_in_zip': 0,
                'files_downloaded': 0,
                'files_skipped': 0,
                'total_bytes': 0
            }
        }
        
        # Download zipball
        token = token_manager.get_token('core')
        url = f"https://api.github.com/repos/{repo_name}/zipball"
        headers = {"Authorization": f"token {token}"}
        
        response = requests.get(url, headers=headers, stream=True, timeout=300)
        
        if response.status_code != 200:
            return repo_name, False, {'error': f'HTTP {response.status_code}'}
        
        zip_path = os.path.join(staging_repo_dir, f"{repo_folder}.zip")
        with open(zip_path, 'wb') as f:
            shutil.copyfileobj(response.raw, f)
        
        # Extract relevant files
        with zipfile.ZipFile(zip_path, 'r') as zf:
            namelist = zf.namelist()
            manifest['stats']['total_files_in_zip'] = len(namelist)
            
            # Find root prefix (GitHub adds commit hash prefix)
            root_prefix = namelist[0].split('/')[0] + '/' if namelist else ''
            
            for item in zf.infolist():
                if item.is_dir():
                    continue
                
                # Get relative path without root prefix
                relative_path = item.filename[len(root_prefix):] if item.filename.startswith(root_prefix) else item.filename
                
                if not relative_path:
                    continue
                
                should_dl, reason = should_download_app_file(relative_path)
                
                if not should_dl:
                    manifest['stats']['files_skipped'] += 1
                    continue
                
                # Check size limit
                if item.file_size > MAX_TEXT_SIZE:
                    manifest['files_extracted'].append({
                        'path': relative_path,
                        'status': 'skipped_too_large',
                        'size': item.file_size,
                        'match_reason': reason
                    })
                    continue
                
                # Extract file
                target_path = os.path.join(staging_repo_dir, 'files', relative_path)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                
                try:
                    with open(target_path, 'wb') as f:
                        f.write(zf.read(item.filename))
                    
                    manifest['files_extracted'].append({
                        'path': relative_path,
                        'local_path': os.path.join('files', relative_path),
                        'status': 'ok',
                        'size': item.file_size,
                        'match_reason': reason
                    })
                    manifest['stats']['files_downloaded'] += 1
                    manifest['stats']['total_bytes'] += item.file_size
                    
                except Exception as e:
                    manifest['files_extracted'].append({
                        'path': relative_path,
                        'status': f'error:{str(e)[:50]}',
                        'match_reason': reason
                    })
        
        # Remove zip file
        os.remove(zip_path)
        
        # Save manifest
        with open(os.path.join(staging_repo_dir, 'extraction_manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        
        # Move to final location
        if os.path.exists(final_repo_dir):
            shutil.rmtree(final_repo_dir)
        shutil.move(staging_repo_dir, final_repo_dir)
        
        return repo_name, True, manifest['stats']
        
    except Exception as e:
        logging.error(f"Failed to extract {repo_name}: {e}")
        if os.path.exists(staging_repo_dir):
            shutil.rmtree(staging_repo_dir)
        return repo_name, False, {'error': str(e)[:100]}


def search_worker_phase8(args):
    """Worker for searching a single model."""
    model_id, github_token_manager, output_dir = args
    
    safe_name = sanitize_path_component(model_id.replace('/', '_'))
    output_file = os.path.join(output_dir, f"{safe_name}.json")
    
    # Skip if already done
    if os.path.exists(output_file) and os.path.getsize(output_file) > 10:
        return model_id, True, {'skipped': True}
    
    try:
        results = search_github_for_model(model_id, github_token_manager)
        
        # Fetch repo metadata for repos found
        for repo_name in list(results['repos'].keys())[:50]:  # Limit to top 50 repos
            metadata = fetch_repo_metadata(repo_name, github_token_manager)
            results['repos'][repo_name].update(metadata)
        
        # Save results
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        return model_id, True, {
            'total_count': results['total_count'],
            'items_found': len(results['items']),
            'repos_found': len(results['repos'])
        }
        
    except Exception as e:
        logging.error(f"Search failed for {model_id}: {e}")
        return model_id, False, {'error': str(e)[:100]}


def extract_worker_phase8(args):
    """Worker for extracting a single repo."""
    repo_name, github_token_manager, output_dir, staging_dir, referenced_files = args
    return download_and_extract_github_repo(
        repo_name, github_token_manager, output_dir, staging_dir, referenced_files
    )


def run_application_search_pipeline(token_manager, test_mode=False):
    """
    PHASE 8: Search GitHub for applications using filtered models and extract repos.
    
    Sub-phases:
    8A: Search GitHub for each model ID from filtered_models.jsonl
    8B: Aggregate unique repositories
    8C: Download and extract repositories
    """
    console_logger.info("\n" + "="*60)
    console_logger.info("PHASE 8: APPLICATION SEARCH AND EXTRACTION")
    console_logger.info("="*60)
    
    # Load GitHub tokens
    github_tokens = load_github_tokens()
    if not github_tokens:
        console_logger.error("❌ No GitHub tokens found!")
        console_logger.error("   Set GITHUB_TOKEN_1, GITHUB_TOKEN_2, etc. in api.env")
        return
    
    console_logger.info(f"🔑 Loaded {len(github_tokens)} GitHub tokens")
    github_token_manager = GitHubTokenManager(github_tokens)
    
    # Check input file
    filtered_models_file = f"{FILTER_OUTPUT_DIR}/filtered_models.jsonl"
    if not os.path.exists(filtered_models_file):
        console_logger.error(f"❌ {filtered_models_file} not found. Run --type filter first.")
        return
    
    # ==================== PHASE 8A: SEARCH ====================
    console_logger.info("\n" + "-"*40)
    console_logger.info("PHASE 8A: GitHub Code Search")
    console_logger.info("-"*40)
    
    # Load model IDs
    console_logger.info("Loading model IDs from filtered models...")
    model_ids = []
    for model in stream_jsonl(filtered_models_file):
        model_id = model.get('id')
        if model_id:
            model_ids.append(model_id)
    
    console_logger.info(f"   Found {len(model_ids):,} models to search")
    
    # Check which are already done
    done_models = set()
    for f in os.listdir(APPS_SEARCH_DIR):
        if f.endswith('.json'):
            done_models.add(f[:-5])  # Remove .json
    
    remaining_models = [m for m in model_ids if sanitize_path_component(m.replace('/', '_')) not in done_models]
    console_logger.info(f"   Already searched: {len(done_models):,}")
    console_logger.info(f"   Remaining: {len(remaining_models):,}")
    
    # Search in batches with threading
    if remaining_models:
        console_logger.info(f"\n🔍 Searching GitHub for {len(remaining_models):,} models...")
        
        search_args = [(m, github_token_manager, APPS_SEARCH_DIR) for m in remaining_models]
        
        success_count = 0
        fail_count = 0
        total_repos = 0
        
        with tqdm(total=len(remaining_models), desc="Searching", unit="model") as pbar:
            with ThreadPoolExecutor(max_workers=4) as executor:
                for i in range(0, len(search_args), BATCH_SIZE):
                    batch = search_args[i:i + BATCH_SIZE]
                    futures = [executor.submit(search_worker_phase8, args) for args in batch]
                    
                    for future in as_completed(futures):
                        try:
                            model_id, success, stats = future.result()
                            if success:
                                success_count += 1
                                if not stats.get('skipped'):
                                    total_repos += stats.get('repos_found', 0)
                            else:
                                fail_count += 1
                        except Exception as e:
                            fail_count += 1
                            logging.error(f"Search worker error: {e}")
                        pbar.update(1)
                    
                    gc.collect()
        
        console_logger.info(f"\n✅ Search complete: {success_count:,} succeeded, {fail_count:,} failed")
    
    # ==================== PHASE 8B: AGGREGATE ====================
    console_logger.info("\n" + "-"*40)
    console_logger.info("PHASE 8B: Aggregate Repositories")
    console_logger.info("-"*40)
    
    # Aggregate all unique repos from search results
    all_repos = {}  # repo_name -> {models: set, files: set, metadata: dict}
    
    for filename in tqdm(os.listdir(APPS_SEARCH_DIR), desc="Aggregating"):
        if not filename.endswith('.json'):
            continue
        
        filepath = os.path.join(APPS_SEARCH_DIR, filename)
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            model_id = data.get('model_id')
            
            for repo_name, repo_info in data.get('repos', {}).items():
                if repo_name not in all_repos:
                    all_repos[repo_name] = {
                        'models': set(),
                        'files': set(),
                        'metadata': repo_info
                    }
                
                all_repos[repo_name]['models'].add(model_id)
                all_repos[repo_name]['files'].update(repo_info.get('files', []))
                
        except Exception as e:
            logging.error(f"Error reading {filename}: {e}")
    
    console_logger.info(f"   Unique repositories found: {len(all_repos):,}")
    # ADD THIS TEST MODE CAP:
    if test_mode:
        MAX_REPOS_TEST = 50
        if len(all_repos) > MAX_REPOS_TEST:
            console_logger.info(f"\n🧪 TEST MODE: Limiting to top {MAX_REPOS_TEST} repos by stars")
            
            # Sort by stars, take top N
            repos_sorted = sorted(
                all_repos.items(),
                key=lambda x: x[1]['metadata'].get('stars', 0) or 0,
                reverse=True
            )
            all_repos = dict(repos_sorted[:MAX_REPOS_TEST])
            console_logger.info(f"   Kept top {len(all_repos):,} most-starred repos")
    # Save aggregation
    aggregation_file = os.path.join(APPS_OUTPUT_DIR, 'repo_aggregation.json')
    with open(aggregation_file, 'w') as f:
        # Convert sets to lists for JSON
        json_safe = {
            repo: {
                'models': list(info['models']),
                'files': list(info['files']),
                'metadata': info['metadata']
            }
            for repo, info in all_repos.items()
        }
        json.dump(json_safe, f, indent=2)
    console_logger.info(f"   ✅ Saved aggregation to {aggregation_file}")
    
    # ==================== PHASE 8C: EXTRACT ====================
    console_logger.info("\n" + "-"*40)
    console_logger.info("PHASE 8C: Extract Repositories")
    console_logger.info("-"*40)
    
    # Check which repos are already done
    done_repos = set()
    for d in os.listdir(APPS_REPOS_DIR):
        manifest_path = os.path.join(APPS_REPOS_DIR, d, 'extraction_manifest.json')
        if os.path.exists(manifest_path):
            done_repos.add(d)
    
    remaining_repos = {
        name: info for name, info in all_repos.items()
        if '/' in name and f"{sanitize_path_component(name.split('/')[0])}_{sanitize_path_component(name.split('/')[1])}" not in done_repos
    }
    
    console_logger.info(f"   Already extracted: {len(done_repos):,}")
    console_logger.info(f"   Remaining: {len(remaining_repos):,}")
    
    if remaining_repos:
        console_logger.info(f"\n📦 Extracting {len(remaining_repos):,} repositories...")
        
        extract_args = [
            (name, github_token_manager, APPS_REPOS_DIR, APPS_STAGING_DIR, info['files'])
            for name, info in remaining_repos.items()
        ]
        
        success_count = 0
        fail_count = 0
        total_files = 0
        total_bytes = 0
        
        with tqdm(total=len(extract_args), desc="Extracting", unit="repo") as pbar:
            with ThreadPoolExecutor(max_workers=3) as executor:
                for i in range(0, len(extract_args), BATCH_SIZE):
                    batch = extract_args[i:i + BATCH_SIZE]
                    futures = [executor.submit(extract_worker_phase8, args) for args in batch]
                    
                    for future in as_completed(futures):
                        try:
                            repo_name, success, stats = future.result()
                            if success:
                                success_count += 1
                                if not stats.get('skipped'):
                                    total_files += stats.get('files_downloaded', 0)
                                    total_bytes += stats.get('total_bytes', 0)
                            else:
                                fail_count += 1
                        except Exception as e:
                            fail_count += 1
                            logging.error(f"Extract worker error: {e}")
                        pbar.update(1)
                    
                    gc.collect()
        
        console_logger.info(f"\n✅ Extraction complete:")
        console_logger.info(f"   Repos extracted: {success_count:,}")
        console_logger.info(f"   Repos failed: {fail_count:,}")
        console_logger.info(f"   Files extracted: {total_files:,}")
        console_logger.info(f"   Total size: {total_bytes / (1024*1024):.1f} MB")
    
    # ==================== SUMMARY ====================
    console_logger.info("\n" + "="*60)
    console_logger.info("PHASE 8 COMPLETE")
    console_logger.info("="*60)
    
    # Save summary stats
    summary = {
        'timestamp': datetime.now().isoformat(),
        'models_searched': len(model_ids),
        'unique_repos_found': len(all_repos),
        'repos_extracted': len(done_repos) + success_count if remaining_repos else len(done_repos),
        'output_directory': APPS_OUTPUT_DIR
    }
    
    with open(os.path.join(APPS_OUTPUT_DIR, 'phase8_summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)
    
    console_logger.info(f"Models searched: {len(model_ids):,}")
    console_logger.info(f"Unique repos found: {len(all_repos):,}")
    console_logger.info(f"Output: {APPS_OUTPUT_DIR}/")
    console_logger.info(f"   ├── search_results/  (per-model search results)")
    console_logger.info(f"   ├── repositories/    (extracted repo files)")
    console_logger.info(f"   ├── repo_aggregation.json")
    console_logger.info(f"   └── phase8_summary.json")

# ------------------------------------------------------------------
# PART 9: SUPPLY CHAIN VERIFICATION AND RECONCILIATION
# ------------------------------------------------------------------
#
# 9A: Archive preservation (tar.gz of application_data/repositories + mined_files)
# 9B: Filter to repos with Python files + ≥1 star
# 9C: AST-based LLM call verification (multiprocessing)
# 9D: Supply chain reconciliation (drop orphaned models/datasets)
# ------------------------------------------------------------------

import ast
import multiprocessing
import subprocess

PHASE9_OUTPUT_DIR = 'supply_chain'
PHASE9_ARCHIVE_DIR = 'archives'
AST_PATTERNS_FILE = 'llm_yes_calls_filtered_frequencies_open.json'
MAX_AST_FILE_SIZE_MB = 100
AST_NUM_WORKERS = max(1, (os.cpu_count() or 4) - 1)

os.makedirs(PHASE9_OUTPUT_DIR, exist_ok=True)
os.makedirs(PHASE9_ARCHIVE_DIR, exist_ok=True)

warnings.filterwarnings("ignore", category=SyntaxWarning)


# --- AST Visitor (adapted from ast_filter_gap.py) ---

class LLMCallFinder(ast.NodeVisitor):
    """AST visitor that detects LLM API call patterns in Python source."""
    
    def __init__(self, target_patterns_set):
        self.target_patterns = target_patterns_set
        self.found_llm_call = False
        self.import_map = {}
    
    def visit_Import(self, node):
        for alias in node.names:
            self.import_map[alias.asname or alias.name] = alias.name
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node):
        module_name = node.module or ""
        module_prefix = "." * node.level + module_name if node.level > 0 else module_name
        if not module_prefix:
            self.generic_visit(node)
            return
        for alias in node.names:
            if alias.name == '*':
                continue
            assigned_name = alias.asname or alias.name
            full_path = f"{module_prefix}.{alias.name}"
            self.import_map[assigned_name] = full_path
        self.generic_visit(node)
    
    def _resolve_call_to_canonical(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._resolve_call_to_canonical(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Call):
            return self._resolve_call_to_canonical(node.func)
        return None
    
    def visit_Call(self, node):
        if self.found_llm_call:
            return
        raw_call_str = self._resolve_call_to_canonical(node.func)
        if not raw_call_str:
            self.generic_visit(node)
            return
        # Direct match
        if (raw_call_str + "(") in self.target_patterns:
            self.found_llm_call = True
            return
        # Resolved import match
        parts = raw_call_str.split('.')
        if parts[0] in self.import_map:
            resolved_base = self.import_map[parts[0]]
            resolved_call = ".".join([resolved_base] + parts[1:])
            if (resolved_call + "(") in self.target_patterns:
                self.found_llm_call = True
                return
        self.generic_visit(node)


def _scan_single_py_file(file_path, target_patterns):
    """Parse a single Python file and check for LLM call patterns."""
    try:
        if not os.path.exists(file_path):
            return False
        size = os.path.getsize(file_path)
        if size == 0 or size > MAX_AST_FILE_SIZE_MB * 1024 * 1024:
            return False
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            code = f.read()
        if not code.strip():
            return False
        tree = ast.parse(code, filename=file_path)
        visitor = LLMCallFinder(target_patterns)
        visitor.visit(tree)
        return visitor.found_llm_call
    except Exception as e:
        logging.debug(f"Unexpected error parsing {file_path}: {e}")
        return False


# --- Multiprocessing infrastructure ---

_ast_worker_patterns = None

def _init_ast_worker(patterns):
    """Initializer for multiprocessing pool - sets shared patterns once per worker."""
    global _ast_worker_patterns
    _ast_worker_patterns = patterns


def _ast_scan_repo_worker(args):
    """
    Worker: scan all .py files in a repo directory for LLM calls.
    Returns (repo_folder, True/False) indicating if repo has active LLM usage.
    """
    global _ast_worker_patterns
    repo_folder, repo_dir = args
    
    files_dir = os.path.join(repo_dir, 'files')
    if not os.path.isdir(files_dir):
        return (repo_folder, False)
    
    for root, _, files in os.walk(files_dir):
        for filename in files:
            if not filename.lower().endswith('.py'):
                continue
            filepath = os.path.join(root, filename)
            if _scan_single_py_file(filepath, _ast_worker_patterns):
                return (repo_folder, True)
    
    return (repo_folder, False)


import hashlib
import tempfile

def verify_archive_integrity(archive_path, source_dir):
    """
    Verify archive integrity by:
    1. Checking archive exists and has content
    2. Testing archive can be opened
    3. Spot-checking a few files can be extracted
    """
    console_logger.info(f"   🔍 Verifying archive integrity...")
    
    # Check 1: File exists and has content
    if not os.path.exists(archive_path):
        console_logger.error(f"   ❌ Archive does not exist: {archive_path}")
        return False
    
    size = os.path.getsize(archive_path)
    if size == 0:
        console_logger.error(f"   ❌ Archive is empty")
        return False
    
    console_logger.info(f"      Archive size: {size / (1024*1024):.1f} MB")
    
    # Check 2: Archive can be opened and listed
    try:
        result = subprocess.run(
            ['tar', 'tzf', archive_path],
            capture_output=True,
            timeout=300,
            check=True
        )
        
        # Count files in archive
        file_count = len(result.stdout.decode('utf-8', errors='ignore').strip().split('\n'))
        console_logger.info(f"      Files in archive: {file_count:,}")
        
        if file_count == 0:
            console_logger.error(f"   ❌ Archive contains no files")
            return False
            
    except subprocess.CalledProcessError as e:
        console_logger.error(f"   ❌ Archive is corrupted (tar failed): {e}")
        return False
    except subprocess.TimeoutExpired:
        console_logger.error(f"   ❌ Archive verification timed out")
        return False
    except Exception as e:
        console_logger.error(f"   ❌ Archive verification failed: {e}")
        return False
    
    # Check 3: Spot-check extraction of a few files
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Get list of files in archive
            result = subprocess.run(
                ['tar', 'tzf', archive_path],
                capture_output=True,
                timeout=300,
                check=True
            )
            files_in_archive = result.stdout.decode('utf-8', errors='ignore').strip().split('\n')
            
            # Try to extract up to 5 random files
            import random
            sample_size = min(5, len(files_in_archive))
            if sample_size > 0:
                sample_files = random.sample([f for f in files_in_archive if f.strip()], sample_size)
                
                for test_file in sample_files:
                    try:
                        subprocess.run(
                            ['tar', 'xzf', archive_path, '-C', temp_dir, test_file],
                            capture_output=True,
                            timeout=60,
                            check=True
                        )
                    except Exception as e:
                        console_logger.error(f"   ❌ Failed to extract test file '{test_file}': {e}")
                        return False
                
                console_logger.info(f"      ✓ Successfully extracted {sample_size} test files")
    
    except Exception as e:
        console_logger.error(f"   ❌ Spot-check extraction failed: {e}")
        return False
    
    console_logger.info(f"   ✅ Archive verified successfully")
    return True


def archive_directory(src_dir, archive_name, verify=True):
    """
    Create tar.gz archive of a directory with integrity verification.
    Uses pigz if available for parallel compression.
    
    Args:
        src_dir: Source directory to archive
        archive_name: Name of the archive file (e.g., 'data.tar.gz')
        verify: If True, verify archive integrity after creation
    
    Returns:
        True if successful and verified, False otherwise
    """
    if not os.path.exists(src_dir):
        console_logger.warning(f"   ⚠️ {src_dir} does not exist, skipping archive.")
        return False
    
    # Check if directory is empty
    if not any(os.scandir(src_dir)):
        console_logger.warning(f"   ⚠️ {src_dir} is empty, skipping archive.")
        return False
    
    archive_path = os.path.join(PHASE9_ARCHIVE_DIR, archive_name)
    
    # If archive already exists and is verified, skip
    if os.path.exists(archive_path):
        console_logger.info(f"   ℹ️ Archive already exists: {archive_path}")
        if verify:
            console_logger.info(f"   🔍 Verifying existing archive...")
            if verify_archive_integrity(archive_path, src_dir):
                console_logger.info(f"   ✅ Existing archive verified")
                return True
            else:
                console_logger.warning(f"   ⚠️ Existing archive failed verification, recreating...")
                try:
                    os.remove(archive_path)
                except Exception as e:
                    console_logger.error(f"   ❌ Failed to remove corrupted archive: {e}")
                    return False
        else:
            return True
    
    parent_dir = os.path.dirname(os.path.abspath(src_dir))
    dir_name = os.path.basename(os.path.abspath(src_dir))
    
    console_logger.info(f"   📦 Archiving {src_dir} → {archive_path}")
    start = time.time()

    # Check for pigz and get its full path
    try:
        result = subprocess.run(['which', 'pigz'], 
                            capture_output=True, 
                            text=True,
                            timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            pigz_path = result.stdout.strip()
            # Test that it actually works
            test = subprocess.run([pigz_path, '--version'],
                                capture_output=True,
                                timeout=5)
            has_pigz = (test.returncode == 0)
            if has_pigz:
                console_logger.info(f"   Using pigz at: {pigz_path}")
        else:
            has_pigz = False
            pigz_path = 'pigz'
    except Exception as e:
        has_pigz = False
        pigz_path = 'pigz'
        console_logger.info(f"   pigz not found, using standard gzip: {e}")
    
    # Create archive
    has_pigz = False

    try:
        if has_pigz:
            cmd = ['tar', '-I', pigz_path, '-cf', os.path.abspath(archive_path),
                '-C', parent_dir, dir_name]
        else:
            cmd = ['tar', 'czf', os.path.abspath(archive_path),
                   '-C', parent_dir, dir_name]
        
        subprocess.run(cmd, check=True, timeout=7200)  # 2hr timeout
        
        elapsed = time.time() - start
        
        # Verify archive was created
        if not os.path.exists(archive_path):
            console_logger.error(f"   ❌ Archive command completed but file doesn't exist")
            return False
        
        size_mb = os.path.getsize(archive_path) / (1024 * 1024)
        
        if size_mb == 0:
            console_logger.error(f"   ❌ Archive created but is empty")
            os.remove(archive_path)
            return False
        
        console_logger.info(f"   ✓ Archived in {elapsed:.1f}s ({size_mb:.1f} MB) {'[pigz]' if has_pigz else '[gzip]'}")
        
        # Verify integrity
        if verify:
            if not verify_archive_integrity(archive_path, src_dir):
                console_logger.error(f"   ❌ Archive verification failed, removing corrupted archive")
                try:
                    os.remove(archive_path)
                except Exception:
                    pass
                return False
        
        return True
        
    except subprocess.TimeoutExpired:
        console_logger.error(f"   ❌ Archive timed out for {src_dir}")
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except Exception:
                pass
        return False
    except subprocess.CalledProcessError as e:
        console_logger.error(f"   ❌ Archive command failed: {e}")
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except Exception:
                pass
        return False
    except Exception as e:
        console_logger.error(f"   ❌ Archive failed: {e}")
        if os.path.exists(archive_path):
            try:
                os.remove(archive_path)
            except Exception:
                pass
        return False


# --- Main Phase 9 function ---

def run_supply_chain_verification(token_manager, extended=False):
    """
    Phase 9: Verify application model usage and reconcile full supply chains.
    
    9A: Archive application_data/repositories and mined_files
    9B: Filter repos to those with .py files and ≥1 star
    9C: AST-scan filtered repos for LLM call patterns
    9D: Reconcile supply chains (drop orphaned models/datasets)
    """
    console_logger.info("\n" + "=" * 60)
    console_logger.info("PHASE 9: SUPPLY CHAIN VERIFICATION AND RECONCILIATION")
    console_logger.info("=" * 60)
    
    # ==================== 9A: ARCHIVE PRESERVATION ====================
    console_logger.info("\n" + "-" * 40)
    console_logger.info("PHASE 9A: Archive Preservation")
    console_logger.info("-" * 40)
    
    # Archive with verification
    app_archive_success = archive_directory(APPS_REPOS_DIR, 'app_repositories.tar.gz', verify=True)
    files_archive_success = archive_directory(FILES_OUTPUT_DIR, 'mined_files.tar.gz', verify=True)
    
    # CRITICAL: Stop if archives failed
    if not app_archive_success:
        console_logger.error("\n" + "!" * 60)
        console_logger.error("CRITICAL ERROR: Application repositories archive failed")
        console_logger.error("Cannot proceed with cleanup - no verified backup exists")
        console_logger.error("!" * 60)
        return
    
    if not files_archive_success:
        console_logger.error("\n" + "!" * 60)
        console_logger.error("CRITICAL ERROR: Mined files archive failed")
        console_logger.error("Cannot proceed with cleanup - no verified backup exists")
        console_logger.error("!" * 60)
        return
    
    console_logger.info("\n✅ All archives created and verified successfully")
    console_logger.info("   Safe to proceed with cleanup operations")
    
    # ==================== 9B: FILTER REPOS ====================
    console_logger.info("\n" + "-" * 40)
    console_logger.info("PHASE 9B: Filter Repos (Python files + ≥1 star)")
    console_logger.info("-" * 40)
    
    # Load repo aggregation for star counts and model mappings
    aggregation_file = os.path.join(APPS_OUTPUT_DIR, 'repo_aggregation.json')
    if not os.path.exists(aggregation_file):
        console_logger.error(f"❌ {aggregation_file} not found. Run --type apps first.")
        return
    
    with open(aggregation_file, 'r') as f:
        repo_aggregation = json.load(f)
    console_logger.info(f"   Loaded {len(repo_aggregation):,} repos from aggregation")
    
    # Filter: ≥1 star
    starred_repos = {}
    no_star_count = 0
    for repo_name, info in repo_aggregation.items():
        stars = info.get('metadata', {}).get('stars', 0) or 0
        if stars >= 1:
            starred_repos[repo_name] = info
        else:
            no_star_count += 1
    
    console_logger.info(f"   Repos with ≥1 star: {len(starred_repos):,}")
    console_logger.info(f"   Repos dropped (0 stars): {no_star_count:,}")
    
    # Filter: has Python files in extracted repo
    repos_with_python = {}
    no_python_count = 0
    missing_count = 0
    
    for repo_name, info in starred_repos.items():
        if '/' not in repo_name:
            continue
        
        owner, repo = repo_name.split('/', 1)
        repo_folder = f"{sanitize_path_component(owner)}_{sanitize_path_component(repo)}"
        repo_dir = os.path.join(APPS_REPOS_DIR, repo_folder)
        manifest_path = os.path.join(repo_dir, 'extraction_manifest.json')
        
        if not os.path.exists(manifest_path):
            missing_count += 1
            continue
        
        # Check manifest for .py files
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
            
            has_py = False
            for file_entry in manifest.get('files_extracted', []):
                path = file_entry.get('path', '')
                if path.lower().endswith('.py') and file_entry.get('status') in ('ok', 'truncated'):
                    has_py = True
                    break
            
            if has_py:
                repos_with_python[repo_name] = {
                    'folder': repo_folder,
                    'dir': repo_dir,
                    'info': info,
                    'models': info.get('models', []),
                    'stars': info.get('metadata', {}).get('stars', 0)
                }
            else:
                no_python_count += 1
                
        except Exception:
            missing_count += 1
    
    console_logger.info(f"   Repos with Python files: {len(repos_with_python):,}")
    console_logger.info(f"   Repos dropped (no .py files): {no_python_count:,}")
    console_logger.info(f"   Repos missing/unreadable manifest: {missing_count:,}")
    
    if not repos_with_python:
        console_logger.error("❌ No candidate repos remain after filtering. Exiting Phase 9.")
        return
    
    # ==================== 9C: AST SCAN ====================
    console_logger.info("\n" + "-" * 40)
    console_logger.info("PHASE 9C: AST-based LLM Call Verification")
    console_logger.info("-" * 40)
    
    # Load patterns
    if not os.path.exists(AST_PATTERNS_FILE):
        console_logger.error(f"❌ Patterns file not found: {AST_PATTERNS_FILE}")
        console_logger.error("   Place llm_yes_calls_filtered_frequencies_open.json in working directory.")
        return
    
    with open(AST_PATTERNS_FILE, 'r', encoding='utf-8') as f:
        patterns_data = json.load(f)
    target_patterns = frozenset(item['pattern'] for item in patterns_data if 'pattern' in item)
    console_logger.info(f"   Loaded {len(target_patterns):,} LLM call patterns")
    
    # Build tasks: (repo_folder, repo_dir) for each candidate
    scan_tasks = [
        (data['folder'], data['dir'])
        for data in repos_with_python.values()
    ]
    
    # Reverse map: folder -> repo_name
    folder_to_repo = {data['folder']: repo_name for repo_name, data in repos_with_python.items()}
    
    console_logger.info(f"   Scanning {len(scan_tasks):,} repos with {AST_NUM_WORKERS} workers...")
    
    verified_folders = set()
    
    with multiprocessing.Pool(
        processes=AST_NUM_WORKERS,
        initializer=_init_ast_worker,
        initargs=(target_patterns,)
    ) as pool:
        results_iter = pool.imap_unordered(_ast_scan_repo_worker, scan_tasks, chunksize=8)
        
        for repo_folder, has_llm_calls in tqdm(results_iter, total=len(scan_tasks), desc="AST Scanning"):
            if has_llm_calls:
                verified_folders.add(repo_folder)
    
    # Map back to repo names
    verified_repos = {
        folder_to_repo[folder]
        for folder in verified_folders
        if folder in folder_to_repo
    }
    
    console_logger.info(f"\n   ✅ Repos with verified LLM calls: {len(verified_repos):,}")
    console_logger.info(f"   ❌ Repos without LLM calls: {len(repos_with_python) - len(verified_repos):,}")
    
    if not verified_repos:
        console_logger.error("❌ No repos passed AST verification. Exiting Phase 9.")
        return
    
    # Save verified apps
    verified_apps_file = os.path.join(PHASE9_OUTPUT_DIR, 'verified_applications.jsonl')
    with open(verified_apps_file, 'w', encoding='utf-8') as f:
        for repo_name in sorted(verified_repos):
            data = repos_with_python[repo_name]
            record = {
                'repo_name': repo_name,
                'folder': data['folder'],
                'stars': data['stars'],
                'models_referenced': data['models'],
            }
            f.write(json.dumps(record) + '\n')
    console_logger.info(f"   ✅ Saved {len(verified_repos):,} verified apps to {verified_apps_file}")
    
    # ==================== 9D: SUPPLY CHAIN RECONCILIATION ====================
    console_logger.info("\n" + "-" * 40)
    console_logger.info("PHASE 9D: Supply Chain Reconciliation")
    console_logger.info("-" * 40)
    
    # Step 1: Get models referenced by verified apps
    console_logger.info("\n🔗 Mapping verified apps → models...")
    app_connected_models = set()
    app_to_models = {}
    
    for repo_name in verified_repos:
        models = repos_with_python[repo_name]['models']
        app_to_models[repo_name] = models
        app_connected_models.update(models)
    
    console_logger.info(f"   Models directly referenced by verified apps: {len(app_connected_models):,}")
    
    # Step 2: Load model indices from filtered_models.jsonl
    filtered_models_file = f"{FILTER_OUTPUT_DIR}/filtered_models.jsonl"
    filtered_datasets_file = f"{FILTER_OUTPUT_DIR}/filtered_datasets.jsonl"
    mapping_file = f"{RESOLUTION_DIR}/dataset_reference_mapping.json"
    
    for f_path, desc in [(filtered_models_file, "filtered models"),
                         (filtered_datasets_file, "filtered datasets"),
                         (mapping_file, "reference mapping")]:
        if not os.path.exists(f_path):
            console_logger.error(f"❌ {f_path} not found. Run previous phases first.")
            return
    
    console_logger.info("\n📂 Loading model graph...")
    
    with open(mapping_file, 'r') as f:
        ref_mapping = json.load(f)
    
    # Build model indices (streaming for memory efficiency)
    model_ids_set = set()
    model_to_base = {}
    model_dataset_refs = {}
    model_full_records = {}
    
    for model in stream_jsonl(filtered_models_file):
        model_id = model.get('id', '')
        if not model_id:
            continue
        
        model_ids_set.add(model_id)
        model_full_records[model_id] = model
        
        base_model = model.get('_base_model')
        if base_model:
            model_to_base[model_id] = base_model
        
        direct_ds = set(model.get('_direct_datasets', []))
        inherited_ds = set(model.get('_inherited_datasets', []))
        all_ds = direct_ds | inherited_ds
        if all_ds:
            model_dataset_refs[model_id] = all_ds
    
    console_logger.info(f"   Loaded {len(model_ids_set):,} filtered models")
    console_logger.info(f"   Models with base_model: {len(model_to_base):,}")
    console_logger.info(f"   Models with datasets: {len(model_dataset_refs):,}")
    
    # Step 3: Find all models in chains connected to verified apps
    console_logger.info("\n🔍 Tracing model chains from verified apps...")
    
    app_connected_in_dataset = app_connected_models & model_ids_set
    console_logger.info(f"   App-connected models in filtered set: {len(app_connected_in_dataset):,}")
    console_logger.info(f"   App-connected models NOT in filtered set: {len(app_connected_models - model_ids_set):,}")
    
    # Walk up ancestor chains from each app-connected model
    kept_models = set(app_connected_in_dataset)
    
    for model_id in app_connected_in_dataset:
        chain = get_ancestor_chain(model_id, model_to_base)
        for ancestor in chain:
            if ancestor in model_ids_set:
                kept_models.add(ancestor)
    
    # Build reverse mapping (parent -> children)
    base_to_children = defaultdict(list)
    for mid, base in model_to_base.items():
        base_to_children[base].append(mid)
    
    def has_app_connected_descendant(model_id, visited=None):
        """Check if any descendant of model_id is app-connected."""
        if visited is None:
            visited = set()
        if model_id in visited:
            return False
        visited.add(model_id)
        for child in base_to_children.get(model_id, []):
            if child in app_connected_in_dataset:
                return True
            if has_app_connected_descendant(child, visited):
                return True
        return False
    
    console_logger.info("   Checking for models with app-connected descendants...")
    additional_kept = set()
    for model_id in tqdm(model_ids_set, desc="Descendant check", disable=len(model_ids_set) < 1000):
        if model_id in kept_models:
            continue
        if has_app_connected_descendant(model_id):
            additional_kept.add(model_id)
            chain = get_ancestor_chain(model_id, model_to_base)
            for ancestor in chain:
                if ancestor in model_ids_set:
                    additional_kept.add(ancestor)
    
    kept_models.update(additional_kept)
    dropped_models = model_ids_set - kept_models
    console_logger.info(f"\n   ✅ Models kept (app-connected chains): {len(kept_models):,}")
    console_logger.info(f"   ❌ Models dropped (no app connection): {len(dropped_models):,}")
    
    # Step 4: Collect datasets referenced by kept models
    console_logger.info("\n📦 Collecting datasets for kept models...")
    kept_datasets = set()
    for model_id in kept_models:
        if model_id in model_dataset_refs:
            kept_datasets.update(model_dataset_refs[model_id])
    console_logger.info(f"   Datasets referenced by kept models: {len(kept_datasets):,}")
    
    # Step 5: Load full dataset records for kept datasets
    console_logger.info("\n📂 Loading dataset records...")
    dataset_full_records = {}
    for ds in stream_jsonl(filtered_datasets_file):
        ds_id = ds.get('id', '')
        if ds_id in kept_datasets:
            dataset_full_records[ds_id] = ds
    
    orphaned_datasets = kept_datasets - set(dataset_full_records.keys())
    if orphaned_datasets:
        console_logger.info(f"   ⚠️ {len(orphaned_datasets)} referenced datasets not found in filtered_datasets.jsonl")
    console_logger.info(f"   Datasets with full records: {len(dataset_full_records):,}")
    
    # ==================== CLEANUP: Delete orphaned files ====================
    console_logger.info("\n" + "-" * 40)
    console_logger.info("CLEANUP: Removing orphaned files from disk")
    console_logger.info("-" * 40)
    
    cleanup_log_file = os.path.join(PHASE9_OUTPUT_DIR, 'cleanup_log.jsonl')
    cleanup_writer = JsonlWriter(cleanup_log_file)
    
    # --- Clean application_data/repositories ---
    console_logger.info("\n🗑️ Cleaning application_data/repositories...")
    
    # Build set of verified repo folders
    verified_repo_folders = set()
    for repo_name in verified_repos:
        if '/' in repo_name:
            owner, repo = repo_name.split('/', 1)
            folder = f"{sanitize_path_component(owner)}_{sanitize_path_component(repo)}"
            verified_repo_folders.add(folder)
    
    app_repos_deleted = 0
    app_repos_kept = 0
    
    if os.path.isdir(APPS_REPOS_DIR):
        for folder_name in os.listdir(APPS_REPOS_DIR):
            folder_path = os.path.join(APPS_REPOS_DIR, folder_name)
            if not os.path.isdir(folder_path):
                continue
            
            if folder_name in verified_repo_folders:
                app_repos_kept += 1
            else:
                # Log before deleting
                cleanup_writer.write({
                    'action': 'delete_app_repo',
                    'folder': folder_name,
                    'path': folder_path,
                    'reason': 'not_in_verified_apps',
                    'timestamp': datetime.now().isoformat()
                })
                shutil.rmtree(folder_path)
                app_repos_deleted += 1
    
    console_logger.info(f"   Kept: {app_repos_kept:,} | Deleted: {app_repos_deleted:,}")
    
    # --- Clean mined_files/models ---
    console_logger.info("\n🗑️ Cleaning mined_files/models...")
    
    # Build set of kept model folder paths (org/repo structure)
    kept_model_folders = set()
    for model_id in kept_models:
        if '/' in model_id:
            org, repo = model_id.split('/', 1)
            kept_model_folders.add((sanitize_path_component(org), sanitize_path_component(repo)))
        else:
            kept_model_folders.add(('_root', sanitize_path_component(model_id)))
    
    mined_models_dir = os.path.join(FILES_OUTPUT_DIR, 'models')
    models_deleted = 0
    models_kept = 0
    
    if os.path.isdir(mined_models_dir):
        for org_name in os.listdir(mined_models_dir):
            org_path = os.path.join(mined_models_dir, org_name)
            if not os.path.isdir(org_path):
                continue
            for repo_name in os.listdir(org_path):
                repo_path = os.path.join(org_path, repo_name)
                if not os.path.isdir(repo_path):
                    continue
                
                if (org_name, repo_name) in kept_model_folders:
                    models_kept += 1
                else:
                    cleanup_writer.write({
                        'action': 'delete_mined_model',
                        'org': org_name,
                        'repo': repo_name,
                        'path': repo_path,
                        'reason': 'model_not_in_kept_set',
                        'timestamp': datetime.now().isoformat()
                    })
                    shutil.rmtree(repo_path)
                    models_deleted += 1
            
            # Remove empty org directories
            if os.path.isdir(org_path) and not os.listdir(org_path):
                os.rmdir(org_path)
    
    console_logger.info(f"   Kept: {models_kept:,} | Deleted: {models_deleted:,}")
    
    # --- Clean mined_files/datasets ---
    console_logger.info("\n🗑️ Cleaning mined_files/datasets...")
    
    kept_dataset_folders = set()
    for ds_id in kept_datasets:
        if '/' in ds_id:
            org, repo = ds_id.split('/', 1)
            kept_dataset_folders.add((sanitize_path_component(org), sanitize_path_component(repo)))
        else:
            kept_dataset_folders.add(('_root', sanitize_path_component(ds_id)))
    
    mined_datasets_dir = os.path.join(FILES_OUTPUT_DIR, 'datasets')
    datasets_deleted = 0
    datasets_kept = 0
    
    if os.path.isdir(mined_datasets_dir):
        for org_name in os.listdir(mined_datasets_dir):
            org_path = os.path.join(mined_datasets_dir, org_name)
            if not os.path.isdir(org_path):
                continue
            for repo_name in os.listdir(org_path):
                repo_path = os.path.join(org_path, repo_name)
                if not os.path.isdir(repo_path):
                    continue
                
                if (org_name, repo_name) in kept_dataset_folders:
                    datasets_kept += 1
                else:
                    cleanup_writer.write({
                        'action': 'delete_mined_dataset',
                        'org': org_name,
                        'repo': repo_name,
                        'path': repo_path,
                        'reason': 'dataset_not_in_kept_set',
                        'timestamp': datetime.now().isoformat()
                    })
                    shutil.rmtree(repo_path)
                    datasets_deleted += 1
            
            if os.path.isdir(org_path) and not os.listdir(org_path):
                os.rmdir(org_path)
    
    console_logger.info(f"   Kept: {datasets_kept:,} | Deleted: {datasets_deleted:,}")
    console_logger.info(f"\n   📝 Cleanup log: {cleanup_log_file}")
    

 # ==================== PHASE 9E: LICENSE/README FILTERING ====================
    if not extended:  # Filter by default, skip only with --extended flag
        console_logger.info("\n" + "-" * 40)
        console_logger.info("PHASE 9E: License/README File Filtering")
        console_logger.info("-" * 40)
        
        # License detection configuration (from paper methodology)
        LICENSE_KEYWORDS = [
            'license', 'licence', 'copying', 'unlicense', 'patents', 'notice',
            'copyright', 'disclaimer', 'authors', 'legal', 'terms', 'attribution',
            'citation', 'model_license', 'data_license', 'dataset_license',
            'modelcard', 'model_card', 'datasheet'
        ]
        LICENSE_DIRECTORIES = [
            'legal', 'license', 'licenses', 'licensing', 'copyright', 'terms', 'attribution'
        ]
        README_PATTERNS = ['readme', 'read_me', 'read-me']
        
        # Build regexes
        LICENSE_REGEX_FILTER = re.compile(
            r"^(.*\/)?([a-z0-9._-]+\.)?(" + "|".join(LICENSE_KEYWORDS) + r")(\.[a-z0-9\._-]+)?$",
            re.IGNORECASE
        )
        README_REGEX_FILTER = re.compile(
            r"^(.*\/)?(" + "|".join(README_PATTERNS) + r")(\..*)?$",
            re.IGNORECASE
        )
        LICENSE_DIRS_LOWER = set(d.lower() for d in LICENSE_DIRECTORIES)
        
        def should_keep_file(filepath):
            """Check if file matches license/README patterns."""
            filepath_lower = filepath.lower()
            
            # Check if file is in a license directory (keep ALL files in license dirs)
            path_parts = filepath_lower.split('/')
            for part in path_parts[:-1]:  # Exclude filename itself
                if part in LICENSE_DIRS_LOWER:
                    return True, 'in_license_directory'
            
            # Check if filename matches license pattern
            if LICENSE_REGEX_FILTER.match(filepath):
                return True, 'license_pattern'
            
            # Check if filename matches README pattern
            if README_REGEX_FILTER.match(filepath):
                return True, 'readme_pattern'
            
            return False, 'not_license_or_readme'
        
        filter_log_file = os.path.join(PHASE9_OUTPUT_DIR, 'file_filter_log.jsonl')
        filter_writer = JsonlWriter(filter_log_file)
        
        # Track stats
        total_files_checked = 0
        total_files_kept = 0
        total_files_deleted = 0
        total_bytes_deleted = 0
        
        # --- Filter mined_files/models ---
        console_logger.info("\n🔍 Filtering mined_files/models...")
        if os.path.isdir(mined_models_dir):
            for org_name in os.listdir(mined_models_dir):
                org_path = os.path.join(mined_models_dir, org_name)
                if not os.path.isdir(org_path):
                    continue
                for repo_name in os.listdir(org_path):
                    repo_path = os.path.join(org_path, repo_name)
                    if not os.path.isdir(repo_path):
                        continue
                    
                    # Walk all files in this repo
                    for root, dirs, files in os.walk(repo_path):
                        for filename in files:
                            filepath = os.path.join(root, filename)
                            relative_path = os.path.relpath(filepath, repo_path)
                            
                            total_files_checked += 1
                            should_keep, reason = should_keep_file(relative_path)
                            
                            if should_keep:
                                total_files_kept += 1
                            else:
                                # Delete file
                                try:
                                    file_size = os.path.getsize(filepath)
                                    os.remove(filepath)
                                    total_files_deleted += 1
                                    total_bytes_deleted += file_size
                                    
                                    filter_writer.write({
                                        'action': 'delete_file',
                                        'type': 'model',
                                        'repo': f"{org_name}/{repo_name}",
                                        'path': relative_path,
                                        'reason': reason,
                                        'size': file_size,
                                        'timestamp': datetime.now().isoformat()
                                    })
                                except Exception as e:
                                    logging.error(f"Failed to delete {filepath}: {e}")
        
        console_logger.info(f"   Models - Kept: {total_files_kept:,} | Deleted: {total_files_deleted:,}")
        
        # --- Filter mined_files/datasets ---
        console_logger.info("\n🔍 Filtering mined_files/datasets...")
        dataset_files_kept = 0
        dataset_files_deleted = 0
        
        if os.path.isdir(mined_datasets_dir):
            for org_name in os.listdir(mined_datasets_dir):
                org_path = os.path.join(mined_datasets_dir, org_name)
                if not os.path.isdir(org_path):
                    continue
                for repo_name in os.listdir(org_path):
                    repo_path = os.path.join(org_path, repo_name)
                    if not os.path.isdir(repo_path):
                        continue
                    
                    for root, dirs, files in os.walk(repo_path):
                        for filename in files:
                            filepath = os.path.join(root, filename)
                            relative_path = os.path.relpath(filepath, repo_path)
                            
                            total_files_checked += 1
                            should_keep, reason = should_keep_file(relative_path)
                            
                            if should_keep:
                                dataset_files_kept += 1
                            else:
                                try:
                                    file_size = os.path.getsize(filepath)
                                    os.remove(filepath)
                                    dataset_files_deleted += 1
                                    total_bytes_deleted += file_size
                                    
                                    filter_writer.write({
                                        'action': 'delete_file',
                                        'type': 'dataset',
                                        'repo': f"{org_name}/{repo_name}",
                                        'path': relative_path,
                                        'reason': reason,
                                        'size': file_size,
                                        'timestamp': datetime.now().isoformat()
                                    })
                                except Exception as e:
                                    logging.error(f"Failed to delete {filepath}: {e}")
        
        console_logger.info(f"   Datasets - Kept: {dataset_files_kept:,} | Deleted: {dataset_files_deleted:,}")
        total_files_kept += dataset_files_kept
        total_files_deleted += dataset_files_deleted
        
        # --- Filter application_data/repositories ---
        console_logger.info("\n🔍 Filtering application_data/repositories...")
        app_files_kept = 0
        app_files_deleted = 0
        
        if os.path.isdir(APPS_REPOS_DIR):
            for folder_name in os.listdir(APPS_REPOS_DIR):
                folder_path = os.path.join(APPS_REPOS_DIR, folder_name)
                if not os.path.isdir(folder_path):
                    continue
                
                files_dir = os.path.join(folder_path, 'files')
                if not os.path.isdir(files_dir):
                    continue
                
                for root, dirs, files in os.walk(files_dir):
                    for filename in files:
                        filepath = os.path.join(root, filename)
                        relative_path = os.path.relpath(filepath, files_dir)
                        
                        total_files_checked += 1
                        should_keep, reason = should_keep_file(relative_path)
                        
                        if should_keep:
                            app_files_kept += 1
                        else:
                            try:
                                file_size = os.path.getsize(filepath)
                                os.remove(filepath)
                                app_files_deleted += 1
                                total_bytes_deleted += file_size
                                
                                filter_writer.write({
                                    'action': 'delete_file',
                                    'type': 'application',
                                    'repo': folder_name,
                                    'path': relative_path,
                                    'reason': reason,
                                    'size': file_size,
                                    'timestamp': datetime.now().isoformat()
                                })
                            except Exception as e:
                                logging.error(f"Failed to delete {filepath}: {e}")
        
        console_logger.info(f"   Applications - Kept: {app_files_kept:,} | Deleted: {app_files_deleted:,}")
        total_files_kept += app_files_kept
        total_files_deleted += app_files_deleted
        
        console_logger.info(f"\n📊 Total filtering results:")
        console_logger.info(f"   Files checked: {total_files_checked:,}")
        console_logger.info(f"   Files kept: {total_files_kept:,}")
        console_logger.info(f"   Files deleted: {total_files_deleted:,}")
        console_logger.info(f"   Space freed: {total_bytes_deleted / (1024*1024*1024):.2f} GB")
        console_logger.info(f"   📝 Filter log: {filter_log_file}")
    
    else:
        console_logger.info("\n📁 EXTENDED MODE: Skipping file filtering (keeping all files)")
        total_files_checked = 0
        total_files_kept = 0
        total_files_deleted = 0
        total_bytes_deleted = 0
    




    # ==================== WRITE CONSOLIDATED OUTPUT ====================
    console_logger.info("\n" + "-" * 40)
    console_logger.info("Writing consolidated supply chain output")
    console_logger.info("-" * 40)
    
    consolidated_file = os.path.join(PHASE9_OUTPUT_DIR, 'supply_chain.jsonl')
    dataset_count = 0
    model_count = 0
    app_count = 0
    chain_count = 0
    
    with open(consolidated_file, 'w', encoding='utf-8') as f:
        
        # --- Datasets ---
        for ds_id in sorted(dataset_full_records.keys()):
            ds = dataset_full_records[ds_id]
            referencing_models = [
                mid for mid in kept_models
                if mid in model_dataset_refs and ds_id in model_dataset_refs[mid]
            ]
            record = {
                'type': 'dataset',
                'id': ds_id,
                '_referencing_models': referencing_models,
                '_referencing_model_count': len(referencing_models),
                'metadata': ds
            }
            f.write(json.dumps(record) + '\n')
            dataset_count += 1
        
        # --- Models ---
        for model_id in sorted(kept_models):
            model = model_full_records.get(model_id)
            if not model:
                continue
            
            referencing_apps = [
                repo for repo, models in app_to_models.items()
                if model_id in models
            ]
            ancestor_chain = get_ancestor_chain(model_id, model_to_base)
            
            record = {
                'type': 'model',
                'id': model_id,
                '_app_connected': model_id in app_connected_in_dataset,
                '_has_app_descendant': model_id in additional_kept and model_id not in app_connected_in_dataset,
                '_referencing_apps': referencing_apps,
                '_ancestor_chain': [a for a in ancestor_chain if a in kept_models],
                '_direct_datasets': list(model.get('_direct_datasets', [])),
                '_inherited_datasets': list(model.get('_inherited_datasets', [])),
                '_all_datasets': list(model_dataset_refs.get(model_id, set())),
                '_chain_depth': len(ancestor_chain),
                'metadata': model
            }
            f.write(json.dumps(record) + '\n')
            model_count += 1
        
        # --- Applications ---
        for repo_name in sorted(verified_repos):
            data = repos_with_python[repo_name]
            models = app_to_models.get(repo_name, [])
            kept_models_for_app = [m for m in models if m in kept_models]
            
            # Collect all datasets reachable through this app's models
            app_datasets = set()
            for mid in kept_models_for_app:
                app_datasets.update(model_dataset_refs.get(mid, set()))
            
            record = {
                'type': 'application',
                'id': repo_name,
                '_stars': data.get('stars', 0),
                '_models_referenced': kept_models_for_app,
                '_model_count': len(kept_models_for_app),
                '_datasets_reachable': list(app_datasets),
                '_dataset_count': len(app_datasets),
                'metadata': data.get('info', {})
            }
            f.write(json.dumps(record) + '\n')
            app_count += 1
        
        # --- Supply chain links (explicit triplets) ---
        for repo_name in sorted(verified_repos):
            models = app_to_models.get(repo_name, [])
            
            for model_id in models:
                if model_id not in kept_models:
                    continue
                
                datasets = list(model_dataset_refs.get(model_id, set()))
                ancestor_chain = get_ancestor_chain(model_id, model_to_base)
                
                record = {
                    'type': 'chain',
                    'id': f"{repo_name}||{model_id}",
                    'application': repo_name,
                    'application_stars': repos_with_python.get(repo_name, {}).get('stars', 0),
                    'model': model_id,
                    'model_likes': model_full_records.get(model_id, {}).get('likes', 0),
                    'ancestor_chain': [a for a in ancestor_chain if a in kept_models],
                    'datasets': datasets,
                    'chain_depth': len(ancestor_chain),
                    'has_direct_datasets': bool(
                        model_full_records.get(model_id, {}).get('_direct_datasets', [])
                    ),
                }
                f.write(json.dumps(record) + '\n')
                chain_count += 1
    
    console_logger.info(f"   ✅ {consolidated_file}")
    console_logger.info(f"      Datasets:     {dataset_count:,}")
    console_logger.info(f"      Models:       {model_count:,}")
    console_logger.info(f"      Applications: {app_count:,}")
    console_logger.info(f"      Chains:       {chain_count:,}")
    
    # --- Summary ---
    summary = {
        'timestamp': datetime.now().isoformat(),
        'phase_9a_archives': {
            'app_repos': os.path.exists(os.path.join(PHASE9_ARCHIVE_DIR, 'app_repositories.tar.gz')),
            'mined_files': os.path.exists(os.path.join(PHASE9_ARCHIVE_DIR, 'mined_files.tar.gz')),
        },
        'phase_9b_filtering': {
            'total_repos_in_aggregation': len(repo_aggregation),
            'repos_with_stars': len(starred_repos),
            'repos_with_python': len(repos_with_python),
            'dropped_no_stars': no_star_count,
            'dropped_no_python': no_python_count,
            'missing_manifests': missing_count,
        },
        'phase_9c_ast_verification': {
            'repos_scanned': len(scan_tasks),
            'repos_verified': len(verified_repos),
            'repos_failed': len(repos_with_python) - len(verified_repos),
            'patterns_used': len(target_patterns),
        },
        'phase_9d_reconciliation': {
            'app_connected_models': len(app_connected_in_dataset),
            'models_with_app_descendants': len(additional_kept),
            'total_models_kept': model_count,
            'total_models_dropped': len(dropped_models),
            'total_datasets_kept': dataset_count,
            'total_supply_chains': chain_count,
        },
        'cleanup': {
            'app_repos_deleted': app_repos_deleted,
            'app_repos_kept': app_repos_kept,
            'mined_models_deleted': models_deleted,
            'mined_models_kept': models_kept,
            'mined_datasets_deleted': datasets_deleted,
            'mined_datasets_kept': datasets_kept,
        },
        'final_counts': {
            'applications': app_count,
            'models': model_count,
            'datasets': dataset_count,
            'supply_chain_records': chain_count,
        },
        'phase_9e_filtering': {
        'total_files_checked': total_files_checked,
        'total_files_kept': total_files_kept,
        'total_files_deleted': total_files_deleted,
        'total_bytes_deleted': total_bytes_deleted,
         }
    }
    
    summary_file = os.path.join(PHASE9_OUTPUT_DIR, 'phase9_summary.json')
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)
    
    console_logger.info(f"\n" + "=" * 60)
    console_logger.info("PHASE 9 COMPLETE")
    console_logger.info("=" * 60)
    console_logger.info(f"Applications: {len(repo_aggregation):,} → {app_count:,}")
    console_logger.info(f"Models:       {len(model_ids_set):,} → {model_count:,}")
    console_logger.info(f"Datasets:     {dataset_count:,}")
    console_logger.info(f"Chains:       {chain_count:,}")
    console_logger.info(f"\nCleanup:")
    console_logger.info(f"   App repos removed:      {app_repos_deleted:,}")
    console_logger.info(f"   Mined models removed:   {models_deleted:,}")
    console_logger.info(f"   Mined datasets removed: {datasets_deleted:,}")
    console_logger.info(f"\nOutput: {PHASE9_OUTPUT_DIR}/")
    console_logger.info(f"   ├── supply_chain.jsonl   (consolidated: datasets + models + apps + chains)")
    console_logger.info(f"   ├── verified_applications.jsonl")
    console_logger.info(f"   ├── cleanup_log.jsonl")
    console_logger.info(f"   └── phase9_summary.json")
    console_logger.info(f"\nArchives: {PHASE9_ARCHIVE_DIR}/")
    console_logger.info(f"   ├── app_repositories.tar.gz")
    console_logger.info(f"   └── mined_files.tar.gz")

# ------------------------------------------------------------------
# PART 10: SCANCODE EXECUTION + FINAL DATASET BUILDER
# ------------------------------------------------------------------
#
# 10A: Discover ScanCode binary + run per-repo scans in parallel
# 10B: Parse scancode results + build final cleaned JSONL
# ------------------------------------------------------------------
#
# Paste this ABOVE the `if __name__ == "__main__":` block in super_scraper.py
# Then update argparse and main block (see instructions at bottom of file).
# ------------------------------------------------------------------

SCANCODE_OUTPUT_DIR = 'scancode_results'
FINAL_OUTPUT_DIR = 'final_dataset'

os.makedirs(SCANCODE_OUTPUT_DIR, exist_ok=True)
os.makedirs(FINAL_OUTPUT_DIR, exist_ok=True)

# Defaults (overridden by argparse)
DEFAULT_SCANCODE_PROCESSES = 4
DEFAULT_SCANCODE_CORES = 1
DEFAULT_SCANCODE_TIMEOUT = 600  # 10 minutes per repo


def discover_scancode_binary():
    """
    Find scancode binary. Priority:
    1. shutil.which('scancode')  — on PATH
    2. SCANCODE_BIN environment variable
    3. Error with instructions
    """
    # Try PATH first
    path_bin = shutil.which('scancode')
    if path_bin:
        console_logger.info(f"   Found scancode on PATH: {path_bin}")
        return path_bin

    # Try environment variable
    env_bin = os.getenv('SCANCODE_BIN')
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        console_logger.info(f"   Found scancode via SCANCODE_BIN: {env_bin}")
        return env_bin

    # Not found
    console_logger.error("❌ ScanCode binary not found!")
    console_logger.error("   Options:")
    console_logger.error("   1. Install: pip install scancode-toolkit")
    console_logger.error("   2. Add to PATH")
    console_logger.error("   3. Set SCANCODE_BIN=/path/to/scancode in api.env")
    return None


def get_scancode_command(scancode_bin, input_path, output_path, cores=1):
    """Build the scancode command with all required flags."""
    return [
        scancode_bin,
        '--license',
        '--license-text',
        '--classify',
        '--license-clarity-score',
        '--license-score', '0',
        '--license-diagnostics',
        '--license-references',
        '--copyright',
        '--summary',
        '--json-pp', output_path,
        '--processes', str(cores),
        input_path
    ]


def run_scancode_on_repo(args):
    """
    Worker: run scancode on a single repo directory.
    Returns (repo_key, success, info_dict).
    """
    repo_key, input_dir, output_path, scancode_bin, cores, timeout = args

    # Skip if already done
    if os.path.exists(output_path) and os.path.getsize(output_path) > 10:
        return (repo_key, True, {'skipped': True})

    # Check if directory is empty or has no files
    has_files = False
    for root, dirs, files in os.walk(input_dir):
        if files:
            has_files = True
            break

    if not has_files:
        return (repo_key, False, {'empty': True})

    # Build and run command
    cmd = get_scancode_command(scancode_bin, input_dir, output_path, cores)

    try:
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False  # Don't raise on non-zero exit
        )

        if result.returncode == 0 and os.path.exists(output_path):
            size = os.path.getsize(output_path)
            return (repo_key, True, {'size': size})
        else:
            stderr = result.stderr.decode('utf-8', errors='ignore')[:200]
            logging.error(f"ScanCode failed for {repo_key}: exit={result.returncode} {stderr}")
            return (repo_key, False, {'error': f'exit_{result.returncode}', 'stderr': stderr})

    except subprocess.TimeoutExpired:
        console_logger.warning(f"   ⏱️ ScanCode timeout for {repo_key} ({timeout}s)")
        # Clean up partial output
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        return (repo_key, False, {'error': 'timeout'})
    except Exception as e:
        logging.error(f"ScanCode error for {repo_key}: {e}")
        return (repo_key, False, {'error': str(e)[:100]})


def collect_scan_targets():
    """
    Collect all repo directories to scan across models, datasets, and applications.
    Returns list of (repo_key, input_dir, output_path, scan_type).
    """
    targets = []

    # --- Models: mined_files/models/{org}/{repo}/ ---
    mined_models_dir = os.path.join(FILES_OUTPUT_DIR, 'models')
    if os.path.isdir(mined_models_dir):
        for org_name in os.listdir(mined_models_dir):
            org_path = os.path.join(mined_models_dir, org_name)
            if not os.path.isdir(org_path):
                continue
            for repo_name in os.listdir(org_path):
                repo_path = os.path.join(org_path, repo_name)
                if not os.path.isdir(repo_path):
                    continue

                repo_key = f"{org_name}/{repo_name}"
                output_path = os.path.join(
                    SCANCODE_OUTPUT_DIR, 'models',
                    f"{org_name}__{repo_name}.json"
                )
                targets.append((repo_key, repo_path, output_path, 'model'))

    # --- Datasets: mined_files/datasets/{org}/{repo}/ ---
    mined_datasets_dir = os.path.join(FILES_OUTPUT_DIR, 'datasets')
    if os.path.isdir(mined_datasets_dir):
        for org_name in os.listdir(mined_datasets_dir):
            org_path = os.path.join(mined_datasets_dir, org_name)
            if not os.path.isdir(org_path):
                continue
            for repo_name in os.listdir(org_path):
                repo_path = os.path.join(org_path, repo_name)
                if not os.path.isdir(repo_path):
                    continue

                repo_key = f"{org_name}/{repo_name}"
                output_path = os.path.join(
                    SCANCODE_OUTPUT_DIR, 'datasets',
                    f"{org_name}__{repo_name}.json"
                )
                targets.append((repo_key, repo_path, output_path, 'dataset'))

    # --- Applications: application_data/repositories/{org}_{repo}/files/ ---
    if os.path.isdir(APPS_REPOS_DIR):
        for folder_name in os.listdir(APPS_REPOS_DIR):
            folder_path = os.path.join(APPS_REPOS_DIR, folder_name)
            if not os.path.isdir(folder_path):
                continue

            # The actual files are in /files/ subdirectory
            files_dir = os.path.join(folder_path, 'files')
            if not os.path.isdir(files_dir):
                # Some repos might have files at root (no /files/ subdir)
                files_dir = folder_path

            # Reconstruct org/repo from folder name (org_repo format)
            if '_' in folder_name:
                parts = folder_name.split('_', 1)
                repo_key = f"{parts[0]}/{parts[1]}"
            else:
                repo_key = folder_name

            output_path = os.path.join(
                SCANCODE_OUTPUT_DIR, 'applications',
                f"{folder_name}.json"
            )
            targets.append((repo_key, files_dir, output_path, 'application'))

    return targets


def run_scancode_phase(scancode_processes=DEFAULT_SCANCODE_PROCESSES,
                       scancode_cores=DEFAULT_SCANCODE_CORES,
                       scancode_timeout=DEFAULT_SCANCODE_TIMEOUT):
    """
    Phase 10A: Run ScanCode on all repos.
    """
    console_logger.info("\n" + "=" * 60)
    console_logger.info("PHASE 10A: ScanCode Execution")
    console_logger.info("=" * 60)

    # Discover binary
    console_logger.info("\n🔍 Discovering ScanCode binary...")
    scancode_bin = discover_scancode_binary()
    if not scancode_bin:
        return False

    # Verify it works
    try:
        result = subprocess.run(
            [scancode_bin, '--version'],
            capture_output=True, timeout=30
        )
        version = result.stdout.decode('utf-8', errors='ignore').strip()
        console_logger.info(f"   ScanCode version: {version}")
    except Exception as e:
        console_logger.error(f"❌ ScanCode binary not functional: {e}")
        return False

    # Create output subdirectories
    for subdir in ['models', 'datasets', 'applications']:
        os.makedirs(os.path.join(SCANCODE_OUTPUT_DIR, subdir), exist_ok=True)

    # Collect targets
    console_logger.info("\n📂 Collecting scan targets...")
    all_targets = collect_scan_targets()
    console_logger.info(f"   Total repos to scan: {len(all_targets):,}")

    # Separate by type for reporting
    by_type = defaultdict(list)
    for t in all_targets:
        by_type[t[3]].append(t)
    for scan_type, targets in by_type.items():
        console_logger.info(f"   {scan_type}s: {len(targets):,}")

    # Check which are already done
    remaining = []
    already_done = 0
    empty_repos = []

    for repo_key, input_dir, output_path, scan_type in all_targets:
        if os.path.exists(output_path) and os.path.getsize(output_path) > 10:
            already_done += 1
            continue

        # Check if directory has files
        has_files = False
        for root, dirs, files in os.walk(input_dir):
            if files:
                has_files = True
                break

        if not has_files:
            empty_repos.append((repo_key, scan_type))
            continue

        remaining.append((repo_key, input_dir, output_path, scan_type))

    console_logger.info(f"\n   Already scanned: {already_done:,}")
    console_logger.info(f"   Empty (no files): {len(empty_repos):,}")
    console_logger.info(f"   Remaining to scan: {len(remaining):,}")

    # Save empty repos list (these will get scancode: null)
    empty_repos_file = os.path.join(SCANCODE_OUTPUT_DIR, 'empty_repos.json')
    with open(empty_repos_file, 'w') as f:
        json.dump([{'repo_key': r, 'type': t} for r, t in empty_repos], f, indent=2)
    console_logger.info(f"   📝 Empty repos saved to {empty_repos_file}")

    if not remaining:
        console_logger.info("   ✅ All repos already scanned!")
        return True

    # Run scans in parallel using ThreadPoolExecutor (subprocess-based, so threads are fine)
    console_logger.info(f"\n🔬 Running ScanCode: {scancode_processes} parallel instances × {scancode_cores} cores each")
    console_logger.info(f"   Timeout per repo: {scancode_timeout}s")

    # Build worker args
    worker_args = [
        (repo_key, input_dir, output_path, scancode_bin, scancode_cores, scancode_timeout)
        for repo_key, input_dir, output_path, scan_type in remaining
    ]

    success_count = 0
    fail_count = 0
    timeout_count = 0

    with tqdm(total=len(worker_args), desc="ScanCode", unit="repo") as pbar:
        with ThreadPoolExecutor(max_workers=scancode_processes) as executor:
            # Process in batches for memory management
            batch_size = scancode_processes * 4
            for i in range(0, len(worker_args), batch_size):
                batch = worker_args[i:i + batch_size]
                futures = [executor.submit(run_scancode_on_repo, args) for args in batch]

                for future in as_completed(futures):
                    try:
                        repo_key, success, info = future.result()
                        if success:
                            success_count += 1
                        else:
                            if info.get('error') == 'timeout':
                                timeout_count += 1
                            elif info.get('empty'):
                                pass  # Already tracked
                            else:
                                fail_count += 1
                    except Exception as e:
                        fail_count += 1
                        logging.error(f"ScanCode worker error: {e}")
                    pbar.update(1)

                gc.collect()

    console_logger.info(f"\n✅ ScanCode execution complete:")
    console_logger.info(f"   Succeeded: {success_count:,}")
    console_logger.info(f"   Failed: {fail_count:,}")
    console_logger.info(f"   Timed out: {timeout_count:,}")
    console_logger.info(f"   Empty (no files): {len(empty_repos):,}")

    return True


# --- Phase 10B: Parse ScanCode results + build final JSONL ---




SCANCODE_RULE_LOOKUP_FILE = 'scancode_rule_lookup.json'

def load_rule_lookup():
    """Load the scancode rule lookup for is_license_text determination."""
    if not os.path.exists(SCANCODE_RULE_LOOKUP_FILE):
        console_logger.warning(f"⚠️ {SCANCODE_RULE_LOOKUP_FILE} not found. match_coverage will all be null.")
        return {}
    with open(SCANCODE_RULE_LOOKUP_FILE, 'r') as f:
        return json.load(f)

def build_scancode_index():
    """
    Build index of all scancode results: repo_key -> parsed scancode data.
    Also loads the empty repos list (scancode: null).

    Returns:
        scancode_index: {repo_key: parsed_data}
        empty_repos: set of repo_keys that had no files to scan
    """
    console_logger.info("\n📂 Building scancode index...")
    scancode_index = {}
    rule_lookup = load_rule_lookup()
    file_count = 0

    for scan_type in ['models', 'datasets', 'applications']:
        scan_dir = os.path.join(SCANCODE_OUTPUT_DIR, scan_type)
        if not os.path.isdir(scan_dir):
            continue

        for filename in os.listdir(scan_dir):
            if not filename.endswith('.json'):
                continue

            filepath = os.path.join(scan_dir, filename)
            stem = filename[:-5]  # Remove .json

            # Normalize to org/repo format
            if scan_type in ('models', 'datasets'):
                # Format: org__repo.json → org/repo
                if '__' in stem:
                    parts = stem.split('__', 1)
                    repo_key = f"{parts[0]}/{parts[1]}"
                else:
                    continue
            else:
                # Format: org_repo.json → org/repo
                if '_' in stem:
                    parts = stem.split('_', 1)
                    repo_key = f"{parts[0]}/{parts[1]}"
                else:
                    continue

            parsed = parse_scancode_file(filepath, rule_lookup)
            scancode_index[repo_key] = parsed
            file_count += 1

    console_logger.info(f"   Parsed {file_count:,} scancode result files")
    console_logger.info(f"   Unique repos indexed: {len(scancode_index):,}")

    # Load empty repos
    empty_repos = set()
    empty_repos_file = os.path.join(SCANCODE_OUTPUT_DIR, 'empty_repos.json')
    if os.path.exists(empty_repos_file):
        with open(empty_repos_file, 'r') as f:
            for entry in json.load(f):
                empty_repos.add(entry['repo_key'])
        console_logger.info(f"   Empty repos (will be null): {len(empty_repos):,}")

    return scancode_index, empty_repos


def extract_licenses_from_scancode(scancode_data):
    """
    Extract deduplicated lowercase license list from scancode results.
    These go in the top-level 'licenses' field.
    """
    if not scancode_data or not scancode_data.get('licenses'):
        return []

    licenses = set()
    for spdx_expr in scancode_data['licenses'].keys():
        # Lowercase for consistency with the final schema
        licenses.add(spdx_expr.lower())

    return sorted(licenses)


def format_scancode_for_output(scancode_data):
    """
    Convert parsed scancode data to the output format:
    [{license_expression_spdx, origins: [{file_path, match_coverage}]}, ...]
    """
    if not scancode_data or not scancode_data.get('licenses'):
        return []

    output = []
    for spdx_expr, lic_data in scancode_data['licenses'].items():
        output.append({
            'license_expression_spdx': spdx_expr,
            'origins': lic_data['origins']
        })

    return output


def format_copyrights_for_output(scancode_data):
    """Convert copyrights dict to output format."""
    if not scancode_data or not scancode_data.get('copyrights'):
        return []

    return [
        {'copyright': text, 'origins': origins}
        for text, origins in scancode_data['copyrights'].items()
    ]


## ============================================================
## PHASE 10B PATCH - Replace these two functions in super_scraper.py
## ============================================================
##
## FIX 1: parse_scancode_file
##   - Uses 'license_expression' (not 'license_expression_spdx')
##   - Also tries 'detected_license_expression_spdx' at file level
##   - Builds is_license_text lookup from top-level license_rule_references
##
## FIX 2: build_final_dataset
##   - Models/datasets: 'licenses' from HF metadata tags
##   - Applications: 'licenses' from scancode detections
##   - All: 'scancode' field from scancode detections
## ============================================================


def parse_scancode_file(file_path, rule_lookup=None):

    """
    Parse a single scancode output file.
    Returns: {
        'licenses': {lic_expr: {'origins': [...]}},
        'copyrights': {text: [origins]},
        'holders': [holder_entries]
    }

    match_coverage is kept numeric ONLY when the matched rule is a full
    license text (is_license_text == True from license_rule_references).
    Otherwise it is set to null.
    """
    result = {
        'licenses': {},
        'copyrights': {},
        'holders': []
    }

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"Failed to parse scancode file {file_path}: {e}")
        return result

    # Build rule_identifier -> is_license_text lookup from top-level references
    rule_is_license_text = {}
    for ref in data.get('license_rule_references', []):
        if isinstance(ref, dict):
            rule_id = ref.get('identifier', '')
            rule_is_license_text[rule_id] = ref.get('is_license_text', False)

    for file_entry in data.get('files', []):
        if file_entry.get('type') != 'file':
            continue

        file_path_rel = file_entry.get('path', '')

        # --- Licenses ---
        # Strategy: Use file-level detected_license_expression_spdx if available,
        # otherwise fall back to detection-level license_expression per detection.

        # Approach: iterate detections, extract license expression + match details
        for lic_detection in file_entry.get('license_detections', []):
            # Try SPDX first, then plain expression
            lic_expr = (
                lic_detection.get('license_expression_spdx')
                or lic_detection.get('license_expression')
                or ''
            )
            if not lic_expr:
                continue

            if lic_expr not in result['licenses']:
                result['licenses'][lic_expr] = {'origins': []}

            for match in lic_detection.get('matches', []):
                raw_coverage = match.get('match_coverage', 0.0)

                rule_id = match.get('rule_identifier', '')
                is_license_text = False
                
                if rule_lookup and rule_id in rule_lookup:
                    is_license_text = rule_lookup[rule_id].get('is_license_text', False)
                else:
                    is_license_text = rule_is_license_text.get(rule_id, False)

                # Only keep numeric coverage for full license text matches
                coverage = raw_coverage if is_license_text else None

                origin = {
                    'file_path': file_path_rel,
                    'match_coverage': coverage
                }

                if origin not in result['licenses'][lic_expr]['origins']:
                    result['licenses'][lic_expr]['origins'].append(origin)

        # If no detections found but file has a file-level SPDX expression, use that
        if not file_entry.get('license_detections') and file_entry.get('detected_license_expression_spdx'):
            lic_expr = file_entry['detected_license_expression_spdx']
            if lic_expr not in result['licenses']:
                result['licenses'][lic_expr] = {'origins': []}
            origin = {
                'file_path': file_path_rel,
                'match_coverage': None
            }
            if origin not in result['licenses'][lic_expr]['origins']:
                result['licenses'][lic_expr]['origins'].append(origin)

        # --- Copyrights ---
        for cr_entry in file_entry.get('copyrights', []):
            cr_text = cr_entry.get('copyright', '') if isinstance(cr_entry, dict) else str(cr_entry)
            if not cr_text:
                continue

            if cr_text not in result['copyrights']:
                result['copyrights'][cr_text] = []

            origin = {'file_path': file_path_rel}
            if origin not in result['copyrights'][cr_text]:
                result['copyrights'][cr_text].append(origin)

        # --- Holders ---
        for holder_entry in file_entry.get('holders', []):
            if holder_entry not in result['holders']:
                result['holders'].append(holder_entry)

    return result


def extract_hf_metadata_licenses(metadata):
    """
    Extract license labels from HuggingFace metadata tags.
    Models/datasets have tags like 'license:apache-2.0'.
    Also checks card_data.license field.
    Returns sorted deduplicated list of lowercase license strings.
    """
    licenses = set()

    # From tags
    tags = metadata.get('tags', [])
    if tags:
        for tag in tags:
            if isinstance(tag, str) and tag.startswith('license:'):
                licenses.add(tag[8:].lower())

    # From card_data.license (some repos use this)
    card_data = metadata.get('card_data')
    if card_data and isinstance(card_data, dict):
        lic = card_data.get('license')
        if isinstance(lic, str) and lic.strip():
            licenses.add(lic.strip().lower())
        elif isinstance(lic, list):
            for l in lic:
                if isinstance(l, str) and l.strip():
                    licenses.add(l.strip().lower())

    return sorted(licenses)


def build_final_dataset():
    """
    Phase 10B: Build final cleaned JSONL from supply_chain.jsonl + scancode results.
    Output matches the schema in document 4 exactly.

    License source:
      - Models/datasets: 'licenses' from HF metadata tags
      - Applications: 'licenses' from scancode detections
      - All: 'scancode' field from scancode detection details
    """
    console_logger.info("\n" + "=" * 60)
    console_logger.info("PHASE 10B: Build Final Dataset")
    console_logger.info("=" * 60)

    supply_chain_file = os.path.join(PHASE9_OUTPUT_DIR, 'supply_chain.jsonl')
    if not os.path.exists(supply_chain_file):
        console_logger.error(f"❌ {supply_chain_file} not found. Run --type verify first.")
        return

    # Build scancode index
    scancode_index, empty_repos = build_scancode_index()

    # Stream supply_chain.jsonl and build final output
    console_logger.info("\n📝 Building final dataset...")

    output_file = 'filtered_complete_chains_cleaned.jsonl'
    stats = {
        'applications': 0,
        'models': 0,
        'datasets': 0,
        'chains_skipped': 0,
        'with_scancode': 0,
        'with_null_scancode': 0,
        'with_empty_scancode': 0,
    }

    with open(output_file, 'w', encoding='utf-8') as out_f:
        for entry in stream_jsonl(supply_chain_file):
            entry_type = entry.get('type')
            entry_id = entry.get('id')

            # Skip chain records — relationship info is embedded in models/apps
            if entry_type == 'chain':
                stats['chains_skipped'] += 1
                continue

            # Look up scancode data
            sc_data = scancode_index.get(entry_id)

            # Determine scancode field value
            if entry_id in empty_repos:
                # No files to scan → null
                scancode_out = None
                scancode_licenses = []
                copyrights_out = []
                holders_out = []
                stats['with_null_scancode'] += 1
            elif sc_data is not None:
                scancode_out = format_scancode_for_output(sc_data)
                scancode_licenses = extract_licenses_from_scancode(sc_data)
                copyrights_out = format_copyrights_for_output(sc_data)
                holders_out = sc_data.get('holders', [])
                if scancode_out:
                    stats['with_scancode'] += 1
                else:
                    stats['with_empty_scancode'] += 1
            else:
                # No scancode output file exists — treat as null
                scancode_out = None
                scancode_licenses = []
                copyrights_out = []
                holders_out = []
                stats['with_null_scancode'] += 1

            # Build output record based on type
            if entry_type == 'application':
                # Likes = GitHub stars
                likes = entry.get('_stars', 0) or 0
                if likes == 0:
                    metadata = entry.get('metadata', {})
                    likes = metadata.get('metadata', {}).get('stars', 0) or 0

                # Models this app references
                models_list = entry.get('_models_referenced', [])

                # Applications: licenses from SCANCODE (no HF metadata)
                record = {
                    'id': entry_id,
                    'type': 'application',
                    'licenses': scancode_licenses,
                    'likes': likes,
                    'models': models_list,
                    'scancode': scancode_out,
                    'copyrights': copyrights_out,
                    'holders': holders_out,
                }
                stats['applications'] += 1

            elif entry_type == 'model':
                # metadata is the full model record from filtered_models.jsonl
                metadata = entry.get('metadata', {})
                likes = metadata.get('likes', 0) or 0

                # Models/datasets: licenses from HF METADATA TAGS
                hf_licenses = extract_hf_metadata_licenses(metadata)

                # Base models and datasets
                base_model = metadata.get('_base_model')
                base_models = [base_model] if base_model else []

                datasets = entry.get('_direct_datasets', [])
                if not datasets:
                    datasets = entry.get('_all_datasets', [])

                record = {
                    'id': entry_id,
                    'type': 'model',
                    'licenses': hf_licenses,
                    'likes': likes,
                    'base_models': base_models,
                    'datasets': datasets,
                    'scancode': scancode_out,
                    'copyrights': copyrights_out,
                    'holders': holders_out,
                }
                stats['models'] += 1

            elif entry_type == 'dataset':
                metadata = entry.get('metadata', {})
                likes = metadata.get('likes', 0) or 0

                # Datasets: licenses from HF METADATA TAGS
                hf_licenses = extract_hf_metadata_licenses(metadata)

                record = {
                    'id': entry_id,
                    'type': 'dataset',
                    'licenses': hf_licenses,
                    'likes': likes,
                    'scancode': scancode_out,
                }
                # Add copyrights/holders only if scancode is not null
                if scancode_out is not None:
                    record['copyrights'] = copyrights_out
                    record['holders'] = holders_out

                stats['datasets'] += 1

            else:
                continue

            out_f.write(json.dumps(record) + '\n')

    console_logger.info(f"\n✅ Final dataset written to {output_file}")
    console_logger.info(f"   Applications: {stats['applications']:,}")
    console_logger.info(f"   Models: {stats['models']:,}")
    console_logger.info(f"   Datasets: {stats['datasets']:,}")
    console_logger.info(f"   Chains skipped: {stats['chains_skipped']:,}")
    console_logger.info(f"\n   With scancode data: {stats['with_scancode']:,}")
    console_logger.info(f"   With empty scancode: {stats['with_empty_scancode']:,}")
    console_logger.info(f"   With null scancode: {stats['with_null_scancode']:,}")

    # Save stats
    stats_file = os.path.join(FINAL_OUTPUT_DIR, 'phase10_summary.json')
    stats['timestamp'] = datetime.now().isoformat()
    with open(stats_file, 'w') as f:
        json.dump(stats, f, indent=2)

    console_logger.info(f"   📝 Summary: {stats_file}")

    return output_file



def run_scancode_pipeline(scancode_processes=DEFAULT_SCANCODE_PROCESSES,
                          scancode_cores=DEFAULT_SCANCODE_CORES,
                          scancode_timeout=DEFAULT_SCANCODE_TIMEOUT):
    """
    Phase 10: Complete scancode + final dataset pipeline.
    """
    console_logger.info("\n" + "=" * 60)
    console_logger.info("PHASE 10: SCANCODE + FINAL DATASET BUILDER")
    console_logger.info("=" * 60)

    # 10A: Run ScanCode
    success = run_scancode_phase(scancode_processes, scancode_cores, scancode_timeout)
    if not success:
        console_logger.error("❌ Phase 10A failed. Cannot build final dataset.")
        return

    # 10B: Build final JSONL
    output_file = build_final_dataset()

    console_logger.info("\n" + "=" * 60)
    console_logger.info("PHASE 10 COMPLETE")
    console_logger.info("=" * 60)
    console_logger.info(f"ScanCode results: {SCANCODE_OUTPUT_DIR}/")
    console_logger.info(f"   ├── models/")
    console_logger.info(f"   ├── datasets/")
    console_logger.info(f"   └── applications/")
    console_logger.info(f"Final dataset: {FINAL_OUTPUT_DIR}/")
    console_logger.info(f"   ├── filtered_complete_chains_cleaned.jsonl")
    console_logger.info(f"   └── phase10_summary.json")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HuggingFace Super Scraper v3.0")
    parser.add_argument('--test', action='store_true', help='Test run with small sample')
    parser.add_argument('--type', 
                        choices=['models', 'datasets', 'both', 'resolve', 'filter', 'mine',
                                 'apps', 'verify', 'scancode', 'all'],
                        default='both',
                        help='''What to process:
                            models - fetch model metadata
                            datasets - fetch dataset metadata  
                            both - fetch models and datasets
                            resolve - resolve ambiguous dataset references (Part 5)
                            filter - create filtered dataset (Part 6)
                            mine - mine license/config files (Part 7)
                            apps - search GitHub for applications (Part 8)
                            verify - supply chain verification and reconciliation (Part 9)
                            scancode - run ScanCode + build final dataset (Part 10)
                            all - run complete pipeline (Parts 1-10)''')
    parser.add_argument('--skip-resolve', action='store_true', 
                        help='Skip resolution step after downloading')
    parser.add_argument('--extended', action='store_true',
                        help='Keep all extracted files (skip license/README filtering in Phase 9)')
    parser.add_argument('--scancode-processes', type=int, default=4,
                        help='Number of parallel ScanCode instances (default: 4)')
    parser.add_argument('--scancode-cores', type=int, default=1,
                        help='CPU cores per ScanCode instance (default: 1)')
    parser.add_argument('--scancode-timeout', type=int, default=600,
                        help='Per-repo ScanCode timeout in seconds (default: 600)')
    args = parser.parse_args()

    tokens = load_tokens()
    if not tokens:
        print("❌ No HuggingFace tokens found in api.env")
        print("   Create api.env with HUGGINGFACE_TOKEN_1=hf_xxx")
        exit(1)
    
    print(f"🔑 Loaded {len(tokens)} HF tokens | Workers: {NUM_WORKERS}")
    if args.test:
        print("🧪 TEST MODE ENABLED")
    token_manager = SmartTokenManager(tokens)

    if args.type == 'resolve':
        if args.test:
            prepare_test_files_for_pipeline()
        run_resolution_pipeline(token_manager)
    
    elif args.type == 'filter':
        if args.test:
            prepare_test_files_for_pipeline()
        run_filter_pipeline(token_manager)
    
    elif args.type == 'mine':
        if args.test:
            prepare_test_files_for_pipeline()
        run_file_mining_pipeline(token_manager)
    
    elif args.type == 'apps':
        if args.test:
            prepare_test_files_for_pipeline()
        run_application_search_pipeline(token_manager, test_mode=args.test)
    
    elif args.type == 'verify':
        if args.test:
            prepare_test_files_for_pipeline()
        run_supply_chain_verification(token_manager, extended=args.extended)

    elif args.type == 'scancode':
        if args.test:
            prepare_test_files_for_pipeline()
        run_scancode_pipeline(
            scancode_processes=args.scancode_processes,
            scancode_cores=args.scancode_cores,
            scancode_timeout=args.scancode_timeout
        )
    
    elif args.type == 'all':
        console_logger.info("🚀 Running FULL pipeline (Parts 1-10)...")
        
        run_pipeline('model', token_manager, test_mode=args.test)
        run_pipeline('dataset', token_manager, test_mode=args.test)
        
        if args.test:
            console_logger.info("\n📋 Preparing test files for Parts 5-10...")
            prepare_test_files_for_pipeline()
        
        run_resolution_pipeline(token_manager)
        run_filter_pipeline(token_manager)
        run_file_mining_pipeline(token_manager)
        run_application_search_pipeline(token_manager, test_mode=args.test)  
        run_supply_chain_verification(token_manager, extended=args.extended)
        run_scancode_pipeline(
            scancode_processes=args.scancode_processes,
            scancode_cores=args.scancode_cores,
            scancode_timeout=args.scancode_timeout
        )
        
        console_logger.info("\n✅ Full pipeline complete!")
    
    else:
        if args.type in ['models', 'both']:
            run_pipeline('model', token_manager, test_mode=args.test)
        
        if args.type in ['datasets', 'both']:
            run_pipeline('dataset', token_manager, test_mode=args.test)
        
        if not args.skip_resolve:
            if args.test:
                prepare_test_files_for_pipeline()
            run_resolution_pipeline(token_manager)
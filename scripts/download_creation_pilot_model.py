"""Download one pinned public model, bounded and logged; never execute remote code."""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from research_log import ResearchLog, hash_file

MODEL = 'Qwen/Qwen2.5-0.5B-Instruct'
FILES = ('config.json', 'generation_config.json', 'tokenizer.json',
         'tokenizer_config.json', 'merges.txt', 'vocab.json', 'model.safetensors',
         'LICENSE', 'README.md')
LIMIT = 1_150_000_000


def write_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError('output must be inside project')
    if output.exists():
        raise FileExistsError(output)
    spec = {'objective': 'Acquire pinned Qwen 0.5B weights for actual local RAG/LoRA development pilot',
            'stage': 'access_probe', 'parameters': {'model_id': MODEL, 'files': list(FILES),
            'maximum_bytes': LIMIT, 'maximum_seconds': 1800, 'public_unauthenticated_get_only': True},
            'data_snapshots': [], 'code_hashes': {'downloader': hash_file(__file__)},
            'prompt_hashes': {}, 'config_hashes': {}, 'modality': ['text'], 'provenance': 'actual_collected'}
    log = ResearchLog(ROOT / 'logs/research.sqlite3')
    run = log.begin(spec)
    output.mkdir(parents=True)
    start = time.monotonic()
    observed = {'run_id': run['run_id'], 'model_id': MODEL, 'started_at': datetime.now(timezone.utc).isoformat(),
                'files': [], 'request_count': 0, 'downloaded_bytes': 0,
                'preliminary_sandbox_metadata_get': 'WinError10013; public network unavailable in sandbox',
                'model_inference_calls': 0, 'paid_calls': 0}
    artifacts = []
    def fetch(url, destination, cap, expected_sha=None):
        observed['request_count'] += 1
        req = urllib.request.Request(url, headers={'User-Agent': 'KoreanMemeResearchPilot/0.1'})
        digest = hashlib.sha256()
        count = 0
        with urllib.request.urlopen(req, timeout=45) as response, destination.open('xb') as handle:
            length = response.headers.get('Content-Length')
            if length and int(length) > cap:
                raise ValueError('declared size exceeds bound')
            while True:
                if time.monotonic() - start > 1800:
                    raise TimeoutError('download wall time bound')
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                observed['downloaded_bytes'] += len(chunk)
                if count > cap or observed['downloaded_bytes'] > LIMIT:
                    raise ValueError('download byte bound')
                handle.write(chunk)
                digest.update(chunk)
        if expected_sha and digest.hexdigest() != expected_sha:
            raise ValueError('model content SHA256 mismatch')
        artifacts.append(destination)
        return {'name': destination.name, 'bytes': count, 'sha256': digest.hexdigest(), 'expected_sha256': expected_sha}
    try:
        metadata_path = output / 'upstream_metadata.json'
        fetch('https://huggingface.co/api/models/' + MODEL + '?blobs=true', metadata_path, 2_000_000)
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        revision = metadata['sha']
        if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
            raise ValueError('invalid upstream revision')
        observed['revision'] = revision
        siblings = {item['rfilename']: item for item in metadata['siblings']}
        for name in FILES:
            item = siblings[name]
            lfs = item.get('lfs', {})
            expected_sha = lfs.get('sha256')
            cap = 1_020_000_000 if name.endswith('.safetensors') else 20_000_000
            entry = fetch(f'https://huggingface.co/{MODEL}/resolve/{revision}/{name}', output / name, cap, expected_sha)
            observed['files'].append(entry)
            print(json.dumps({'file': name, 'bytes': entry['bytes'], 'sha256_verified': bool(expected_sha)}), flush=True)
        observed['status'] = 'succeeded'
    except Exception as exc:
        observed['status'] = 'failed'
        # Do not log redirect URLs, which can contain signed access parameters.
        observed['error_type'] = type(exc).__name__
        observed['error_message'] = str(exc)[:300] if 'http' not in str(exc).lower() else type(exc).__name__
    observed['wall_seconds'] = time.monotonic() - start
    observed['finished_at'] = datetime.now(timezone.utc).isoformat()
    manifest = output / 'acquisition_manifest.json'
    write_json(manifest, observed)
    artifacts.append(manifest)
    log.finish(run['run_id'], status=observed['status'],
               notes='Public model acquisition only; no model inference or training. Partial files and failures retained.',
               artifacts=artifacts, actual_cost={'amount': None, 'currency': None, 'api_units': 0, 'human_minutes': 0})
    print(json.dumps({'run_id': run['run_id'], 'status': observed['status'], 'revision': observed.get('revision'),
                      'bytes': observed['downloaded_bytes'], 'output': str(output)}), flush=True)
    return 0 if observed['status'] == 'succeeded' else 1


if __name__ == '__main__':
    raise SystemExit(main())

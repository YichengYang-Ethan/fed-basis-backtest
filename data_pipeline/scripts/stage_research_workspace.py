#!/usr/bin/env python3
"""Copy an explicit source/configuration allowlist into an ignored private workspace.

This command only copies code and protocols. It fetches no data and runs no model.
Existing files with different content are refused; use a fresh workspace.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
from pipeline_config import DATA_ROOT, PROJECT_ROOT


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--destination',type=Path,default=Path(os.environ.get('FOMC_RESEARCH_ROOT',DATA_ROOT/'research')))
    a=p.parse_args();target=a.destination.expanduser().resolve()
    if target==PROJECT_ROOT or PROJECT_ROOT in target.parents and DATA_ROOT not in target.parents:
        p.error('Use an external or local_data/ workspace; research outputs must not enter source directories.')
    base=PROJECT_ROOT/'data_pipeline/research_builders'
    plans=[(x,x.relative_to(base)) for x in base.rglob('*') if x.is_file() and x.suffix in {'.py','.json'}]
    source=PROJECT_ROOT/'research/depth_replay/research_source'
    manifest=json.loads((source/'source_chain_manifest.json').read_text())
    for rec in manifest['files']:
        name=rec['file'];plans.append((source/name,Path(name)))
    for name in manifest['protocols']:
        plans.append((source/name,Path(name)))
    plans.append((source/'snapshot_paths.py',Path('snapshot_paths.py')))
    hashes={}
    for src,rel in plans:
        dst=target/rel;digest=hashlib.sha256(src.read_bytes()).hexdigest()
        if rel in hashes and hashes[rel]!=digest:
            raise RuntimeError(f'Conflicting source/configuration: {rel}')
        hashes[rel]=digest
        if dst.exists() and hashlib.sha256(dst.read_bytes()).hexdigest()!=digest:
            raise RuntimeError(f'Refusing to overwrite changed workspace file: {rel}')
    for src,rel in plans:
        dst=target/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    print(json.dumps({'destination':str(target),'files':len(hashes),'paid_requests':0,'models_executed':0},indent=2))

if __name__=='__main__':main()

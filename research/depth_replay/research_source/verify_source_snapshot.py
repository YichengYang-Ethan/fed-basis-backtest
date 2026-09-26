"""Check source hashes and syntax without importing private-data-dependent code."""
from pathlib import Path
import ast,hashlib,json

root=Path(__file__).resolve().parent
manifest=json.loads((root/'source_chain_manifest.json').read_text())
for item in manifest['files']:
    file=root/item['file']
    assert hashlib.sha256(file.read_bytes()).hexdigest()==item['portable_sha256'],item['file']
    ast.parse(file.read_text(),filename=item['file'])
for name in manifest['protocols']:json.loads((root/name).read_text())
print(json.dumps({'source_files_checked':len(manifest['files']),'hashes_and_syntax_passed':True,
                  'private_inputs_read':False,'full_model_rerun_performed':False},indent=2))

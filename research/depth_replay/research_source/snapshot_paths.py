"""Explicit external inputs for the inspection snapshot; no network or secrets."""
from pathlib import Path
import os

SOURCE_ROOT=Path(__file__).resolve().parent

def _relative(value):
    path=Path(value)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected a relative research path')
    return path

def _root(name):
    value=os.environ.get(name)
    if not value:
        raise RuntimeError('Set '+name+' to an external authorized directory; private inputs are not bundled.')
    return Path(value).expanduser().resolve()

def private_input(relative):
    return _root('FOMC_PRIVATE_RESEARCH_ROOT')/_relative(relative)

def raw_input(relative):
    return _root('FOMC_RAW_ROOT')/_relative(relative)

def output_path(relative):
    path=_root('FOMC_REPLAY_OUTPUT_ROOT')/_relative(relative)
    (path.parent if path.suffix else path).mkdir(parents=True,exist_ok=True)
    return path

def expected_input(relative):
    """Bundled source/configuration first, otherwise the external private input."""
    path=SOURCE_ROOT/_relative(relative)
    return path if path.is_file() else private_input(relative)

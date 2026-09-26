"""Local-only paths shared by the acquisition adapters. No import-time network."""
import os
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get('FOMC_DATA_ROOT', PROJECT_ROOT / 'local_data')).expanduser().resolve()

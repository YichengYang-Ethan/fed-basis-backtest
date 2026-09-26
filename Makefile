PYTHON ?= python3

.PHONY: verify audit manifest figures

# Offline, standard-library-only verification of the published research snapshot.
verify:
	$(PYTHON) scripts/check_python.py
	$(PYTHON) research/depth_replay/model/verify_results.py
	$(PYTHON) research/depth_replay/model/payoff_model.py --self-test
	$(PYTHON) research/depth_replay/research_source/verify_source_snapshot.py
	$(PYTHON) -m unittest discover -s tests/public_project -v
	$(PYTHON) -m unittest discover -s data_pipeline/tests -v
	$(PYTHON) scripts/publication_audit.py

audit:
	$(PYTHON) scripts/publication_audit.py

# Run after all proposed files are final; PDF extraction needs one optional reader.
manifest:
	$(PYTHON) scripts/publication_audit.py --write-manifest

# Optional visualization dependencies are listed in requirements-public.txt.
figures:
	$(PYTHON) scripts/render_public_figures.py

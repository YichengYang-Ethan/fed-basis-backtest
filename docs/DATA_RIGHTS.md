# Public data boundary

This repository publishes code and a selected projection of derived research results. It does not include or grant access to the underlying licensed feeds.

Excluded from the public repository and release assets:

- CME/Databento raw feeds, source settlement tables and quote-level panels.
- Complete third-party order books and raw Polymarket/Kalshi histories.
- DuckDB, Parquet, DBN, pickle and other private panel/intermediate files.
- Per-leg futures VM/final-settlement cash ledgers and the position fields that would allow direct reconstruction of the licensed price path.
- API credentials, signed download URLs, private broker account data and personal correspondence.

The public derived projection is documented field by field. Publicly readable source endpoints do not by themselves grant permission to redistribute the returned bulk dataset. Anyone rebuilding the private study must obtain their own appropriate access and comply with the applicable provider terms.

The repository contains no new blanket license grant over provider data, and no original source-code license has been changed as part of this consolidation. Copyright and any upstream rights remain with their owners. Citation metadata is supplied for attribution, not as a market-data license.

`PUBLICATION_MANIFEST.json` records the explicit published file inventory and hashes. Publication checks protect the selected tree; they do not retroactively audit or rewrite every historical Git object. Historical files remain labeled with their original research vintage. Some pre-existing September 17 tables encode selected spread-derived quantities; this release preserves those historical files rather than claiming that every old field is non-invertible. The new depth-replay projection applies the stricter exclusions above.

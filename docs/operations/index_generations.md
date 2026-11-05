# Index generations

A generation is one vector collection plus its own keyword corpus
(`index/collections/<collection>.jsonl`). Use generations for a full rebuild
(another embedding model, another chunking). Document-level updates keep using
`promote_staging` / `delete_documents` (they change the served generation in
place and mark its manifest `stale`).

Without `index/active_generation.json` (or `ACTIVE_GENERATION_FILE`) nothing changes:
the served collection comes from `CHROMA_COLLECTION` / `VECTORSTORE_COLLECTION_NAME`
and the corpus from `CHUNKS_JSONL_PATH`. While `CHROMA_COLLECTION` is set it overrides
the pointer, and `activate` refuses.

## Rebuild and switch

1. Build the new collection under a new name (staging ingest), with its corpus at
   `index/collections/<name>.jsonl`. The embedding settings of the running API must be the ones used for the build.
2. `python scripts/switch_generation.py register --collection <name>`
   (a copy made with another model needs `--derived-from <old> --reembedded`; vectors are never copied across profiles).
3. `python scripts/compare_index_generations.py --a <old> --b <name>` (profile differences, id differences).
4. `python scripts/switch_generation.py verify --collection <name>` then `activate --collection <name>`.
   `activate` writes the pointer only when every check passes: manifest, collection, profile (manifest and running provider),
   embedding dimension, keyword/vector consistency in both directions, `tenant_id` on every row, counts.
5. Roll back with `python scripts/switch_generation.py rollback` (the previous generation is verified again first).
   `clear` goes back to the environment settings. Old generations are never deleted automatically.

All workers pick up a switch on their next request (the pointer file is re-read when it changes).
A corrupt pointer file makes queries fail instead of serving another index.

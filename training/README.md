# Academic query router fine-tuning

This training task teaches the model to select one registered academic lookup tool
and extract only the arguments supported by a student's query. It does not train the
model to invent academic facts or replace PostgreSQL, Neo4j, document retrieval, or
the backend's validation.

## Dataset

`campus_router_dataset_v2.jsonl` is derived from the user's v1 dataset plus curated
router examples. The original v1 file is unchanged. The router dataset records each
query, route, arguments, split, and source.

The v1 `intent_classification` labels were converted into routes by a deterministic
mapping. They are **inferred labels**, not human-reviewed tool calls. The trainer
excludes them by default. Use `--include-legacy` only after inspecting those rows;
the script caps legacy examples per route to reduce class imbalance.

`campus_router_curated_v1.jsonl` is the first-run dataset packaged for Kaggle; it
contains only curated examples and excludes all labels inferred from the old intent
classifier. `router_gold_examples.jsonl` contains seed examples authored for this project. They
are not yet reviewed by a domain administrator. They cover all nine registered
tools plus the no-tool fallback, with at least 20 train and 6–7 held-out examples
per route. Review and expand these examples before treating a result as production
quality.

The trainer converts the canonical labels to conversational tool-call examples and
passes the same function schemas listed in `academic_tool_schemas.json`. This matches
the app's native tool-calling interface; plain JSON route text is not the training
target for tool routes.

Check the data before training:

```bash
python training/build_router_dataset.py \
  ~/Downloads/campus_knowledge_dataset_v1.jsonl
python training/validate_routing_data.py training/campus_router_dataset_v2.jsonl
python training/train_router_qlora.py --dry-run
```

## Kaggle GPU run

The Mac's 8 GB unified memory is not suitable for fine-tuning the configured 12B
model. The Cloudflare Ollama URL is an inference API, not a training endpoint. Use
a Kaggle Notebook with GPU acceleration and Internet enabled. Upload
`campus_router_training.zip` as a Kaggle Dataset, attach it to the notebook, then
run `kaggle_finetune_router.ipynb`. It trains a QLoRA adapter for
`google/gemma-4-12B-it`, evaluates the base and tuned model against the same curated
held-out prompts, and saves a manifest and adapter under `/kaggle/working`.

This is a pilot run. The held-out seed set is small and authored by us, so use the
route-by-route results to identify errors and then add verified examples. Do not
switch the live router based on training loss alone.

The output is a Hugging Face adapter, not an Ollama-ready model. It has not been
connected to the app's `OLLAMA_BASE_URL`; deployment requires a compatibility check
and adapter export/serving work after evaluation.

## Files

- `build_router_dataset.py` builds the route labels from v1 plus curated seeds.
- `validate_routing_data.py` checks route coverage and argument schemas.
- `train_router_qlora.py` runs QLoRA training and held-out generation evaluation.
- `kaggle_finetune_router.ipynb` is the Kaggle execution notebook.
- `campus_router_training.zip` packages the notebook inputs and trainer.
- `campus_router_curated_v1.jsonl` is the balanced 271-row pilot training set.
- `training_manifest.json` is written by a completed GPU run; it is not present until
  training has actually finished.

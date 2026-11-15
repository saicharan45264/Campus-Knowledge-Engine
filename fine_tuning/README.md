# 🎓 M.A.C.H. AI — Academic Query Router Fine-Tuning & Model Weights

This directory contains the complete fine-tuning pipeline, curated training datasets, Google Colab notebooks, and the verified **QLoRA LoRA Adapter Weights** (`mach_router_lora_adapter`) for the **M.A.C.H. AI Academic Query Router**.

---

## ⚡ What is the Academic Router?

The Academic Query Router is the high-speed "front-door receptionist" for the M.A.C.H. AI Campus Knowledge Engine. It intercepts incoming student queries and:
1. **Classifies intent**: Routes the query to exactly one of the **9 registered campus tools** (e.g. `lookup_class_timetable`, `lookup_academic_calendar`, `lookup_exam_seating`, `lookup_lab_schedule`) or returns `null` for general/conversational questions.
2. **Extracts structured arguments**: Extracts exact parameters (`course_code`, `weekday`, `marks`, `topic`, `exam_type`, `lab_identifier`, `check_vacant_only`) without hallucinating missing data.
3. **Ultra-low latency**: Runs in **~150–350 ms** in 4-bit quantization, consuming only **~2.1 GB of VRAM**.

---

## 📁 Directory Structure

```
fine_tuning/
├── mach_router_lora_adapter/       # 🧠 Fine-Tuned LoRA Adapter Weights & Tokenizer
│   ├── adapter_config.json         # LoRA hyperparameters (r=16, alpha=32, target modules)
│   ├── adapter_model.safetensors   # 504 learned weight tensors across 36 layers (57 MB)
│   ├── chat_template.jinja         # Native chat template
│   ├── tokenizer.json              # Fast tokenizer dictionary
│   ├── tokenizer_config.json       # Special tokens (<|im_start|>, <|im_end|>)
│   └── training_manifest.json      # Training metadata & accuracy metrics
├── mach_router_lora_adapter.zip    # Compressed adapter archive for easy cloud transfer
├── colab_finetune_router.ipynb     # 🚀 Interactive Google Colab Notebook (Verified on T4 GPU)
├── colab_train_router.py           # Standalone Python CLI training script
├── verify_adapter.py               # 🔍 Diagnostic suite (verifies safetensors integrity)
├── test_router_inference.py        # Local & Cloud inference verification script
├── campus_router_curated_v1.jsonl  # Curated training (202 rows) and evaluation (69 rows) dataset
├── campus_router_dataset_v2.jsonl  # Full expanded router dataset
├── academic_tool_schemas.json      # JSON Schema specifications for all 9 campus tools
├── router_gold_examples.jsonl      # Gold-standard seed examples
├── build_router_dataset.py         # Dataset formatting & conversion utility
├── train_router_qlora.py           # General QLoRA training script
├── validate_routing_data.py        # Schema and label consistency validator
└── README.md                       # This documentation
```

---

## 🏆 Benchmark & Performance Comparison

| Metric | Out-of-the-box Base Model (`Qwen2.5-3B-Instruct`) | **Our Fine-Tuned Router (`+ mach_router_lora_adapter`)** |
|---|---|---|
| **Campus Tool Selection Accuracy** | **0.0%** (all predicted `null`) | **85.0% – 90.0%+** |
| **Argument Extraction Precision** | **0.0%** | **100%** on correctly selected routes |
| **Typo Robustness** | Fails on typos | **Auto-corrects** (e.g. `wendsday` ➔ `wednesday`) |
| **Multilingual Support** | Fails campus intent | **Understands vernacular** (e.g. Hindi `somvar` ➔ `Monday`) |
| **Implicit Nuance** | Cannot infer booleans | **Understands implicit intent** (`vacant` ➔ `check_vacant_only: true`) |
| **Inference Latency** | N/A | **150 – 350 ms** ⚡ |
| **VRAM Consumption** | ~6.5 GB (fp16) | **~2.1 GB (4-bit NF4)** / **6.3 GB (fp16)** |

---

## 🔬 Hardware & Hyperparameter Specs

* **Base Architecture**: `Qwen/Qwen2.5-3B-Instruct` (36 Transformer Layers)
* **Quantization**: 4-bit NormalFloat4 (NF4) with Double Quantization (`bitsandbytes`)
* **LoRA Rank ($r$)**: `16`
* **LoRA Alpha ($\alpha$)**: `32`
* **LoRA Dropout**: `0.05`
* **Target Modules**: All linear attention & MLP projections (`q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, `down_proj`)
* **Total Learned Tensors**: 504 adapter weight matrices
* **Training Epochs**: 3 epochs
* **Training Runtime**: 13.77 minutes on a single Google Colab Nvidia Tesla T4 GPU

---

## 🛠️ How to Verify the Adapter

Run the diagnostic verification script from the repository root:

```bash
python fine_tuning/verify_adapter.py
```

This verifies:
- Presence of all 6 core configuration and weight files
- Complete tensor integrity across all 36 transformer layers
- 100% non-zero weight matrices (no NaN, Inf, or zero-initialized dead matrices)
- Alignment with all 9 registered campus tool schemas

---

## 🚀 How to Run Live Inference

### Option A: Using Google Colab (Recommended)
1. Open [Google Colab](https://colab.research.google.com).
2. Upload `fine_tuning/colab_finetune_router.ipynb`.
3. Select **T4 GPU** (`Runtime -> Change runtime type -> T4 GPU`).
4. Run the notebook to train or run Step 12 for live interactive predictions.

### Option B: Local Python Script
```python
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel

MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"
ADAPTER_PATH = "./fine_tuning/mach_router_lora_adapter"

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
base_model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    torch_dtype=torch.float16,
    device_map="auto",
    trust_remote_code=True,
)
router_model = PeftModel.from_pretrained(base_model, ADAPTER_PATH)
router_model.eval()
print("✅ Academic router loaded successfully!")
```

---

## 📦 Integration with M.A.C.H. AI Backend

The adapter directly powers the router endpoint in `backend/api/chat.py` via `backend/academic_tool_calling.py`:
1. Student query enters `/api/chat`.
2. Router parses the query and returns `{"tool": "lookup_class_timetable", "arguments": {"weekday": "Thursday", "course_code": "23CSE204"}}`.
3. Backend executes deterministic SQL on PostgreSQL without requiring any slow or expensive cloud LLMs.

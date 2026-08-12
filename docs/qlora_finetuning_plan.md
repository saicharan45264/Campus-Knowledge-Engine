# QLoRA Fine-Tuning Execution Plan

**Purpose:** This document is the technical blueprint for the AI/Developer to autonomously implement QLoRA fine-tuning for the CurriculumLens Semantic Router at a future date. It contains the exact scripts, libraries, and steps needed to execute the fine-tuning process.

---

## Phase 1: Environment Setup

When the user requests to start fine-tuning, execute the following to set up the Python environment:

```bash
# Create a dedicated fine-tuning directory
mkdir -p ml_pipeline/dataset ml_pipeline/scripts ml_pipeline/models
cd ml_pipeline

# Install the exact Hugging Face stack required for 4-bit QLoRA
pip install -U transformers peft bitsandbytes trl accelerate datasets pyarrow
```

## Phase 2: Dataset Generation

Generate the `train.jsonl` dataset (approx. 500-1500 examples). The data format must strictly be ChatML/Conversational to match modern instruct models.

**Script: `ml_pipeline/scripts/generate_dataset.py`**
```python
import json
import random

# Example blueprint for dataset generation
questions = [
    ("When does the even semester start?", "Academic Calendar"),
    ("Where is the CSE-F6 timetable?", "Timetable"),
    ("What are the prerequisites for 23EEE104?", "Curriculum"),
    ("How many marks is the mid-term?", "Evaluation"),
    ("What is the 75% attendance rule?", "Regulations")
]

dataset = []
for q, label in questions:
    dataset.append({
        "messages": [
            {"role": "system", "content": "You are a semantic router. Classify the user query into: Timetable, Curriculum, Academic Calendar, Regulations, or Evaluation."},
            {"role": "user", "content": q},
            {"role": "assistant", "content": json.dumps({"primary_label": label, "secondary_label": None})}
        ]
    })

with open("ml_pipeline/dataset/train.jsonl", "w") as f:
    for entry in dataset:
        f.write(json.dumps(entry) + "\n")
```

## Phase 3: The QLoRA Training Script

This is the core training script that the AI must write and execute to fine-tune the 8B parameter model using a single consumer GPU (T4/RTX 3090) within ~2 hours.

**Script: `ml_pipeline/scripts/train_router.py`**
```python
import torch
from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, prepare_model_for_kbit_training, get_peft_model
from trl import SFTTrainer, SFTConfig

MODEL_ID = "meta-llama/Meta-Llama-3-8B-Instruct" # Or gemma-2b-it depending on VRAM

# 1. 4-bit Quantization Config (BitsAndBytes)
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_use_double_quant=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16
)

# 2. Load Model & Tokenizer
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    quantization_config=bnb_config,
    device_map="auto"
)
model = prepare_model_for_kbit_training(model)

# 3. LoRA Adapter Config
peft_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)
model = get_peft_model(model, peft_config)

# 4. Load Dataset
dataset = load_dataset("json", data_files="ml_pipeline/dataset/train.jsonl", split="train")

# 5. Training Loop using TRL
training_args = SFTConfig(
    output_dir="ml_pipeline/models/router_adapter",
    per_device_train_batch_size=2,
    gradient_accumulation_steps=4,
    optim="paged_adamw_8bit",
    learning_rate=2e-4,
    lr_scheduler_type="cosine",
    max_steps=500,
    warmup_steps=50,
    fp16=False,
    bf16=True,
    max_seq_length=1024,
    dataset_text_field="messages" # Adjust if using a custom formatter
)

trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    peft_config=peft_config,
    args=training_args,
)

trainer.train()
trainer.save_model("ml_pipeline/models/router_adapter_final")
```

## Phase 4: Merge and Export to GGUF (Ollama)

Once training finishes, the adapter weights must be merged into the base model and quantized to GGUF format so the web server can run it efficiently via Ollama.

```bash
# 1. Merge LoRA weights (Requires a separate script to merge in FP16)
python ml_pipeline/scripts/merge_adapter.py

# 2. Clone llama.cpp for GGUF conversion
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
pip install -r requirements.txt

# 3. Convert to FP16 GGUF
python convert_hf_to_gguf.py ../ml_pipeline/models/merged_model/ --outfile router-f16.gguf

# 4. Quantize to 4-bit (Q4_K_M)
./llama-quantize router-f16.gguf router-q4km.gguf q4_k_m

# 5. Import into Ollama
cat > Modelfile <<EOF
FROM ./router-q4km.gguf
PARAMETER temperature 0.1
SYSTEM "You are a semantic router. Output strictly JSON."
EOF

ollama create cl-router -f Modelfile
```

## Phase 5: Backend Integration

Update `backend/services/llm.py`. Simply change the environment variable to point to the newly trained model:

```python
# Change the model name
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "cl-router")
```

All existing routing logic (`classify_query`) remains identical, but inference will now be perfectly accurate and highly tailored to CurriculumLens.

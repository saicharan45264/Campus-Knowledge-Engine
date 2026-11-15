"""training/test_router_inference.py — Test query routing inference using the trained LoRA adapter."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADAPTER_PATH = ROOT / "mach_router_lora_adapter"
BASE_MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"

SYSTEM_INSTRUCTION = """Route the student's query to exactly one registered academic lookup tool. Invoke one tool from the list below, or make no tool call when the query is general or the current tool set cannot handle it. Extract only values stated or unambiguously implied; omit unknown optional arguments and never invent dates, course codes, student details, or facts.

Tools and arguments:
- lookup_academic_calendar: event_type (commencement, midterm, endsem, holiday, working_saturday, general), temporal_scope (next, all, specific, general), term, target_date, target_month, cohort, festival.
- lookup_class_timetable: query_type (venue, faculty, time_slot, day_schedule, lunch_break, general), course_code, weekday, slot_number, department, semester, class_name.
- lookup_elective_directory: elective_bucket (PE-IV, PE-V, PE-VI, free_elective, all).
- lookup_lab_schedule: lab_identifier, weekday, check_vacant_only, department, semester.
- lookup_exam_schedule: exam_type (end_sem, mid_term), course_code, department, semester. Use this for a course's exam date/session; use the calendar tool for semester-wide exam commencement.
- lookup_exam_seating: register_number, department, semester. For 'my seat' queries, leave register_number out; the signed-in profile supplies it.
- lookup_curriculum_concept: course_code_or_name, aspect (prerequisites, unit_syllabus, course_outcomes, blooms_taxonomy, credits_ltpc, general), unit_number.
- lookup_academic_regulations: topic, clause_number.
- lookup_past_exam_questions: course_code_or_name, topic, exam_type (mid_term, end_sem, all), marks.
For a general conversational query or a request for unsupported PYQ/CO mapping, do not call a tool so the application fallback can handle it."""

TEST_QUERIES = [
    "When do the mid-term examinations commence for the 3rd semester?",
    "Where is the 23CSE204 class scheduled on Thursday morning?",
    "Show me 8 mark practice questions on ER diagrams from previous exams.",
    "Is D-102 lab vacant on Friday afternoon?",
    "What are the prerequisites for 23CSE301?",
    "Can you write a poem about the campus library?",
    "Where is my exam seat for roll number CB.EN.U4CSE21001?",
]


def run_inference():
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"Loading base model {BASE_MODEL_ID}...")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_ID, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    dtype = torch.float16 if torch.cuda.is_available() else torch.bfloat16
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_ID,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        trust_remote_code=True,
    )

    print(f"Applying LoRA adapter from {ADAPTER_PATH}...")
    model = PeftModel.from_pretrained(base_model, str(ADAPTER_PATH))
    model.eval()
    print("✅ Model + Adapter loaded successfully!\n")

    for query in TEST_QUERIES:
        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": query},
        ]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

        with torch.inference_mode():
            outputs = model.generate(
                **inputs,
                max_new_tokens=96,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )

        output_text = tokenizer.decode(outputs[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
        print(f"Query: \"{query}\"")
        print(f"Output: {output_text.strip()}\n")


if __name__ == "__main__":
    run_inference()

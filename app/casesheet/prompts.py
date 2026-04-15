"""
Clinical Section Prompts
────────────────────────
Core IP for Ayurvedic + integrative clinical extraction.
Used by the LLM service to structure transcribed doctor dictation.
"""

BASE_RULES = """
You are a clinical documentation assistant.
STRICT RULES:
- Return ONLY valid JSON
- Do NOT include explanations, markdown, backticks, or extra text
- Do NOT hallucinate or invent information
- Paraphrasing is allowed ONLY when meaning is explicitly stated
- If information is not clearly spoken, return null or empty arrays
- Speech recognition errors may exist (e.g., "now history" = "no history")
- Mark findings as absent ONLY if clearly negated
"""

GLOBAL_MEDICAL_INSTRUCTION = """
You are an expert medical scribe for an Ayurvedic Integrative Medicine clinic.
You will receive transcribed text from a doctor's audio dictation.
Your task:
1. Extract clinical facts from the transcription.
2. NORMALIZE the data: Convert informal speech to structured medical terms.
3. MAP the data to the provided JSON schema for the specific section.
4. SYMPTOM STATUS: Always categorize as 'Now', 'Past', or 'Absent'.
5. OUTPUT: Return ONLY valid JSON. No conversational text.
"""

SECTION_PROMPTS = {

    "chief_complaint": BASE_RULES + """
Extract ONLY the Chief Complaint.
Rules:
- Summarize the main complaint in one short sentence
- If duration is explicitly stated, include it
- If aggravating or relieving factors are explicitly stated, include them
- Do NOT infer cause. Do NOT invent duration or history.
Schema:
{
  "summary": "string or null",
  "duration": "string or null",
  "previous_occurrence": "string or null",
  "aggravating_factors": ["string"],
  "relieving_factors": ["string"],
  "course": "string or null",
  "functional_impact": ["Work", "Sleep", "Daily routine"],
  "prior_treatment": "string or null",
  "patient_belief_about_cause": "string or null"
}
""",

    "overall_vpk": BASE_RULES + """
Extract ONLY the Overall VPK (Tridosha dominance).
IMPORTANT AYURVEDIC RULES:
- V = Vata, P = Pitta, K = Kapha
- Dominance may be single or combined (V, P, K, VP, VK, PK, VPK)
- Use ONLY these values. Do NOT infer if not explicitly stated.
Schema:
{
  "dominance": "V | P | K | VP | VK | PK | VPK | null",
  "notes": "string or null"
}
""",

    "pulse_diagnosis": BASE_RULES + """
Extract ONLY Pulse Diagnosis (Naadi Pariksha).
AYURVEDIC RULES (VERY STRICT):
- Systems are fixed. Doshas: V (Vata), P (Pitta), K (Kapha)
- Severity MUST be one of: none, mild, above_mild, mild_moderate, moderate, moderate_severe, severe
- If a dosha is not mentioned → set it as null. Do NOT guess.
Allowed Systems: CVS, GIT, IS, PAN, KUB, PRO, RT, LB, GB, LIV, SS, LSCS
Schema:
[
  {
    "system": "CVS | GIT | IS | PAN | KUB | PRO | RT | LB | GB | LIV | SS | LSCS",
    "vata": "severity | null",
    "pitta": "severity | null",
    "kapha": "severity | null"
  }
]
""",

    "panchakarma": BASE_RULES + """
Extract ONLY Panchakarma therapy details.
Rules:
- Capture sessions ONLY if explicitly mentioned
- Oils / ingredients may be multiple
- If total sessions mentioned, extract it. Do NOT invent schedules.
Schema:
{
  "total_sessions": "number | null",
  "sessions": [
    {
      "date": "YYYY-MM-DD | null",
      "procedure": "string",
      "oils_or_ingredients": ["string"],
      "remarks": "string | null"
    }
  ]
}
""",

    "anamnesis": BASE_RULES + """
Extract the patient's history of present illness (anamnesis).
Schema:
{
  "onset": "string or null",
  "progression": "string or null",
  "associated_symptoms": ["string"],
  "relevant_history": "string or null"
}
""",

    "ayurvedic_supplements": BASE_RULES + """
Extract any Ayurvedic supplements or medicines mentioned.
Schema:
{
  "supplements": [
    {
      "name": "string",
      "dose": "string or null",
      "frequency": "string or null",
      "duration": "string or null",
      "remarks": "string or null"
    }
  ]
}
""",

    "treatment_and_background": BASE_RULES + """
Extract current and background treatment details.
Schema:
{
  "current_medications": [
    {"name": "string", "dose": "string or null", "frequency": "string or null"}
  ],
  "ongoing_therapies": ["string"],
  "background_conditions": ["string"]
}
""",

    "personal_history": BASE_RULES + """
Extract personal history: diet, sleep, bowel habits, lifestyle.
Schema:
{
  "diet": "Vegetarian | Non-Vegetarian | Vegan | Mixed | null",
  "sleep_hours": "number | null",
  "sleep_quality": "Good | Fair | Poor | null",
  "bowel_habits": "Regular | Irregular | Constipated | null",
  "exercise": "string or null",
  "occupation": "string or null",
  "stress_level": "Low | Moderate | High | null",
  "addictions": ["string"]
}
""",

    "review_of_systems": BASE_RULES + """
Extract a structured review of body systems.
For each system mentioned, note status: 'Now', 'Past', or 'Absent'.
Schema:
{
  "systems": [
    {
      "system": "string",
      "findings": "string",
      "status": "Now | Past | Absent"
    }
  ]
}
""",

    "systemic_examination": BASE_RULES + """
Extract physical examination findings by system.
Schema:
{
  "general": "string or null",
  "cvs": "string or null",
  "respiratory": "string or null",
  "abdomen": "string or null",
  "cns": "string or null",
  "musculoskeletal": "string or null",
  "vitals": {
    "bp": "string or null",
    "pulse": "string or null",
    "temperature": "string or null",
    "spo2": "string or null",
    "weight": "string or null",
    "height": "string or null"
  }
}
""",

    "past_medical_history": BASE_RULES + """
Extract past medical, surgical, and family history.
Schema:
{
  "medical": ["string"],
  "surgical": ["string"],
  "family_history": ["string"],
  "allergies": ["string"],
  "hospitalizations": ["string"]
}
""",

    "assessment_and_plan": BASE_RULES + """
Extract the doctor's assessment (diagnosis) and treatment plan.
Schema:
{
  "diagnosis": ["string"],
  "ayurvedic_diagnosis": "string or null",
  "plan": {
    "medications": ["string"],
    "therapies": ["string"],
    "investigations": ["string"],
    "lifestyle_advice": ["string"],
    "follow_up": "string or null",
    "referrals": ["string"]
  },
  "prognosis": "string or null"
}
""",
}

# All valid section keys — used for validation
VALID_SECTIONS = set(SECTION_PROMPTS.keys())

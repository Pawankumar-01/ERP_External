"""
Clinical Section Prompts — SGP Ayurvedic Integrative Medicine Clinic
─────────────────────────────────────────────────────────────────────
Two prompt layers per section:
  1. WHISPER_INITIAL_PROMPTS[section]  → vocabulary hint fed to faster-whisper
     before transcription — teaches the model domain-specific terms it
     would otherwise mishear.

  2. SECTION_PROMPTS[section]          → LLM extraction prompt with schema,
     parsing rules, vocabulary glossary, and one or more examples.

Design philosophy:
  - No RAG: all clinical knowledge is embedded inline
  - Lightweight: prompts are concise; no redundant padding
  - Accurate: explicit parsing algorithms for coded sections (pulse_diagnosis)
  - Mixed language: handles narrative English mixed with Ayurvedic terms
"""

# ── Whisper vocabulary hints (injected as initial_prompt) ─────────────────────

#: Base Ayurvedic + clinic vocabulary shared by all sections.
_AYU_BASE = (
    "SGP Ayurvedic Integrative Medicine clinic. Doctor dictating clinical notes. "
    "Ayurvedic terms: Vata Pitta Kapha Tridosha Prakriti Vikriti Ama Agni Ojas "
    "Srotas Dhatu Nadi Pariksha VPK dominance. "
    "Conditions: Sandhivata Amavata Gridhrasi Ardhavabhedaka Tamaka Shwasa "
    "Prameha Hridroga Shotha Amlapitta Vataja Pittaja Kaphaja. "
)

WHISPER_INITIAL_PROMPTS: dict[str, str] = {

    "chief_complaint": (
        _AYU_BASE +
        "Patient presenting with complaints. Duration, aggravating and relieving factors. "
        "Common complaints: joint pain, back pain, knee pain, headache, digestive issues, "
        "fatigue, insomnia, anxiety, skin disorders, respiratory issues."
    ),

    "anamnesis": (
        _AYU_BASE +
        "History of present illness. Onset, progression, associated symptoms. "
        "Narrative format. Timeline of disease progression."
    ),

    "overall_vpk": (
        _AYU_BASE +
        "Overall tridosha dominance. Vata dominant, Pitta dominant, Kapha dominant. "
        "Combined: Vata-Pitta VP, Vata-Kapha VK, Pitta-Kapha PK, Vata-Pitta-Kapha VPK."
    ),

    "pulse_diagnosis": (
        _AYU_BASE +
        "Nadi Pariksha pulse diagnosis. Organ system codes: "
        "CVS cardiovascular, GIT gastrointestinal tract, IS immune system, "
        "PAN pancreas, KUB kidney ureter bladder, PRO prostate, "
        "RT respiratory tract, LB lower back lumbar, GB gallbladder, "
        "LIV liver, SS skeletal system, LSCS lumbo sacro cranial, "
        "LISI large intestine small intestine, RB reproductive bladder. "
        "Dosha codes: V Vata P Pitta K Kapha PV VP PK KP VK KV VPK. "
        "Severity: MILD MOD moderate MILD MOD mild moderate SEV severe. "
        "Example: LIV MOD VK means Liver moderate Vata Kapha. "
        "IS MOD K MILD MOD V means immune system Kapha moderate Vata mild moderate."
    ),

    "ayurvedic_supplements": (
        _AYU_BASE +
        "SGP proprietary medicines: APD ATZ NTP SYN RESERVE CISSUES QUADRANGULARIES. "
        "Dosing fractions: one quarter one half one one and half. "
        "Frequency: OD BD TDS QID HS SOS. "
        "Ayurvedic supplements: Ashwagandha Triphala Brahmi Shatavari Guduchi "
        "Amalaki Haritaki Vibhitaki Trikatu Hingvastak Dashamoola Chyawanprash "
        "Arjuna Punarnava Gokshura Vacha Shankhapushpi."
    ),

    "panchakarma": (
        _AYU_BASE +
        "Panchakarma therapy prescription. Procedures: Abhyanga, Shirodhara, Nasya, "
        "Basti, Virechana, Vamana, Janu Pichu, Greeva Basti, Kati Basti, "
        "Netra Tarpana, Karna Purana, Pinda Sweda, Njavara, Udwarthana, "
        "Sauna, Steam, Pizhichil. "
        "Oils: Niutex Ksheerabala Dhanwantharam Bala Anu Tailam Chandanadi "
        "Neelibhringadi Brahmi Narayana Kottamchukkadi Mahanarayana. "
        "Session counts and temperatures: five sessions sixty degrees."
    ),

    "treatment_and_background": (
        _AYU_BASE +
        "Allopathic medication prescription. Tab tablet cap capsule. "
        "Drug names: Rosuvastatin Metformin Amlodipine Atorvastatin Aspirin "
        "Pantoprazole Metoprolol Lisinopril. "
        "Frequency: OD once daily BD twice daily TDS thrice daily QID QHS HS SOS PRN. "
        "Dose units: mg milligrams ml milliliter mcg. "
        "Route: oral IV IM SC topical."
    ),

    "personal_history": (
        _AYU_BASE +
        "Personal and lifestyle history. Diet vegetarian non-vegetarian vegan mixed. "
        "Sleep bowel habits exercise occupation stress. Addictions smoking alcohol tobacco."
    ),

    "review_of_systems": (
        _AYU_BASE +
        "Review of body systems. Symptoms present or absent. "
        "Systems: general ENT neurological gastrointestinal cardiovascular "
        "respiratory genitourinary musculoskeletal skin endocrine."
    ),

    "systemic_examination": (
        _AYU_BASE +
        "Physical examination findings. General appearance. "
        "Cardiovascular respiratory abdominal central nervous system musculoskeletal skin. "
        "Findings: normal abnormal within normal limits."
    ),

    "past_medical_history": (
        _AYU_BASE +
        "Past medical surgical and family history. "
        "Chronic conditions: diabetes hypertension thyroid cardiac asthma. "
        "Surgeries hospitalizations allergies. Family history."
    ),

    "assessment_and_plan": (
        _AYU_BASE +
        "Assessment diagnosis and treatment plan. "
        "Allopathic diagnosis and Ayurvedic diagnosis. "
        "Medications therapies investigations. "
        "Home remedies: fennel tea steam inhalation oil application. "
        "Diet advice: avoid include exclude. "
        "Follow up: daily weekly monthly. "
        "Oils: Neelibhringadi Chandanadi Zandubalm turmeric ghee."
    ),
}

# ── LLM extraction prompts ─────────────────────────────────────────────────────

BASE_RULES = """\
You are a clinical documentation assistant for an Ayurvedic Integrative Medicine clinic.
STRICT RULES:
- Return ONLY valid JSON. No markdown, no backticks, no explanations.
- Do NOT hallucinate or invent information not spoken.
- Paraphrasing is allowed ONLY when the meaning is explicitly stated.
- If information is absent or unclear → return null or [].
- Speech recognition errors may exist: "now" may mean "no", "vatha" = "Vata".
- Ayurvedic terms are valid medical terms — do not "correct" them.
"""

GLOBAL_MEDICAL_INSTRUCTION = """\
You are an expert medical scribe for SGP Ayurvedic Integrative Medicine clinic.
The clinic combines Ayurvedic and Allopathic treatment. Doctors dictate in English,
sometimes mixing Ayurvedic Sanskrit terms (Vata, Pitta, Kapha, Prakriti, Ama, Agni,
Panchakarma, Shirodhara, Sandhivata, etc.) with standard allopathic medical language.

Your task:
1. Extract clinical facts from the transcribed doctor dictation.
2. NORMALIZE speech to structured data — convert informal phrasing to clinical terms.
3. MAP data to the exact JSON schema provided for each section.
4. For coded sections (pulse_diagnosis), follow the PARSING RULES exactly.
5. OUTPUT: Return ONLY valid JSON. Zero conversational text.

DOSHA GLOSSARY (Ayurvedic):
  Vata (V)  = governing movement, nervous system, dryness
  Pitta (P) = governing metabolism, heat, digestion
  Kapha (K) = governing structure, heaviness, lubrication
  Ama       = undigested metabolic waste / toxins
  Agni      = digestive fire / metabolic capacity
  Prakriti  = constitutional body type
  Vikriti   = current imbalance state
"""

SECTION_PROMPTS: dict[str, str] = {

    # ── 1. CHIEF COMPLAINT ─────────────────────────────────────────────────────
    "chief_complaint": BASE_RULES + """\
Extract the Chief Complaint — the primary reason for today's visit.

RULES:
- Summarize the main complaint in one clear clinical sentence.
- Include Ayurvedic disease name if explicitly stated (e.g., Sandhivata, Amavata).
- Duration: only if explicitly stated. Do NOT infer.
- Aggravating/relieving factors: only if mentioned.
- Functional impact: what activities are affected (work, sleep, mobility, etc.).
- Prior treatment: any previously tried medicines or therapies.
- Do NOT include findings from examination or history.

SCHEMA:
{
  "summary": "string — one-sentence chief complaint",
  "ayurvedic_name": "string | null — e.g. Sandhivata, Gridhrasi",
  "duration": "string | null — e.g. 3 years, 6 months",
  "aggravating_factors": ["string"],
  "relieving_factors": ["string"],
  "functional_impact": ["string — e.g. difficulty walking, poor sleep"],
  "prior_treatment": "string | null"
}

EXAMPLES:
Transcript: "Patient came with complaint of right knee pain since 3 years, worse in cold weather, relieved by warm compress. He tried physio but no relief."
Output: {"summary":"Right knee pain for 3 years","ayurvedic_name":null,"duration":"3 years","aggravating_factors":["cold weather"],"relieving_factors":["warm compress"],"functional_impact":["difficulty walking"],"prior_treatment":"Physiotherapy — no relief"}

Transcript: "Sandhivata, bilateral knee, two years, Vata aggravation."
Output: {"summary":"Bilateral knee pain for 2 years","ayurvedic_name":"Sandhivata","duration":"2 years","aggravating_factors":["Vata aggravation"],"relieving_factors":[],"functional_impact":[],"prior_treatment":null}
""",

    # ── 2. ANAMNESIS ──────────────────────────────────────────────────────────
    "anamnesis": BASE_RULES + """\
Extract the Anamnesis (History of Present Illness).

RULES:
- Write a concise clinical narrative of how the illness developed.
- Capture: onset, progression, evolution over time, associated symptoms.
- Do NOT repeat the chief complaint verbatim.
- Do NOT include examination findings or past history.

SCHEMA:
{
  "onset": "string | null — when and how it started",
  "progression": "string | null — how it changed over time",
  "associated_symptoms": ["string — symptoms mentioned alongside the main complaint"],
  "relevant_history": "string | null — any contextual background mentioned"
}
""",

    # ── 3. OVERALL VPK ────────────────────────────────────────────────────────
    "overall_vpk": BASE_RULES + """\
Extract the Overall VPK (Tridosha dominance) of the patient.

AYURVEDIC RULES:
- V = Vata, P = Pitta, K = Kapha
- Dominance is ALWAYS one of: V, P, K, VP, VK, PK, VPK
- Alphabetical order always: V before P before K (so "PV" becomes "VP")
- Extract ONLY if explicitly stated. Do NOT infer from symptoms.
- If not stated → return null

SCHEMA:
{
  "dominance": "V | P | K | VP | VK | PK | VPK | null",
  "notes": "string | null — any additional qualifier spoken"
}

EXAMPLES:
"Overall VPK is PV" → {"dominance":"VP","notes":null}
"Kapha dominant patient" → {"dominance":"K","notes":null}
"Mixed Vata-Pitta constitution" → {"dominance":"VP","notes":"mixed"}
"Tridoshaja" → {"dominance":"VPK","notes":null}
"Vata Kapha dominant" → {"dominance":"VK","notes":null}
""",

    # ── 4. PULSE DIAGNOSIS ────────────────────────────────────────────────────
    "pulse_diagnosis": BASE_RULES + """\
Extract Pulse Diagnosis (Nadi Pariksha) — dosha severity per organ system.

─── SYSTEM CODES (exact strings to use in output) ───
CVS  = Cardiovascular System
GIT  = Gastrointestinal Tract
IS   = Immune System
PAN  = Pancreas
KUB  = Kidney-Ureter-Bladder
PRO  = Prostate
RT   = Respiratory Tract
LB   = Lower Back / Lumbar Region
GB   = Gallbladder
LIV  = Liver
SS   = Skeletal System
LSCS = Lumbo-Sacro-Cranial System
LISI = Large Intestine & Small Intestine
RB   = Reproductive System & Bladder

─── DOSHA CODES ───
V = Vata   P = Pitta   K = Kapha
PV/VP → both Pitta and Vata
VK/KV → both Vata and Kapha
PK/KP → both Pitta and Kapha
VPK   → all three

─── SEVERITY SCALE (use EXACTLY these strings) ───
"mild"          → MILD
"mild_moderate" → MILD MOD / MILD-MOD
"moderate"      → MOD
"severe"        → SEV

─── PARSING ALGORITHM ───
Each line of the transcript = one system entry.
Format (flexible order): [SYSTEM] [DOSHAS] [SEVERITY] or [SYSTEM] [SEVERITY] [DOSHAS]

Rule 1: When doshas appear TOGETHER (e.g., PV, VK) next to ONE severity → they share it.
Rule 2: When doshas appear SEPARATED by DIFFERENT severity words → each gets its own severity.
Rule 3: Unmentioned doshas → null

─── EXAMPLES ───
"LISI MILD MOD PV"    → {"system":"LISI","pitta":"mild_moderate","vata":"mild_moderate","kapha":null}
"RT MOD V"            → {"system":"RT","vata":"moderate","pitta":null,"kapha":null}
"LB VK MILD MOD"      → {"system":"LB","vata":"mild_moderate","kapha":"mild_moderate","pitta":null}
"GB MILD V"           → {"system":"GB","vata":"mild","pitta":null,"kapha":null}
"LIV MOD VK"          → {"system":"LIV","vata":"moderate","kapha":"moderate","pitta":null}
"SS MILD PK"          → {"system":"SS","pitta":"mild","kapha":"mild","vata":null}
"CVS MILD MOD K"      → {"system":"CVS","kapha":"mild_moderate","vata":null,"pitta":null}
"RB VK MOD"           → {"system":"RB","vata":"moderate","kapha":"moderate","pitta":null}
"GIT MILD V"          → {"system":"GIT","vata":"mild","pitta":null,"kapha":null}
"IS MOD K MILD MOD V" → {"system":"IS","kapha":"moderate","vata":"mild_moderate","pitta":null}
"PAN MILD MOD K"      → {"system":"PAN","kapha":"mild_moderate","vata":null,"pitta":null}
"PRO MILD K"          → {"system":"PRO","kapha":"mild","vata":null,"pitta":null}
"KUB MILD PV"         → {"system":"KUB","pitta":"mild","vata":"mild","kapha":null}

SCHEMA (return an array, one object per system mentioned):
[
  {
    "system": "CVS | GIT | IS | PAN | KUB | PRO | RT | LB | GB | LIV | SS | LSCS | LISI | RB",
    "vata":   "mild | mild_moderate | moderate | severe | null",
    "pitta":  "mild | mild_moderate | moderate | severe | null",
    "kapha":  "mild | mild_moderate | moderate | severe | null"
  }
]
""",

    # ── 5. AYURVEDIC SUPPLEMENTS ─────────────────────────────────────────────
    "ayurvedic_supplements": BASE_RULES + """\
Extract Ayurvedic supplements AND SGP proprietary medicines prescribed.

─── SGP MEDICINE DOSING FORMAT ───
Fractions like 1/4, 1/2, 1, 1.5 = quarter, half, one, one-and-half tablets/units.
Sequence is: morning → afternoon → evening → night (not all four need to be present).
Example: "APD 1/4 1/2 1 1.5" → dose_morning=1/4, dose_afternoon=1/2, dose_evening=1, dose_night=1.5

─── RULES ───
- Capture SGP codes exactly as spoken: APD, ATZ, NTP, SYN RESERVE, etc.
- Do NOT interpret what these codes mean — just capture name + dosing.
- Also capture standard Ayurvedic supplements (Ashwagandha, Triphala, etc.).
- If only some dose timings are given, set the rest to null.
- frequency applies when the doctor says OD/BD/TDS instead of individual timings.

SCHEMA:
[
  {
    "name":            "string — medicine name or SGP code",
    "dose_morning":    "string | null",
    "dose_afternoon":  "string | null",
    "dose_evening":    "string | null",
    "dose_night":      "string | null",
    "frequency":       "string | null — e.g. BD, TDS, OD (when no individual timings given)",
    "remarks":         "string | null — e.g. after food, with warm water"
  }
]

EXAMPLES:
"APD 1/4 1/2 1 1.5" → {"name":"APD","dose_morning":"1/4","dose_afternoon":"1/2","dose_evening":"1","dose_night":"1.5","frequency":null,"remarks":null}
"SYN RESERVE" → {"name":"SYN RESERVE","dose_morning":null,"dose_afternoon":null,"dose_evening":null,"dose_night":null,"frequency":null,"remarks":null}
"Ashwagandha 500mg BD after food" → {"name":"Ashwagandha","dose_morning":"500mg","dose_afternoon":null,"dose_evening":"500mg","dose_night":null,"frequency":"BD","remarks":"after food"}
""",

    # ── 6. PANCHAKARMA ────────────────────────────────────────────────────────
    "panchakarma": BASE_RULES + """\
Extract Panchakarma therapy prescription details.

─── RULES ───
- Each distinct procedure = one entry in the sessions array.
- Capture: procedure name, any companion procedure, oils/ingredients used,
  number of sessions, temperature (if mentioned), remarks.
- Do NOT invent session counts or oils not mentioned.
- Common procedures: Abhyanga, Shirodhara, Nasya, Basti, Virechana, Vamana,
  Janu Pichu, Greeva Basti, Kati Basti, Netra Tarpana, Karna Purana,
  Pinda Sweda, Njavara, Udwarthana, Sauna, Steam, Pizhichil.

SCHEMA:
{
  "total_sessions": "number | null",
  "sessions": [
    {
      "procedure":            "string — primary procedure name",
      "companion_procedure":  "string | null — e.g. Janu Pichu paired with Abhyanga",
      "oils_or_ingredients":  ["string"],
      "session_count":        "number | null",
      "temperature_celsius":  "number | null",
      "remarks":              "string | null"
    }
  ]
}

EXAMPLES:
"Abhyanga with Janu Pichu 5 sessions with Niutex oil" →
  {"procedure":"Abhyanga","companion_procedure":"Janu Pichu","oils_or_ingredients":["Niutex"],"session_count":5,"temperature_celsius":null,"remarks":null}

"Sauna 60 degrees" →
  {"procedure":"Sauna","companion_procedure":null,"oils_or_ingredients":[],"session_count":null,"temperature_celsius":60,"remarks":null}
""",

    # ── 7. TREATMENT & BACKGROUND ─────────────────────────────────────────────
    "treatment_and_background": BASE_RULES + """\
Extract current Allopathic medications and background treatment context.

─── ALLOPATHIC DRUG FORMAT ───
"tab [name] [dose] [frequency]" or "cap [name] [dose] [frequency]"
Frequency codes: OD=once daily, BD=twice daily, TDS=thrice daily,
QID=four times, HS=at bedtime, SOS=as needed, PRN=as needed.

─── RULES ───
- Include ONLY explicitly mentioned allopathic drugs.
- Do NOT include Ayurvedic or SGP medicines here (those go in ayurvedic_supplements).
- Capture allergies and ongoing non-drug therapies if mentioned.

SCHEMA:
{
  "current_medications": [
    {
      "name":      "string — drug name",
      "dose":      "string | null — e.g. 20mg",
      "frequency": "string | null — e.g. OD, BD, HS",
      "route":     "string | null — e.g. oral, topical"
    }
  ],
  "ongoing_therapies": ["string — e.g. physiotherapy, dialysis"],
  "allergies":         ["string"],
  "background_notes":  "string | null — any other relevant treatment context"
}

EXAMPLE:
"Tab Rosuvastatin 20mg at bedtime, Tab CoQ10 supplement twice daily" →
  medications: [
    {"name":"Rosuvastatin","dose":"20mg","frequency":"HS","route":"oral"},
    {"name":"CoQ10","dose":null,"frequency":"BD","route":"oral"}
  ]
""",

    # ── 8. PERSONAL HISTORY ───────────────────────────────────────────────────
    "personal_history": BASE_RULES + """\
Extract Personal and Lifestyle History.

RULES:
- Capture only what is explicitly spoken.
- Diet: Vegetarian / Non-Vegetarian / Vegan / Mixed.
- Bowel habits: Regular / Irregular / Constipated / Loose.
- Sleep quality: Good / Fair / Poor.
- Stress level: Low / Moderate / High.
- Return nulls for fields not mentioned.

SCHEMA:
{
  "diet":          "Vegetarian | Non-Vegetarian | Vegan | Mixed | string | null",
  "appetite":      "Good | Fair | Poor | string | null",
  "sleep_hours":   "number | null",
  "sleep_quality": "Good | Fair | Poor | null",
  "bowel_habits":  "Regular | Irregular | Constipated | Loose | null",
  "exercise":      "string | null — type and frequency",
  "occupation":    "string | null",
  "stress_level":  "Low | Moderate | High | null",
  "addictions":    ["string — e.g. smoking, alcohol, tobacco, coffee"]
}
""",

    # ── 9. REVIEW OF SYSTEMS ──────────────────────────────────────────────────
    "review_of_systems": BASE_RULES + """\
Extract Review of Systems — a checklist of symptoms by body system.

RULES:
- For each system mentioned → create an entry.
- Symptoms explicitly stated → "present".
- Symptoms explicitly denied → "absent".
- Uncertain or unmentioned → skip (do not include).
- Do NOT invent systems not mentioned.

SCHEMA:
{
  "[system_name]": {
    "present": ["string — symptom"],
    "absent":  ["string — symptom"]
  }
}

EXAMPLE:
"No headache, but patient has nausea and mild abdominal pain. Chest clear."
→ {
    "neurological": {"present":[],"absent":["headache"]},
    "gastrointestinal": {"present":["nausea","mild abdominal pain"],"absent":[]},
    "cardiovascular": {"present":[],"absent":["chest pain"]}
  }
""",

    # ── 10. SYSTEMIC EXAMINATION ──────────────────────────────────────────────
    "systemic_examination": BASE_RULES + """\
Extract Systemic Examination findings from physical examination.

RULES:
- Include findings ONLY if examination is explicitly described.
- "Normal" or "within normal limits" is a valid finding.
- Do NOT invent normal findings for systems not mentioned.
- Return null for any system not examined.

SCHEMA:
{
  "general":          "string | null — overall appearance, built, nourishment",
  "cardiovascular":   "string | null",
  "respiratory":      "string | null",
  "abdomen":          "string | null",
  "nervous_system":   "string | null",
  "musculoskeletal":  "string | null",
  "skin":             "string | null",
  "ent":              "string | null",
  "eyes":             "string | null"
}
""",

    # ── 11. PAST MEDICAL HISTORY ──────────────────────────────────────────────
    "past_medical_history": BASE_RULES + """\
Extract Past Medical, Surgical, and Family History.

RULES:
- Medical: chronic conditions previously diagnosed (NOT current complaints).
- Surgical: past operations or procedures.
- Family history: conditions in blood relatives.
- Allergies: known drug or food allergies.
- If "no history" or "nil" stated → return empty list.

SCHEMA:
{
  "medical":       ["string — e.g. Hypertension since 10 years"],
  "surgical":      ["string — e.g. Appendectomy 2015"],
  "family_history":["string — e.g. Father: Diabetes"],
  "allergies":     ["string — e.g. Penicillin allergy"]
}
""",

    # ── 12. ASSESSMENT & PLAN ─────────────────────────────────────────────────
    "assessment_and_plan": BASE_RULES + """\
Extract the Assessment (diagnosis) and complete Treatment Plan for this visit.
This section captures: diagnosis, medicines, therapies, investigations,
home remedies, diet advice, and follow-up schedule.

─── RULES ───
- Allopathic diagnosis: standard medical terms (e.g., Hypertension, Cerebral Ataxia).
- Ayurvedic diagnosis: Ayurvedic terms (e.g., Sandhivata, Pitta Pradhana Vikriti).
- Medications here = plan summary, not detailed doses (those are in other sections).
- Home remedies: specific home-care instructions (oils, teas, steam, etc.).
- Diet: explicit inclusions, exclusions, and recommendations.
- Follow-up: daily check-in, weekly review, monthly assessment.
- Investigations: tests advised (lab, imaging, etc.).

SCHEMA:
{
  "allopathic_diagnosis":  ["string"],
  "ayurvedic_diagnosis":   "string | null",
  "plan": {
    "medications":       ["string — brief medicine names"],
    "therapies":         ["string — Panchakarma or other therapies planned"],
    "investigations":    ["string — tests advised"],
    "home_remedies":     ["string — e.g. Neelibhringadi oil on scalp, fennel tea"],
    "diet_advice": {
      "include":  ["string"],
      "exclude":  ["string"]
    },
    "lifestyle_advice":  ["string"],
    "follow_up": {
      "daily":   "string | null",
      "weekly":  "string | null",
      "monthly": "string | null"
    },
    "referrals":         ["string"]
  },
  "prognosis": "string | null"
}

EXAMPLE (partial):
Transcript: "Diagnosis is Cerebral Ataxia. Counsel patient on Neelibhringadi oil on scalp daily.
Exclude raagi and jowar. Include fennel tea. Weekly review. Tests: MRI brain."

Output:
{
  "allopathic_diagnosis": ["Cerebral Ataxia"],
  "ayurvedic_diagnosis": null,
  "plan": {
    "medications": [],
    "therapies": [],
    "investigations": ["MRI brain"],
    "home_remedies": ["Neelibhringadi Kera Tailam on scalp daily"],
    "diet_advice": {
      "include": ["Fennel tea"],
      "exclude": ["Raagi", "Jowar"]
    },
    "lifestyle_advice": [],
    "follow_up": {"daily": null, "weekly": "Weekly review", "monthly": null},
    "referrals": []
  },
  "prognosis": null
}
""",
}

# ── Valid section keys — used for request validation ──────────────────────────
VALID_SECTIONS = set(SECTION_PROMPTS.keys())

"""
Clinical Section Prompts V1 - SGP / Docture Poly Case Sheet Automation
======================================================================

Drop-in replacement candidate for:
    app/casesheet/prompts.py

Design:
1. WHISPER_INITIAL_PROMPTS: section vocabulary hints for faster-whisper.
2. SECTION_PROMPTS: transcript-to-JSON extraction prompts.
3. QUALITY_PROMPTS: full draft quality checks.
4. COMPOSER_PROMPTS: full draft-to-elaborate-case-sheet prompts.
5. SECTION_MAX_TOKENS: token budget per operation.
6. DISPLAY_SECTION_ORDER: suggested UI order.

Clinical safety rule:
This prompt pack is for clinical documentation and clinician-reviewed case-sheet
automation. It must not autonomously diagnose, prescribe, order investigations,
or finalize care decisions.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# Shared speech vocabulary for faster-whisper initial_prompt
# -----------------------------------------------------------------------------

_AYU_BASE = (
    "SGP Ayurvedic Integrative Medicine clinic. Docture Poly case sheet automation. "
    "Doctor dictating clinical notes in English with possible Hindi, Telugu, Kannada, Sanskrit, "
    "Ayurvedic and allopathic medical terms. Preserve medicine names, doses, codes and abbreviations. "
    "Ayurvedic terms: Vata Pitta Kapha Tridosha Prakriti Vikriti Ama Agni Ojas Tejas Bala Mala Mutra "
    "Koshta Srotas Dhatu Nadi Pariksha VPK dominance Nadi Rogi Roga Samprapti Nidana Hetu Purvarupa "
    "Rupa Upashaya Anupashaya Chikitsa Panchakarma Rasayana Vajikarana. "
    "Common Ayurvedic diagnoses: Sandhivata Amavata Gridhrasi Katigraha Manyastambha Ardhavabhedaka "
    "Tamaka Shwasa Prameha Hridroga Shotha Amlapitta Grahani Pandu Udavarta. "
)

_MEDICAL_BASE = (
    "Clinical terms: chief complaint history of present illness anamnesis past history family history "
    "drug allergy medication history diabetes hypertension thyroid asthma COPD cardiac renal liver autoimmune "
    "neurological psychiatric cancer infection surgery hospitalization vitals height weight BMI temperature BP PR RR "
    "SpO2 respiratory rate pulse blood pressure examination inspection palpation percussion auscultation swelling "
    "tenderness warmth deformity range of motion gait neurological vascular investigations CBC ESR CRP HbA1c LFT RFT "
    "lipid profile urine routine X ray MRI CT ultrasound ECG diagnosis provisional diagnosis differential diagnosis. "
)

_MEDICINE_BASE = (
    "Medication terms: tablet tab capsule cap syrup ointment gel drops injection IV IM SC topical oral nasal inhalation. "
    "Dosing: mg mcg gram ml drops units OD BD BID TDS QID HS QHS SOS PRN before food after food empty stomach morning afternoon "
    "evening night weekly monthly. Allopathic medicines: Metformin Amlodipine Telmisartan Losartan Atorvastatin Rosuvastatin "
    "Aspirin Clopidogrel Pantoprazole Omeprazole Thyroxine Metoprolol Pregabalin Gabapentin Duloxetine Paracetamol Ibuprofen "
    "Diclofenac Etoricoxib Vitamin D B12 calcium. "
    "SGP proprietary canonical medicine names: APD ATHEROLYZIN MIGRANONE IMUMODULIN BIOTIN ALLOWYN SYNGEN D-TOX NEUROTROPIN "
    "CAG Nuts RESERVE CISSUES QUADRANGULARIES. "
    "Common SGP medicine spoken variants: atherolyzine ethylizine migranine neurotropine imd allowin syngin singin detox dtox. "
    "Ayurvedic medicines: Ashwagandha Triphala Brahmi Shatavari Guduchi Amalaki Haritaki Vibhitaki Trikatu "
    "Hingvastak Dashamoola Chyawanprash Arjuna Punarnava Gokshura Vacha Shankhapushpi."
)

_PANCHAKARMA_BASE = (
    "Panchakarma and therapy terms: Abhyanga Shirodhara Nasya Basti Virechana Vamana Janu Pichu Januvasthi Greeva Vasthi "
    "Kati Vasthi Netra Tarpana Karna Purana Pinda Sweda Njavara Udwarthana Sauna Steam Pizhichil Patra Pinda Sweda "
    "Choornasweda Valuka Sweda Lepam Dhanyamla Dhara. "
    "Oils: Nutex Niutex Ksheerabala Dhanwantharam Bala Anu Tailam Chandanadi Thailam "
    "NeeliBringadi Keera Tailam Neelibhringadi Brahmi Narayana Kottamchukkadi Mahanarayana Sahacharadi Murivenna. "
    "SGP procedures: Gandusham Gandusha Nithya Virechana Prathivaara Virechana Karma Anutailam Steam Inhalations SGP Covid Protocol. "
    "Detox decoctions: Fennel Tea Barley Soup Rice Soup Tapioca Soup Sabu Dana Raagi Soup Jowar Soup. "
    "Exercises yoga: Naukasanam Bhujangasanam Stretching Pranayama."
)

# -----------------------------------------------------------------------------
# SGP Medicine Canonical Name Correction Table
# Embedded into LLM prompts to fix speech-to-text spelling errors
# -----------------------------------------------------------------------------

_SGP_MEDICINE_KNOWLEDGE = """\
SGP PROPRIETARY MEDICINE CANONICAL NAME CORRECTION TABLE:
When the transcript phonetically or visually matches any listed spoken/misspelled variant,
output ONLY the canonical name shown. Never output the misspelled/spoken variant.

Spoken/Misspelled -> Canonical Name:
- apd, a p d, apidi, a.p.d -> APD
- atherolyzin, atherolyzine, ethylizine, ethylizin, athero lizin -> ATHEROLYZIN
- migranone, migranine, migraine tab, migranol, migranon -> MIGRANONE
- imumodulin, imd, immunodulin, imumoduline, immu modulin, i.m.d -> IMUMODULIN
- biotin, biotine, bio tin -> BIOTIN
- allowyn, allowin, allo win, alowyn -> ALLOWYN
- syngen, syngin, singin, singen, syn gen -> SYNGEN
- d-tox, dtox, detox tab, dee tox, d tox -> D-TOX
- neurotropin, neurotropine, neurotrophin, neuro tropin -> NEUROTROPIN
- cag nuts, cag, c a g, kag nuts -> CAG Nuts
- reserve, reserw, reserv -> RESERVE
- cissues, c issues, sishues -> CISSUES
- quadrangularies, quadrangularis, quadrangulary -> QUADRANGULARIES

CRITICAL: Always use the canonical name. If uncertain between a known SGP canonical name
and an unknown name, prefer the canonical SGP name and note uncertainty in needs_doctor_confirmation.
"""

# -----------------------------------------------------------------------------
# SGP Procedure and Therapy Canonical Name Correction Table
# -----------------------------------------------------------------------------

_SGP_PROCEDURE_KNOWLEDGE = """\
SGP PROCEDURE AND THERAPY CANONICAL NAME CORRECTION TABLE:
When the transcript phonetically matches any listed spoken/misspelled variant,
output ONLY the canonical procedure name shown.

Spoken/Misspelled -> Canonical Name:
- gandusham, gandusha, gandoosha, gandush -> Gandusham
- nutex oil, nutex, nu tex -> Nutex Oil
- chandanadi, chandanadi tailam, chandanadi oil -> Chandanadi Thailam
- neelibringadi, neelibrungadi, nilibringadi -> NeeliBringadi Keera Tailam
- nithya virechana, daily virechana, nithya virechan -> Nithya Virechana Process
- prathivaara virechana, prathivara virechana, weekly virechana -> Prathivaara Virechana Karma
- anutailam, anu tailam, anutail -> Anutailam
- steam inhalation, steam inhalations, steaming -> Steam Inhalations
- fennel tea, fennel, saunf tea -> Fennel Tea
- barley soup, barley water -> Barley Soup
- rice soup, rice water, kanji -> Rice Soup
- tapioca soup, sabu dana, saboo dana, sago -> Tapioca Soup (Sabu Dana)
- raagi soup, ragi soup, finger millet soup -> Raagi Soup (Finger Millet)
- jowar soup, jwar soup -> Jowar Soup
- sgp covid protocol, covid protocol -> SGP Covid Protocol
- januvasthi, januvasti, janu basti, knee basti -> Januvasthi
- greeva vasthi, greeva basti, neck basti -> Greeva Vasthi
- kati vasthi, kati basti, lumbar basti -> Kati Vasthi
"""

WHISPER_INITIAL_PROMPTS: dict[str, str] = {
    "patient_identity": _AYU_BASE + _MEDICAL_BASE + (
        "Patient name age gender mobile patient ID OP number appointment visit type new follow up doctor. "
        "Assigned doctor followup doctor followup contact alternate number PT number OP number."
    ),
    "encounter_context": _AYU_BASE + _MEDICAL_BASE + (
        "Encounter date doctor department case type consent verified source voice dictation follow up consultation "
        "assigned doctor followup doctor name followup doctor contact."
    ),
    "transcript_cleanup": _AYU_BASE + _MEDICAL_BASE + _MEDICINE_BASE + _PANCHAKARMA_BASE + (
        "Clean transcript without changing clinical meaning. "
        "APD ATHEROLYZIN MIGRANONE IMUMODULIN BIOTIN ALLOWYN SYNGEN D-TOX NEUROTROPIN CAG RESERVE CISSUES QUADRANGULARIES."
    ),
    "chief_complaint": _AYU_BASE + _MEDICAL_BASE + (
        "Chief complaint duration site side laterality severity aggravating relieving factors functional impact."
    ),
    "anamnesis": _AYU_BASE + _MEDICAL_BASE + (
        "History of present illness onset progression course associated symptoms negative history disease timeline."
    ),
    "pulse_diagnosis": _AYU_BASE + (
        "Nadi Pariksha pulse diagnosis. System codes CVS GIT IS PAN KUB PRO RT LB GB LIV SS LSCS LISI RB OBG. "
        "Dosha codes V P K VP VK PK VPK. Severity Mild Very Mild Mild Moderate Moderate Severe."
    ),
    "ayurvedic_assessment_extended": _AYU_BASE + (
        "Prakriti Vikriti VPK dominance Ama Agni Koshta Ojas Bala Srotas Dhatu Mala Mutra Jihva Nidana Samprapti Ayurvedic diagnosis."
    ),
    "ayurvedic_supplements": _AYU_BASE + _MEDICINE_BASE + (
        "SGP Rx Ayurvedic supplements. APD ATHEROLYZIN MIGRANONE IMUMODULIN BIOTIN ALLOWYN SYNGEN D-TOX NEUROTROPIN "
        "CAG Nuts RESERVE CISSUES QUADRANGULARIES. Doses morning afternoon evening night frequency remarks start week."
    ),
    "panchakarma": _AYU_BASE + _PANCHAKARMA_BASE + (
        "Panchakarma sessions procedures oils ingredients session count temperature remarks. "
        "Gandusham Nithya Virechana Prathivaara Virechana Anutailam Steam Inhalations Fennel Tea Barley Soup."
    ),
    "exercises_yoga": _AYU_BASE + _PANCHAKARMA_BASE + (
        "Exercises yoga prescribed. Naukasanam Bhujangasanam Stretching Pranayama walking swimming."
    ),
    "detox_procedures": _AYU_BASE + _PANCHAKARMA_BASE + (
        "Detoxifying procedures decoctions. Fennel Tea Barley Soup Rice Soup Tapioca Soup Raagi Soup Jowar Soup. "
        "Gandusham Nithya Virechana Prathivaara Virechana Anutailam Steam Inhalations."
    ),
    "followup_details": _AYU_BASE + _MEDICAL_BASE + (
        "Follow up details assigned doctor followup doctor name contact number next visit date department."
    ),
    "treatment_and_background": _AYU_BASE + _MEDICAL_BASE + _MEDICINE_BASE + (
        "Treatment background allopathic drugs ongoing therapies allergies previous treatments."
    ),
    "medication_history": _AYU_BASE + _MEDICAL_BASE + _MEDICINE_BASE + (
        "Medication history current medicines past medicines stopped medicines dose route frequency duration indication compliance side effects."
    ),
    "past_medical_history": _AYU_BASE + _MEDICAL_BASE + (
        "Past medical surgical family history chronic conditions surgeries hospitalizations allergies trauma transfusion implants."
    ),
    "surgical_history": _AYU_BASE + _MEDICAL_BASE + (
        "Surgical history operations hospitalization procedures trauma implants transfusion anesthesia complications."
    ),
    "allergy_history": _AYU_BASE + _MEDICAL_BASE + _MEDICINE_BASE + (
        "Drug allergy food allergy environmental allergy herbal intolerance reaction severity confirmed suspected."
    ),
    "family_history_detailed": _AYU_BASE + _MEDICAL_BASE + (
        "Family history diabetes hypertension cardiac disease cancer autoimmune neurological hereditary diseases relation age."
    ),
    "personal_history": _AYU_BASE + _MEDICAL_BASE + (
        "Diet appetite bowel urine sleep exercise occupation stress addictions smoking alcohol tobacco tea coffee."
    ),
    "menstrual_obstetric_history": _AYU_BASE + _MEDICAL_BASE + (
        "Female history LMP cycle regularity flow pain pregnancy obstetric history menopause contraception gynecological disease."
    ),
    "vitals_anthropometry": _AYU_BASE + _MEDICAL_BASE + (
        "Vitals height weight BMI temperature BP PR RR pulse respiratory rate SpO2 blood sugar waist hip wrist forearm."
    ),
    "general_examination": _AYU_BASE + _MEDICAL_BASE + (
        "General examination built nourishment pallor icterus cyanosis clubbing lymph nodes edema hydration gait pain score."
    ),
    "systemic_examination": _AYU_BASE + _MEDICAL_BASE + (
        "Systemic examination cardiovascular respiratory abdomen CNS musculoskeletal skin ENT eyes genitourinary normal abnormal."
    ),
    "investigation_reports": _AYU_BASE + _MEDICAL_BASE + (
        "Investigations lab reports imaging reports values units reference ranges abnormal findings tests advised pending reports."
    ),
    "assessment_and_plan": _AYU_BASE + _MEDICAL_BASE + _MEDICINE_BASE + _PANCHAKARMA_BASE + (
        "Assessment diagnosis plan medicines therapies investigations home remedies diet lifestyle follow up prognosis referral."
    ),
}

# -----------------------------------------------------------------------------
# Shared LLM instruction rules
# -----------------------------------------------------------------------------

BASE_RULES = """\
You are a clinical documentation assistant for SGP Ayurvedic Integrative Medicine clinic.
STRICT RULES:
- Return ONLY valid JSON. No markdown, no backticks, no explanations.
- Extract only facts stated in the transcript. Do not hallucinate.
- Paraphrase only when the clinical meaning is clearly stated.
- If information is absent, return null, an empty string, or [] as required by the schema.
- If information is unclear, use null and add the item to needs_doctor_confirmation when available.
- Preserve Ayurvedic, SGP, Sanskrit, medicine, dose, and procedure terms exactly when spoken.
- Do not correct Ayurvedic terms into allopathic terms.
- Do not create a final diagnosis, prescription, order, referral, or treatment change unless explicitly dictated by the clinician.
- Distinguish patient-reported history, doctor examination findings, and clinician assessment.
- If a negation is stated, record it as absent/denied rather than deleting it.

ANTI-HALLUCINATION RULES (CRITICAL — NEVER VIOLATE):
- REJECT any transcript segment that contains non-Latin, non-Sanskrit, non-ASCII characters
  (e.g. Korean, Chinese, Japanese, Arabic, Cyrillic). Treat such segments as noise and return null.
- NEVER output raw JSON field names (e.g. needs_doctor_confirmation, _raw, _error) as clinical text.
- NEVER let internal schema keys or Python dict keys appear inside string values.
- If a transcript segment is incoherent, garbled, or contains random characters, skip it and return null.
- NEVER invent or assume numeric values (vitals, measurements, doses) not explicitly stated.
- NEVER copy text from the system prompt or schema into clinical output fields.
- The needs_doctor_confirmation field is for INTERNAL USE ONLY. Its values must never be rendered
  as clinical text in any other field.
- If the same field could be filled from the transcript OR from a schema example, use ONLY the transcript.
- QUALITY GATE / UNRELATED AUDIO DETECTION: If the transcribed text appears to be background chatter, random conversation, non-clinical speech, or acoustic hallucination without valid clinical instructions for this section, you MUST return ONLY an error object: {"_reprompt": {"required": true, "reason": "Detected irrelevant conversational audio or background chatter without valid clinical instructions."}}.
"""

GLOBAL_MEDICAL_INSTRUCTION = """\
You are an expert medical scribe for SGP Ayurvedic Integrative Medicine clinic.
The clinic combines Ayurvedic, allopathic, integrative, and regenerative clinical documentation.
Doctors may dictate in English with Ayurvedic Sanskrit terms, local-language phrases, abbreviations,
medicine codes, and speech-to-text errors.

Your task:
1. Extract clinical facts from doctor dictation only.
2. Normalize speech into structured clinical data WITHOUT inventing facts.
3. Map data exactly to the requested JSON schema — no extra keys, no missing required keys.
4. Preserve Ayurvedic terms, SGP medicine names, drug doses, and clinical abbreviations exactly.
5. Return only valid JSON. No markdown, no code fences, no explanation text.

Core Ayurvedic glossary:
- Vata, Pitta, Kapha: doshas.
- Prakriti: constitution. Vikriti: current imbalance.
- Ama: metabolic toxin. Agni: digestive capacity. Koshta: bowel tendency.
- Srotas: channel/system. Dhatu: tissue. Ojas: vitality.
- Nadi Pariksha: pulse diagnosis. Samprapti: pathogenesis.

SGP Proprietary Medicine Canonical Names (correct any misspelling to these):
APD | ATHEROLYZIN | MIGRANONE | IMUMODULIN | BIOTIN | ALLOWYN | SYNGEN |
D-TOX | NEUROTROPIN | CAG Nuts | RESERVE | CISSUES | QUADRANGULARIES

SGP System Codes for Pulse Diagnosis:
CVS (cardiovascular) | GIT (gastrointestinal) | IS (immune system) | PAN (pancreas) |
KUB (kidney ureter bladder) | PRO (prostate) | RT (respiratory tract) | LB (lower back) |
GB (gallbladder) | LIV (liver) | SS (skeletal system) | LSCS (lumbo sacro cranial) |
LISI (large intestine small intestine) | RB (reproductive bladder) | OBG (obstetrics gynecology)

CRITICAL OUTPUT RULES:
- If any transcript segment contains non-ASCII, non-Latin, non-Sanskrit characters
  (Korean, Chinese, Arabic, Cyrillic, etc.) treat the entire segment as noise and return null for that field.
- The needs_doctor_confirmation field content must NEVER appear in any other clinical field.
- Never copy schema keys, field names, or prompt text into output values.
- Never fabricate numeric measurements, vitals, or doses not spoken in the transcript.
- Respond with valid JSON only — parseable by json.loads() without preprocessing.

Safety boundary:
This is documentation support only. Do not act as an autonomous clinician.
"""

# -----------------------------------------------------------------------------
# Reusable schema fragments in prompt text
# -----------------------------------------------------------------------------

_SECTION_FOOTER = """\
VALIDATION:
- Output must be parseable by Python json.loads() with zero preprocessing.
- No markdown fences, no backticks, no code blocks, no explanatory text.
- No comments inside JSON.
- Do not include extra keys outside the schema.
- If the transcript has no data for this section, return the schema with all values as null or [].
- NEVER place raw field names, schema keys, or prompt text inside string values.
- NEVER include non-ASCII characters (Korean, Chinese, Arabic, etc.) in any output field.
  If the transcript contained such characters, skip that segment and return null.
"""

SECTION_PROMPTS: dict[str, str] = {

    "patient_identity": BASE_RULES + """\
Extract patient identity and basic encounter identifiers from the transcript.

Rules:
- Only extract identity details if explicitly spoken.
- Do not guess age, sex, mobile number, patient ID, or appointment ID.
- If a doctor says "old patient" or "follow up", capture visit_type.
- If the transcript contains multiple possible names, record them in needs_doctor_confirmation.

Schema:
{
  "patient_name": "string | null",
  "patient_id": "string | null",
  "mobile": "string | null",
  "age": "number | null",
  "age_unit": "years | months | days | null",
  "gender": "male | female | other | unknown | null",
  "appointment_id": "string | null",
  "visit_type": "new | follow_up | emergency | walk_in | teleconsultation | null",
  "language_detected": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "encounter_context": BASE_RULES + """\
Extract encounter context for the case sheet.

Rules:
- Capture administrative and workflow context only.
- Do not include clinical history here unless it directly affects case type or consent.
- consent_verified is true only when consent is explicitly confirmed.

Schema:
{
  "encounter_date_text": "string | null",
  "encounter_time_text": "string | null",
  "department": "string | null",
  "specialty": "string | null",
  "case_type": "Ayurvedic | Allopathic | Integrated | Regenerative | Wellness | null",
  "source": "voice | text | manual | imported_report | null",
  "consent_verified": "boolean | null",
  "payment_context": "string | null",
  "appointment_context": "string | null",
  "notes": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "transcript_cleanup": BASE_RULES + """\
Clean the transcript for readability before clinical extraction.

Rules:
- Do not add or remove clinical facts.
- Correct obvious speech-to-text punctuation and spacing errors.
- Preserve medicine names, SGP codes, doses, Ayurvedic terms, dates, durations, and laterality.
- Keep uncertain words in square brackets with a question mark, e.g. [APD?].
- Segment the cleaned transcript into probable case sheet sections if possible.

Schema:
{
  "cleaned_transcript": "string",
  "probable_sections": {
    "chief_complaint": "string | null",
    "history": "string | null",
    "medication_history": "string | null",
    "examination": "string | null",
    "assessment_plan": "string | null",
    "other": "string | null"
  },
  "unclear_terms": ["string"],
  "possible_speech_errors": ["string"]
}
""" + _SECTION_FOOTER,

    "chief_complaint": BASE_RULES + """\
Extract the Chief Complaint: the primary reason for today's visit.

Rules:
- Summarize the main complaint in one clinical sentence.
- Include Ayurvedic disease name only if stated.
- Include duration, site, laterality, severity, course, aggravating and relieving factors only if stated.
- Do not include examination findings or diagnoses unless the doctor explicitly says the complaint is a known diagnosis.

Schema:
{
  "summary": "string | null",
  "complaints": [
    {
      "complaint": "string",
      "ayurvedic_name": "string | null",
      "site": "string | null",
      "laterality": "right | left | bilateral | midline | generalized | null",
      "duration": "string | null",
      "severity": "string | null",
      "course": "acute | subacute | chronic | recurrent | progressive | improving | worsening | intermittent | null",
      "aggravating_factors": ["string"],
      "relieving_factors": ["string"],
      "functional_impact": ["string"],
      "prior_treatment": "string | null",
      "needs_doctor_confirmation": ["string"]
    }
  ]
}
""" + _SECTION_FOOTER,

    "anamnesis": BASE_RULES + """\
Extract Anamnesis / History of Present Illness.

Rules:
- Write a concise clinical narrative of how the illness developed.
- Capture onset, progression, episode pattern, associated symptoms, relevant context and negative history.
- Do not repeat the chief complaint unless needed for continuity.
- Do not include examination or final assessment.

Schema:
{
  "summary": "string | null",
  "onset": "string | null",
  "mode_of_onset": "sudden | gradual | traumatic | spontaneous | post_infective | post_procedure | unknown | null",
  "progression": "string | null",
  "duration_total": "string | null",
  "episode_pattern": "string | null",
  "associated_symptoms": ["string"],
  "negative_history": ["string"],
  "relevant_context": "string | null",
  "patient_reported_concerns": ["string"],
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "pulse_diagnosis": BASE_RULES + """\
Extract Pulse Diagnosis / Nadi Pariksha, including overall VPK / Tridosha dominance and system-wise pulse severity.

Rules for overall VPK:
- Use only one of: V, P, K, VP, VK, PK, VPK, null for dominance.
- Normalize: PV->VP, KV->VK, KP->PK.
- Extract Prakriti, Vikriti, notes, dominance ONLY if explicitly stated.

Rules for system-wise pulse:
- Valid system codes ONLY (do not invent codes):
  CVS (cardiovascular), GIT (gastrointestinal), IS (immune system), PAN (pancreas),
  KUB (kidney ureter bladder), PRO (prostate), RT (respiratory tract), LB (lower back),
  GB (gallbladder), LIV (liver), SS (skeletal system), LSCS (lumbo sacro cranial system),
  LISI (large intestine small intestine), RB (reproductive bladder), OBG (obstetrics gynecology).
- Doshas: V=Vata, P=Pitta, K=Kapha. VP=Vata+Pitta, VK=Vata+Kapha, PK=Pitta+Kapha, VPK=all three.
- Severity normalization (map spoken terms):
  "very mild", "very-mild" -> "very_mild"
  "mild" -> "mild"
  "mild moderate", "mild-moderate", "mild-mod" -> "mild_moderate"
  "moderate", "mod" -> "moderate"
  "moderate severe", "moderate-severe" -> "moderate_severe"
  "severe", "sev" -> "severe"
- When doshas appear together next to one severity, apply that severity to all listed doshas.
- When doshas appear separately with separate severities, assign individually.
- Unmentioned doshas for a system must be null (do not assume zero/absent).
- ONLY include systems explicitly mentioned in the transcript. Do not list systems not spoken.

ANTI-HALLUCINATION: Never invent system codes. Only use codes from the valid list above.

Schema:
{
  "overall_vpk": {
    "dominance": "V | P | K | VP | VK | PK | VPK | null",
    "prakriti": "string | null",
    "vikriti": "string | null",
    "notes": "string | null"
  },
  "systems": [
    {
      "system": "CVS | GIT | IS | PAN | KUB | PRO | RT | LB | GB | LIV | SS | LSCS | LISI | RB | OBG",
      "vata": "very_mild | mild | mild_moderate | moderate | moderate_severe | severe | null",
      "pitta": "very_mild | mild | mild_moderate | moderate | moderate_severe | severe | null",
      "kapha": "very_mild | mild | mild_moderate | moderate | moderate_severe | severe | null",
      "raw_phrase": "string | null",
      "needs_doctor_confirmation": ["string"]
    }
  ],
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "ayurvedic_assessment_extended": BASE_RULES + """\
Extract extended Ayurvedic assessment.

Rules:
- Extract only what is explicitly dictated.
- Do not infer Prakriti, Vikriti, Ama, Agni, Srotas or Dhatu involvement from symptoms unless the doctor says it.
- Preserve Ayurvedic terminology exactly.

Schema:
{
  "prakriti": "string | null",
  "vikriti": "string | null",
  "vpk_dominance": "string | null",
  "ama": "present | absent | mild | moderate | severe | null",
  "agni": "manda | tikshna | vishama | sama | unknown | string | null",
  "koshta": "mridu | madhyama | krura | string | null",
  "ojas_bala": "string | null",
  "srotas_involved": ["string"],
  "dhatu_involved": ["string"],
  "mala_mutra_notes": "string | null",
  "jihva_notes": "string | null",
  "ayurvedic_diagnosis": "string | null",
  "samprapti_summary": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "ayurvedic_supplements": BASE_RULES + _SGP_MEDICINE_KNOWLEDGE + """\
Extract Ayurvedic supplements and SGP proprietary medicines prescribed or currently used.

Rules:
- APPLY the SGP MEDICINE CANONICAL NAME CORRECTION TABLE above to fix any misspelled medicine names (e.g., NETROLYZIN -> ATHEROLYZIN, NEEROTROPIN -> NEUROTROPIN).
- When the doctor says a name that matches a known SGP canonical name (or variant), output the canonical name.
- CRITICAL DOSAGE FRACTION NORMALIZATION: Always convert spoken fractions or raw STT numeric strings into clean standard mathematical fractions:
  * "one fourth" / "1 4th" / "1 by 4th" / "quarter" -> "1/4"
  * "half" / "one half" -> "1/2"
  * "three fourths" / "3 4th" / "3 by 4th" -> "3/4"
- MANDATORY 8-WEEK DOSAGE MATRIX ("weeks" field):
  * For EVERY medicine or supplement, you MUST output an explicit 8-element array of strings corresponding to Week 1 through Week 8 in the "weeks" field. NEVER set "weeks" to null!
  * SGP TITRATION PROTOCOLS ("quarter half one titration" / "1/4 1/2 1 titration" / "quarter half one dosage"):
    This specific clinical shorthand defines a 3-stage dose escalation over consecutive weeks:
    - 1st active week of taking the medicine: dose is "1/4"
    - 2nd active week of taking the medicine: dose is "1/2"
    - 3rd active week onwards (until week 8): dose is "1"
  * EFFECT OF START WEEK ("starting week X"):
    Any week BEFORE the start_week must be marked as "--" (inactive/not started yet). The titration schedule begins strictly on the specified start_week!
    - Example 1 ("APD starting week 1 at quarter half 1 titration"): start_week="1", weeks=["1/4", "1/2", "1", "1", "1", "1", "1", "1"]
    - Example 2 ("ATHEROLYZIN starting week 2 at quarter half 1 titration"): start_week="2", weeks=["--", "1/4", "1/2", "1", "1", "1", "1", "1"] (Notice Week 1 is "--", Week 2 is "1/4", Week 3 is "1/2", Weeks 4-8 are "1")
    - Example 3 ("SYNGEN starting in week 3 at half to 1 tablet titration"): start_week="3", weeks=["--", "--", "1/2", "1", "1", "1", "1", "1"]
    - Example 4 ("BIOTIN starting week 2 twice daily" without titration): start_week="2", weeks=["--", "1", "1", "1", "1", "1", "1", "1"]
  * RESERVE / SOS MEDICATIONS:
    If a medicine is ordered "to be kept on reserve" or "SOS" (e.g. NEUROTROPIN twice daily to be kept on reserve for acute nerve flare ups):
    - Do NOT assign numbers like "1/4" or "1" across the weeks!
    - Set weeks=["Reserve", "Reserve", "Reserve", "Reserve", "Reserve", "Reserve", "Reserve", "Reserve"], frequency="SOS (Reserve)", remarks="To be kept on reserve for acute nerve flare ups".
  * FOODS / NUTS PROTOCOLS (e.g. CAG Nuts):
    If starting week 1: weeks=["1", "1", "1", "1", "1", "1", "1", "1"] (or appropriate weekly status), and put preparation/soaking instructions in "timing" or "remarks".
- QUANTITY / BASE WEIGHT: Extract the exact quantity, strength, or weight (e.g., "500mg", "250mg", "10 ml", "2 tablets") as specified by the doctor in "quantity_mg". Do NOT default to "1000mg" or hallucinate quantities if not stated. If not mentioned, output null or empty string.
- GLOBAL FREQUENCY PROPAGATION: If a trailing or overarching command is spoken such as "all twice daily morning and evening", "take all BID", "6 to 8 am and 6 to 8 pm on empty stomach", apply that frequency ("BID") and timing/instructions to ALL medicines in the extracted list unless specifically overridden for an individual item.
- SGP dose sequence when spoken as four values: morning / afternoon / evening / night.
- start_week: capture the number of the starting week (e.g. "1", "2", "3").
- Do not interpret or translate the clinical meaning of SGP codes.
- Do NOT mix allopathic medicines here; put them in treatment_and_background.

Schema:
[
  {
    "name": "string",
    "medicine_category": "SGP proprietary | Ayurvedic classical | Ayurvedic supplement | herb | unknown | null",
    "quantity_mg": "string | null",
    "weeks": ["string", "string", "string", "string", "string", "string", "string", "string"],
    "start_week": "string | null",
    "dose_morning": "string | null",
    "dose_afternoon": "string | null",
    "dose_evening": "string | null",
    "dose_night": "string | null",
    "dose": "string | null",
    "frequency": "OD | BID | TDS | QID | SOS | string | null",
    "route": "PO (Oral) | topical | nasal | string | null",
    "duration": "string | null",
    "timing": "string | null",
    "indication": "string | null",
    "remarks": "string | null",
    "needs_doctor_confirmation": ["string"]
  }
]
""" + _SECTION_FOOTER,

    "panchakarma": BASE_RULES + _SGP_PROCEDURE_KNOWLEDGE + """\
Extract Panchakarma and classical therapy prescription details.

Rules:
- APPLY the SGP PROCEDURE CANONICAL NAME CORRECTION TABLE above to fix any misspelled procedure names.
- When the doctor names a procedure that matches a canonical SGP procedure, output the canonical name.
- One procedure or procedure combination per session object.
- Capture procedure, companion procedure, oils/ingredients, session count, temperature, duration, body site, laterality, remarks.
- Do not invent oils, session counts, or temperatures not spoken.
- If doctor says "already done" for a procedure, set status to "completed". If prescribed, set to "prescribed".
- status field: "prescribed" | "completed" | "ongoing" | null.

Schema:
{
  "total_sessions": "number | null",
  "sessions": [
    {
      "procedure": "string",
      "companion_procedure": "string | null",
      "body_site": "string | null",
      "laterality": "right | left | bilateral | midline | generalized | null",
      "oils_or_ingredients": ["string"],
      "session_count": "number | null",
      "duration_per_session": "string | null",
      "temperature_celsius": "number | null",
      "sequence_or_schedule": "string | null",
      "status": "prescribed | completed | ongoing | null",
      "indication": "string | null",
      "contraindication_or_caution_mentioned": ["string"],
      "remarks": "string | null",
      "needs_doctor_confirmation": ["string"]
    }
  ],
  "overall_remarks": "string | null"
}
""" + _SECTION_FOOTER,

    "treatment_and_background": BASE_RULES + """\
Extract current treatment background with focus on allopathic medications and ongoing non-drug therapies.

Rules:
- Include allopathic medicines here.
- Do not include SGP/Ayurvedic medicines unless they are mixed into treatment context and cannot be separated.
- Capture allergies if mentioned, but dedicated allergy extraction should be used when available.

Schema:
{
  "current_medications": [
    {
      "name": "string",
      "dose": "string | null",
      "frequency": "string | null",
      "route": "string | null",
      "duration": "string | null",
      "indication": "string | null",
      "timing": "string | null",
      "adherence": "good | poor | irregular | stopped | unknown | null",
      "side_effects": ["string"],
      "needs_doctor_confirmation": ["string"]
    }
  ],
  "ongoing_therapies": ["string"],
  "previous_treatments": ["string"],
  "allergies": ["string"],
  "background_notes": "string | null"
}
""" + _SECTION_FOOTER,

    "medication_history": BASE_RULES + """\
Extract a robust medication history from the transcript.

Rules:
- Include current, past, stopped, OTC, supplement, allopathic, Ayurvedic and SGP medicines if spoken.
- For each medication, extract name, dose, frequency, route, duration, indication, adherence and adverse effects.
- If medicine name or dose is unclear, keep raw text and add needs_doctor_confirmation.
- Do not create or recommend new medicines.

Schema:
{
  "current_medicines": [
    {
      "name": "string",
      "system": "allopathic | ayurvedic | SGP | supplement | home_remedy | unknown",
      "dose": "string | null",
      "frequency": "string | null",
      "route": "string | null",
      "duration": "string | null",
      "timing": "string | null",
      "indication": "string | null",
      "adherence": "good | poor | irregular | stopped | unknown | null",
      "side_effects": ["string"],
      "raw_phrase": "string | null",
      "needs_doctor_confirmation": ["string"]
    }
  ],
  "past_medicines": ["string"],
  "stopped_medicines": ["string"],
  "otc_or_self_medication": ["string"],
  "duplicate_or_unclear_medicine_risks": ["string"],
  "medication_summary": "string | null"
}
""" + _SECTION_FOOTER,


    "past_medical_history": BASE_RULES + """\
Extract past medical, surgical, hospitalization, trauma and family history.

Rules:
- Medical history means prior or chronic conditions, not today's chief complaint.
- If no history/nil is stated, return [] for the relevant category and mention in negative_history.
- Preserve duration if spoken.

Schema:
{
  "medical": ["string"],
  "surgical": ["string"],
  "hospitalizations": ["string"],
  "trauma_history": ["string"],
  "blood_transfusion": "string | null",
  "implant_or_prosthesis_history": ["string"],
  "family_history": ["string"],
  "allergies": ["string"],
  "negative_history": ["string"],
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "surgical_history": BASE_RULES + """\
Extract surgical, procedural and hospitalization history.

Schema:
{
  "surgeries": [
    {
      "procedure": "string",
      "year_or_date": "string | null",
      "indication": "string | null",
      "hospital": "string | null",
      "complications": ["string"],
      "notes": "string | null"
    }
  ],
  "hospitalizations": ["string"],
  "trauma": ["string"],
  "implants_or_devices": ["string"],
  "blood_transfusion": "string | null",
  "anesthesia_reaction": "string | null",
  "negative_surgical_history": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "allergy_history": BASE_RULES + """\
Extract allergy and adverse reaction history.

Rules:
- Include drug, food, environmental and Ayurvedic/herbal allergies or intolerances.
- Differentiate confirmed allergy, suspected allergy, intolerance and side effect when stated.
- If no known allergy is explicitly stated, set no_known_allergies to true.

Schema:
{
  "no_known_allergies": "boolean | null",
  "allergies": [
    {
      "substance": "string",
      "category": "drug | food | environmental | herbal | ayurvedic | unknown",
      "reaction": "string | null",
      "severity": "mild | moderate | severe | anaphylaxis | unknown | null",
      "date_or_period": "string | null",
      "status": "confirmed | suspected | intolerance | side_effect | unknown | null",
      "notes": "string | null",
      "needs_doctor_confirmation": ["string"]
    }
  ]
}
""" + _SECTION_FOOTER,

    "family_history_detailed": BASE_RULES + """\
Extract detailed family history.

Rules:
- Capture condition, relation and relevant age/duration if spoken.
- If no family history is stated, capture that explicitly.

Schema:
{
  "family_conditions": [
    {
      "relation": "string | null",
      "condition": "string",
      "age_or_duration": "string | null",
      "status": "alive | deceased | unknown | null",
      "notes": "string | null"
    }
  ],
  "negative_family_history": ["string"],
  "hereditary_risk_notes": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "personal_history": BASE_RULES + """\
Extract personal, dietary and lifestyle history.

Rules:
- Capture diet, appetite, bowel, urine, sleep, exercise, occupation, stress and addictions only if stated.
- Do not judge or add advice here.

Schema:
{
  "diet": "Vegetarian | Non-Vegetarian | Vegan | Mixed | Jain | Satvik | string | null",
  "diet_details": "string | null",
  "appetite": "Good | Fair | Poor | Increased | Reduced | string | null",
  "water_intake": "string | null",
  "bowel_habits": "Regular | Irregular | Constipated | Loose | string | null",
  "urine": "string | null",
  "sleep_hours": "number | null",
  "sleep_quality": "Good | Fair | Poor | Disturbed | string | null",
  "exercise": "string | null",
  "occupation": "string | null",
  "stress_level": "Low | Moderate | High | string | null",
  "addictions": [
    {"type": "string", "quantity": "string | null", "duration": "string | null", "status": "current | past | stopped | unknown | null"}
  ],
  "lifestyle_summary": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "menstrual_obstetric_history": BASE_RULES + """\
Extract menstrual, obstetric and gynecological history when relevant.

Rules:
- Use only if patient is female or if gynecological/obstetric details are dictated.
- Do not ask or infer pregnancy; only record what is stated.
- If not applicable or not mentioned, return not_applicable_or_not_mentioned as true.

Schema:
{
  "not_applicable_or_not_mentioned": "boolean",
  "lmp": "string | null",
  "cycle_regularity": "regular | irregular | unknown | string | null",
  "cycle_length": "string | null",
  "flow": "scanty | normal | heavy | string | null",
  "dysmenorrhea": "present | absent | unknown | string | null",
  "pregnancy_status": "pregnant | not_pregnant | possible | unknown | null",
  "obstetric_history": "string | null",
  "menopause_status": "pre_menopausal | peri_menopausal | post_menopausal | unknown | null",
  "contraception": "string | null",
  "gynecological_conditions": ["string"],
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,


    "vitals_anthropometry": BASE_RULES + """\
Extract vitals and anthropometry.

Rules:
- Extract numeric values and units exactly as spoken.
- Do not calculate BMI unless height and weight are explicitly present and the transcript asks for BMI; otherwise null.
- The ERP fields available are height_cm, weight_kg, wrist_cm, waist_cm, fore_arm_cm, hip_cm, temp, bp, pr, rr.

Schema:
{
  "height_cm": "number | null",
  "weight_kg": "number | null",
  "bmi": "number | null",
  "wrist_cm": "number | null",
  "waist_cm": "number | null",
  "fore_arm_cm": "number | null",
  "hip_cm": "number | null",
  "temperature": "string | null",
  "bp": "string | null",
  "pulse_rate": "string | null",
  "respiratory_rate": "string | null",
  "spo2": "string | null",
  "blood_sugar": "string | null",
  "pain_score": "string | null",
  "notes": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "general_examination": BASE_RULES + """\
Extract general physical examination.

Schema:
{
  "general_condition": "string | null",
  "built": "string | null",
  "nourishment": "string | null",
  "orientation": "string | null",
  "pallor": "present | absent | unknown | null",
  "icterus": "present | absent | unknown | null",
  "cyanosis": "present | absent | unknown | null",
  "clubbing": "present | absent | unknown | null",
  "lymphadenopathy": "present | absent | unknown | null",
  "edema": "present | absent | unknown | null",
  "hydration": "string | null",
  "gait": "string | null",
  "pain_score": "string | null",
  "other_findings": ["string"],
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "systemic_examination": BASE_RULES + """\
Extract systemic examination findings.

Rules:
- Include findings only if examination is explicitly described.
- "Normal" or "within normal limits" is valid only for systems explicitly mentioned.
- Do not invent normal findings.

Schema:
{
  "summary": "string | null",
  "cardiovascular": "string | null",
  "respiratory": "string | null",
  "abdomen": "string | null",
  "nervous_system": "string | null",
  "musculoskeletal": "string | null",
  "skin": "string | null",
  "ent": "string | null",
  "eyes": "string | null",
  "genitourinary": "string | null",
  "other": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,


    "investigation_reports": BASE_RULES + """\
Extract investigations, reports and tests advised.

Rules:
- Separate previous reports, current reported values, abnormal findings, pending reports, and tests advised.
- For lab values, capture test name, value, unit, reference range and date if spoken.
- For imaging, capture modality, region, findings and impression if spoken.
- Do not interpret abnormality unless the doctor explicitly states it.

Schema:
{
  "lab_results": [
    {
      "test_name": "string",
      "value": "string | null",
      "unit": "string | null",
      "reference_range": "string | null",
      "date": "string | null",
      "flag_or_interpretation_spoken": "string | null"
    }
  ],
  "imaging_reports": [
    {
      "modality": "string | null",
      "body_region": "string | null",
      "date": "string | null",
      "findings": ["string"],
      "impression": "string | null"
    }
  ],
  "other_reports": ["string"],
  "abnormal_findings_mentioned": ["string"],
  "pending_reports": ["string"],
  "investigations_advised": ["string"],
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "assessment_and_plan": BASE_RULES + """\
Extract the clinician's Assessment and Plan matching the UI voice dictation structure.

SPEAKING INSTRUCTION FORMAT EXPECTED IN VOICE TRANSCRIPT:
"Assessments: Allopathic and Ayurvedic diagnoses. Plan: medicines, therapies, panchakarma, investigations advised, home remedies, diet include/exclude (foods to eat and avoid), lifestyle advice, and follow-up schedule."

EXTRACTION RULES:
1. ASSESSMENTS:
   - Extract allopathic diagnoses into "allopathic_diagnosis" array.
   - Extract Ayurvedic diagnoses into "ayurvedic_diagnosis" string.
   - Extract integrated impression or provisional diagnoses if spoken.
2. PLAN:
   - "medications": List all prescribed medicines, SGP supplements, doses, and schedules.
   - "therapies": External therapies (e.g. Abhyanga, Lepanam, Kashaya Dhara).
   - "panchakarma": Specific Panchakarma procedures prescribed (e.g. Vamana, Virechana, Basti, Nasyam).
   - "procedures": Minor clinical procedures.
   - "investigations": Lab tests, blood tests, X-rays, MRI, CT, USG scans advised.
   - "home_remedies": Home preparations, teas, decoctions, warm compresses spoken in plan.
   - "diet_advice":
     - "include": Foods, soups, grains, or drinks explicitly recommended to eat/drink (e.g. barley soup, warm water, green gram).
     - "exclude": Foods, drinks, or habits explicitly restricted/avoided (e.g. curd, cold water, fried food, nightshades).
     - "general": General dietary rules spoken.
   - "lifestyle_advice": Ergonomic advice, posture, habits, or exercise instructions.
   - "follow_up":
     - Extract follow-up duration or next visit date (e.g. "after 2 weeks", "next month", "daily", "weekly").

STRICT RULE: Extract every single item dictated by the doctor without missing any points. Do NOT invent recommendations not present in the transcript.

Schema:
{
  "allopathic_diagnosis": ["string"],
  "ayurvedic_diagnosis": "string | null",
  "integrated_clinical_impression": "string | null",
  "differential_considerations_spoken": ["string"],
  "plan": {
    "medications": ["string"],
    "therapies": ["string"],
    "panchakarma": ["string"],
    "procedures": ["string"],
    "investigations": ["string"],
    "home_remedies": ["string"],
    "diet_advice": {
      "include": ["string"],
      "exclude": ["string"],
      "general": ["string"]
    },
    "lifestyle_advice": ["string"],
    "exercise_or_rehab": ["string"],
    "referrals": ["string"],
    "follow_up": {
      "daily": "string | null",
      "weekly": "string | null",
      "monthly": "string | null",
      "next_visit": "string | null"
    }
  },
  "prognosis": "string | null",
  "patient_education_spoken": ["string"],
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "exercises_yoga": BASE_RULES + """\
Extract exercises, yoga, and physical activity prescriptions.

Rules:
- Capture only exercises explicitly prescribed by the doctor.
- Each exercise gets its own object. Do not merge separate exercises.
- frequency: how often (daily, alternate days, twice a week, etc.).
- duration_minutes: only if explicitly stated.
- sequence_note: any ordering instruction (e.g., "do after Abhyanga").
- Do NOT include Panchakarma procedures here; only physical exercises and yoga asanas.

Schema:
{
  "exercises": [
    {
      "name": "string",
      "category": "yoga | stretching | breathing | walking | swimming | general_exercise | null",
      "frequency": "string | null",
      "duration_minutes": "number | null",
      "repetitions": "string | null",
      "sequence_note": "string | null",
      "contraindication_note": "string | null",
      "remarks": "string | null",
      "needs_doctor_confirmation": ["string"]
    }
  ],
  "general_activity_advice": "string | null",
  "restrictions": ["string"],
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "detox_procedures": BASE_RULES + _SGP_PROCEDURE_KNOWLEDGE + """\
Extract detoxifying procedures, decoctions, home therapies, and SGP-specific protocols.

Rules:
- APPLY the SGP PROCEDURE CANONICAL NAME CORRECTION TABLE above to identify canonical procedure names.
- Capture each detox/decoction/home therapy as a separate item.
- Include: Gandusham, Nithya Virechana Process, Prathivaara Virechana Karma, Anutailam,
  Steam Inhalations, Fennel Tea, Barley Soup, Rice Soup, Tapioca Soup (Sabu Dana),
  Raagi Soup (Finger Millet), Jowar Soup, SGP Covid Protocol, hair/skin oil applications,
  and any other detox, cleanse or decoction procedure mentioned.
- Do NOT include classical Panchakarma procedures (Abhyanga, Basti, Shirodhara, etc.) here;
  those belong in the panchakarma section.
- quantity: e.g., "2 drops", "1-2 tablespoons", "1 litre".
- frequency: e.g., "daily", "twice a week", "once in a week".
- timing: e.g., "before bed", "morning empty stomach", "alternate days".

Schema:
{
  "detox_items": [
    {
      "name": "string",
      "category": "decoction | oil_application | nasal_drops | virechana | steam | protocol | other | null",
      "quantity": "string | null",
      "frequency": "string | null",
      "timing": "string | null",
      "instructions": "string | null",
      "indication": "string | null",
      "remarks": "string | null",
      "needs_doctor_confirmation": ["string"]
    }
  ],
  "overall_detox_notes": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "followup_details": BASE_RULES + """\
Extract follow-up and care continuity details.

Rules:
- Capture assigned doctor, follow-up doctor name and contact, next visit date/duration.
- If the doctor mentions different doctors for different purposes (e.g., assigned vs followup), capture separately.
- Do not infer doctor names from context; only use explicitly dictated names.
- next_visit_date: as spoken (e.g., "after 2 weeks", "15th March").
- followup_doc_contact: phone number or contact as spoken.

Schema:
{
  "assigned_doc": "string | null",
  "followup_doc_name": "string | null",
  "followup_doc_contact": "string | null",
  "followup_doc_department": "string | null",
  "next_visit_date": "string | null",
  "next_visit_duration": "string | null",
  "followup_instructions": "string | null",
  "referral_doctor": "string | null",
  "referral_reason": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,

    "prescription_sheet": BASE_RULES + """\
Extract or structure executive summaries for the clinical prescription sheet and patient report.

Rules:
- Capture quick reference summaries for allopathy medicines, panchakarma, tests to be done, and others.
- Capture daily regimen instructions for oil applications, detox procedures, home remedies, and breathing exercises.
- Capture duration-based diet plans (e.g. PPD 4 weeks, KPD 2 weeks).

Schema:
{
  "quick_summary": {
    "allopathy_medicines": "string | null",
    "panchakarma": "string | null",
    "tests_to_be_done": "string | null",
    "others": "string | null"
  },
  "daily_regimen": {
    "oil_applications": "string | null",
    "detox_procedures": "string | null",
    "home_remedies": "string | null",
    "breathing_exercises": "string | null"
  },
  "diet_plan_weeks": [
    {
      "diet_item": "string",
      "no_of_weeks": "string"
    }
  ],
  "review_after": "string | null",
  "needs_doctor_confirmation": ["string"]
}
""" + _SECTION_FOOTER,
}

# -----------------------------------------------------------------------------
# Quality prompts - operate on the complete draft JSON, not on a transcript
# -----------------------------------------------------------------------------

QUALITY_PROMPTS: dict[str, str] = {
    "missing_information_check": BASE_RULES + """\
You will receive the full case sheet draft JSON. Identify missing information needed for a complete clinical case sheet.

Rules:
- Do not invent missing values.
- Ask only questions relevant to information already suggested by the case.
- Prioritize critical safety and documentation gaps.
- If a symptom is present but site/duration/laterality/severity are missing, flag them.
- If medications are present but dose/frequency/route are missing, flag them.
- If treatment plan exists without follow-up, flag follow-up.
- Check that exercises_yoga, detox_procedures, and followup_details sections are present if treatment was prescribed.

Return JSON schema:
{
  "missing_critical": ["string"],
  "missing_recommended": ["string"],
  "doctor_questions": ["string"],
  "patient_questions": ["string"],
  "section_completion_score": {
    "chief_complaint": "complete | partial | missing",
    "history": "complete | partial | missing",
    "medication_history": "complete | partial | missing",
    "past_history": "complete | partial | missing",
    "examination": "complete | partial | missing",
    "assessment_plan": "complete | partial | missing",
    "exercises_yoga": "complete | partial | missing | not_applicable",
    "detox_procedures": "complete | partial | missing | not_applicable",
    "followup_details": "complete | partial | missing"
  }
}
""",

    "contradiction_check": BASE_RULES + """\
You will receive the full case sheet draft JSON. Identify contradictions, ambiguous items and data quality problems.

Check for:
- left/right/bilateral conflicts.
- duration conflicts.
- allergy conflicts (substance listed as both allergic and prescribed).
- same medicine repeated with different doses.
- male patient with pregnancy/gynecology entries.
- female patient with prostate entries.
- diagnosis/plan mismatch.
- unclear or garbled speech-to-text terms (flag as ambiguous).
- unsupported plan items not present in dictated assessment.
- non-medical characters or symbols appearing in clinical fields (flag as data quality issue).
- SGP medicine names that appear misspelled (not matching canonical names).

Return JSON schema:
{
  "contradictions": [
    {"issue": "string", "sections_involved": ["string"], "severity": "low | moderate | high", "needs_doctor_action": true}
  ],
  "ambiguous_items": ["string"],
  "duplicate_items": ["string"],
  "data_quality_issues": ["string"],
  "unsafe_or_sensitive_items_for_review": ["string"],
  "overall_quality_status": "ready_for_review | needs_completion | unsafe_until_review"
}
""",

    "red_flag_check": BASE_RULES + """\
You will receive the full case sheet draft JSON. Identify red flags that require clinician attention.

Rules:
- Do not diagnose.
- Only flag red flags explicitly present in the draft.
- Also flag critical red flag fields that were not asked/documented when relevant to the chief complaint.

Return JSON schema:
{
  "red_flags_present": ["string"],
  "red_flags_absent_documented": ["string"],
  "red_flags_not_documented_but_relevant": ["string"],
  "urgency_for_doctor_review": "routine | priority | urgent | emergency_possible",
  "reason": "string | null"
}
""",

    "json_repair": """\
You will receive invalid or partial JSON plus the required schema context. Repair it into valid JSON.
Rules:
- Preserve all clinical facts.
- Do not add new clinical facts.
- If uncertain, place content in _raw or needs_doctor_confirmation.
- Remove any non-ASCII, non-Latin, non-Sanskrit characters found in string values.
- Return only valid JSON.
""",
}

# -----------------------------------------------------------------------------
# Composer prompts - operate on complete draft JSON
# -----------------------------------------------------------------------------

COMPOSER_PROMPTS: dict[str, str] = {
    "final_case_sheet": BASE_RULES + """\
You will receive the full casesheet draft JSON produced from multiple dictation sections.
Create an elaborate, doctor-readable integrated case sheet for clinician review.

Strict rules:
- Do not invent clinical facts.
- Do not add a diagnosis unless it is explicitly present in the draft.
- Do not prescribe new treatment.
- Do not create new medicines, doses, investigations or Panchakarma procedures.
- If a field is missing, write "Not documented" in the markdown.
- If a field is unclear, write "Needs doctor confirmation".
- Preserve Ayurvedic, SGP and allopathic terms exactly.
- NEVER output internal field names (needs_doctor_confirmation, _raw, _error) as clinical text.
- NEVER output non-ASCII characters in any field.
- Output only valid JSON.

Return JSON schema:
{
  "case_sheet_markdown": "string",
  "case_sheet_summary": "string",
  "erp_field_summaries": {
    "chief_complaint": "string",
    "anamnesis": "string",
    "vpk_dominance": "string",
    "pulse_diagnosis": "string",
    "ayurvedic_diagnosis": "string",
    "allopathic_diagnosis": "string",
    "general_examination": "string",
    "systemic_examination": "string",
    "sgp_rx": "string",
    "allopathic_medicines": "string",
    "panchakarma": "string",
    "detox_procedures": "string",
    "exercises_yoga": "string",
    "home_remedies": "string",
    "diet_include": "string",
    "diet_exclude": "string",
    "lifestyle_advice": "string",
    "investigations_advised": "string",
    "personal_history_diet": "string",
    "personal_history_sleep": "string",
    "past_medical_history": "string",
    "family_history": "string",
    "allergies": "string",
    "follow_up": "string",
    "followup_doc": "string",
    "prognosis": "string",
    "notes": "string"
  },
  "missing_information": {
    "critical": ["string"],
    "recommended": ["string"],
    "doctor_questions": ["string"],
    "patient_questions": ["string"]
  },
  "contradictions_or_unclear_items": ["string"],
  "safety_red_flags": ["string"],
  "doctor_review_required": true
}

The case_sheet_markdown must use this section order:
1. Encounter Details
2. Chief Complaints
3. History of Present Illness / Anamnesis
4. Past Medical History
5. Surgical / Hospitalization History
6. Medication and Treatment History
7. Allergy History
8. Family History
9. Personal and Lifestyle History
10. Menstrual / Obstetric History (if applicable)
11. Vitals and Anthropometry
12. General Examination
13. Systemic Examination
14. Investigations and Reports
15. Pulse Diagnosis (Nadi Pariksha)
16. Ayurvedic Assessment
17. Allopathic / Integrated Assessment
18. Ayurvedic Supplements (SGP Rx)
19. Panchakarma / Purvakarma Therapies
20. Detoxifying Procedures and Decoctions
21. Exercises and Yoga
22. Diet Plan (Include / Exclude)
23. Lifestyle Advice
24. Follow-Up Details
25. Doctor Review Checklist
""",

    "doctor_review_summary": BASE_RULES + """\
You will receive the full casesheet draft JSON and/or final case sheet JSON.
Create a concise doctor review summary.

Return JSON schema:
{
  "one_line_summary": "string",
  "key_clinical_points": ["string"],
  "important_missing_items": ["string"],
  "items_to_confirm_before_approval": ["string"],
  "possible_safety_concerns": ["string"],
  "documentation_quality": "good | acceptable | incomplete | unsafe_until_review",
  "recommended_next_documentation_action": "string"
}
""",

    "patient_friendly_summary": BASE_RULES + """\
Create a patient-friendly summary from the final clinician-reviewed draft.

Rules:
- Do not give new medical advice.
- Use simple language.
- Include only plan items already documented.
- End with "Please follow your doctor's instructions." only if a plan is present.

Return JSON schema:
{
  "patient_summary": "string",
  "what_was_documented": ["string"],
  "doctor_advice_as_documented": ["string"],
  "follow_up_as_documented": "string | null",
  "caution": "string | null"
}
""",

    "erpnext_field_mapper": BASE_RULES + """\
You will receive the full draft JSON and the final case sheet JSON. Produce short summaries that fit the SGP Encounter ERP fields.

Rules:
- Use concise text for Small Text fields.
- Do not add facts.
- If a composer erp_field_summaries object already exists, clean it and ensure all keys exist.
- notes must contain a compact full case sheet plus JSON backup marker if possible.

Return JSON schema:
{
  "chief_complaint": "string",
  "anamnesis": "string",
  "height_cm": "number | null",
  "weight_kg": "number | null",
  "wrist_cm": "number | null",
  "waist_cm": "number | null",
  "fore_arm_cm": "number | null",
  "hip_cm": "number | null",
  "temp": "string",
  "bp": "string",
  "pr": "string",
  "rr": "string",
  "vpk_dominance": "string",
  "pulse_diagnosis": "string",
  "ayurvedic_diagnosis": "string",
  "allopathic_diagnosis": "string",
  "general_examination": "string",
  "systemic_examination": "string",
  "sgp_rx": "string",
  "allopathic_medicines": "string",
  "panchakarma": "string",
  "home_remedies": "string",
  "diet_include": "string",
  "diet_exclude": "string",
  "lifestyle_advice": "string",
  "investigations_advised": "string",
  "personal_history_diet": "string",
  "personal_history_sleep": "string",
  "past_medical_history": "string",
  "family_history": "string",
  "allergies": "string",
  "follow_up": "string",
  "prognosis": "string",
  "notes": "string"
}
""",
}

# -----------------------------------------------------------------------------
# Token budgets
# -----------------------------------------------------------------------------

SECTION_MAX_TOKENS: dict[str, int] = {
    "patient_identity": 1200,
    "encounter_context": 1200,
    "transcript_cleanup": 3000,
    "chief_complaint": 1800,
    "anamnesis": 2500,
    "pulse_diagnosis": 2500,
    "ayurvedic_assessment_extended": 2500,
    "ayurvedic_supplements": 3000,
    "panchakarma": 3000,
    "exercises_yoga": 2000,
    "detox_procedures": 2500,
    "followup_details": 1500,
    "prescription_sheet": 2500,
    "treatment_and_background": 2500,
    "medication_history": 3000,
    "past_medical_history": 2500,
    "surgical_history": 2200,
    "allergy_history": 2000,
    "family_history_detailed": 2000,
    "personal_history": 2200,
    "menstrual_obstetric_history": 2000,
    "vitals_anthropometry": 1800,
    "general_examination": 2200,
    "systemic_examination": 2500,
    "investigation_reports": 3000,
    "assessment_and_plan": 3500,
    "missing_information_check": 3000,
    "contradiction_check": 3000,
    "red_flag_check": 2500,
    "final_case_sheet": 8000,
    "doctor_review_summary": 2500,
    "patient_friendly_summary": 2500,
    "erpnext_field_mapper": 3500,
}

# -----------------------------------------------------------------------------
# UI / orchestration metadata
# -----------------------------------------------------------------------------

DISPLAY_SECTION_ORDER: list[str] = [
    "patient_identity",
    "encounter_context",
    "followup_details",
    "chief_complaint",
    "anamnesis",
    "past_medical_history",
    "surgical_history",
    "medication_history",
    "allergy_history",
    "family_history_detailed",
    "personal_history",
    "menstrual_obstetric_history",
    "vitals_anthropometry",
    "general_examination",
    "systemic_examination",
    "investigation_reports",
    "pulse_diagnosis",
    "ayurvedic_assessment_extended",
    "ayurvedic_supplements",
    "panchakarma",
    "detox_procedures",
    "exercises_yoga",
    "treatment_and_background",
    "assessment_and_plan",
    "prescription_sheet",
]

COMPOSER_SECTIONS = set(COMPOSER_PROMPTS.keys())
QUALITY_SECTIONS = set(QUALITY_PROMPTS.keys())
VALID_SECTIONS = set(SECTION_PROMPTS.keys())

# These are not audio sections; they operate on the entire draft.
NON_AUDIO_SECTIONS = COMPOSER_SECTIONS | QUALITY_SECTIONS

ERP_FIELD_MAPPING_RULES: dict[str, str] = {
    "chief_complaint": "Use chief_complaint.summary or final_case_sheet.erp_field_summaries.chief_complaint.",
    "anamnesis": "Use anamnesis.summary plus symptom timeline.",
    "height_cm": "Use vitals_anthropometry.height_cm.",
    "weight_kg": "Use vitals_anthropometry.weight_kg.",
    "wrist_cm": "Use vitals_anthropometry.wrist_cm.",
    "waist_cm": "Use vitals_anthropometry.waist_cm.",
    "fore_arm_cm": "Use vitals_anthropometry.fore_arm_cm.",
    "hip_cm": "Use vitals_anthropometry.hip_cm.",
    "temp": "Use vitals_anthropometry.temperature.",
    "bp": "Use vitals_anthropometry.bp.",
    "pr": "Use vitals_anthropometry.pulse_rate.",
    "rr": "Use vitals_anthropometry.respiratory_rate.",
    "vpk_dominance": "Use pulse_diagnosis.overall_vpk.dominance or ayurvedic_assessment_extended.vpk_dominance.",
    "pulse_diagnosis": "Summarize pulse_diagnosis.systems (each system row: system code, vata/pitta/kapha severity) and overall_vpk.",
    "ayurvedic_diagnosis": "Use assessment_and_plan.ayurvedic_diagnosis or ayurvedic_assessment_extended.ayurvedic_diagnosis.",
    "allopathic_diagnosis": "Join assessment_and_plan.allopathic_diagnosis.",
    "general_examination": "Compact summary of general_examination findings (built, nourishment, orientation, clinical signs).",
    "systemic_examination": "Compact summary of systemic_examination fields only. Do not include needs_doctor_confirmation text.",
    "sgp_rx": "Summarize ayurvedic_supplements: each medicine name with start_week, dose, frequency.",
    "allopathic_medicines": "Summarize treatment_and_background.current_medications and medication_history allopathic medicines.",
    "panchakarma": "Summarize panchakarma.sessions with procedure, session_count, status, oils.",
    "detox_procedures": "Summarize detox_procedures.detox_items with name, quantity, frequency, timing.",
    "exercises_yoga": "Summarize exercises_yoga.exercises with name, frequency, remarks.",
    "home_remedies": "Use assessment_and_plan.plan.home_remedies.",
    "diet_include": "Use assessment_and_plan.plan.diet_advice.include.",
    "diet_exclude": "Use assessment_and_plan.plan.diet_advice.exclude.",
    "lifestyle_advice": "Use assessment_and_plan.plan.lifestyle_advice.",
    "investigations_advised": "Use investigation_reports.investigations_advised and assessment_and_plan.plan.investigations.",
    "personal_history_diet": "Use personal_history.diet and personal_history.diet_details.",
    "personal_history_sleep": "Use personal_history.sleep_hours or personal_history.sleep_quality.",
    "past_medical_history": "Use past_medical_history.medical.",
    "family_history": "Use family_history_detailed.family_conditions or past_medical_history.family_history.",
    "allergies": "Use allergy_history.allergies plus past_medical_history.allergies.",
    "follow_up": "Use assessment_and_plan.plan.follow_up. Also check followup_details.next_visit_date.",
    "followup_doc": "Use followup_details.followup_doc_name and followup_details.followup_doc_contact.",
    "prognosis": "Use assessment_and_plan.prognosis.",
    "notes": "Store final_case_sheet.case_sheet_markdown plus full JSON backup.",
}

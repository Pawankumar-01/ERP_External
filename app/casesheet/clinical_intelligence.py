"""
Clinical Extraction Intelligence (CEI)
=====================================
Deterministic, offline reasoning layer for the AI casesheet engine.

The LLM speaks to the doctor's words; this module guarantees the *schema* and
the *canonical vocabulary* regardless of how those words were phrased. It is a
zero-cost safety net that runs on every extraction:

  1. normalize_value()   - units, dosing shorthand, punctuation, severity.
  2. sanitize_section()  - guarantee the section JSON shape: strip internal
                           keys (_error, _raw, _reprompt), clamp enum fields,
                           coerce numeric types, dedupe lists.
  3. merge_section_data() - deep-merge a newly extracted section into the
                            existing draft *instead of overwriting* it, so
                            segmented/partial dictation is never lost.
  4. build_variant_examples() - few-shot "the doctor may say X / Y / Z but the
                            canonical fact is VALUE" blocks embedded into the
                            prompts, so the model learns to map *many phrasings*
                            onto *one* stable structured fact.

No network / LLM calls here -- the module is unit-testable offline.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.config.settings import settings

# ---------------------------------------------------------------------------
# Canonical vocabulary -- medicine & dose aliases
# ---------------------------------------------------------------------------

# Spoken / ASR-mutated variants -> canonical SGP proprietary medicine names.
SGP_MEDICINE_ALIASES: Dict[str, str] = {
    "apd": "APD",
    "atherolyzin": "ATHEROLYZIN",
    "atherlyzin": "ATHEROLYZIN",
    "aterolysin": "ATHEROLYZIN",
    "ethylizine": "ATHEROLYZIN",
    "ecylizine": "ATHEROLYZIN",
    "migranone": "MIGRANONE",
    "migranine": "MIGRANONE",
    "migraine": "MIGRANONE",
    "migraine no": "MIGRANONE",
    "imumodulin": "IMUMODULIN",
    "immunodulin": "IMUMODULIN",
    "imodulin": "IMUMODULIN",
    "biotin": "BIOTIN",
    "allowyn": "ALLOWYN",
    "allow in": "ALLOWYN",
    "allowing": "ALLOWYN",
    "alloein": "ALLOWYN",
    "syngen": "SYNGEN",
    "singin": "SYNGEN",
    "sing en": "SYNGEN",
    "syngene": "SYNGEN",
    "d-tox": "D-TOX",
    "dtox": "D-TOX",
    "detox": "D-TOX",
    "d tox": "D-TOX",
    "neurotropin": "NEUROTROPIN",
    "neuro tropin": "NEUROTROPIN",
    "neuratropine": "NEUROTROPIN",
    "neurotropine": "NEUROTROPIN",
    "cag nuts": "CAG Nuts",
    "cag nats": "CAG Nuts",
    "reserve": "RESERVE",
    "cissues": "CISSUES",
    "tissues": "CISSUES",
    "quadrangularies": "QUADRANGULARIES",
    "quadrangularis": "QUADRANGULARIES",
}

# ---------------------------------------------------------------------------
# Protocol / Procedure canonical taxonomy (8 categories, clinic ground truth)
# ---------------------------------------------------------------------------
CAT_PROCEDURES = "Procedures"
CAT_OILS = "Oil Applications"
CAT_EXERCISES = "Yoga / Exercises"
CAT_BREATHING = "Breathing"
CAT_DETOX = "Detox / Panchakarma"
CAT_DIET = "Diet / Food"
CAT_COMPLIANCE = "Compliance / Patient Notes"
CAT_OTHER = "Other / Clinical Notes"

PROTOCOL_CATEGORIES = [
    CAT_PROCEDURES, CAT_OILS, CAT_EXERCISES, CAT_BREATHING,
    CAT_DETOX, CAT_DIET, CAT_COMPLIANCE, CAT_OTHER,
]

# canonical -> {category, aliases, instructions (clinic template; "" = doctor
# words only; NEVER fabricate instructions for entries without a template).}
PROTOCOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # -- Procedures (in-clinic) --
    "Abhyanga": {"category": CAT_PROCEDURES, "aliases": ["abhyanga", "abhyangam", "full body massage"], "instructions": ""},
    "Kati Vasti": {"category": CAT_PROCEDURES, "aliases": ["kati vasthi", "kati basti", "lumbar basti", "kativasti", "kativasthi"], "instructions": ""},
    "Janu Vasti": {"category": CAT_PROCEDURES, "aliases": ["januvasthi", "januvasti", "janu basti", "knee vasti"], "instructions": ""},
    "Greeva Vasti": {"category": CAT_PROCEDURES, "aliases": ["greeva vasthi", "greeva basti", "griva vasti", "griever vasthi", "griever vasti", "neck basti"], "instructions": ""},
    "Pichu": {"category": CAT_PROCEDURES, "aliases": ["pichu", "jhanu pichu", "janu pichu"], "instructions": ""},
    "Shirodhara": {"category": CAT_PROCEDURES, "aliases": ["shirodhara", "shiro dhara", "sirodhara"], "instructions": ""},
    "Nadi Swedhana": {"category": CAT_PROCEDURES, "aliases": ["nadi swedana", "nadi swedanam"], "instructions": ""},
    "Navara Lepanam": {"category": CAT_PROCEDURES, "aliases": ["njavara lepanam", "njavara kizhi", "navara kizhi", "njavara", "shashtika lepa"], "instructions": ""},
    "Takradhara": {"category": CAT_PROCEDURES, "aliases": ["takra dhara", "thakradhara"], "instructions": ""},
    "Swedana": {"category": CAT_PROCEDURES, "aliases": ["swedanam", "savana", "savanna", "steam bath", "sauna"], "instructions": ""},
    "Hot Pack": {"category": CAT_PROCEDURES, "aliases": ["hot pack", "hot fomentation"], "instructions": ""},
    "Cold Pack": {"category": CAT_PROCEDURES, "aliases": ["cold pack", "ice pack"], "instructions": ""},
    "Nasya": {"category": CAT_PROCEDURES, "aliases": ["nasal drops therapy"], "instructions": ""},
    "Virechana": {"category": CAT_PROCEDURES, "aliases": ["virechana", "virechan", "therapeutic purgation", "purgation therapy"], "instructions": ""},
    "Vamana": {"category": CAT_PROCEDURES, "aliases": ["vamana", "therapeutic emesis", "emesis therapy"], "instructions": ""},
    "Basti (General)": {"category": CAT_PROCEDURES, "aliases": ["basti therapy", "vasti therapy", "general basti"], "instructions": ""},
    "Anuvasana Basti": {"category": CAT_PROCEDURES, "aliases": ["anuvasana basti", "sneha basti"], "instructions": ""},
    "Niruha Basti": {"category": CAT_PROCEDURES, "aliases": ["niruha basti", "kashaya basti"], "instructions": ""},
    "Uttara Basti": {"category": CAT_PROCEDURES, "aliases": ["uttara basti"], "instructions": ""},
    "Matra Basti": {"category": CAT_PROCEDURES, "aliases": ["matra basti"], "instructions": ""},
    # -- Oil Applications --
    "Neelibhringadi": {"category": CAT_OILS, "aliases": ["neeli bringadi", "neelibhringadi keera tailam", "keera tailam"], "instructions": "Apply over the scalp on every alternate day."},
    "Nutex Oil": {"category": CAT_OILS, "aliases": ["nutex"], "instructions": ""},
    "Chandanadi Thailam": {"category": CAT_OILS, "aliases": ["chandanadi thailam", "chandanadi tailam"], "instructions": ""},
    "Pinda Tailam": {"category": CAT_OILS, "aliases": ["pinda thailam"], "instructions": ""},
    "Murivenna": {"category": CAT_OILS, "aliases": ["murivenna oil"], "instructions": ""},
    "Erand Tailam (Castor Oil)": {"category": CAT_OILS, "aliases": ["erand tailam", "erand thailam", "castor oil"], "instructions": ""},
    # -- Yoga / Exercises --
    "Surya Namaskar": {"category": CAT_EXERCISES, "aliases": ["suryanamaskaram", "suryanamaskar", "surya namas"], "instructions": ""},
    "Naukasana": {"category": CAT_EXERCISES, "aliases": ["naukasanam", "naukasam", "boat pose"], "instructions": "For back/spine issues, only Naukasana is advised."},
    "Bhujangasana": {"category": CAT_EXERCISES, "aliases": ["bhujangasanam", "cobra pose"], "instructions": ""},
    "Kegel Exercises": {"category": CAT_EXERCISES, "aliases": ["kegel exercise", "pelvic floor exercise"], "instructions": ""},
    "Walking": {"category": CAT_EXERCISES, "aliases": ["morning walk", "evening walk"], "instructions": ""},
    "Leg Exercises": {"category": CAT_EXERCISES, "aliases": ["leg exercise", "leg raising", "leg raises"], "instructions": ""},
    "Hip Rotation": {"category": CAT_EXERCISES, "aliases": ["hip exercise"], "instructions": ""},
    "Stretching": {"category": CAT_EXERCISES, "aliases": ["stretches", "stretch"], "instructions": ""},
    "Gym": {"category": CAT_EXERCISES, "aliases": ["weight training", "strength training"], "instructions": ""},
    "Knee Strengthening": {"category": CAT_EXERCISES, "aliases": ["knee exercise", "quadriceps exercise"], "instructions": ""},
    # -- Breathing --
    "DNB (Differential Nostril Breathing)": {"category": CAT_BREATHING, "aliases": ["dnb", "differential nostril breathing", "alternate nostril breathing", "anulom vilom"], "instructions": "1:4:2 ratio (inhale 1s, hold 4s, exhale 2s), 10 mins before and after bed."},
    "R-DNB": {"category": CAT_BREATHING, "aliases": ["rdnb", "r dnb"], "instructions": ""},
    "Reverse DNB": {"category": CAT_BREATHING, "aliases": ["reverse breathing"], "instructions": ""},
    # -- Detox / Panchakarma (home routines) --
    "Nitya Virechana (NVK)": {"category": CAT_DETOX, "aliases": ["nvk", "nithya virechana", "daily virechana", "nitya virechana karma"], "instructions": "At bedtime (2 hrs after dinner): warm water + 1-2 lemons + 2 pinch black salt, then Erand Tailam. Water ml = weight(kg)x10; Oil ml = weight(kg)/3."},
    "Prativaara Virechana (PVVK)": {"category": CAT_DETOX, "aliases": ["pvvk", "prathivaara virechana", "weekly virechana", "once a week virechana"], "instructions": "Once a week, same method as Nitya Virechana."},
    "D-Tox": {"category": CAT_DETOX, "aliases": ["dtox", "d tox"], "instructions": ""},
    "Gandusham": {"category": CAT_DETOX, "aliases": ["gandusha", "gandoosha", "oil pulling"], "instructions": "Swish 1-2 tbsp sesame oil 10-20 mins, do not swallow, rinse thoroughly."},
    "Anutailam": {"category": CAT_DETOX, "aliases": ["anu tailam", "anu taila", "nasya drops"], "instructions": "2 drops in each nostril and ears."},
    "Steam Inhalations": {"category": CAT_DETOX, "aliases": ["steam inhalation", "turmeric steam"], "instructions": "Steam with 1 tsp turmeric; Zandu balm on nose/temples/throat/chest; inhale 10 mins."},
    # -- Diet / Food --
    "Fennel Water": {"category": CAT_DIET, "aliases": ["fennel tea", "fennel", "saunf water", "somph water"], "instructions": "9 tsp fennel seeds in 2 L water, boil 5-10 mins, add 50-75 gms jaggery; drink through the day."},
    "CCRSTT (Avoid)": {"category": CAT_DIET, "aliases": ["avoid ccrstt"], "instructions": "Avoid Cabbage, Cauliflower, Radish, Spinach, Tamarind, Tomato."},
    "Coriander Milk": {"category": CAT_DIET, "aliases": [], "instructions": ""},
    "Ginger Tea": {"category": CAT_DIET, "aliases": [], "instructions": ""},
    "Curd Rice": {"category": CAT_DIET, "aliases": ["thayir sadam"], "instructions": ""},
    "Barley Soup": {"category": CAT_DIET, "aliases": ["barley water", "barley"], "instructions": "Grind 2 tsp barley, soak 5-10 mins, boil in 1/2 L water; drink same day."},
    "Rice Soup": {"category": CAT_DIET, "aliases": ["rice water", "kanjee", "kanji"], "instructions": "150 gms rice in 5 cups water, simmer to 3 cups; rock salt; same day."},
    "Tapioca Soup (Sabu Dana)": {"category": CAT_DIET, "aliases": ["tapioca", "sabu dana", "sabudana"], "instructions": "Soak 2 tsp, cook in 1/2 L boiled water 5 mins; rock salt; same day."},
    "Raagi Soup": {"category": CAT_DIET, "aliases": ["ragi soup", "ragi", "finger millet"], "instructions": "Kapha diet only."},
    "Jowar Soup": {"category": CAT_DIET, "aliases": ["sorghum"], "instructions": "Kapha diet only."},
}

# Common allopathic medicines -- spelling variants -> canonical.
COMMON_MEDICINE_ALIASES: Dict[str, str] = {
    "telmesartan": "Telmisartan",
    "telma": "Telmisartan",
    "telmisarta": "Telmisartan",
    "losartan": "Losartan",
    "losartran": "Losartan",
    "pantoprazole": "Pantoprazole",
    "pantaprazole": "Pantoprazole",
    "pantocid": "Pantoprazole",
    "omeprazole": "Omeprazole",
    "emeprazole": "Omeprazole",
    "metformin": "Metformin",
    "metformine": "Metformin",
    "glyciphage": "Metformin",
    "amlodipine": "Amlodipine",
    "amlopres": "Amlodipine",
    "amlodipin": "Amlodipine",
    "atorvastatin": "Atorvastatin",
    "atorvastatine": "Atorvastatin",
    "rosuvastatin": "Rosuvastatin",
    "rosuvastatine": "Rosuvastatin",
    "aspirin": "Aspirin",
    "ecosprin": "Aspirin",
    "clopidogrel": "Clopidogrel",
    "clopitab": "Clopidogrel",
    "thyroxine": "Thyroxine",
    "thyronorm": "Thyroxine",
    "pregabalin": "Pregabalin",
    "gabapentin": "Gabapentin",
    "duloxetine": "Duloxetine",
    "paracetamol": "Paracetamol",
    "dolo": "Paracetamol",
    "ibuprofen": "Ibuprofen",
    "diclofenac": "Diclofenac",
    "etoricoxib": "Etoricoxib",
    "voveran": "Diclofenac",
    "vitamin d": "Vitamin D",
    "vitamin b12": "Vitamin B12",
    "calcium": "Calcium",
}

# Dosing / frequency shorthand -> canonical readable form.
DOSING_ALIASES: Dict[str, str] = {
    "od": "once daily",
    "once daily": "once daily",
    "bid": "twice daily",
    "b d": "twice daily",
    "b.d": "twice daily",
    "tid": "thrice daily",
    "tds": "thrice daily",
    "t.d.s": "thrice daily",
    "qid": "four times daily",
    "q.i.d": "four times daily",
    "hs": "at bedtime",
    "qhs": "at bedtime",
    "sos": "as required",
    "prn": "as required",
    "a.c": "before food",
    "ac": "before food",
    "p.c": "after food",
    "pc": "after food",
    "empty stomach": "empty stomach",
    "before food": "before food",
    "after food": "after food",
    "morning": "morning",
    "evening": "evening",
    "night": "night",
    "weekly": "weekly",
    "daily": "daily",
}

# Spoken VPK system aliases -> canonical code.
PULSE_SYSTEM_ALIASES: Dict[str, str] = {
    "lisi": "LISI",
    "li si": "LISI",
    "large intestine small intestine": "LISI",
    "cvs": "CVS",
    "heart": "CVS",
    "rb": "RB",
    "renal bladder": "RB",
    "kidney bladder": "RB",
    "git": "GIT",
    "gastrointestinal": "GIT",
    "is": "IS",
    "immune": "IS",
    "immune system": "IS",
    "pan": "PAN",
    "pancreas": "PAN",
    "pancreatic": "PAN",
    "pro": "PRO",
    "prostate": "PRO",
    "prostate reproductive": "PRO",
    "lb": "LB",
    "lungs": "LB",
    "lower back": "LB",
    "gb": "GB",
    "gallbladder": "GB",
    "gall bladder": "GB",
    "liv": "LIV",
    "liver": "LIV",
    "rt": "RT",
    "respiratory tract": "RT",
    "ss": "SS",
    "skeletal": "SS",
    "skeletal system": "SS",
    "lscs": "LSCS",
    "lumbo sacro cranial": "LSCS",
    "obg": "OBG",
    "obstetrics gynecology": "OBG",
    "li": "LI",
    "l i": "LI",
    "large intestine": "LI",
    "si": "SI",
    "s i": "SI",
    "small intestine": "SI",
    "c v s": "CVS",
    "r b": "RB",
    "g b": "GB",
    "r t": "RT",
    "s s": "SS",
    "l v": "LIV",
    "i s e": "IS",
    "ise": "IS",
    "iscs": "LSCS",
}

PULSE_SYSTEM_CODES = frozenset({
    "LI", "SI", "LISI", "CVS", "RB", "GIT", "IS", "PAN", "PRO",
    "LB", "GB", "LIV", "RT", "SS", "KUB", "LSCS", "OBG",
})

# Severity spoken variance -> canonical severity token (as used by pulse JSON).
SEVERITY_ALIASES: Dict[str, str] = {
    "very mild": "very_mild",
    "verymild": "very_mild",
    "very-mild": "very_mild",
    "low": "very_mild",
    "minimal": "very_mild",
    "trace": "very_mild",
    "slight": "mild",
    "mild": "mild",
    "mild to moderate": "mild_moderate",
    "mild moderate": "mild_moderate",
    "mod": "moderate",
    "moderate": "moderate",
    "moderate to severe": "moderate_severe",
    "moderate severe": "moderate_severe",
    "moderate +": "moderate",
    "severe": "severe",
    "high": "severe",
    "marked": "severe",
    "pronounced": "severe",
}

# Compound dosha shorthand -> the individual doshas the severity applies to.
DOSHA_COMPOUNDS: Dict[str, List[str]] = {
    "pv": ["pitta", "vata"],
    "vp": ["vata", "pitta"],
    "vk": ["vata", "kapha"],
    "kv": ["kapha", "vata"],
    "pk": ["pitta", "kapha"],
    "kp": ["kapha", "pitta"],
    "vpk": ["vata", "pitta", "kapha"],
    "kvp": ["kapha", "vata", "pitta"],
}
SINGLE_DOSHA = {"v": "vata", "p": "pitta", "k": "kapha"}

# Internal bookkeeping keys the model uses that must NEVER reach clinical text.
_INTERNAL_KEYS = {
    "_error", "_raw", "_raw_draft", "_llm_output", "_reprompt", "_image",
    "_debug", "needs_doctor_confirmation", "needs_clarification",
}

# Key used to signal the frontend that the LLM wants the doctor to confirm data
# without polluting any clinical field pushed to the ERP.
CLARIFICATION_FLAG = "_needs_clarification"

# Section wrapper keys that carry UI/metadata (not clinical content).
_METADATA_KEYS = {"status", "updated_at", "images", "data", "imgs"}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _clean(value: str) -> str:
    """Collapse multiple spaces / tabs / newlines to a single space."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (set, tuple)):
        return list(value)
    return [value]


def _to_number(value: Any) -> Any:
    """Coerce a string/int/float to a plain float, or None if not numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = _clean(value).replace(",", "").replace("cm", "").replace("kg", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def normalize_medicine_name(name: str) -> str:
    """Best-effort canonical medicine name lookup (also fixes ASR variants)."""
    raw = _clean(name)
    if not raw:
        return raw
    lookup = raw.strip().lower().strip(".")
    canonical = SGP_MEDICINE_ALIASES.get(lookup)
    if not canonical:
        canonical = COMMON_MEDICINE_ALIASES.get(lookup)
    return canonical or raw


def normalize_frequency(text: str) -> str:
    """Expand dosing shorthand inside arbitrary free text."""
    out = []
    for token in re.split(r"[,;]+", _clean(text)):
        tok = token.strip().strip(".")
        low = tok.lower()
        if low in DOSING_ALIASES:
            out.append(DOSING_ALIASES[low])
        else:
            out.append(tok)
    return ", ".join(o for o in out if o)


def normalize_severity(value: Any) -> Optional[str]:
    """Map any spoken severity phrase to the canonical pulse severity token."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value <= 1:
            return "very_mild"
        if value <= 2:
            return "mild"
        if value <= 3:
            return "moderate"
        return "severe"
    low = _clean(value).lower().strip()
    if not low:
        return None
    return SEVERITY_ALIASES.get(low, low.replace(" ", "_").replace("-", "_"))


def normalize_system_code(value: Any) -> Optional[str]:
    """Map spoken system alias -> canonical pulse system code (or None)."""
    if value is None:
        return None
    low = _clean(value).lower().strip().rstrip(".:")
    if not low:
        return None
    canonical = PULSE_SYSTEM_ALIASES.get(low) or low.upper()
    return canonical if canonical in PULSE_SYSTEM_CODES else None


def expand_dosha_shorthand(code: Any) -> Dict[str, str]:
    """
    Expand compound dosha shorthand into {dosha: raw_code} entries.
    e.g. 'PV' -> {'pitta': 'PV', 'vata': 'PV'}; 'V' -> {'vata': 'V'}.
    Returns an empty dict when nothing recognizable is found.
    """
    if code is None:
        return {}
    low = _clean(code).lower().replace(" ", "").strip()
    if len(low) == 1 and low in SINGLE_DOSHA:
        return {SINGLE_DOSHA[low]: code.upper()}
    if low in DOSHA_COMPOUNDS:
        return {d: low.upper() for d in DOSHA_COMPOUNDS[low]}
    return {}


def normalize_bp(value: Any) -> Optional[str]:
    """Normalize a blood-pressure reading to 'SYS/DIA' canonical form."""
    if value is None:
        return None
    low = _clean(value).lower()
    m = re.search(r"(\d{2,3})\s*[/|]\s*(\d{2,3})", low)
    if m:
        return f"{int(m.group(1))}/{int(m.group(2))}"
    m = re.search(r"(\d{2,3})\s+over\s+(\d{2,3})", low)
    if m:
        return f"{int(m.group(1))}/{int(m.group(2))}"
    if low in ("normal", "normotensive", "bp normal"):
        return "Normal"
    if low in ("high", "hypertensive", "elevated", "bp high", "raised"):
        return "Elevated"
    if low in ("low", "hypotensive"):
        return "Low"
    return _clean(value) or None


# ---------------------------------------------------------------------------
# Section schema registry (validation contract per draft section)
# Type tags:  "str" | "num" | "bp" | "str_list" | "str_dict_list" |
#             "supplement_list" | "pulse_systems" | "free"
# ---------------------------------------------------------------------------

SECTION_SCHEMA: Dict[str, Dict[str, Any]] = {
    "patient_identity": {
        "patient_name": "str", "age": "num", "gender": "str",
        "phone": "str", "patient_id": "str", "op_number": "str",
    },
    "encounter_context": {"visit_type": "str", "notes": "str"},
    "chief_complaint": {
        "summary": "str", "complaints": "str_dict_list",
        "_item_keys": {"complaint", "ayurvedic_name", "duration", "site",
                       "laterality", "severity", "onset", "pain_grade"},
    },
    "anamnesis": {"summary": "str", "timeline": "str", "notes": "str"},
    "medication_history": {"current_medicines": "str_dict_list",
                           "summary": "str",
                           "_item_keys": {"name", "dosage", "frequency",
                                          "duration", "instructions",
                                          "raw"}},
    "past_medical_history": {"conditions": "str_dict_list", "summary": "str",
                             "_item_keys": {"condition", "duration", "status",
                                            "ayurvedic_name", "note"}},
    "surgical_history": {"surgeries": "str_dict_list", "summary": "str",
                         "_item_keys": {"surgery", "year", "duration",
                                        "hospital", "complications"}},
    "allergy_history": {"allergies": "str_list", "summary": "str"},
    "family_history_detailed": {"conditions": "str_dict_list", "summary": "str",
                                "_item_keys": {"relation", "condition",
                                               "ayurvedic_name", "note"}},
    "personal_history": {
        "diet": "str", "appetite": "str", "bowel": "str",
        "sleep_hours": "str", "sleep_quality": "str", "occupation": "str",
        "stress": "str", "exercise": "str", "addictions": "str_dict_list",
        "_item_keys": {"substance", "duration", "quantity", "status",
                       "remarks"},
    },
    "menstrual_obstetric_history": {
        "lmp": "str", "cycle_length": "str", "regular": "str",
        "pregnancy_history": "str", "parturition": "str", "notes": "str",
    },
    "followup_details": {"next_visit": "str", "followup_notes": "str",
                         "advised_after": "str"},
    "vitals_anthropometry": {
        "height_cm": "num", "weight_kg": "num", "bp": "bp",
        "pulse_rate": "str", "temperature": "str",
        "wrist_cm": "num", "waist_cm": "num", "fore_arm_cm": "num",
        "hip_cm": "num", "spo2": "num", "bmi": "num",
    },
    "general_examination": {
        "built": "str", "nourishment": "str", "pallor": "str",
        "icterus": "str", "edema": "str", "orientation": "str",
        "cyanosis": "str", "clubbing": "str", "other": "str",
    },
    "systemic_examination": {
        "cardiovascular": "str", "respiratory": "str", "abdomen": "str",
        "nervous_system": "str", "musculoskeletal": "str",
        "local_examination": "str", "summary": "str",
    },
    "investigation_reports": {
        "reports_reviewed": "str_list", "key_findings": "str_list",
        "investigations_advised": "str_list", "summary": "str",
    },
    "pulse_diagnosis": {"overall_vpk": "free", "systems": "pulse_systems",
                        "summary": "str"},
    "ayurvedic_assessment_extended": {
        "prakriti": "str", "vikriti": "str", "dominance": "str",
        "samprapti": "str", "notes": "str", "summary": "str",
    },
    "ayurvedic_supplements": {
        "list": "supplement_list", "supplements": "supplement_list",
        "medicines": "supplement_list",
        "_item_keys": {"name", "dosage", "quantity", "frequency", "duration",
                       "start_week", "instructions", "remarks", "timing",
                       "weeks", "department"},
    },
    "panchakarma": {
        "sessions": "supplement_list", "summary": "str",
        "_item_keys": {"name", "details", "frequency", "duration",
                       "instructions", "remarks", "quantity"},
    },
    "detox_procedures": {
        "detox_items": "supplement_list", "procedures": "supplement_list",
        "summary": "str",
        "_item_keys": {"name", "details", "frequency", "duration",
                       "instructions", "remarks", "quantity"},
    },
    "exercises_yoga": {
        "exercises": "supplement_list", "summary": "str",
        "_item_keys": {"name", "repetitions", "frequency", "duration",
                       "instructions", "remarks", "quantity"},
    },
    "treatment_and_background": {"goals": "str_list", "background": "str",
                                 "education": "str", "notes": "str"},
    "assessment_and_plan": {
        "diagnosis": "str", "summary": "str", "plan": "free",
        "follow_up": "str", "advice": "str_list",
    },
    "prescription_sheet": {"medicines": "str_dict_list", "diet_plan": "free",
                           "summary": "str",
                           "_item_keys": {"name", "dosage", "frequency",
                                          "duration", "instructions",
                                          "remarks", "timing"}},
}

# Item keys allowed inside each entry of a str_dict_list / supplement_list.
_DEFAULT_ITEM_KEYS = {"name", "details", "notes", "remarks", "instructions"}


def _coerce_scalar(value: Any, tag: str) -> Any:
    """Coerce a single value to its schema-type."""
    if _is_empty(value):
        return None
    if tag == "str":
        return _clean(value) or None
    if tag == "num":
        num = _to_number(value)
        return int(num) if num is not None and num.is_integer() else num
    if tag == "bp":
        return normalize_bp(value)
    return value


def _sanitize_pulse_systems(systems: Any) -> List[Dict[str, Any]]:
    """Normalize pulse_diagnosis.systems entries (codes + severity vocab)."""
    if not isinstance(systems, list):
        return []
    by_system: Dict[str, Dict[str, Any]] = {}
    for entry in systems:
        if not isinstance(entry, dict):
            continue
        normalized: Dict[str, Any] = {}
        code = normalize_system_code(entry.get("system"))
        if not code:
            continue
        normalized["system"] = code
        expanded = expand_dosha_shorthand(entry.get("dosha_shorthand")
                                          or entry.get("raw_phrase")
                                          or entry.get("system"))
        severity_src = entry.get("severity") or entry.get("vpk") or None
        for dosha in ("vata", "pitta", "kapha"):
            val = entry.get(dosha)
            if _is_empty(val) and expanded:
                val = expanded.get(dosha)
            normalized[dosha] = normalize_severity(
                val if not _is_empty(val) else severity_src
            )
        raw = entry.get("raw_phrase")
        if raw:
            normalized["raw_phrase"] = _clean(raw)
        existing = by_system.get(code)
        if existing is None:
            by_system[code] = normalized
        else:
            for dosha in ("vata", "pitta", "kapha"):
                if existing.get(dosha) is None and normalized.get(dosha) is not None:
                    existing[dosha] = normalized[dosha]
            if not existing.get("raw_phrase") and normalized.get("raw_phrase"):
                existing["raw_phrase"] = normalized["raw_phrase"]
    return list(by_system.values())


_PULSE_CODES = (
    "LISI", "LSCS", "CVS", "GIT", "KUB", "PAN", "PRO", "LIV",
    "OBG", "RB", "IS", "LB", "GB", "RT", "SS", "LI", "SI",
)
_PULSE_SEVERITY = (
    r"very\s+mild|mild\s+(?:to\s+)?moderate|"
    r"moderate\s+(?:to\s+)?severe|mild|moderate|severe|low"
)
_PULSE_DOSHA = r"VPK|PV|VP|VK|KV|PK|KP|V|P|K"
_PULSE_PAIR_RE = re.compile(
    rf"(?:"
    rf"(?P<severity>{_PULSE_SEVERITY})\s*(?:,|\band\b)?\s*(?P<dosha>{_PULSE_DOSHA})\b"
    rf"|(?P<reverse_dosha>{_PULSE_DOSHA})\s*(?:,|\bis\b)?\s*(?P<reverse_severity>{_PULSE_SEVERITY})\b"
    rf")",
    re.IGNORECASE,
)


def _pulse_doshas(value: str) -> List[str]:
    token = re.sub(r"[^vpk]", "", value.lower())
    return DOSHA_COMPOUNDS.get(token, [SINGLE_DOSHA[token]] if token in SINGLE_DOSHA else [])


def _prepare_pulse_transcript(raw: str) -> str:
    """Apply only Pulse-specific ASR repairs, leaving unrelated sections untouched."""
    text = raw or ""
    # Compact common spoken/ASR variants before identifying the system code.
    replacements = (
        (r"\b(?:L|I)\s*S\s*C\s*S\b", "LSCS"),  # LSCS / ISCS
        (r"\bC\s*V\s*S\b", "CVS"),
        (r"\bP\s*R\s*O\b", "PRO"),
        (r"\bR\s*T\b", "RT"),
        (r"\bG\s*B\b", "GB"),
        (r"\bL\s*V\b", "LIV"),
        (r"\bS\s*S\b", "SS"),
        (r"\bK\s*B\b", "KUB"),
        (r"\bL\s*I\s*S\s*I\b", "LISI"),
        (r"\bLISMODERATE\b", "LISI moderate"),
        (r"\bMILE\b", "mild"),
        # A single spoken P is frequently transcribed as B. This is safe only
        # here, after the section has already been identified as Pulse.
        (r"\b(?:P\s*B|B)\b", "P"),
        (r"\b(?:kafa|kaffa|caffa)\b", "K"),
        (r"\bvata\b", "V"),
        (r"\bpitta\b", "P"),
        (r"\bkapha\b", "K"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # "LI SI mild P ..." is a single combined LISI reading.  Do not combine
    # rows when LI and SI each have their own reading ("LI mild P, SI ...").
    text = re.sub(
        rf"\bLI\s+SI\b(?=\s*(?:,\s*)?(?:{_PULSE_SEVERITY}|{_PULSE_DOSHA})\b)",
        "LISI",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _find_pulse_system_mentions(text: str) -> List[tuple[int, str]]:
    """Find explicit Pulse system codes without treating ordinary ``is`` as IS."""
    matches: List[tuple[int, str]] = []
    for code in _PULSE_CODES:
        for found in re.finditer(rf"\b{code}\b", text, re.IGNORECASE):
            if code == "IS" and found.group() != "IS":
                before = text[:found.start()].rstrip()
                # Lowercase "is" is accepted only when it is deliberately
                # separated like a code in a comma-list.  This excludes
                # "Overall dominance is severe Pitta".
                if before and before[-1] not in ",;:\n":
                    continue
            matches.append((found.start(), code))
    return sorted(matches)


def _dominance_from_transcript(raw: str) -> Optional[str]:
    """Recover the explicitly stated overall VPK dominance, if present."""
    match = re.search(
        r"\boverall(?:\s+[vpk]+)?\s+dominance\b\s*(?:is|was|:)?\s*([^,.;]+)",
        raw or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    phrase = match.group(1).lower()
    symbols = set()
    if re.search(r"\bvata\b", phrase):
        symbols.add("V")
    if re.search(r"\bpitta\b", phrase):
        symbols.add("P")
    if re.search(r"\bkapha\b|\bkafa\b|\bkaffa\b", phrase):
        symbols.add("K")
    if not symbols:
        for shorthand in re.findall(r"\b(?:vpk|pv|vp|vk|kv|pk|kp|v|p|k)\b", phrase):
            symbols.update(d[0].upper() for d in _pulse_doshas(shorthand))
    return "".join(code for code in ("V", "P", "K") if code in symbols) or None


def normalize_pulse_diagnosis(data: Any, raw_transcript: str = "") -> Dict[str, Any]:
    """Canonical, source-first Pulse parser used by extraction and ERP export."""
    output = sanitize_section("pulse_diagnosis", data)
    by_system = {
        row["system"]: dict(row)
        for row in _sanitize_pulse_systems(output.get("systems"))
    }

    if raw_transcript:
        text = _prepare_pulse_transcript(raw_transcript)
        mentions = _find_pulse_system_mentions(text)
        for index, (start, code) in enumerate(mentions):
            end = mentions[index + 1][0] if index + 1 < len(mentions) else len(text)
            segment = text[start + len(code):end]
            parsed: Dict[str, str] = {}
            # One combined expression prevents the end-dosha of "mild P" from
            # being reused as the beginning of a false "P moderate" pair.
            for pair in _PULSE_PAIR_RE.finditer(segment):
                severity = normalize_severity(pair.group("severity") or pair.group("reverse_severity"))
                dosha_token = pair.group("dosha") or pair.group("reverse_dosha")
                if severity and dosha_token:
                    for dosha in _pulse_doshas(dosha_token):
                        parsed[dosha] = severity
            if not parsed:
                continue
            row = by_system.setdefault(
                code,
                {"system": code, "vata": None, "pitta": None, "kapha": None},
            )
            row.update(parsed)
            row["raw_phrase"] = f"{code}{segment}".strip()

        overall = output.get("overall_vpk") if isinstance(output.get("overall_vpk"), dict) else {}
        dominance = _dominance_from_transcript(raw_transcript)
        if dominance:
            overall = {**overall, "dominance": dominance}
        output["overall_vpk"] = overall

    output["systems"] = _sanitize_pulse_systems(list(by_system.values()))
    return output


def _sanitize_dict_list(items: Any, allowed_keys: Optional[set] = None) -> List[Dict[str, Any]]:
    """
    Normalize a list of clinical item dicts. Only internal bookkeeping keys
    and underscore-prefixed keys are stripped -- every other key is preserved
    so real clinical fields ('procedure', 'session_count', 'weeks', ...) are
    never lost. ``allowed_keys`` is advisory (documented in SECTION_SCHEMA)
    and is deliberately NOT enforced here.
    """
    out: List[Any] = []
    for raw_item in _as_list(items):
        if not isinstance(raw_item, dict):
            s = _clean(raw_item)
            if s:
                out.append({"name": normalize_medicine_name(s)})
            continue
        filtered: Dict[str, Any] = {}
        for k, v in raw_item.items():
            if k in _INTERNAL_KEYS:
                continue
            if isinstance(k, str) and k.startswith("_"):
                continue
            filtered[k] = v
        if filtered.get("name"):
            filtered["name"] = normalize_medicine_name(str(filtered["name"]))
        out.append(filtered)
    return out


def _sanitize_supplement_list(items: Any) -> List[Dict[str, Any]]:
    return _sanitize_dict_list(items, _DEFAULT_ITEM_KEYS)


def _dedupe_strings(items: Any) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in _as_list(items):
        s = _clean(item)
        if not s:
            continue
        key = s.lower().strip(".")
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _strip_internal(value: Any) -> Any:
    """Recursively remove internal-only keys from a model-produced dict."""
    if isinstance(value, dict):
        return {k: _strip_internal(v) for k, v in value.items()
                if not (isinstance(k, str) and k in _INTERNAL_KEYS)}
    if isinstance(value, list):
        return [_strip_internal(v) for v in value]
    return value


def normalize_value(value: Any, section: str = "", field: str = "") -> Any:
    """
    Apply context-aware canonical normalization to a single *clinical* value
    using (in order): medicine-name lookup, dosing/frequency expansion,
    blood-pressure canonical form, and severity vocabulary.
    """
    if _is_empty(value):
        return value
    if section == "vitals_anthropometry" and field == "bp":
        return normalize_bp(value)
    if field in ("frequency", "timing", "duration") and isinstance(value, str) \
            and len(value) < 64:
        return normalize_frequency(value)
    if field == "severity" and isinstance(value, str):
        return normalize_severity(value)
    if field in ("name", "medicine", "medication", "supplement",
                 "procedure") and isinstance(value, str) and len(value) < 64:
        return normalize_medicine_name(value)
    if field == "system" and isinstance(value, str):
        return normalize_system_code(value)
    return value


def is_clinical_noise(segment: str) -> bool:
    """
    Quick deterministic check for obviously non-clinical speech (background
    chatter, gibberish), used as a cheap pre-filter before the LLM reviewer.
    """
    s = _clean(segment).lower()
    if not s or len(s) < 4:
        return True
    # Foreign non-Latin scripts (scripts this ASR pipeline cannot transcribe).
    if re.search(
        r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\u0400-\u04ff"
        r"\u0590-\u05ff\u0600-\u06ff\u0900-\u097f]",
        s,
    ):
        return True
    gibberish = re.sub(r"[^a-z ]+", "", s)
    if len(gibberish) < 3:
        return True
    return False


def sanitize_section(section: str, data: Any) -> Dict[str, Any]:
    """
    Deterministically enforce the section's schema contract on model output:
      - strip internal keys (_error, _raw, _reprompt, ...)
      - coerce scalars to str / num per the schema
      - normalize list-of-dict sections (medicines, complaints, supplements)
      - normalize pulse systems (codes + severity vocabulary)
      - dedupe string lists and item lists
      - normalize BP, medicine names, frequencies, severities
    Returns a plain dict -- never raises.
    """
    if not isinstance(data, dict):
        if _is_empty(data):
            return {}
        return {"_raw_note": _clean(data)}
    data = _strip_internal(data)
    schema = SECTION_SCHEMA.get(section, {})
    allowed_item_keys = schema.get("_item_keys")
    output: Dict[str, Any] = {}
    for key, value in data.items():
        if key in _INTERNAL_KEYS:
            continue
        if isinstance(key, str) and key.startswith("_"):
            if key == CLARIFICATION_FLAG:
                output[key] = value
            continue
        if key in _METADATA_KEYS and key not in schema:
            if key in ("images", "imgs", "data", "status"):
                output[key] = value
                continue
        tag = schema.get(key, "free")
        if tag in ("str", "num", "bp"):
            output[key] = _coerce_scalar(value, tag)
        elif tag == "str_list":
            output[key] = _dedupe_strings(value)
        elif tag == "pulse_systems":
            output[key] = _sanitize_pulse_systems(value)
        elif tag == "supplement_list":
            output[key] = _sanitize_supplement_list(value)
        elif tag == "str_dict_list":
            output[key] = _sanitize_dict_list(
                value, allowed_item_keys or set())
        else:
            output[key] = _strip_internal(value)
    # Normalize an outer "data" wrapper produced by the app layer if present.
    if isinstance(output.get("data"), dict):
        inner = sanitize_section(section, output["data"])
        for k, v in inner.items():
            output.setdefault(k, v)
        output["data"] = inner
    return output


def sanitize_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize every clinical section of a full draft in one pass.
    Keeps metadata keys (status, images, updated_at) and the raw transcripts
    store (_raw_transcripts) intact.
    """
    if not isinstance(draft, dict):
        return {}
    from app.casesheet.prompts import VALID_SECTIONS

    out: Dict[str, Any] = {}
    for key, value in draft.items():
        if key == "_raw_transcripts":
            out[key] = value
            continue
        if key.startswith("_"):
            out[key] = value
            continue
        if key in VALID_SECTIONS:
            out[key] = sanitize_section(key, value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Intelligent merge -- never lose existing dictation
# ---------------------------------------------------------------------------

def merge_section_data(existing: Any, incoming: Any) -> Dict[str, Any]:
    """
    Deep-merge a freshly extracted section into the draft's existing value.

    Rules (recursive):
      * dict  + dict  -> key-wise recursive merge.
      * list  + list  -> merge + dedupe by canonical item name;
                         incoming items are preferred when they are non-empty.
      * scalars       -> keep `incoming` if it is non-empty, otherwise keep
                         the existing value.
      * empty incoming never wipes out a populated existing field.
    The {"data": ...} wrapper used by the image/OCR path is unwrapped
    transparently on both sides.
    """
    existing = _unwrap_data(existing)
    incoming = _unwrap_data(incoming)

    if _is_empty(incoming):
        return existing if isinstance(existing, dict) else {}

    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged: Dict[str, Any] = dict(existing)
        for key, new_val in incoming.items():
            if key in _INTERNAL_KEYS or (isinstance(key, str)
                                         and key.startswith("_")):
                continue
            old_val = existing.get(key)
            if isinstance(old_val, dict) and isinstance(new_val, dict):
                merged[key] = merge_section_data(old_val, new_val)
            elif isinstance(old_val, list) and isinstance(new_val, list):
                merged[key] = _merge_lists(old_val, new_val)
            elif not _is_empty(new_val):
                merged[key] = new_val
        return merged

    if isinstance(existing, list) and isinstance(incoming, list):
        return _merge_lists(existing, incoming)

    return incoming if not _is_empty(incoming) else existing


def _unwrap_data(value: Any) -> Any:
    """Extract `data` when wrapped in the `{"data": ...}` UI shape."""
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        return value["data"]
    return value


def _list_item_key(item: Any) -> str:
    if isinstance(item, dict):
        # Pulse rows have no name.  Their clinical identity is the system
        # code, so retries must merge CVS with CVS rather than append a second
        # contradictory row.
        for k in ("system", "name", "complaint", "condition", "surgery", "medicine"):
            if item.get(k):
                return _clean(item[k]).lower().strip(".")
        return _clean(str(item)).lower().strip(".")
    return _clean(str(item)).lower().strip(".")


def _merge_lists(old_items: List[Any], new_items: List[Any]) -> List[Any]:
    """Merge lists by clinical identity while retaining non-empty old fields."""
    if _is_empty(old_items):
        return sanitize_items_merge(new_items)
    if _is_empty(new_items):
        return sanitize_items_merge(old_items)

    merged: Dict[str, Any] = {}
    order: List[str] = []
    for item in old_items:
        key = _list_item_key(item)
        if not key:
            continue
        if key not in merged:
            merged[key] = item
            order.append(key)
    for item in new_items:
        key = _list_item_key(item)
        if not key:
            continue
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(item, dict):
            merged[key] = merge_section_data(existing, item)
        else:
            # A non-empty newer scalar is an intentional correction.
            merged[key] = item if not _is_empty(item) else existing
        if key not in order:
            order.append(key)
    return sanitize_items_merge([merged[key] for key in order])


def _dedupe_lists(old_items: List[Any], new_items: List[Any]) -> List[Any]:
    seen: set = set()
    out: List[Any] = []
    for item in list(new_items) + list(old_items):
        key = _list_item_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def sanitize_items_merge(items: List[Any]) -> List[Any]:
    """Final tidy on a merged list: drop blanks + collapse whitespace."""
    out: List[Any] = []
    for item in items:
        if isinstance(item, dict):
            clean_item = {k: (_clean(v) if isinstance(v, str) else v)
                          for k, v in item.items()}
            if all(_is_empty(v) for v in clean_item.values()):
                continue
            out.append(clean_item)
        else:
            s = _clean(item)
            if s:
                out.append(s)
    return out


# ---------------------------------------------------------------------------
# Speech-variant few-shot examples
# "The doctor may say X, Y or Z -- the canonical clinical fact is VALUE."
# Embedded into section prompts so the model maps MANY phrasings to ONE fact.
# ---------------------------------------------------------------------------

SPEECH_VARIANTS: Dict[str, List[str]] = {
    "chief_complaint": [
        'Doctor says: "he has got this bad knee pain since two months" -- '
        "canonical: {'complaint': 'Knee pain', '"
        "'ayurvedic_name': 'Janu Sandhivata', 'duration': '2 months', "
        "'site': 'knee'}",
        'Doctor says: "she lifts weights and now her back is killing her '
        'every morning" -- canonical: {"complaint": "Lower back pain", '
        '"ayurvedic_name": "Katigraha", "onset": "after weight lifting", '
        '"site": "lower back"}',
        'Doctor says: "patient came with giddiness and vertigo since last '
        'week" -- canonical: {"complaint": "Giddiness / Vertigo", '
        '"duration": "1 week"}',
        'Doctor says: "fever off and on, mostly in the evenings since 3 '
        'days" -- canonical: {"complaint": "Intermittent fever", '
        '"duration": "3 days", "onset": "evening"}',
        'Doctor says: "chronic acidity, burning in chest after meals and '
        'breathlessness sometimes" -- canonical: {"complaint": "Amlapitta '
        '/ Acidity", "ayurvedic_name": "Amlapitta", "trigger": "after '
        'meals"}',
        'Doctor says: "he had a fall, now unable to bend his right shoulder" '
        '-- canonical: {"complaint": "Right shoulder pain / stiffness", '
        '"onset": "after fall", "site": "right shoulder"}',
    ],
    "medication_history": [
        'Doctor says: "he is on metformin 500 twice a day since five years" '
        '-- canonical: {"name": "Metformin", "dosage": "500mg", '
        '"frequency": "twice daily", "duration": "5 years"}',
        'Doctor says: "bp tab telmesartan 40 in the morning" -- canonical: '
        '{"name": "Telmisartan", "dosage": "40mg", "frequency": "morning"}',
        'Doctor says: "she takes her insulin every night before bed" -- '
        'canonical: {"name": "Insulin", "frequency": "at bedtime"}',
        'Doctor says: "diabetic medicines, tab metformin and tab pantoprazole '
        'before food" -- canonical: {"name": "Pantoprazole", '
        '"frequency": "before food"}',
    ],
    "past_medical_history": [
        'Doctor says: "she is a known diabetic for ten years, sugar was '
        'always high" -- canonical: {"condition": "Type 2 Diabetes '
        'Mellitus", "duration": "10 years"}',
        'Doctor says: "he has got piles for a long time, diagnosed as grade '
        '3" -- canonical: {"condition": "Piles", "status": "Grade 3"}',
        'Doctor says: "patient had typhoid two years back and malaria as a '
        'child" -- canonical: {"condition": "Typhoid", "duration": "2 '
        'years ago"}',
    ],
    "surgical_history": [
        'Doctor says: "he had a hernia operation four years back, mesh '
        'surgery" -- canonical: {"surgery": "Inguinal hernia repair", '
        '"year": "4 years ago", "note": "mesh surgery"}',
        'Doctor says: "gallbladder was removed earlier" -- canonical: '
        '{"surgery": "Gallbladder removal"}',
    ],
    "vitals_anthropometry": [
        'Doctor says: "BP is one fifty by ninety" -- canonical: '
        '{"bp": "150/90"}',
        'Doctor says: "blood pressure reads 130 over 80" -- canonical: '
        '{"bp": "130/80"}',
        'Doctor says: "weight 72 kilograms, height about 5 foot 6" -- '
        'canonical: {"weight_kg": 72, "height_cm": 168}',
        'Doctor says: "temperature 99 point 4 Fahrenheit" -- canonical: '
        '{"temperature": "99.4 F"}',
        'Doctor says: "pulse is 72, spo2 98" -- canonical: '
        '{"pulse_rate": "72 bpm", "spo2": 98}',
    ],
    "pulse_diagnosis": [
        'Doctor says: "LISI moderate, PV mild" -- canonical: system LISI '
        'with {"pitta": "mild", "vata": "mild"} (compound shorthand '
        'expansion: PV = Pitta AND Vata, same severity on both doshas)',
        'Doctor says: "liver is low V" -- canonical: system LIV with '
        '{"vata": "very_mild"}',
        'Doctor says: "heart is C V S moderate pitta" -- canonical: system '
        'CVS with {"pitta": "moderate"}',
        'Doctor says: "gallbladder and liver fine, prostate slightly elevated" '
        '-- canonical: systems GB "very_mild", LIV "very_mild", PRO "mild"',
    ],
    "ayurvedic_supplements": [
        'Doctor says: "give APD plus neurotropin tablets, half twice daily" -- '
        'canonical: {"name": "APD", "dosage": "/2", "frequency": "twice '
        'daily"}, {"name": "NEUROTROPIN", "dosage": "/2", "frequency": '
        '"twice daily"}',
        'Doctor says: "atherolyzine capsule one in the morning and one at '
        'night" -- canonical: {"name": "ATHEROLYZIN", "dosage": "1", '
        '"frequency": "morning and night"}',
        'Doctor says: "d-tox sachet after food in the evening" -- '
        'canonical: {"name": "D-TOX", "timing": "after food", '
        '"frequency": "evening"}',
    ],
    "detox_procedures": [
        'Doctor says: "oil pulling with sesame oil daily on empty stomach for '
        'two weeks" -- canonical: {"name": "Gandusham (Oil Pulling)", '
        '"frequency": "daily", "timing": "empty stomach", "duration": '
        '"2 weeks"}',
        'Doctor says: "steam inhalation twice daily" -- canonical: {"name": '
        '"Steam Inhalation", "frequency": "twice daily"}',
        'Doctor says: "castor oil routine at night with warm lemon water as per '
        'body weight" -- canonical: {"name": "Nithya Virechana", '
        '"frequency": "daily at bedtime"}',
    ],
    "exercises_yoga": [
        'Doctor says: "naukasanam six rounds, then after five weeks start '
        'suryanamaskaram ten rounds" -- canonical: {"name": "Naukasana", '
        '"repetitions": "6"}, {"name": "Suryanamaskaram", "repetitions": '
        '"10", "start": "after 5 weeks"}',
        'Doctor says: "anulom vilom ten minutes morning and evening" -- '
        'canonical: {"name": "Differential Nostril Breathing", "frequency": '
        '"10 min morning and evening"}',
    ],
}

# Signal words per section -- helps the model route an utterance to the right
# section (directly addresses utterance-to-section misalignment).
SECTION_SIGNAL_WORDS: Dict[str, List[str]] = {
    "patient_identity": ["name is", "age", "years old", "male", "female",
                         "patient id"],
    "chief_complaint": ["complaint", "pain", "fever", "cough", "headache",
                        "vomiting", "itching", "burning", "since"],
    "medication_history": ["taking", "on ", "tablet", "tab", "capsule",
                           "injection", "mg", "units", "dose",
                           "prescribed", "medication", "medicine"],
    "past_medical_history": ["diabetic", "diabetes", "hypertension",
                             "thyroid", "asthma", "history of", "known",
                             "previously", "old case"],
    "surgical_history": ["surgery", "operation", "operated",
                         "appendectomy", "hysterectomy", "hernia", "removed"],
    "allergy_history": ["allerg", "reaction", "rash", "intolerant"],
    "detox_procedures": ["gandush", "virechana", "oil pulling", "steam",
                         "tailam", "detox", "soup"],
    "ayurvedic_supplements": ["apd", "supplement", "tablet", "sachet",
                              "d-tox", "atherolyzin", "migranone"],
    "exercises_yoga": ["naukasan", "suryanamas", "yoga", "exercise",
                       "pranayama", "stretch"],
}


def build_variant_examples(section: str) -> str:
    """
    Return the few-shot block for a section, e.g.:

      SPEECH VARIANTS -- all phrasings map to the same canonical fact:
      - "he has got this bad knee pain since two months" ->
          {'complaint': 'Knee pain', 'duration': '2 months'}
      ...

    Empty string when the section has no registered variants.
    """
    variants = SPEECH_VARIANTS.get(section)
    if not variants:
        return ""
    lines = ["SPEECH VARIANTS -- the SAME clinical fact can be dictated in many "
             "ways. Map every variant below (and any variant of the same "
             "meaning) to the canonical structured fact shown:",
             "----------------------------------------------------------------"]
    lines.extend(f"- {v}" for v in variants)
    signals = SECTION_SIGNAL_WORDS.get(section)
    if signals:
        lines.append("")
        lines.append("Dictation often contains these signal words -- use them "
                     f"to decide the fact belongs to this '{section}' section: "
                     + ", ".join(signals) + ".")
    lines.append("----------------------------------------------------------------")
    return "\n".join(lines)
# ──────────────────────────────────────────────────────────────────────────
# Thin integration helpers (used by llm_service / casesheet router)
# ──────────────────────────────────────────────────────────────────────────


def postprocess_section(section: str, raw_data: Any) -> Dict[str, Any]:
    """
    Thin wrapper around :func:`sanitize_section` for the extraction pipeline.

    Every LLM extraction passes here so the draft always receives
    schema-clean, canonical-vocabulary data regardless of phrasing.
    Error markers (``_error`` / ``_reprompt``) are intentionally NOT added
    here — callers guard on them *before* calling this function.
    """
    sanitized = sanitize_section(section, raw_data)
    if settings.CASE_APPLY_NOMENCLATURE:
        sanitized = apply_nomenclature_to_section(sanitized)
    return sanitized


# ---------------------------------------------------------------------------
# Clinic nomenclature renames (branding updated by the clinic)
# Only *display text* is renamed — JSON keys stay stable so the ERP field
# mapping and the Flutter casesheet app keep working.
# ---------------------------------------------------------------------------
NOMENCLATURE_RENAMES: Dict[str, str] = {
    "panchakarma": "Physiological Recalibration Protocol",
}

_NOMENCLATURE_PATTERNS = [
    (re.compile(rf"\b{re.escape(old)}\b", re.IGNORECASE), new)
    for old, new in NOMENCLATURE_RENAMES.items()
]

# Keys whose values are file references / metadata — never rewrite these.
_NOMENCLATURE_SKIP_KEYS = {
    "filename", "url", "file_path", "image_url", "uploaded_at", "id",
}


def apply_nomenclature(text: str) -> str:
    """Rename deprecated nomenclature inside a display string."""
    if not text or not isinstance(text, str):
        return text
    for pattern, new in _NOMENCLATURE_PATTERNS:
        if pattern.search(text):
            text = pattern.sub(new, text)
    return text


def apply_nomenclature_to_section(data: Any, _key: str = "") -> Any:
    """
    Recursively apply :func:`apply_nomenclature` to every *string value* of a
    section dict/list. Dict KEYS are never touched, so structural keys like
    ``panchakarma`` / ``panchakarma_summary`` stay intact, and file-reference
    values (filenames, URLs) are skipped so links never break.
    """
    if isinstance(data, dict):
        return {
            k: apply_nomenclature_to_section(
                v,
                _key=k if isinstance(k, str) else "",
            )
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [apply_nomenclature_to_section(v, _key=_key) for v in data]
    if isinstance(data, str):
        if _key in _NOMENCLATURE_SKIP_KEYS:
            return data
        return apply_nomenclature(data)
    return data


def with_variant_examples(section: str, prompt: str) -> str:
    """
    Append the speech-variant few-shot block to a section prompt.
    No-op when the section has no registered variants (prompt unchanged).
    """
    block = build_variant_examples(section)
    if not block:
        return prompt
    return f"{prompt.rstrip()}\n\n{block}\n"


def with_batch_variant_examples(sections: List[str], prompt: str) -> str:
    """
    Append the speech-variant few-shot blocks for a whole batch to its
    prompt (one block per section, deduped). No-op when none are registered.
    """
    blocks: List[str] = []
    seen: set = set()
    for sec in sections:
        block = build_variant_examples(sec)
        if block and block not in seen:
            seen.add(block)
            blocks.append(block)
    if not blocks:
        return prompt
    return f"{prompt.rstrip()}\n\n" + "\n\n".join(blocks) + "\n"

# ---------------------------------------------------------------------------
# Vitals plausibility + unit conversion (fixes garbled values like "Height: 8")
# ---------------------------------------------------------------------------

# Plausible adult ranges (lo, hi). Values outside are flagged, not destroyed.
VITALS_RANGES: Dict[str, tuple] = {
    "height_cm": (50, 250),
    "weight_kg": (2, 300),
    "pulse_rate": (30, 220),
    "temperature": (89.0, 111.0),   # Fahrenheit
    "spo2": (50, 100),
    "wrist_cm": (10, 30),
    "waist_cm": (40, 200),
    "fore_arm_cm": (10, 60),
    "hip_cm": (50, 200),
}


def length_to_cm(value: Any) -> Any:
    """
    Convert spoken imperial lengths to centimetres.
    "6.5 inches" -> 16.5; "5 feet 7 inches" -> 170.2; "5'7\"" -> 170.2.
    Bare numbers pass through unchanged (unit is ambiguous without context).
    """
    if value is None or isinstance(value, (int, float)):
        return value
    s = _clean(value)
    low = s.lower()
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:feet|foot|ft)\s*(\d+(?:\.\d+)?)?\s*(?:inches|inch|in\b)?",
        low,
    )
    if m:
        feet = float(m.group(1))
        inches = float(m.group(2) or 0.0)
        return round((feet * 12.0 + inches) * 2.54, 1)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:inches|inch|\bin\b|\u2033)", low)
    if m:
        return round(float(m.group(1)) * 2.54, 1)
    return s


def check_vitals_plausibility(vitals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flag (never destroy) vital-sign values outside plausible adult ranges by
    appending human-readable notes to the section's ``_needs_clarification``.
    """
    if not isinstance(vitals, dict):
        return vitals
    flags: List[str] = []

    def _check(field: str, value: Any) -> None:
        lo, hi = VITALS_RANGES[field]
        num = _to_number(value)
        if num is None:
            return
        if num < lo or num > hi:
            flags.append(
                f"{field}: '{_clean(value)}' outside plausible range {lo}-{hi}"
            )

    for field in VITALS_RANGES:
        _check(field, vitals.get(field))

    bp = vitals.get("bp")
    if isinstance(bp, str) and "/" in bp:
        sys_s, _, dia_s = bp.partition("/")
        sys_n = _to_number(sys_s)
        dia_n = _to_number(dia_s)
        if sys_n is not None and not (50 <= sys_n <= 250):
            flags.append(f"bp systolic: '{_clean(sys_s)}' outside plausible range 50-250")
        if dia_n is not None and not (30 <= dia_n <= 150):
            flags.append(f"bp diastolic: '{_clean(dia_s)}' outside plausible range 30-150")

    if flags:
        existing = vitals.get(CLARIFICATION_FLAG)
        merged = existing if isinstance(existing, list) else ([existing] if existing else [])
        for f in flags:
            if f not in merged:
                merged.append(f)
        vitals[CLARIFICATION_FLAG] = merged
    return vitals


def salvage_cross_section_fields(draft: Dict[str, Any]) -> Dict[str, Any]:
    """
    Route facts dictated inside one section to their canonical home section.
    Today: age spoken during the vitals block -> patient_identity (which is
    where the ERP mapper and the patient card read it from).
    """
    if not isinstance(draft, dict):
        return draft
    vitals = draft.get("vitals_anthropometry")
    if not isinstance(vitals, dict):
        return draft
    age = vitals.get("age")
    if _is_empty(age):
        return draft
    identity = draft.get("patient_identity")
    if not isinstance(identity, dict):
        identity = {}
        draft["patient_identity"] = identity
    if _is_empty(identity.get("age")):
        num = _to_number(age)
        identity["age"] = int(num) if num is not None and num.is_integer() else (num or age)
        age_unit = vitals.get("age_unit")
        if not _is_empty(age_unit) and _is_empty(identity.get("age_unit")):
            identity["age_unit"] = _clean(age_unit)
    vitals.pop("age", None)
    vitals.pop("age_unit", None)
    return draft


# ---------------------------------------------------------------------------
# Recall helpers (fill-gaps pass)
# ---------------------------------------------------------------------------

def section_has_data(data: Any) -> bool:
    """True when a section dict carries at least one meaningful clinical value."""
    if not isinstance(data, dict):
        return not _is_empty(data)
    for key, value in data.items():
        if key in _METADATA_KEYS or key == CLARIFICATION_FLAG:
            continue
        if not _is_empty(value):
            return True
    return False


def section_signals_present(section: str, text: str) -> bool:
    """True when the transcript contains signal words for this section."""
    signals = SECTION_SIGNAL_WORDS.get(section)
    if not signals or not text:
        return False
    low = text.lower()
    return any(sig in low for sig in signals)


# ---------------------------------------------------------------------------
# Protocol classification / enrichment (doctor-first instructions)
# ---------------------------------------------------------------------------
_COMPLIANCE_PATTERNS = [
    "not following", "skipped", "paused", "irregular", "stopped",
    "discontinued", "travelling", "traveling", "out of station",
    "not taking", "missed dose", "off the diet", "broke the diet",
]

_PROTOCOL_ALIAS_INDEX: List[tuple] = []
for _canon, _meta in PROTOCOL_REGISTRY.items():
    for _al in [_canon.lower()] + [a.lower() for a in _meta["aliases"]]:
        _PROTOCOL_ALIAS_INDEX.append((_al, _canon, _meta["category"]))
_PROTOCOL_ALIAS_INDEX.sort(key=lambda t: -len(t[0]))

_COMPILED_COMPLIANCE = [re.compile(re.escape(p)) for p in _COMPLIANCE_PATTERNS]


def classify_protocol_item(text: Any) -> tuple:
    """Return (category, canonical_name); (CAT_OTHER, '') when unrecognized."""
    if not text:
        return (CAT_OTHER, "")
    t = str(text).lower().strip()
    for alias, canon, cat in _PROTOCOL_ALIAS_INDEX:
        if alias in t:
            return (cat, canon)
    for pat in _COMPILED_COMPLIANCE:
        if pat.search(t):
            return (CAT_COMPLIANCE, "")
    return (CAT_OTHER, "")


def protocol_standard_instructions(canon: str) -> str:
    return (PROTOCOL_REGISTRY.get(canon) or {}).get("instructions", "") or ""


def enrich_protocol_item(item: Any) -> Any:
    """
    Deterministic protocol enrichment, doctor-first instruction priority:
      * canonical name always enforced via alias map.
      * doctor-dictated instructions kept verbatim in `instructions`
        (source="doctor"); clinic template attached in `standard_instructions`.
      * template used only when the doctor dictated nothing.
      * unrecognized items pass through untouched (never fabricated).
    """
    if isinstance(item, str):
        cat, canon = classify_protocol_item(item)
        if not canon:
            return item
        template = protocol_standard_instructions(canon)
        return {"name": canon, "category": cat,
                "instructions": template or item.strip(),
                "standard_instructions": template,
                "instructions_source": "template" if template else "doctor",
                "raw_text": item.strip()}

    if not isinstance(item, dict):
        return item

    new_item = dict(item)
    name = str(new_item.get("name") or new_item.get("procedure")
               or new_item.get("item_name") or "").strip()
    doctor_instr = str(new_item.get("instructions") or new_item.get("remarks") or "").strip()
    cat, canon = classify_protocol_item(f"{name} {doctor_instr}")
    if not canon:
        return new_item

    template = protocol_standard_instructions(canon)
    new_item["name"] = canon
    # Canonicalize every name-bearing key so no raw alias survives.
    if "procedure" in new_item:
        new_item["procedure"] = canon
    if "item_name" in new_item:
        new_item["item_name"] = canon
    new_item["category"] = cat
    new_item["standard_instructions"] = template
    if doctor_instr:
        if not str(new_item.get("instructions") or "").strip():
            new_item["instructions"] = doctor_instr
        new_item["instructions_source"] = "doctor"
    elif template:
        new_item["instructions"] = template
        new_item["instructions_source"] = "template"
    else:
        new_item["instructions_source"] = "doctor"
    return new_item


def is_protocol_section(section: str) -> bool:
    return section in ("panchakarma", "detox_procedures", "exercises_yoga",
                       "followup_details", "treatment_and_background")


PROTOCOL_TAXONOMY_PROMPT_BLOCK = (
    "PROCEDURE / THERAPY TAXONOMY (canonical names + categories):\n"
    "- Procedures (in-clinic): Abhyanga, Virechana, Vamana, Basti, Kati Vasti, Janu Vasti, Greeva Vasti, Pichu, "
    "Shirodhara, Nadi Swedhana, Navara Lepanam, Takradhara, Swedana (incl. sauna/steam bath), "
    "Hot Pack, Cold Pack, Nasya.\n"
    "- Oil Applications (never standalone procedures): Neelibhringadi, Nutex Oil, Chandanadi Thailam, "
    "Pinda Tailam, Murivenna, Erand Tailam (Castor Oil), pain oils, skin oils.\n"
    "- Yoga / Exercises: Surya Namaskar, Naukasana, Bhujangasana, Kegel, Walking, Leg Exercises, "
    "Hip Rotation, Stretching, Gym, Knee Strengthening.\n"
    "- Breathing: DNB (Differential Nostril Breathing), R-DNB, Reverse DNB.\n"
    "- Detox (home routines): Nitya Virechana (NVK), Prativaara Virechana (PVVK), "
    "D-Tox, Gandusham, Anutailam, Steam Inhalations. Virechana and Vamana are ALWAYS in-clinic procedures.\n"
    "- Diet / Food: Fennel Water, soups (Barley/Rice/Tapioca/Raagi/Jowar), CCRSTT, Coriander Milk, "
    "Ginger Tea, Curd Rice.\n"
    "- Abbreviations MUST be expanded: NVK -> Nitya Virechana (NVK); PVVK -> Prativaara Virechana (PVVK); "
    "DNB -> DNB (Differential Nostril Breathing).\n"
    "- INSTRUCTIONS PRIORITY: if the doctor dictates specific instructions for an item, put them verbatim "
    "in 'instructions'. Attach the clinic standard instruction only when nothing is dictated.\n"
    "- NEVER add a catalog item merely because it appears in this taxonomy. Add it only when the doctor explicitly names it.\n"
    "- Session counts apply only to the procedure the doctor explicitly associates with that count; never copy a total across named procedures.\n"
    "- Always store the canonical spelling above in the 'name' field.\n"
)


def with_protocol_taxonomy(prompt: str) -> str:
    if "PROCEDURE / THERAPY TAXONOMY" in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + PROTOCOL_TAXONOMY_PROMPT_BLOCK


def _item_label(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("procedure") or item.get("name")
                   or item.get("item_name") or "")
    return str(item or "")


def apply_protocol_boundaries(draft: Dict[str, Any]) -> int:
    """
    Deterministic boundary rerouting (never drops data):
      detox/diet items in panchakarma.sessions -> detox_procedures.detox_items
      in-clinic procedures in detox_procedures -> panchakarma.sessions
      yoga/breathing items in either -> exercises_yoga.exercises
    Returns number of moved items.
    """
    if not isinstance(draft, dict):
        return 0
    panca = draft.get("panchakarma")
    detox = draft.get("detox_procedures")
    yoga = draft.get("exercises_yoga")
    if not isinstance(panca, dict) or not isinstance(detox, dict):
        return 0
    moved = 0

    panca_sessions = panca.get("sessions") if isinstance(panca.get("sessions"), list) else []
    detox_items = detox.get("detox_items") if isinstance(detox.get("detox_items"), list) else []
    yoga_list = (yoga.get("exercises") if isinstance(yoga, dict)
                 and isinstance(yoga.get("exercises"), list) else None)

    keep_panca = []
    for s in panca_sessions:
        label = _item_label(s)
        cat, _ = classify_protocol_item(label)
        if not label:
            keep_panca.append(s)
        elif cat in (CAT_DETOX, CAT_DIET):
            moved_item = dict(s) if isinstance(s, dict) else {"name": s}
            moved_item.setdefault("name", label)  # normalize key for detox readers
            detox_items.append(moved_item)
            moved += 1
        elif cat in (CAT_EXERCISES, CAT_BREATHING) and yoga_list is not None:
            moved_item = dict(s) if isinstance(s, dict) else {"name": s}
            moved_item.setdefault("name", label)
            yoga_list.append(moved_item)
            moved += 1
        else:
            keep_panca.append(s)
    panca["sessions"] = keep_panca

    keep_detox = []
    for it in detox_items:
        label = _item_label(it)
        cat, _ = classify_protocol_item(label)
        if not label:
            keep_detox.append(it)
        elif cat == CAT_PROCEDURES:
            if isinstance(it, dict):
                it.setdefault("procedure", it.get("name", label))
            else:
                it = {"procedure": it, "name": it}
            panca_sessions.append(it)
            moved += 1
        elif cat in (CAT_EXERCISES, CAT_BREATHING) and yoga_list is not None:
            moved_item = dict(it) if isinstance(it, dict) else {"name": it}
            moved_item.setdefault("name", label)
            yoga_list.append(moved_item)
            moved += 1
        else:
            keep_detox.append(it)
    detox["detox_items"] = _dedupe_protocol_items(keep_detox)
    panca["sessions"] = _dedupe_protocol_items(panca_sessions)
    if isinstance(yoga, dict) and yoga_list is not None:
        yoga["exercises"] = _dedupe_protocol_items(yoga_list)
    return moved


def _dedupe_protocol_items(items: List[Any]) -> List[Any]:
    """Keep one patient-specific item per canonical procedure/remedy."""
    merged: Dict[str, Any] = {}
    order: List[str] = []
    for item in items:
        label = _item_label(item)
        _category, canonical = classify_protocol_item(label)
        key = (canonical or label).strip().lower()
        if not key:
            continue
        normalized = enrich_protocol_item(item)
        if key not in merged:
            merged[key] = normalized
            order.append(key)
            continue
        # A repeated extraction may fill omitted fields, but it cannot create
        # a second row or silently replace an explicit earlier session count.
        if isinstance(merged[key], dict) and isinstance(normalized, dict):
            for field, value in normalized.items():
                if field not in merged[key] or _is_empty(merged[key].get(field)):
                    merged[key][field] = value
    return [merged[key] for key in order]


import re
from typing import Any, Dict, List

SGP_PROTOCOLS = {
    "Gandusham": {
        "keywords": ["gandush", "gandusha", "gandusham", "gandoosha", "oil pulling"],
        "text": (
            "Swish 1-2 tablespoons of sesame oil into your mouth for 10-20 mins and remember not to swallow. "
            "After this, just clean it thoroughly. Repeat it as prescribed by your physician."
        ),
        "clean_quantity": "1-2 tbsp",
        "clean_frequency": "As prescribed",
    },
    "Differential Nostril Breathing": {
        "keywords": ["nostril breathing", "differential nostril", "1:4:2", "anulom vilom", "anulom", "nostril"],
        "text": (
            "Inhale from the left nostril, hold the breath, and exhale from the right nostril. "
            "Breathing ratio should be 1:4:2 (Inhale: 1 sec, Hold: 4 secs, Exhale: 2 secs). "
            "Do it for 10 mins before and after bed."
        ),
        "clean_quantity": "",
        "clean_frequency": "10 mins before and after bed",
    },
    "Naukasana and Suryanamaskaram": {
        "keywords": ["naukasa", "surya namas", "suryanamas", "boat pose", "naukasam", "suryanamaskar"],
        "text": (
            "Practice 6 times Naukasana. After 5 weeks start practicing Suryanamaskaram - 10 times per day (morning or evening). "
            "Note: For people having back and spine issues, only Naukasanam is advised."
        ),
        "clean_quantity": "",
        "clean_frequency": "Daily",
    },
    "Avoid CCRSTT": {
        "keywords": ["ccrstt", "cabbage", "cauliflower", "tamarind", "avoid ccrstt"],
        "text": "Avoid Cabbage, Cauliflower, Radish, Spinach, Tamarind, Tomato (CCRSTT).",
        "clean_quantity": "",
        "clean_frequency": "",
    },
    "NeeliBringadi Keera Tailam": {
        "keywords": ["neelibringadi", "keera tailam", "keera thailam", "neelibhringadi keera tailam", "neelibhringadi", "neeli bringadi"],
        "text": "Apply over the scalp on every alternate days.",
        "clean_quantity": "",
        "clean_frequency": "Alternate days",
    },
    "Nutex Oil": {
        "keywords": ["nutex oil", "nutex", "nu tex"],
        "text": "Apply over the scalp / affected area as prescribed.",
        "clean_quantity": "",
        "clean_frequency": "As prescribed",
    },
    "Chandanadi Thailam": {
        "keywords": ["chandanadi", "chandanadi thailam", "chandanadi tailam", "chandanadi oil"],
        "text": "Apply over the scalp / affected area as prescribed.",
        "clean_quantity": "",
        "clean_frequency": "As prescribed",
    },
    "Nithya Virechana Process": {
        "keywords": ["nithya virechan", "daily virechan", "errant thailam", "erand tailam", "erand thailam", "castor oil routine", "castor oil quantity", "nithya virechana"],
        "text": (
            "Before going to bed (2 hrs after dinner) take warm water mixed with 1-2 lemons + 2 pinch black salt and within 10 mins have Erand Tailam (castor oil).\n"
            "Quantity of water: Approx weight(kg)*10 = Qtn.ml\n"
            "Quantity of oil(ml): Approx weight(kg)/3 = Qtn.ml"
        ),
        "clean_quantity": "",
        "clean_frequency": "Daily at bedtime",
    },
    "Prathivaara Virechana Karma": {
        "keywords": ["prathivaar", "prathivar", "weekly virechan", "once a week virechana", "prathivaara"],
        "text": (
            "Should do once in a week. Before going to bed (2 hrs after dinner) take warm water mixed with 1-2 lemons + 2 pinch black salt and within 10 mins have Erand Tailam (castor oil).\n"
            "Quantity of water: Approx weight(kg)*10 = Qtn.ml\n"
            "Quantity of oil(ml): Approx weight(kg)/3 = Qtn.ml"
        ),
        "clean_quantity": "",
        "clean_frequency": "Once a week at bedtime",
    },
    "Anutailam": {
        "keywords": ["anutail", "anu tail", "nasya drops"],
        "text": "Put 2 drops into each nostril and ears.",
        "clean_quantity": "2 drops",
        "clean_frequency": "Daily",
    },
    "Steam Inhalations": {
        "keywords": ["steam inhal", "turmeric steam", "zindu balm", "zandu balm"],
        "text": (
            "Take a big vessel, pour 1-2 liters of water and add 1 tsp of turmeric and boil it, not letting the steam leave by placing a lid on the vessel. "
            "After the water is boiled enough, turn off the flame and add Zandu balm to the water. Apply Zandu balm on the nose, temples, frontal region (between eye brows), behind the ears, on the throat and on the chest, and inhale the steam for 10 minutes.\n"
            "a. Syrups: Mucolyte or Bromhexine (2 tsp thrice a day if prescribed).\n"
            "b. Strictly follow for 7-14 days. If symptoms do not subside then continue the process."
        ),
        "clean_quantity": "",
        "clean_frequency": "Twice daily",
    },
    "SGP Covid Protocol": {
        "keywords": ["covid protocol", "corona protocol", "precautionary measures for corona", "sgp covid"],
        "text": (
            "Precautionary measures for Corona:\n\n"
            "DECOCTIONS:\n"
            "1. Ginger Tea: 1/4 spoon pepper powder, 1/2 spoon turmeric, 1 spoon ginger. Boil above ingredients in 200ml thick milk (full cream milk)/200ml water and add honey to make tea. Have at least 4-5 times a day. (Without milk is suggested only for people in whom cough aggravates with milk).\n"
            "2. Guava (Jama) leaves: Add 12 leaves in 1 liter water and boil until it becomes 800 ml.\n"
            "3. Neem leaves: Boil 50 gms in 1 liter water and drink.\n"
            "4. Somph water: Add 45 gms somph in 1 liter water and boil for 5-7 mins, add jaggery and drink. (Only when you observe overheat).\n\n"
            "STEAM INHALATIONS:\n"
            "Add Turmeric (1 tsp), Zandu balm (1/4 tsp), and Ghee (3 tsps) in boiling water of about 1 liter and have steam inhalations twice daily.\n\n"
            "BREATHING:\n"
            "Nostril breathing (1:4:2 ratio) - Inhale from left nostril for 1 sec, hold for 4 secs, and exhale for 2 secs. Do it for 10 mins before and after bed.\n\n"
            "EXERCISE / ASANAS:\n"
            "1. Surya Namaskaram & Naukasanam (People having back and spine issues, only Naukasanam is advised).\n"
            "2. Stretching exercises.\n"
            "3. Bhujangasanam."
        ),
        "clean_quantity": "",
        "clean_frequency": "Daily",
    },
    "Fennel Tea": {
        "keywords": ["fennel tea", "fennel water", "fennel sachet", "saunf", "somph tea", "fennel seeds", "fennel"],
        "text": (
            "Add 9 teaspoons of fennel seeds in 2 liters of water. Boil them for 5-10 mins. Filter the seeds and add 50-75 gms of jaggery to 2 liters of fennel water. At least drink 2 liters of water daily throughout the day. Drink the same day.\n"
            "Sachets: To 2 liters of hot/lukewarm water add 50-75 gms of jaggery, mix until dissolved. Then add 1 sachet of fennel powder, mix well and drink throughout the day. Drink the same day."
        ),
        "clean_quantity": "2 liters",
        "clean_frequency": "Daily throughout the day",
    },
    "Barley Soup": {
        "keywords": ["barley soup", "barley water", "ground barley", "soaked barley", "barley"],
        "text": (
            "Grind 2 teaspoons of barley seeds and soak the powder in water for 5-10 mins. "
            "Boil 1/2 liter of water, add the soaked barley powder, cook, and store it. Drink the same day."
        ),
        "clean_quantity": "1/2 liter",
        "clean_frequency": "Daily (same day)",
    },
    "Rice Soup": {
        "keywords": ["rice soup", "rice water", "kanjee", "kanji", "simmered down to 3 cups", "rice cooked and simmered"],
        "text": (
            "Wash about 150 gms white rice and cook with 5 cups of water. Drain the rice water and save rice. "
            "The rice water should be simmered down to 3 cups. Add Saindhav salt (Rock salt or Himalayan Pink salt) according to taste and serve hot. Drink the same day."
        ),
        "clean_quantity": "3 cups",
        "clean_frequency": "Daily (same day)",
    },
    "Tapioca Soup (Sabu Dana)": {
        "keywords": ["tapioca", "sabu dana", "sabudana", "sabu"],
        "text": (
            "Soak 2 teaspoons of tapioca (sabu dana). Add soaked tapioca in 1/2 liter of boiled water and cook for 5 mins. "
            "Add Saindhav salt (Rock salt or Himalayan Pink salt) according to taste and serve warm. Drink the same day."
        ),
        "clean_quantity": "1/2 liter",
        "clean_frequency": "Daily (same day)",
    },
    "Raagi Soup (Finger Millet)": {
        "keywords": ["raagi", "ragi", "finger millet"],
        "text": (
            "Grind 2 teaspoons of ragi seeds and soak in water. Boil 1/2 liter of water, add the ragi powder, and cook for 5-10 mins. "
            "Add Saindhav salt according to taste and serve. Drink the same day. (Avoid in Pitta Pacifying Diet)\n"
            "Remarks: IN KAPHA DIET ONLY."
        ),
        "clean_quantity": "1/2 liter",
        "clean_frequency": "Daily (Kapha diet only)",
    },
    "Jowar Soup": {
        "keywords": ["jowar", "sorghum"],
        "text": (
            "Grind 2 teaspoons of jowar seeds and soak in water. Boil 1/2 liter of water, add the jowar powder, and cook for 5 mins. "
            "Strain, add Saindhav salt according to taste, and serve. Drink the same day. (Avoid in Pitta Pacifying Diet)\n"
            "Remarks: IN KAPHA DIET ONLY."
        ),
        "clean_quantity": "1/2 liter",
        "clean_frequency": "Daily (Kapha diet only)",
    },
}


def match_protocol(name_or_text: str) -> str:
    if not name_or_text:
        return ""
    text_lower = str(name_or_text).lower().strip()
    for canon_name, data in SGP_PROTOCOLS.items():
        for kw in data["keywords"]:
            if kw in text_lower:
                return canon_name
    return ""


def clean_recipe_leakage(val: str, backup: str = "") -> str:
    if not val or not isinstance(val, str):
        return backup
    val_clean = val.strip()
    if len(val_clean) > 20:
        return backup
    leak_words = ["soaked", "cooked", "boiled", "simmered", "water", "seeds", "weight", "kilograms", "millimetres", "castor", "routine", "process", "ground", "jaggery"]
    if any(w in val_clean.lower() for w in leak_words):
        return backup
    if "90 spoon" in val_clean.lower():
        return backup
    return val_clean


def enrich_item_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    """Delegate to the CEI protocol registry (canonical names, 8 categories,
    doctor-first instruction priority)."""
    from app.casesheet.clinical_intelligence import enrich_protocol_item
    enriched = enrich_protocol_item(item)
    return enriched if isinstance(enriched, dict) else dict(item)


def enrich_string_item(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    from app.casesheet.clinical_intelligence import (
        classify_protocol_item, protocol_standard_instructions,
        CAT_OTHER,
    )
    _cat, canon = classify_protocol_item(text)
    if not canon:
        return text
    template = protocol_standard_instructions(canon)
    if template and template[:30].lower() not in text.lower():
        return f"{canon}:\n{template}"
    if canon.lower() not in text.lower():
        return f"{canon}: {text.strip()}"
    return text


def enrich_section_data(section: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data

    enriched = dict(data)

from app.casesheet.clinical_intelligence import (
    enrich_protocol_item, classify_protocol_item, is_protocol_section,
    apply_protocol_boundaries, CAT_OTHER,
)

# Keys known to hold enrichable protocol item lists, plus any other list of
# item-like dicts when the section itself is protocol-related (fixes the old
# fragile "array must be named exactly X" coupling).
_KNOWN_ITEM_KEYS = ("detox_items", "sessions", "exercises", "procedures", "items")


def enrich_section_data(section: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data

    enriched = dict(data)

    if is_protocol_section(section) or any(k in enriched for k in _KNOWN_ITEM_KEYS):
        arr_keys = [k for k, v in enriched.items()
                    if isinstance(v, list)
                    and (k in _KNOWN_ITEM_KEYS
                         or (is_protocol_section(section) and v
                             and any(isinstance(i, (dict, str)) for i in v)))]
        for arr_key in arr_keys:
            items = enriched.get(arr_key)
            new_items = []
            seen_canons = set()
            for item in items:
                if isinstance(item, dict):
                    e_item = enrich_protocol_item(item)
                    canon_name = str(e_item.get("name", "")).strip().lower()
                    if canon_name and canon_name in seen_canons:
                        continue
                    if canon_name:
                        seen_canons.add(canon_name)
                    new_items.append(e_item)
                elif isinstance(item, str):
                    # Upgrade recognized strings to structured canonical items
                    # (single dialect: dicts with name/category/instructions).
                    e_item = enrich_protocol_item(item)
                    if isinstance(e_item, dict):
                        canon_name = str(e_item.get("name", "")).strip().lower()
                        if canon_name and canon_name in seen_canons:
                            continue
                        if canon_name:
                            seen_canons.add(canon_name)
                        new_items.append(e_item)
                    else:
                        key = item.strip().lower()
                        if key in seen_canons:
                            continue
                        seen_canons.add(key)
                        new_items.append(item)
                else:
                    new_items.append(item)
            enriched[arr_key] = new_items

    if section == "assessment_and_plan" or "plan" in enriched:
        plan = enriched.get("plan")
        if isinstance(plan, dict):
            new_plan = dict(plan)
            for k in ("home_remedies", "lifestyle_advice"):
                arr = new_plan.get(k, [])
                if isinstance(arr, list):
                    new_arr = []
                    seen = set()
                    for x in arr:
                        item_val = enrich_string_item(x) if isinstance(x, str) else x
                        key_val = str(item_val).lower().strip()[:30]
                        if key_val in seen:
                            continue
                        seen.add(key_val)
                        new_arr.append(item_val)
                    new_plan[k] = new_arr
            enriched["plan"] = new_plan

    return enriched

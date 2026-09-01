"""
Novadigm Health — WhatsApp Chatbot FAQ Database
All patient-facing FAQ content, emergency keywords, safety rules, and menu structure.
"""

# ── Emergency Detection ──────────────────────────────────────────────────────

EMERGENCY_KEYWORDS = [
    "emergency", "dying", "die", "unconscious", "fainted", "collapse", "collapsed",
    "chest pain", "heart attack", "cant breathe", "can't breathe", "difficulty breathing",
    "severe pain", "seizure", "convulsion", "stroke", "bleeding heavily",
    "loss of consciousness", "not responding", "vomiting blood", "black stool",
    "very low bp", "bp very low", "sugar very low", "sugar critically low",
    "paralysis", "paralysed", "swollen throat", "swollen face", "anaphylaxis",
    "allergic reaction", "call ambulance", "ambulance", "icu", "life threatening",
    "very low blood sugar", "hypoglycemia severe", "cannot stand", "falling",
]

EMERGENCY_RESPONSE = (
    "⚠️ *Your message may describe a condition requiring urgent medical assessment.*\n\n"
    "This chatbot *cannot* evaluate or manage medical emergencies.\n\n"
    "🏥 Please seek *immediate medical attention* at the nearest emergency department "
    "or contact your local emergency medical service.\n\n"
    "*Do not wait for a WhatsApp reply.*"
)

ESCALATION_RESPONSE = (
    "Your question requires individualized clinical advice. I will help route your query "
    "to the appropriate *Novadigm Health* clinical team.\n\n"
    "⚠️ Please do *not* change, stop or increase any prescribed medicine or procedure "
    "while waiting unless a qualified treating healthcare professional has instructed you to do so.\n\n"
    "If your condition is urgent or worsening, please seek immediate medical care "
    "rather than waiting for a WhatsApp response."
)

IMPORTANT_NOTICE = (
    "📋 *Important Notice*\n\n"
    "This chatbot provides general information only. It does not diagnose diseases, "
    "prescribe medicines, or replace consultation with a qualified healthcare professional.\n\n"
    "_Your individual prescription and doctor's instructions always take priority over general chatbot information._"
)

# ── FAQ Data ──────────────────────────────────────────────────────────────────

FAQ_DATA = {

    "medicines": {
        "title": "Medicines / Supplements",
        "emoji": "💊",
        "questions": [
            {
                "id": "med_1",
                "short_title": "Medicine timing schedule",
                "answer": (
                    "💊 *Medicine Timing (General Guide)*\n\n"
                    "Unless your prescription states otherwise:\n"
                    "• *Morning:* 6:00 AM – 8:00 AM\n"
                    "• *Evening:* 6:00 PM – 8:00 PM\n"
                    "• Generally taken on an *empty stomach* before food.\n\n"
                    "⚠️ Some medicines have different timings. Always follow your individual prescription."
                ),
            },
            {
                "id": "med_2",
                "short_title": "Medicines taken after food",
                "answer": (
                    "💊 *Medicines After Food*\n\n"
                    "Certain medicines have special instructions:\n"
                    "• *D-Tox:* 2 hours after food\n"
                    "• *Lithozen:* 20 minutes after food\n"
                    "• *Carcincure R:* 2 hours after food\n\n"
                    "Always follow the latest prescription provided to you."
                ),
            },
            {
                "id": "med_3",
                "short_title": "Gap between medicines",
                "answer": (
                    "💊 *Gap Between Medicines*\n\n"
                    "• After *APD:* 15-minute gap before the next medicine.\n"
                    "• Between other medicines: generally a 5-minute gap.\n"
                    "• Follow the medicine sequence in your prescription."
                ),
            },
            {
                "id": "med_4",
                "short_title": "Milk or water with medicines",
                "answer": (
                    "💊 *Medicine Medium*\n\n"
                    "• *Tablets:* usually with water\n"
                    "• *Powder medicines:* usually with milk\n"
                    "• *Lithozen:* with ginger tea\n\n"
                    "Please follow your prescription rather than changing the medium yourself."
                ),
            },
            {
                "id": "med_5",
                "short_title": "Can't get prescribed milk",
                "answer": (
                    "💊 *Milk Substitution*\n\n"
                    "Please do not substitute the prescribed medium on your own.\n\n"
                    "Contact the patient-care team — the doctor/team can advise an appropriate alternative "
                    "based on your prescription and medical condition."
                ),
            },
            {
                "id": "med_6",
                "short_title": "Missed a dose",
                "answer": (
                    "💊 *Missed Dose*\n\n"
                    "Do *not* automatically double the next dose.\n\n"
                    "Continue according to your prescribed schedule. "
                    "If unsure, please contact the patient-care team."
                ),
            },
            {
                "id": "med_7",
                "short_title": "Changing supplement quantity",
                "answer": (
                    "💊 *Changing Supplement Quantity*\n\n"
                    "⚠️ *No.* Take only the quantity prescribed to you.\n\n"
                    "Any increase, reduction, addition or discontinuation should be "
                    "discussed with your treating doctor."
                ),
            },
            {
                "id": "med_8",
                "short_title": "Stopping if feeling better",
                "answer": (
                    "💊 *Stopping Medicines When Feeling Better*\n\n"
                    "Please do not make treatment changes solely on the basis of improved symptoms.\n\n"
                    "Contact your treating team for review before stopping or modifying prescribed treatment."
                ),
            },
        ],
    },

    "other_meds": {
        "title": "Other / Allopathic Medicines",
        "emoji": "🏥",
        "questions": [
            {
                "id": "omed_1",
                "short_title": "Continue regular medicines",
                "answer": (
                    "🏥 *Continuing Regular Medicines*\n\n"
                    "Do *not* stop or change an existing prescribed medicine without medical advice.\n\n"
                    "Please inform the Novadigm clinical team about *all* medicines, OTC products "
                    "and supplements you are currently taking."
                ),
            },
            {
                "id": "omed_2",
                "short_title": "Stop diabetes/BP/thyroid meds",
                "answer": (
                    "🏥 *Chronic Disease Medicines*\n\n"
                    "⚠️ *No.* Do not stop them on your own.\n\n"
                    "Suddenly stopping certain medicines can cause serious complications. "
                    "Any adjustment should be made only after review by the appropriate treating physician."
                ),
            },
            {
                "id": "omed_3",
                "short_title": "New medicine from another doctor",
                "answer": (
                    "🏥 *New Medicine From Another Doctor*\n\n"
                    "Follow the advice of the prescribing healthcare professional.\n\n"
                    "Please also inform the Novadigm clinical team so your complete medication list "
                    "can be reviewed for possible interactions."
                ),
            },
        ],
    },

    "diet": {
        "title": "Diet Instructions",
        "emoji": "🥗",
        "questions": [
            {
                "id": "diet_1",
                "short_title": "Following diet chart strictly",
                "answer": (
                    "🥗 *Diet Chart*\n\n"
                    "Please follow the personalized diet plan provided to you.\n\n"
                    "The general guide recommends following it for *8 weeks*, with the diet selected "
                    "according to your prescribed diet type."
                ),
            },
            {
                "id": "diet_2",
                "short_title": "Vegetables to avoid",
                "answer": (
                    "🥗 *Vegetables to Avoid (CCRSTT)*\n\n"
                    "• Cabbage\n• Cauliflower\n• Radish\n• Spinach\n• Tomato\n• Tamarind\n\n"
                    "_Note: Cabbage may be permitted in the specifically instructed soup preparation._\n\n"
                    "Always follow your individual diet chart if it differs."
                ),
            },
            {
                "id": "diet_3",
                "short_title": "Tomato & tamarind alternatives",
                "answer": (
                    "🥗 *Alternatives to Tomato & Tamarind*\n\n"
                    "• Raw mango\n• Aamchur powder\n• Amla\n\n"
                    "*For chillies/spices:*\n• Ginger\n• Ajwain\n• Cinnamon\n\n"
                    "Your individual dietary restrictions take priority."
                ),
            },
            {
                "id": "diet_4",
                "short_title": "Nuts to take",
                "answer": (
                    "🥗 *Nuts (Overnight Soaked)*\n\n"
                    "• Cashews – 5\n• Almonds – 5\n• Groundnuts – 2 tablespoons\n\n"
                    "Generally taken 10–20 minutes after medicines.\n\n"
                    "⚠️ *Kidney patients:* half the listed quantity. Please confirm with your clinician."
                ),
            },
            {
                "id": "diet_5",
                "short_title": "Honey/jaggery for diabetics",
                "answer": (
                    "🥗 *Honey / Jaggery for Diabetic Patients*\n\n"
                    "Honey and jaggery contain sugars and *can affect blood glucose.*\n\n"
                    "If specifically included in your prescription, use only the instructed quantity "
                    "and monitor your glucose. If your glucose is uncontrolled, contact your doctor first."
                ),
            },
            {
                "id": "diet_6",
                "short_title": "Soups & fennel water",
                "answer": (
                    "🥗 *Soups & Fennel Water*\n\n"
                    "*Suitable soups:*\n"
                    "• Barley • Sabudana/Tapioca • Rice • Broccoli\n\n"
                    "*Fennel water:* ~2 litres/day for 60 kg, but varies by weight, doctor's advice "
                    "and fluid restrictions. Patients with kidney/heart/liver disease should follow "
                    "their prescribed fluid allowance."
                ),
            },
        ],
    },

    "exercise": {
        "title": "Exercise & Breathing",
        "emoji": "🧘",
        "questions": [
            {
                "id": "ex_1",
                "short_title": "What exercises to do",
                "answer": (
                    "🧘 *Prescribed Exercise Program*\n\n"
                    "Only perform exercises prescribed or demonstrated to you:\n"
                    "• DNB breathing exercise\n"
                    "• Leg exercises\n"
                    "• Hand exercises\n"
                    "• Naukasan (Boat Pose)\n"
                    "• Suryanamaskar (when appropriate)\n\n"
                    "Follow your personalized exercise instructions carefully."
                ),
            },
            {
                "id": "ex_2",
                "short_title": "DNB breathing exercise",
                "answer": (
                    "🧘 *DNB Breathing Exercise*\n\n"
                    "• Perform *left to right*\n"
                    "• ~10 minutes after waking\n"
                    "• ~10 minutes before sleep\n\n"
                    "⚠️ Reverse DNB (right to left) only when *specifically prescribed* by your doctor."
                ),
            },
            {
                "id": "ex_3",
                "short_title": "When to start Suryanamaskar",
                "answer": (
                    "🧘 *Suryanamaskar*\n\n"
                    "Begin only after you can hold Naukasan for at least *40 seconds* "
                    "without neck or back pain.\n\n"
                    "⚠️ Patients with cardiovascular disease, recent surgery, severe joint/spine "
                    "problems, dizziness or uncontrolled BP should obtain medical clearance first.\n\n"
                    "Stop and seek medical advice if you develop concerning symptoms."
                ),
            },
        ],
    },

    "oils": {
        "title": "Oils & Daily Procedures",
        "emoji": "🫙",
        "questions": [
            {
                "id": "oil_1",
                "short_title": "Pain oil application",
                "answer": (
                    "🫙 *Pain Oil Application*\n\n"
                    "May be applied daily and left overnight, or for at least 1 hour "
                    "when overnight application is not possible.\n\n"
                    "Do *not* apply to broken, infected or irritated skin unless advised by your clinician."
                ),
            },
            {
                "id": "oil_2",
                "short_title": "Neelibringadi Keera Thailam",
                "answer": (
                    "🫙 *Neelibringadi Keera Thailam*\n\n"
                    "Apply to the scalp like hair oil, daily or on alternate days, "
                    "leaving it overnight or for the instructed duration.\n\n"
                    "Follow your individual prescription."
                ),
            },
            {
                "id": "oil_3",
                "short_title": "Anutailam (nose & ear drops)",
                "answer": (
                    "🫙 *Anutailam*\n\n"
                    "General: 2 drops in each nostril and ear, once daily for two weeks.\n\n"
                    "Follow the exact procedure demonstrated or prescribed to you. "
                    "Contact your clinician if you experience irritation, pain, bleeding or difficulty breathing."
                ),
            },
            {
                "id": "oil_4",
                "short_title": "Steam inhalation",
                "answer": (
                    "🫙 *Steam Inhalation*\n\n"
                    "• Once daily for 2 weeks\n"
                    "• During cold/cough/sinus: twice daily\n\n"
                    "Avoid excessively hot steam (risk of burns). "
                    "Elderly patients and those with respiratory or cardiovascular conditions "
                    "should use steam only with appropriate supervision."
                ),
            },
            {
                "id": "oil_5",
                "short_title": "Gandusham / Oil Pulling",
                "answer": (
                    "🫙 *Gandusham (Oil Pulling)*\n\n"
                    "Sesame oil, once daily for two weeks.\n\n"
                    "Do *not* swallow the oil. Follow the procedure demonstrated by your care team."
                ),
            },
            {
                "id": "oil_6",
                "short_title": "Castor oil / Nithya Virechana",
                "answer": (
                    "🫙 *Castor Oil Procedure*\n\n"
                    "Only perform if *specifically prescribed* for you.\n\n"
                    "General: Take before sleep, ~2 hours after dinner, following the prescribed warm-water preparation.\n\n"
                    "⚠️ Not suitable for everyone — pregnant patients, elderly/frail, those with diarrhea, "
                    "abdominal pain, or significant medical conditions should obtain specific medical advice first."
                ),
            },
        ],
    },

    "symptoms": {
        "title": "Symptoms / Treatment Query",
        "emoji": "🌡️",
        "questions": [
            {
                "id": "sym_1",
                "short_title": "Body pain increased",
                "answer": (
                    "🌡️ *Increased Body Pain*\n\n"
                    "Do not assume increased pain is a normal treatment reaction.\n\n"
                    "If pain is new, severe, persistent, worsening, or associated with weakness, "
                    "numbness, chest pain, breathing difficulty, injury or fever — please obtain medical assessment.\n\n"
                    "Contact the clinical team for review."
                ),
            },
            {
                "id": "sym_2",
                "short_title": "Blood sugar increasing",
                "answer": (
                    "🌡️ *Rising Blood Sugar*\n\n"
                    "• Continue monitoring as advised\n"
                    "• Check your prescribed diet, exercise and medication plan\n"
                    "• Do *not* change medicine doses yourself\n"
                    "• Contact your doctor if readings remain above your individualized target\n\n"
                    "⚠️ Seek *urgent medical attention* for very high readings with vomiting, "
                    "severe weakness, dehydration, confusion or breathing difficulty."
                ),
            },
            {
                "id": "sym_3",
                "short_title": "Low blood sugar",
                "answer": (
                    "🌡️ *Low Blood Glucose*\n\n"
                    "Follow the hypoglycaemia plan provided by your treating doctor.\n\n"
                    "⚠️ If you experience sweating, trembling, confusion, unusual drowsiness "
                    "or loss of consciousness — obtain *immediate assistance.*\n\n"
                    "Do not use the chatbot to adjust diabetes medication."
                ),
            },
            {
                "id": "sym_4",
                "short_title": "Low blood pressure",
                "answer": (
                    "🌡️ *Low Blood Pressure*\n\n"
                    "Sit or lie down safely and recheck if possible.\n\n"
                    "⚠️ If the low reading persists, or you experience fainting, chest pain, "
                    "severe weakness, breathing difficulty or confusion — seek *prompt medical attention.*\n\n"
                    "Do not stop or reduce BP medicines without medical advice."
                ),
            },
            {
                "id": "sym_5",
                "short_title": "Loose stools / diarrhoea",
                "answer": (
                    "🌡️ *Loose Stools / Diarrhoea*\n\n"
                    "Maintain hydration according to your medical restrictions and contact the clinical team "
                    "if symptoms are significant or persistent.\n\n"
                    "⚠️ Seek *prompt medical attention* for: very frequent/severe diarrhoea, blood in stools, "
                    "persistent vomiting, severe abdominal pain, high fever, dizziness or signs of dehydration."
                ),
            },
            {
                "id": "sym_6",
                "short_title": "Constipation / bloating",
                "answer": (
                    "🌡️ *Constipation / Bloating*\n\n"
                    "Continue only the diet, fluids and procedures already prescribed.\n\n"
                    "Contact the clinical team if persistent or worsening.\n\n"
                    "⚠️ Seek *urgent assessment* for severe abdominal pain, persistent vomiting, "
                    "marked abdominal swelling or blood in stools."
                ),
            },
            {
                "id": "sym_7",
                "short_title": "Loss of appetite",
                "answer": (
                    "🌡️ *Loss of Appetite*\n\n"
                    "Do not automatically stop all medicines.\n\n"
                    "Contact the clinical team, particularly if accompanied by vomiting, severe weakness, "
                    "abdominal pain, dehydration, jaundice, significant weight loss or other concerning symptoms.\n\n"
                    "The clinician will advise whether any medicine needs to be held or changed."
                ),
            },
            {
                "id": "sym_8",
                "short_title": "Feeling unusually hot / fever",
                "answer": (
                    "🌡️ *Feeling Hot / Fever*\n\n"
                    "Measure your temperature with a thermometer if available.\n\n"
                    "Maintain appropriate hydration unless under fluid restriction.\n\n"
                    "If you have a significant or persistent fever, contact a healthcare professional."
                ),
            },
        ],
    },

    "dispatch": {
        "title": "Medicine Dispatch / Delivery",
        "emoji": "📦",
        "questions": [
            {
                "id": "dis_1",
                "short_title": "Check dispatch status",
                "answer": (
                    "📦 *Medicine Dispatch Status*\n\n"
                    "To track your order, please select:\n"
                    "➡️ *Medicine Dispatch → Track Order*\n\n"
                    "Or provide your registered mobile number or order reference and "
                    "we will connect you to the dispatch team."
                ),
            },
            {
                "id": "dis_2",
                "short_title": "Medicines are about to finish",
                "answer": (
                    "📦 *Medicines About to Finish*\n\n"
                    "Please contact the patient-care/dispatch team *sufficiently in advance.*\n\n"
                    "Do not substitute another product or change your treatment because supply is running low "
                    "without appropriate advice from your doctor."
                ),
            },
            {
                "id": "dis_3",
                "short_title": "Damaged package / missing item",
                "answer": (
                    "📦 *Damaged Package / Missing Item*\n\n"
                    "Do not use a product if the container is damaged, seal integrity is compromised, "
                    "or the product appears contaminated.\n\n"
                    "Please send clear photographs of:\n"
                    "• Outer package\n• Product\n• Label\n• Batch details\n\n"
                    "The support team will assist you."
                ),
            },
        ],
    },

    "appointments": {
        "title": "Appointments & Follow-up",
        "emoji": "📅",
        "questions": [
            {
                "id": "appt_1",
                "short_title": "How to book a consultation",
                "answer": (
                    "📅 *Book a Consultation*\n\n"
                    "Select *Book Appointment* from the main menu.\n\n"
                    "We will collect:\n"
                    "• Patient name\n"
                    "• Existing or new patient\n"
                    "• Registered mobile number\n"
                    "• Preferred consultation location/mode\n"
                    "• Preferred date\n"
                    "• Reason for follow-up\n\n"
                    "_Final appointment confirmation depends on availability._"
                ),
            },
            {
                "id": "appt_2",
                "short_title": "When to schedule follow-up",
                "answer": (
                    "📅 *Follow-up Scheduling*\n\n"
                    "Follow the review date advised by your doctor.\n\n"
                    "If no review date was provided, the chatbot can forward an appointment request "
                    "to the patient-care team."
                ),
            },
            {
                "id": "appt_3",
                "short_title": "What to prepare for follow-up",
                "answer": (
                    "📅 *For Your Follow-up Consultation*\n\n"
                    "Please keep ready:\n"
                    "• Latest prescription/visit report\n"
                    "• Current medicine list\n"
                    "• Recent investigations/reports\n"
                    "• BP/glucose records (where advised)\n"
                    "• Details of new symptoms\n"
                    "• Medicines started by another doctor\n"
                    "• Questions you would like to discuss"
                ),
            },
        ],
    },

    "special": {
        "title": "Pregnancy, Surgery & Special",
        "emoji": "🤰",
        "questions": [
            {
                "id": "sp_1",
                "short_title": "Pregnant / breastfeeding",
                "answer": (
                    "🤰 *Pregnancy / Breastfeeding*\n\n"
                    "Please inform your treating doctor promptly.\n\n"
                    "Do not assume that every supplement, herbal preparation, oil, procedure or exercise "
                    "is suitable during pregnancy or breastfeeding.\n\n"
                    "Your treatment plan should be individually reviewed."
                ),
            },
            {
                "id": "sp_2",
                "short_title": "Scheduled for surgery",
                "answer": (
                    "🤰 *Planned Surgery / Medical Procedure*\n\n"
                    "Inform both:\n"
                    "1. Your surgeon/procedure team about all supplements and medicines you take\n"
                    "2. The Novadigm clinical team about your planned procedure\n\n"
                    "Some products may need specific peri-operative instructions from the treating doctors."
                ),
            },
            {
                "id": "sp_3",
                "short_title": "Allergy or reaction to medicine",
                "answer": (
                    "🤰 *Allergy / Reaction*\n\n"
                    "Stop using the suspected non-essential product and contact a healthcare professional.\n\n"
                    "⚠️ Seek *emergency medical assistance immediately* for breathing difficulty, "
                    "swelling of the face/tongue/throat, collapse, severe widespread rash "
                    "or other signs of a serious allergic reaction."
                ),
            },
        ],
    },
}

# ── Main Menu (Section 23) ────────────────────────────────────────────────────

MAIN_MENU_ROWS = [
    {"id": "menu_medicines",    "title": "💊 Medicines & Supplements",  "description": "Timing, dosage, missed dose, medium"},
    {"id": "menu_other_meds",   "title": "🏥 Other / Allopathic Meds",  "description": "Continuing existing medications"},
    {"id": "menu_diet",         "title": "🥗 Diet Instructions",         "description": "Diet chart, vegetables, nuts, soups"},
    {"id": "menu_exercise",     "title": "🧘 Exercise & Breathing",      "description": "DNB breathing, Naukasan, Surya"},
    {"id": "menu_oils",         "title": "🫙 Oils & Procedures",         "description": "Pain oil, Anutailam, steam, castor"},
    {"id": "menu_symptoms",     "title": "🌡️ Symptoms / Treatment Query", "description": "Pain, BP, sugar, fever, digestion"},
    {"id": "menu_appointments", "title": "📅 Appointments & Follow-up",  "description": "Book or reschedule consultation"},
    {"id": "menu_dispatch",     "title": "📦 Medicine Dispatch",         "description": "Track order, missing items"},
    {"id": "menu_special",      "title": "🤰 Special Situations",        "description": "Pregnancy, surgery, reactions"},
    {"id": "menu_team",         "title": "🩺 Speak to Patient-Care Team","description": "Connect with clinical support"},
]

# Maps menu IDs to FAQ_DATA keys
MENU_TO_FAQ_KEY = {
    "menu_medicines":    "medicines",
    "menu_other_meds":   "other_meds",
    "menu_diet":         "diet",
    "menu_exercise":     "exercise",
    "menu_oils":         "oils",
    "menu_symptoms":     "symptoms",
    "menu_appointments": "appointments",
    "menu_dispatch":     "dispatch",
    "menu_special":      "special",
}

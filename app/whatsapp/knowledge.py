"""Approved, universal patient-support knowledge for the WhatsApp bot.

The content in this module is derived from the clinician-provided Novadigm
patient FAQ and official Novadigm product pages. It is deliberately general:
the bot does not inspect or explain an individual patient's prescription. A
future admin workflow can move these records into PostgreSQL without changing
the retrieval interface.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, List, Sequence


EMERGENCY_MESSAGE = (
    "⚠️ Your message may describe a condition requiring urgent medical "
    "assessment. This chatbot cannot evaluate or manage medical emergencies. "
    "Please seek immediate medical attention at the nearest emergency "
    "department or contact your local emergency medical service. Do not wait "
    "for a WhatsApp reply."
)

ESCALATION_MESSAGE = (
    "Your question requires individualized clinical advice. Please contact the "
    "Novadigm Health clinical team before changing, stopping or increasing any "
    "prescribed medicine or procedure. If your condition is urgent or "
    "worsening, seek immediate medical care rather than waiting for a WhatsApp "
    "response."
)


@dataclass(frozen=True)
class KnowledgeArticle:
    article_id: str
    title: str
    keywords: Sequence[str]
    answer: str
    category: str
    source_urls: Sequence[str] = ()


@dataclass(frozen=True)
class KnowledgeMatch:
    article: KnowledgeArticle
    score: int


ARTICLES: Sequence[KnowledgeArticle] = (
    KnowledgeArticle(
        "organization",
        "Novadigm Health services",
        ("novadigm", "sgp", "services", "what do you treat", "specialities", "specialties"),
        "Novadigm Health provides personalized integrative-care consultations that may combine modern clinical assessment, Ayurveda-informed care, supportive therapies, diet and lifestyle planning. The appropriate approach depends on a clinician's assessment; the chatbot cannot diagnose a condition or promise an outcome.",
        "organization",
    ),
    KnowledgeArticle(
        "docture_poly_overview",
        "What is Docture-Poly VPK42?",
        (
            "docture poly", "docture-poly", "doctor poly", "doctor-poly",
            "docturepoly", "vpk42 device", "vpk 42 device", "health device",
            "wellness device", "what is the device",
        ),
        "Docture-Poly VPK42 is a non-invasive physiological-signal platform "
        "derived from the PRISM Ayurveda research framework. It uses "
        "sensor-based pulse-wave and PPG signals, HRV-related computation and "
        "AI/ML analysis to create a VPK42 homeostasis fingerprint across 42 "
        "organ-system domains. It is intended to support wellness monitoring, "
        "preventive-health awareness and physician review; it is not an "
        "independent diagnostic or treatment device.",
        "docture_poly",
        (
            "https://docture-poly.com/",
            "https://docture-poly.com/about-us/",
        ),
    ),
    KnowledgeArticle(
        "docture_poly_scan",
        "How a Docture-Poly scan works",
        (
            "how does docture poly work", "how does doctor poly work",
            "how device works", "how scan works", "device scan", "vpk42 scan",
            "finger scan", "sensor scan", "ppg", "pulse wave", "max30102",
            "does it need blood", "blood sample", "needle",
        ),
        "The official product information describes a non-invasive scan using "
        "physiological signals, including PPG or pulse-wave morphology and "
        "HRV-derived computation. The sensor data is processed into structured "
        "VPK42 and preventive-wellness insights for professional review. It is "
        "not a blood sample or a substitute for a laboratory test.",
        "docture_poly",
        (
            "https://docture-poly.com/",
            "https://docture-poly.com/about-us/",
        ),
    ),
    KnowledgeArticle(
        "docture_poly_vpk42",
        "Meaning of VPK42",
        (
            "what is vpk42", "vpk42", "vpk 42", "v p k 42",
            "variability processing kinetics", "42 organ systems",
            "42 domains", "homeostasis fingerprint",
        ),
        "In this platform, VPK means Variability, Processing and Kinetics. "
        "Variability relates to changing electrophysiological and autonomic "
        "patterns; Processing relates to functions such as digestion, "
        "metabolism and energy conversion; and Kinetics relates to structure, "
        "strength, stability, fluid behaviour and mechanical reserve. The "
        "number 42 refers to the organ-system domains used in the fingerprint; "
        "these outputs are physician-reviewable estimates, not diagnoses.",
        "docture_poly",
        ("https://docture-poly.com/",),
    ),
    KnowledgeArticle(
        "docture_poly_outputs",
        "What a Docture-Poly scan may provide",
        (
            "what does the scan show", "scan report", "device report",
            "vpk42 fingerprint", "regeneration readiness",
            "regeneration readiness index", "nova diet", "nexa nova",
            "yoga regimen", "what are the results", "device output",
        ),
        "The official product page lists a VPK42 organ-system fingerprint, a "
        "Regeneration Readiness Index, non-invasive OMICS trend indicators, "
        "NOVA or NEXA-NOVA nutrition guidance, and YOGA-based movement or "
        "breathing guidance. These are decision-support and wellness outputs "
        "that need appropriate professional review; they are not confirmed "
        "medical diagnoses or exact laboratory results.",
        "docture_poly",
        ("https://docture-poly.com/",),
    ),
    KnowledgeArticle(
        "docture_poly_readiness",
        "Regeneration Readiness Index",
        (
            "what is regeneration readiness", "readiness index",
            "regeneration index", "ready for repair", "recovery readiness",
        ),
        "The Regeneration Readiness Index is described as an estimated signal "
        "of whether an organ-system domain appears prepared for repair, "
        "recovery and adaptation. It should be interpreted as a platform "
        "indicator for physician review, not proof that an organ will "
        "regenerate or that a disease has improved.",
        "docture_poly",
        ("https://docture-poly.com/",),
    ),
    KnowledgeArticle(
        "docture_poly_omics",
        "Docture-Poly OMICS trend indicators",
        (
            "omics", "omics module", "blood value", "lab value",
            "biochemical value", "hba1c", "glucose estimate",
            "metabolic marker", "chemistry signature",
        ),
        "The OMICS module provides organ-mapped indicative chemistry signals "
        "and non-invasive metabolic trend indicators. They are algorithmic "
        "estimates, not exact biochemical or laboratory measurements. Any "
        "clinically important result, including an HbA1c-related trend, must "
        "be confirmed with standard laboratory testing when appropriate.",
        "docture_poly",
        (
            "https://docture-poly.com/",
            "https://docture-poly.com/research/",
        ),
    ),
    KnowledgeArticle(
        "docture_poly_recommendations",
        "Diet, yoga and supplement guidance from Docture-Poly",
        (
            "device diet", "scan diet", "nova diet", "nexa nova diet",
            "device yoga", "yoga recommendation", "exercise recommendation",
            "supplement recommendation", "does device prescribe",
        ),
        "Docture-Poly may support personalized nutrition, movement, yoga, "
        "breathing and supplement considerations based on its VPK42 outputs. "
        "These suggestions require the stated safety filters and physician "
        "review. The device does not independently prescribe treatment, and "
        "its output must not be used to start, stop or change prescribed "
        "medicines without the treating clinician.",
        "docture_poly",
        ("https://docture-poly.com/",),
    ),
    KnowledgeArticle(
        "docture_poly_limits",
        "What Docture-Poly cannot do",
        (
            "does docture poly diagnose", "can docture poly diagnose",
            "does doctor poly diagnose", "can vpk42 diagnose",
            "can device diagnose", "does device diagnose", "device detect disease",
            "device cure", "vpk42 cure", "device prevent disease",
            "device replace doctor", "device replace blood test",
            "device replace lab", "device replace ecg", "device replace scan",
            "emergency device",
        ),
        "Docture-Poly does not independently diagnose, treat, cure, mitigate "
        "or prevent disease. It does not replace a doctor, laboratory testing, "
        "ECG, medical imaging, specialist consultation, emergency care or "
        "prescribed treatment. Concerning symptoms and abnormal outputs still "
        "need assessment through the appropriate standard medical pathway.",
        "docture_poly",
        (
            "https://docture-poly.com/",
            "https://docture-poly.com/about-us/",
        ),
    ),
    KnowledgeArticle(
        "docture_poly_accuracy",
        "Accuracy and validation of Docture-Poly",
        (
            "device accuracy", "docture poly accuracy", "doctor poly accuracy",
            "vpk42 accuracy", "how accurate is the device",
            "is the device accurate", "is docture poly clinically validated",
            "is vpk42 clinically validated", "device validation",
            "device evidence", "device research proof", "device false result",
        ),
        "The platform is based on PRISM Ayurveda translational research and "
        "physiological-signal analysis. Its official research page also states "
        "that proprietary organ-wise, OMICS and regeneration-readiness "
        "performance claims require prospective comparator datasets and "
        "appropriate validation. The chatbot therefore cannot promise an "
        "accuracy percentage or treat a result as a confirmed diagnosis.",
        "docture_poly",
        ("https://docture-poly.com/research/",),
    ),
    KnowledgeArticle(
        "docture_poly_comfort",
        "Is the Docture-Poly scan invasive or painful?",
        (
            "is the scan painful", "does the scan hurt", "is the scan invasive",
            "is device invasive", "non invasive device", "non-invasive device",
            "device safety", "is the device safe", "is docture poly safe",
            "docture poly device safe", "is vpk42 safe", "device side effect",
            "vpk42 side effect",
        ),
        "The official product information describes Docture-Poly as "
        "non-invasive and sensor-based. It does not describe the VPK42 scan as "
        "requiring a needle or blood collection. Suitability, preparation and "
        "any precautions for a particular device model or health condition "
        "should still be confirmed with the trained team before use.",
        "docture_poly",
        (
            "https://docture-poly.com/",
            "https://docture-poly.com/about-us/",
        ),
    ),
    KnowledgeArticle(
        "docture_poly_monitoring",
        "Scan versus continuous monitoring",
        (
            "continuous monitoring", "24 hour monitoring", "24/7 monitoring",
            "around the clock", "wearable", "ring", "one scan",
            "blood pressure monitor", "oxygen monitor", "heart rate monitor",
        ),
        "The public website refers both to a non-invasive VPK42 scan and to "
        "broader monitoring products. Do not assume every Docture-Poly model "
        "has continuous, blood-pressure, oxygen or wearable monitoring. Please "
        "ask the product team which exact model is being offered and what that "
        "model measures before purchase or use.",
        "docture_poly",
        ("https://docture-poly.com/",),
    ),
    KnowledgeArticle(
        "docture_poly_availability",
        "Docture-Poly demo, price and availability",
        (
            "device price", "how much device", "cost of device", "buy device",
            "purchase device", "book demo", "device demo", "try device",
            "where available", "where can i buy", "scan price", "scan cost",
            "scan duration", "how long does scan take",
        ),
        "The official website offers options to explore products, book a demo "
        "and enquire about purchase, but the approved chatbot knowledge does "
        "not contain a verified current price, scan duration or location-wise "
        "availability. Please contact the product or patient-care team on "
        "7331109988, email info@sgprs.com, or visit docture-poly.com for the "
        "current details.",
        "docture_poly",
        ("https://docture-poly.com/",),
    ),
    KnowledgeArticle(
        "docture_poly_regulatory_privacy",
        "Docture-Poly approval and data privacy",
        (
            "fda approved", "ce approved", "regulatory approval",
            "government approved", "medical device approval", "certified",
            "device privacy", "device data", "where is data stored",
            "who can see data", "privacy policy",
        ),
        "The reviewed public product pages do not provide enough verified "
        "information for this chatbot to confirm a current regulatory approval, "
        "certification or the complete handling of scan data. Please request "
        "the applicable approval documents, product label and current privacy "
        "policy from the official product team before relying on such a claim "
        "or sharing personal health data.",
        "docture_poly",
        (
            "https://docture-poly.com/",
            "https://docture-poly.com/research/",
        ),
    ),
    KnowledgeArticle(
        "clinic_contact",
        "Clinic contact and location",
        ("contact", "phone", "number", "location", "address", "clinic hours", "timings", "where are you"),
        "Novadigm Health is located at BO-1, B Block, Indu Fortune Fields The Annexe, beside Indu Villas, 13th Phase Road, KPHB Colony, Hyderabad, Telangana 500085. The currently published patient-care number is 7331109988. Please confirm current operating hours with the patient-care team before travelling.",
        "administration",
    ),
    KnowledgeArticle(
        "appointments",
        "Appointments and follow-up",
        ("appointment", "book consultation", "consultation", "follow up", "follow-up", "review date", "reschedule"),
        "You can request a consultation or follow-up through this WhatsApp service. Final confirmation depends on practitioner availability. For a follow-up, keep your latest visit report, current medicine list, recent investigations, advised BP or glucose records, new symptoms and questions ready.",
        "administration",
    ),
    KnowledgeArticle(
        "medicine_timing",
        "General medicine timing",
        ("medicine timing", "supplement timing", "when to take", "before food", "empty stomach", "morning medicine", "evening medicine"),
        "Unless the latest prescription says otherwise, the general schedule is morning between 6–8 AM and evening between 6–8 PM, usually before food on an empty stomach. Individual prescription instructions always take priority.",
        "medicines",
    ),
    KnowledgeArticle(
        "special_medicine_timing",
        "Special medicine timing",
        ("d-tox", "detox tablet", "lithozen", "carcincure", "after food"),
        "The general patient guide lists D-Tox about 2 hours after food, Lithozen about 20 minutes after food with ginger tea, and Carcincure R about 2 hours after food. Always follow the latest prescription if it differs.",
        "medicines",
    ),
    KnowledgeArticle(
        "medicine_gap",
        "Gap between medicines",
        ("gap between medicines", "medicine gap", "apd gap", "sequence of medicines", "how many minutes"),
        "Unless instructed differently, the general guide recommends a 15-minute gap after APD before the next medicine and about 5 minutes between other medicines. Follow the sequence in the latest prescription.",
        "medicines",
    ),
    KnowledgeArticle(
        "medicine_medium",
        "Taking medicines with milk or water",
        ("milk or water", "with milk", "with water", "anupana", "medicine medium", "powder medicine", "toned milk", "skimmed milk", "powdered milk"),
        "The required medium depends on the medicine. Tablets are generally taken with water, powders may be prescribed with milk, and Lithozen is generally taken with ginger tea. Do not replace a prescribed medium such as milk or honey without asking the clinical team.",
        "medicines",
    ),
    KnowledgeArticle(
        "missed_dose",
        "Missed dose",
        ("forgot dose", "missed dose", "skip dose", "double dose", "forgot medicine"),
        "Do not automatically double the next dose. Continue with the prescribed schedule unless your doctor provided specific missed-dose instructions, and contact the clinical team if you are uncertain.",
        "medicines",
    ),
    KnowledgeArticle(
        "change_treatment",
        "Changing or stopping treatment",
        ("stop medicine", "stop supplement", "change dose", "increase dose", "decrease dose", "reduce medicine", "feel better", "dosage"),
        "Do not independently start, stop, increase or reduce prescribed medicines or supplements, even if you feel better. Treatment changes require review by the appropriate treating clinician.",
        "medicines",
    ),
    KnowledgeArticle(
        "conventional_medicines",
        "Continuing conventional medicines",
        ("allopathic medicine", "regular medicine", "bp medicine", "diabetes medicine", "thyroid medicine", "heart medicine", "insulin", "another doctor"),
        "Do not stop or change conventional prescription medicines on your own. Continue them as directed by the prescribing doctor and inform the Novadigm clinical team about all medicines and supplements you take, including anything newly prescribed elsewhere.",
        "medicines",
    ),
    KnowledgeArticle(
        "treatment_expectations",
        "Treatment duration and improvement",
        ("no improvement", "not improving", "how long", "treatment duration", "when improve", "cure", "one month"),
        "Response and treatment duration vary with the condition, severity, other illnesses, medicines and adherence. A cure or fixed improvement timeline cannot be guaranteed. If improvement is slow or absent, request a clinical follow-up instead of changing doses yourself.",
        "treatment",
    ),
    KnowledgeArticle(
        "vpk",
        "Vata, Pitta and Kapha assessment",
        ("vpk", "vata", "pitta", "kapha", "dosha", "pulse diagnosis", "nadi", "sapta dhatu"),
        "Vata, Pitta and Kapha are Ayurvedic concepts used to describe constitution and physiological patterns. Clinicians may use them when planning diet, lifestyle and supportive treatment, but they do not replace conventional diagnosis or required medical investigations.",
        "ayurveda",
    ),
    KnowledgeArticle(
        "diet_plan",
        "Following the diet plan",
        ("diet chart", "diet plan", "follow diet", "eight week diet", "8 week diet"),
        "Follow the personalized diet chart for the advised period unless the treating clinician changes it. General chatbot information should not override an individual diet plan or medical dietary restriction.",
        "diet",
    ),
    KnowledgeArticle(
        "diet_avoid",
        "General CCRSTT avoidance guidance",
        ("avoid vegetables", "cabbage", "cauliflower", "radish", "spinach", "tomato", "tamarind", "ccrstt"),
        "The general guide lists cabbage, cauliflower, radish, spinach, tomato and tamarind under CCRSTT avoidance guidance, although cabbage may be permitted in a specifically instructed soup. Follow the individual diet chart when it differs.",
        "diet",
    ),
    KnowledgeArticle(
        "diet_substitutes",
        "Alternatives to tomato, tamarind and chillies",
        ("instead of tomato", "instead of tamarind", "tomato substitute", "tamarind substitute", "spice substitute", "chilli substitute", "raw mango", "amchur", "amla"),
        "General alternatives to tomato and tamarind include raw mango, amchur powder and amla. Suggested alternatives for chillies or strong spices include ginger, ajwain and cinnamon, subject to individual restrictions.",
        "diet",
    ),
    KnowledgeArticle(
        "nuts",
        "General soaked-nut guidance",
        ("nuts", "cashew", "almond", "groundnut", "soaked nuts", "kidney patient nuts"),
        "The general guide lists 5 cashews, 5 almonds and 2 tablespoons of groundnuts soaked overnight, usually taken 10–20 minutes after medicines. Kidney disease or potassium, phosphorus or fluid restrictions require individualized dietary advice.",
        "diet",
    ),
    KnowledgeArticle(
        "honey_jaggery",
        "Honey or jaggery with diabetes",
        ("honey", "jaggery", "diabetes sugar", "blood glucose jaggery"),
        "Honey and jaggery contain sugars and can raise blood glucose. They are not automatically safe for everyone with diabetes. Use only an instructed quantity and consult the treating clinician when glucose is uncontrolled or sugar restriction has been advised.",
        "diet",
    ),
    KnowledgeArticle(
        "soups",
        "General soup guidance",
        ("soup", "barley soup", "sabudana soup", "tapioca soup", "rice soup", "broccoli soup", "ragi soup", "jowar soup"),
        "The general guide includes barley, sabudana or tapioca, rice and broccoli soups where available. Ragi and jowar soups are intended only for the relevant prescribed diet type. Follow the individual diet chart.",
        "diet",
    ),
    KnowledgeArticle(
        "fennel_water",
        "Fennel-water guidance",
        ("fennel water", "saunf water", "how much water", "2 litres", "two litres"),
        "The general guide gives approximately 2 litres per day for a 60 kg person as an example, not a universal amount. Body weight, the doctor's advice and fluid restrictions matter. Kidney, heart or liver disease and other fluid restrictions require individualized guidance.",
        "diet",
    ),
    KnowledgeArticle(
        "exercise",
        "Exercise and breathing guidance",
        ("exercise", "dnb", "breathing exercise", "leg exercise", "hand exercise", "naukasan", "naukasana", "suryanamaskar", "surya namaskar"),
        "Perform only exercises prescribed or demonstrated to you. The general program may include DNB breathing, hand and leg exercises, Naukasan and, when appropriate, Suryanamaskar. Stop and obtain advice for concerning pain, dizziness, breathing difficulty or other significant symptoms.",
        "exercise",
    ),
    KnowledgeArticle(
        "dnb",
        "DNB breathing",
        ("how to do dnb", "dnb breathing", "alternate nostril", "reverse dnb"),
        "The general guide describes DNB from left to right for about 10 minutes after waking and about 10 minutes before sleep. Reverse DNB from right to left should be performed only when specifically prescribed.",
        "exercise",
    ),
    KnowledgeArticle(
        "oil_purpose",
        "Why external oil applications are used",
        ("why oil", "oil application", "pain oil", "ayurvedic oil", "purpose of oil", "use oils", "oil treatment"),
        "External oil applications are used in Ayurveda as supportive local or body therapies. Depending on the formulation and procedure, they may be included for comfort, massage, lubrication or local supportive care. The purpose varies, so this general explanation does not identify why a particular oil was prescribed. Use only the advised oil and avoid broken, infected or irritated skin unless a clinician says otherwise.",
        "procedures",
    ),
    KnowledgeArticle(
        "pain_oil_use",
        "Using prescribed pain oil",
        ("apply pain oil", "how to use oil", "leave oil overnight", "oil overnight", "neelibringadi", "keera thailam", "scalp oil"),
        "A prescribed pain oil may generally be applied to the advised area and left overnight where appropriate, or for at least one hour when overnight use is not possible. Scalp oils such as Neelibringadi Keera Thailam may be advised daily or on alternate days. Follow the specific instructions supplied with the treatment.",
        "procedures",
    ),
    KnowledgeArticle(
        "anutailam",
        "Anutailam guidance",
        ("anutailam", "nasal drops", "nose drops", "ear drops"),
        "The current general guide describes 2 drops in each nostril and ear once daily for two weeks, but nasal or ear administration is not suitable for everyone. Follow the demonstrated procedure and obtain advice for irritation, pain, bleeding, breathing difficulty or another unexpected symptom.",
        "procedures",
    ),
    KnowledgeArticle(
        "steam",
        "Steam inhalation",
        ("steam inhalation", "how often steam", "hot steam", "cold cough sinus"),
        "The general guide describes steam once daily for two weeks and, for cold, cough or sinus symptoms, sometimes twice daily. Avoid excessively hot steam because of burn risk. Children, older adults and people with significant respiratory or cardiovascular conditions need appropriate supervision or advice.",
        "procedures",
    ),
    KnowledgeArticle(
        "gandusham",
        "Gandusham or oil pulling",
        ("gandusham", "gandusha", "oil pulling", "sesame oil mouth"),
        "Gandusham is an Ayurvedic oral practice in which the instructed oil is held in the mouth. The general guide describes sesame oil once daily for two weeks. Do not swallow the oil, and follow the procedure demonstrated by the care team.",
        "procedures",
    ),
    KnowledgeArticle(
        "nithya_virechana",
        "Castor oil or Nithya Virechana",
        ("castor oil", "nithya virechana", "virechana at home", "purgation", "laxative procedure"),
        "A castor-oil or Nithya Virechana procedure should be performed only when specifically prescribed. The amount and schedule must follow the provided instructions. It should not be started automatically, especially during pregnancy, frailty, dehydration, diarrhea, abdominal pain or significant gastrointestinal, kidney or heart illness.",
        "procedures",
    ),
    KnowledgeArticle(
        "pain_worsening",
        "New or worsening pain",
        ("pain increased", "pain worsening", "severe pain", "body pain", "persistent pain", "numbness", "weakness"),
        "Do not assume increased pain is a normal treatment reaction. Follow only already-prescribed oil and exercise instructions. New, severe, persistent or worsening pain—especially with weakness, numbness, chest pain, breathing difficulty, injury or fever—requires medical assessment.",
        "symptoms",
    ),
    KnowledgeArticle(
        "glucose_high",
        "High blood glucose",
        ("sugar increasing", "glucose high", "high blood sugar", "hyperglycemia"),
        "Continue monitoring glucose as advised and follow the prescribed diet, exercise and medication plan. Do not compensate by changing medicine doses or exercising excessively. Persistent readings above the individualized target require clinician review; severe symptoms such as vomiting, confusion, dehydration or breathing difficulty require urgent care.",
        "symptoms",
    ),
    KnowledgeArticle(
        "glucose_low",
        "Low blood glucose",
        ("sugar low", "glucose low", "low blood sugar", "hypoglycemia", "sweating trembling"),
        "Low glucose can require prompt treatment. Follow the hypoglycaemia plan previously given by the treating doctor. Sweating, trembling, confusion, unusual drowsiness or loss of consciousness requires immediate assistance. The chatbot cannot adjust diabetes medication.",
        "symptoms",
    ),
    KnowledgeArticle(
        "low_bp",
        "Low blood pressure",
        ("bp low", "low blood pressure", "fainting", "feeling faint"),
        "Sit or lie down safely and recheck blood pressure if possible. Persistent low readings or fainting, chest pain, severe weakness, breathing difficulty or confusion require prompt medical attention. Do not stop or reduce BP medicines without medical advice.",
        "symptoms",
    ),
    KnowledgeArticle(
        "diarrhoea",
        "Loose stools or diarrhoea",
        ("loose stools", "diarrhea", "diarrhoea", "detoxification", "frequent stools", "blood in stool", "black stool"),
        "Loose stools should not automatically be described as toxins leaving the body. They can have many causes and may cause dehydration. Significant, persistent or severe diarrhoea, blood or black stools, vomiting, severe pain, fever, fainting, confusion, very little urine or inability to retain fluids requires prompt medical assessment.",
        "symptoms",
    ),
    KnowledgeArticle(
        "digestive_symptoms",
        "Constipation, bloating or appetite loss",
        ("constipation", "bloating", "cannot pass stool", "cannot pass gas", "loss of appetite", "no appetite"),
        "Continue only diet, fluids and procedures already advised. Persistent or worsening symptoms require clinical review. Severe abdominal pain, repeated vomiting, marked swelling, blood in stool, inability to pass stool or gas, jaundice, dehydration or significant weight loss needs prompt assessment.",
        "symptoms",
    ),
    KnowledgeArticle(
        "fever",
        "Feeling hot or fever",
        ("fever", "feeling hot", "temperature", "body hot"),
        "Feeling hot does not necessarily mean fever. Measure temperature with a thermometer if available and maintain appropriate hydration unless fluid-restricted. Significant or persistent fever or concerning associated symptoms requires medical advice.",
        "symptoms",
    ),
    KnowledgeArticle(
        "reports",
        "Sending medical reports",
        ("send report", "upload report", "lab report", "scan report", "report photo", "blood report"),
        "Medical reports may be sent through the official WhatsApp service when report submission is enabled. Include the correct patient identifier and send clear, complete images or documents. Reports require clinician review; the chatbot does not diagnose from uploaded reports.",
        "support",
    ),
    KnowledgeArticle(
        "dispatch",
        "Medicine dispatch and damaged packages",
        ("dispatch", "delivery", "track order", "medicine finish", "running out", "package damaged", "item missing", "seal broken"),
        "Contact the patient-care or dispatch team before medicines run out. For a damaged or missing item, send clear photographs of the outer package, product, label and visible batch details. Do not use a product when the container or seal is significantly damaged or the product appears contaminated or different.",
        "support",
    ),
    KnowledgeArticle(
        "pregnancy_surgery",
        "Pregnancy, breastfeeding or surgery",
        ("pregnant", "pregnancy", "planning pregnancy", "breastfeeding", "surgery", "operation", "medical procedure"),
        "Pregnancy, breastfeeding and planned surgery require individual review of all medicines, supplements, oils, procedures and exercises. Inform both the relevant medical or surgical team and the Novadigm clinical team; do not assume every treatment remains appropriate.",
        "safety",
    ),
    KnowledgeArticle(
        "allergic_reaction",
        "Possible allergic reaction",
        ("allergy", "allergic reaction", "rash", "face swelling", "tongue swelling", "throat swelling", "reaction after medicine"),
        "Stop using the suspected non-essential product and obtain professional advice. Breathing difficulty, swelling of the face, tongue or throat, collapse or a severe widespread rash may represent a serious reaction and requires immediate emergency assistance.",
        "safety",
    ),
    KnowledgeArticle(
        "privacy",
        "WhatsApp privacy",
        ("privacy", "share reports", "someone else report", "medical information", "is whatsapp safe"),
        "Share only necessary information through the official WhatsApp service. Send another person's medical information only when you are authorized to do so. WhatsApp is not an emergency service and messages may not be monitored continuously.",
        "privacy",
    ),
)


_STOPWORDS = {
    "a", "about", "an", "and", "are", "can", "do", "for", "how", "i",
    "in", "is", "it", "me", "my", "of", "on", "the", "this", "to",
    "use", "was", "what", "when", "where", "why", "with", "your",
}

_EMERGENCY_PHRASES = (
    "chest pain", "chest pressure", "severe breathing difficulty",
    "cannot breathe", "can't breathe", "loss of consciousness", "unconscious",
    "face drooping", "one sided weakness", "one-sided weakness",
    "difficulty speaking", "slurred speech", "seizure", "severe bleeding",
    "vomiting blood", "severe allergic reaction", "throat swelling", "collapse",
    "altered consciousness", "suicidal", "attempted suicide",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+\-']+", " ", text.lower())).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in _normalize(text).split() if len(token) > 1 and token not in _STOPWORDS}


def _phrase_is_negated(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase)
    return bool(
        re.search(
            rf"\b(?:no|not|without|never|don't|do not|didn't|did not)\b"
            rf"(?:\s+\w+){{0,3}}\s+{escaped}",
            text,
        )
    )


def is_emergency_query(query: str) -> bool:
    normalized = _normalize(query)
    for phrase in _EMERGENCY_PHRASES:
        if phrase in normalized and not _phrase_is_negated(normalized, phrase):
            return True
    return False


def search_knowledge(
    query: str,
    limit: int = 4,
    category: str | None = None,
) -> List[KnowledgeMatch]:
    normalized = _normalize(query)
    query_tokens = _tokens(query)
    matches: list[KnowledgeMatch] = []

    for article in ARTICLES:
        if category and article.category != category:
            continue

        score = 0
        title_tokens = _tokens(article.title)
        score += 2 * len(query_tokens & title_tokens)

        keyword_tokens: set[str] = set()
        best_phrase_score = 0
        for keyword in article.keywords:
            keyword_norm = _normalize(keyword)
            keyword_tokens.update(_tokens(keyword))
            if keyword_norm and keyword_norm in normalized:
                best_phrase_score = max(
                    best_phrase_score,
                    5 + min(3, len(keyword_norm.split())),
                )

        # Count each overlapping word once. Without this, repeating a common
        # word such as "device" across several aliases can overwhelm a much
        # more relevant article.
        score += best_phrase_score
        score += 2 * len(query_tokens & keyword_tokens)

        if score > 0:
            matches.append(KnowledgeMatch(article=article, score=score))

    matches.sort(key=lambda item: (-item.score, item.article.article_id))
    return matches[: max(1, limit)]


def format_knowledge_context(matches: Iterable[KnowledgeMatch]) -> str:
    blocks = []
    for match in matches:
        source_line = ""
        if match.article.source_urls:
            source_line = "\nOfficial sources: " + ", ".join(match.article.source_urls)
        blocks.append(
            f"[{match.article.article_id}] {match.article.title}\n"
            f"Approved guidance: {match.article.answer}{source_line}"
        )
    return "\n\n".join(blocks)

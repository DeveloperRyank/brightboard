"""Curriculum taxonomy: Curriculum -> Exam -> Subject -> Topic.

The app is organised so a student picks a curriculum (currently KCSE), then a
subject, then a topic, and from a topic can take a Lesson, a Test, or upload a
paper for analysis.

Chemistry carries the full official KCSE syllabus and is wired to the real
past-paper bank in data/questions.csv. Every other subject lists its syllabus
topics but is *content-gated*: lessons/tests stay disabled until a question bank
is dropped in at data/<subject_slug>/questions.csv (columns: question,topic).

Topic slugs are stable identifiers used as keys in the profile DB and as folder
names for question data. Legacy CSV slugs are remapped to canonical slugs via
LEGACY_TOPIC_MAP so the existing Chemistry data lands on the right topics.
"""

# Canonical Chemistry topics: slug -> (display name, KCSE Form).
# Superset of the official KNEC Chemistry syllabus (Form 1-4).
CHEMISTRY_TOPICS = [
    ("introduction_to_chemistry", "Introduction to Chemistry", 1),
    ("classification_of_substances", "Simple Classification of Substances", 1),
    ("separation_techniques", "Separation of Mixtures", 1),
    ("laboratory_techniques", "Laboratory Techniques and Safety", 1),
    ("acids_bases_indicators", "Acids, Bases and Indicators", 1),
    ("air_and_combustion", "Air and Combustion", 1),
    ("water_and_hydrogen", "Water and Hydrogen", 1),
    ("hard_water", "Hard Water", 1),
    ("states_of_matter", "States of Matter", 1),
    ("atomic_structure", "Structure of the Atom", 2),
    ("periodic_table", "The Periodic Table", 2),
    ("periodicity", "Periodic Trends", 2),
    ("chemical_bonding", "Chemical Bonding", 2),
    ("mole_concept", "Moles and Chemical Equations", 2),
    ("solutions_and_solubility", "Solutions and Solubility", 2),
    ("thermochemistry", "Energy Changes in Reactions", 2),
    ("acids_bases_salts", "Acids, Bases and Salts", 2),
    ("electrolysis", "Electrolysis", 2),
    ("redox_reactions", "Oxidation and Reduction", 2),
    ("reaction_kinetics", "Rates of Reaction", 3),
    ("chemical_equilibrium", "Chemical Equilibrium", 3),
    ("electrochemistry", "Electrochemical Cells", 3),
    ("nitrogen", "Nitrogen and its Compounds", 3),
    ("sulphur", "Sulphur and its Compounds", 3),
    ("halogens", "Chlorine and its Compounds", 3),
    ("organic_chemistry", "Organic Chemistry I", 2),
    ("crude_oil", "Crude Oil and Petroleum", 2),
    ("organic_chemistry_2", "Organic Chemistry II", 4),
    ("extraction_of_metals", "Metals and their Ores", 4),
    ("industrial_processes", "Industrial Chemical Processes", 3),
    ("radioactivity", "Radioactivity", 4),
]

# Old questions.csv topic slug -> canonical slug above. Only the renames are
# listed; any slug not here is already canonical and maps to itself.
LEGACY_TOPIC_MAP = {
    "lab_techniques": "laboratory_techniques",
    "combustion": "air_and_combustion",
}


def _topics_from_pairs(pairs):
    return [{"slug": s, "name": n, "form": f} for (s, n, f) in pairs]


def _flat_topics(slugs_names):
    """(slug, name) pairs for subjects we don't tag by Form."""
    return [{"slug": s, "name": n} for (s, n) in slugs_names]


# --- Other KCSE subjects: syllabus topic lists (content-gated until data added) ---

MATHEMATICS_TOPICS = _flat_topics([
    ("numbers", "Numbers and Number Systems"),
    ("algebraic_expressions", "Algebraic Expressions"),
    ("equations_inequalities", "Equations and Inequalities"),
    ("functions_graphs", "Functions and Graphs"),
    ("sequences_series", "Sequences and Series"),
    ("trigonometry", "Trigonometry"),
    ("vectors", "Vectors"),
    ("matrices_transformations", "Matrices and Transformations"),
    ("analytic_geometry", "Analytic Geometry"),
    ("calculus", "Differentiation and Integration"),
    ("probability", "Probability"),
    ("statistics", "Statistics"),
    ("commercial_arithmetic", "Commercial Arithmetic"),
    ("linear_programming", "Linear Programming"),
    ("volume_area", "Volume, Area and Perimeter"),
    ("solid_geometry", "Solid Geometry"),
])

PHYSICS_TOPICS = _flat_topics([
    ("measurement", "Measurement and Units"),
    ("forces", "Forces and Newton's Laws"),
    ("pressure", "Pressure"),
    ("moments", "Turning Effect of Force"),
    ("energy_work_power", "Work, Energy and Power"),
    ("heat", "Heat and Temperature"),
    ("waves", "Waves"),
    ("sound", "Sound"),
    ("light", "Light and Optics"),
    ("electrostatics", "Electrostatics"),
    ("current_electricity", "Current Electricity"),
    ("magnetism", "Magnetism and Electromagnetism"),
    ("capacitors", "Capacitors"),
    ("electronics", "Diodes and Electronics"),
    ("gas_laws", "Gas Laws"),
    ("atomic_physics", "Atomic Physics"),
    ("radioactivity", "Radioactivity"),
    ("photoelectric_effect", "Photoelectric Effect"),
])

BIOLOGY_TOPICS = _flat_topics([
    ("cell_biology", "Cell Structure and Function"),
    ("classification", "Classification of Living Things"),
    ("nutrition_plants", "Nutrition in Plants"),
    ("nutrition_animals", "Nutrition in Animals"),
    ("transport_plants", "Transport in Plants"),
    ("transport_animals", "Transport in Animals"),
    ("respiration", "Respiration"),
    ("excretion", "Excretion and Osmoregulation"),
    ("reproduction_plants", "Reproduction in Plants"),
    ("reproduction_animals", "Reproduction in Animals"),
    ("growth_development", "Growth and Development"),
    ("coordination_nervous", "Coordination: Nervous System"),
    ("coordination_hormonal", "Coordination: Hormones"),
    ("homeostasis", "Homeostasis"),
    ("genetics", "Genetics"),
    ("evolution", "Evolution"),
    ("ecology", "Ecology"),
    ("immunity", "Immunity"),
])

ENGLISH_TOPICS = _flat_topics([
    ("comprehension", "Comprehension"),
    ("summary_writing", "Summary Writing"),
    ("grammar", "Grammar and Usage"),
    ("composition", "Composition Writing"),
    ("functional_writing", "Functional Writing"),
    ("poetry", "Poetry"),
    ("prose", "Prose and Novel Study"),
    ("drama", "Drama and Play Study"),
    ("oral_skills", "Oral and Listening Skills"),
])

KISWAHILI_TOPICS = _flat_topics([
    ("sarufi", "Sarufi"),
    ("ufahamu", "Ufahamu"),
    ("insha", "Uandishi: Insha"),
    ("shairi", "Fasihi: Shairi"),
    ("riwaya", "Fasihi: Riwaya"),
    ("tamthiliya", "Fasihi: Tamthiliya"),
    ("methali", "Methali na Tungo"),
    ("mawasiliano", "Lugha ya Mawasiliano"),
])

HISTORY_TOPICS = _flat_topics([
    ("early_man", "Early Man and Human Evolution"),
    ("agriculture", "Origin and Development of Agriculture"),
    ("early_civilizations", "Early Civilizations"),
    ("citizenship", "Citizenship and the Constitution"),
    ("devolution", "Devolution and Governance"),
    ("land_tenure", "Land Tenure Systems"),
    ("trade", "Development of Trade"),
    ("transport_communication", "Transport and Communication"),
    ("world_wars", "The World Wars"),
    ("independence", "Independence Struggles"),
    ("african_nationalism", "African Nationalism"),
    ("international_relations", "International Relations"),
])

GEOGRAPHY_TOPICS = _flat_topics([
    ("maps", "Maps and Map Work"),
    ("landforms", "Landforms and Internal Processes"),
    ("climate", "Climate and Weather"),
    ("vegetation", "Vegetation"),
    ("water_bodies", "Water Bodies and Hydrology"),
    ("soils", "Soils"),
    ("population", "Population"),
    ("settlement", "Settlement and Urbanisation"),
    ("agriculture_geo", "Agriculture"),
    ("mining", "Mining and Minerals"),
    ("industry", "Industry and Manufacturing"),
    ("transport_geo", "Transport and Trade"),
    ("fieldwork", "Fieldwork and Research"),
])

CRE_TOPICS = _flat_topics([
    ("god_creation", "God and Creation"),
    ("old_testament", "Old Testament Foundations"),
    ("new_testament", "New Testament"),
    ("life_of_jesus", "The Life and Ministry of Jesus"),
    ("apostolic_church", "The Apostolic Church"),
    ("christian_living", "Christian Living and Ethics"),
    ("african_heritage", "African Religious Heritage"),
    ("contemporary_issues", "Contemporary Issues in Society"),
])

BUSINESS_TOPICS = _flat_topics([
    ("intro_business", "Introduction to Business"),
    ("entrepreneurship", "Entrepreneurship"),
    ("trade", "Trade and Commerce"),
    ("money_banking", "Money and Banking"),
    ("insurance", "Insurance"),
    ("transport_business", "Transport and Communication"),
    ("office_practice", "Office Practice"),
    ("accounting", "Basic Accounting"),
    ("taxation", "Taxation"),
    ("government_business", "Government and Business"),
])

AGRICULTURE_TOPICS = _flat_topics([
    ("intro_agriculture", "Introduction to Agriculture"),
    ("soil_science", "Soil Science"),
    ("crop_production", "Crop Production"),
    ("horticulture", "Horticulture"),
    ("livestock_beef", "Beef Production"),
    ("livestock_dairy", "Dairy Production"),
    ("livestock_poultry", "Poultry Production"),
    ("farm_structures", "Farm Structures and Machinery"),
    ("agri_economics", "Agricultural Economics"),
    ("agroforestry", "Agroforestry"),
    ("land_reclamation", "Land Reclamation"),
])

COMPUTER_TOPICS = _flat_topics([
    ("intro_computers", "Introduction to Computers"),
    ("hardware", "Computer Hardware"),
    ("software", "Software and Operating Systems"),
    ("data_processing", "Data Processing"),
    ("programming", "Programming"),
    ("databases", "Databases"),
    ("networking", "Networking"),
    ("internet", "The Internet and Web"),
    ("security", "Information Security"),
    ("ethics", "Computer Ethics and Society"),
])


# --- SAT (digital): the two scored sections, split into the official content
# domains. Subject slugs are namespaced (sat_*) so they never collide with KCSE
# slugs in profile keys. Content-gated like every non-Chemistry subject.
SAT_MATH_TOPICS = _flat_topics([
    ("sat_algebra", "Algebra"),
    ("sat_advanced_math", "Advanced Math"),
    ("sat_problem_solving", "Problem-Solving and Data Analysis"),
    ("sat_geometry_trig", "Geometry and Trigonometry"),
])

SAT_RW_TOPICS = _flat_topics([
    ("sat_information_ideas", "Information and Ideas"),
    ("sat_craft_structure", "Craft and Structure"),
    ("sat_expression_ideas", "Expression of Ideas"),
    ("sat_standard_english", "Standard English Conventions"),
])


SUBJECTS = [
    {
        "slug": "chemistry",
        "name": "Chemistry",
        "icon": "flask",
        "accent": "#FF6B57",
        "live": True,  # has a real question bank wired up
        "topics": _topics_from_pairs(CHEMISTRY_TOPICS),
    },
    {"slug": "mathematics", "name": "Mathematics", "icon": "sigma", "accent": "#74A98D", "live": False, "topics": MATHEMATICS_TOPICS},
    {"slug": "physics", "name": "Physics", "icon": "magnet", "accent": "#D9A648", "live": False, "topics": PHYSICS_TOPICS},
    {"slug": "biology", "name": "Biology", "icon": "leaf", "accent": "#BFB09C", "live": False, "topics": BIOLOGY_TOPICS},
    {"slug": "english", "name": "English Language", "icon": "book", "accent": "#E2492F", "live": False, "topics": ENGLISH_TOPICS},
    {"slug": "kiswahili", "name": "Kiswahili", "icon": "speech", "accent": "#F4EBDD", "live": False, "topics": KISWAHILI_TOPICS},
    {"slug": "history", "name": "History and Government", "icon": "landmark", "accent": "#D9A648", "live": False, "topics": HISTORY_TOPICS},
    {"slug": "geography", "name": "Geography", "icon": "globe", "accent": "#74A98D", "live": False, "topics": GEOGRAPHY_TOPICS},
    {"slug": "cre", "name": "Christian Religious Education", "icon": "cross", "accent": "#BFB09C", "live": False, "topics": CRE_TOPICS},
    {"slug": "business", "name": "Business Studies", "icon": "briefcase", "accent": "#E2492F", "live": False, "topics": BUSINESS_TOPICS},
    {"slug": "agriculture", "name": "Agriculture", "icon": "sprout", "accent": "#74A98D", "live": False, "topics": AGRICULTURE_TOPICS},
    {"slug": "computer", "name": "Computer Studies", "icon": "monitor", "accent": "#BFB09C", "live": False, "topics": COMPUTER_TOPICS},
]

SAT_SUBJECTS = [
    {"slug": "sat_math", "name": "SAT Math", "icon": "sigma", "accent": "#74A98D", "live": False, "topics": SAT_MATH_TOPICS},
    {"slug": "sat_rw", "name": "SAT Reading & Writing", "icon": "book", "accent": "#E2492F", "live": False, "topics": SAT_RW_TOPICS},
]


CURRICULA = [
    {
        "slug": "kcse",
        "name": "KCSE",
        "full_name": "Kenya Certificate of Secondary Education",
        "country": "Kenya",
        "subjects": SUBJECTS,
    },
    {
        "slug": "sat",
        "name": "SAT",
        "full_name": "SAT (Digital)",
        "country": "Global",
        "subjects": SAT_SUBJECTS,
    },
]


# ---------------- Lookups ----------------

def curricula():
    return [{"slug": c["slug"], "name": c["name"], "full_name": c["full_name"],
             "country": c["country"], "subject_count": len(c["subjects"])} for c in CURRICULA]


def get_curriculum(cur_slug):
    for c in CURRICULA:
        if c["slug"] == cur_slug:
            return c
    return None


def subjects(cur_slug="kcse"):
    cur = get_curriculum(cur_slug)
    if not cur:
        return []
    return [{"slug": s["slug"], "name": s["name"], "icon": s["icon"], "accent": s["accent"],
             "live": s["live"], "topic_count": len(s["topics"])} for s in cur["subjects"]]


def get_subject(subject_slug):
    """Resolve a subject across every curriculum; slugs are globally unique."""
    for c in CURRICULA:
        for s in c["subjects"]:
            if s["slug"] == subject_slug:
                return s
    return None


def curriculum_of_subject(subject_slug):
    for c in CURRICULA:
        for s in c["subjects"]:
            if s["slug"] == subject_slug:
                return c["slug"]
    return None


def topics(subject_slug):
    s = get_subject(subject_slug)
    return s["topics"] if s else []


def get_topic(subject_slug, topic_slug):
    for t in topics(subject_slug):
        if t["slug"] == topic_slug:
            return t
    return None


def topic_name(subject_slug, topic_slug):
    t = get_topic(subject_slug, topic_slug)
    return t["name"] if t else topic_slug.replace("_", " ").title()


def canonical_topic(topic_slug):
    """Map a legacy CSV topic slug to its canonical syllabus slug."""
    return LEGACY_TOPIC_MAP.get(topic_slug, topic_slug)


def all_topic_slugs(subject_slug):
    return {t["slug"] for t in topics(subject_slug)}


def valid_subjects(cur_slug="kcse"):
    return {s["slug"] for s in subjects(cur_slug)}

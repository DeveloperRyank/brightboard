"""Curriculum taxonomy: Qualification -> Subject -> Strand -> Topic.

A student picks a qualification, then a subject, then a topic, and from a topic can
take a Lesson, a Test, or upload a paper for analysis. The five qualifications are the
Kenyan ones a student actually sits — 8-4-4's KCSE, and the CBC's KPSEA (Grade 6),
KJSEA (Grade 9) and KCBE (Grade 12) — plus the SAT.

8-4-4 is not gone: KNEC's last sitting of the KCSE is November 2027, so the two systems
run in parallel for another year of schooling and both have to be selectable.

Chemistry carries the full official KCSE syllabus and is wired to the real past-paper
bank in data/questions.csv. Every other subject lists its syllabus topics but is
*content-gated*: lessons/tests stay disabled until a question bank is dropped in at
data/<subject_slug>/questions.csv (columns: question,topic).

The CBC topic lists come from the KICD curriculum designs: the rationalised Junior
School edition of July 2024 for Grades 7-9, and the Senior School designs of 2024-2026
for Grades 10-12. Strand and sub-strand names are KICD's own.

Subject slugs are globally unique across every qualification — stage prefixes p_, j_
and s_ keep the CBC years from colliding with the KCSE ones — because a subject slug is
a URL, a folder of question data and half of every mastery key. Two students sitting the
same-named subject in different systems must not share a profile, and a slug that
appears twice would silently resolve to whichever curriculum is listed first.

Topic slugs are stable identifiers used as keys in the profile DB. Legacy CSV slugs are
remapped to canonical slugs via LEGACY_TOPIC_MAP so the existing Chemistry data lands on
the right topics.
"""

import json
import re
from pathlib import Path

# How a qualification names the year a student is in. 8-4-4 students still say Form
# (its last KCSE sitting is 2027); CBC students say Grade; an SAT candidate has no
# school year at all, so that list is empty and the field simply does not appear.
KCSE_FORMS = ("Form 1", "Form 2", "Form 3", "Form 4")
KPSEA_GRADES = ("Grade 4", "Grade 5", "Grade 6")
KJSEA_GRADES = ("Grade 7", "Grade 8", "Grade 9")
KCBE_GRADES = ("Grade 10", "Grade 11", "Grade 12")

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
    """(slug, name, year) triples. The year number is a Form for 8-4-4 subjects and a
    Grade for CBC ones; which word goes in front of it is the curriculum's business."""
    return [{"slug": s, "name": n, "grade": y} for (s, n, y) in pairs]


def _flat_topics(slugs_names):
    """(slug, name) pairs for subjects whose topics are not split by year."""
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


def _slug(text):
    """A topic name to a stable key: lowercase, underscores, only letters and digits."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")


def _by_strand(groups):
    """Strand -> grade -> sub-strand names, as printed in a KICD curriculum design,
    flattened into topics that each remember their year and the strand over them.

    A strand with no sub-strands listed for a year is not taught that year, so it
    contributes nothing rather than a half-empty card. KICD repeats some sub-strand
    names across years — "Drawing and Painting" appears in the Grade 7, 8 and 9
    designs — so a repeated name keeps its year in the slug: one student's Grade 7
    attempt must not be stored under the same mastery key as their Grade 9 one."""
    names = [s for by_grade in groups.values() for subs in by_grade.values() for s in subs]
    out, seen = [], set()
    for strand, by_grade in groups.items():
        for grade in sorted(by_grade):
            for sub in by_grade[grade]:
                slug = _slug(sub)
                if names.count(sub) > 1 or slug in seen:
                    base, n = f"{slug}_g{grade}", 2
                    slug = base
                    while slug in seen:
                        slug = f"{base}_{n}"
                        n += 1
                seen.add(slug)
                out.append({"slug": slug, "name": sub, "strand": strand, "grade": grade})
    return out


# The KICD designs are transcribed out of band by tools/build_cbc_strands.py and arrive as
# data rather than as code: six hundred and eighty-eight sub-strands would be a wall of
# literals in the middle of this file. A subject missing from the file simply keeps
# whatever table is written beside it, so nothing here depends on the file being complete.
_KICD_FILE = Path(__file__).resolve().parent / "data" / "curriculum" / "kicd_strands.json"


def _kicd_tables():
    if not _KICD_FILE.exists():
        return {}
    loaded = json.loads(_KICD_FILE.read_text(encoding="utf-8"))["subjects"]
    return {slug: {strand: {int(grade): list(subs) for grade, subs in by_grade.items()}
                   for strand, by_grade in entry["strands"].items()}
            for slug, entry in loaded.items()}


_KICD = _kicd_tables()


def _merge_strands(*tables):
    """Strand tables laid one over another, keeping the first table's strand order."""
    out = {}
    for table in tables:
        for strand, by_grade in table.items():
            slot = out.setdefault(strand, {})
            for grade, subs in by_grade.items():
                bucket = slot.setdefault(grade, [])
                bucket.extend(s for s in subs if s not in bucket)
    return out


def _topics(slug, base=None):
    """A subject's topics: the table written beside it plus the transcribed design for the
    same subject, so a design added later fills the subject in without touching this file."""
    table = _merge_strands(base or {}, _KICD.get(slug, {}))
    return _by_strand(table) if table else []


# --- CBC Senior School (KCBE, Grades 10-12) -------------------------------------------
#
# Content taken from the KICD Senior School curriculum designs. Strand and sub-strand
# names are KICD's own. Where a design has not been transcribed yet the subject is
# listed with no topics rather than with guessed ones — the topic page then says so.

_KB_CHEMISTRY = {
    "1.0 Inorganic Chemistry": {10: ["Introduction to Chemistry", "The Atom",
                                     "The Periodic Table", "Chemical Bonding", "Periodicity"]},
    "2.0 Physical Chemistry": {10: ["Acids and Bases", "Introduction to Salts"]},
    "3.0 Organic Chemistry": {},
}

_KB_BIOLOGY = {
    "1.0 Cell Biology and Biodiversity": {10: ["Introduction to Biology",
                                               "Scientific Investigations in Biology",
                                               "Cell Structure and Specialization",
                                               "Chemicals of Life"]},
    "2.0 Anatomy and Physiology of Plants": {10: ["Nutrition", "Transport",
                                                  "Gaseous Exchange and Respiration"]},
    "3.0 Anatomy and Physiology of Animals": {10: ["Nutrition", "Transport",
                                                   "Gaseous Exchange and Respiration"]},
}

_KB_BUSINESS = {
    "1.0 Business and Money Management": {10: ["Money", "Business Goals",
                                               "Budgeting in Business", "Banking"]},
    "2.0 Business and Its Environment": {10: ["Business Activities", "Types of Business Ownership",
                                              "Social Responsibility of Business",
                                              "Entrepreneurship", "Production",
                                              "Consumer Satisfaction"]},
    "3.0 Government and Global Influence in Business": {10: ["Public Finance",
                                                             "International Trade"]},
    "4.0 Financial Records in Business": {10: ["Business Transactions",
                                               "Effects of Business Transactions",
                                               "Source Documents and Journals"]},
}

_KB_COMPUTER_SCIENCE = {
    "1.0 Foundation of Computer Science": {10: ["Evolution of Computers",
                                                "Computer Architecture",
                                                "Input/Output (I/O) Devices", "Computer Storage",
                                                "Central Processing Unit (CPU)",
                                                "Operating System (OS)", "Computer Setup"]},
    "2.0 Computer Networking": {10: ["Data Communication", "Data Transmission Media",
                                     "Computer Network Elements", "Network Topologies"]},
    "3.0 Software Development": {10: ["Computer Programming Concepts", "Programme Development",
                                      "Identifiers and Operators", "Control Structures",
                                      "Containers", "Functions"]},
}

_KB_PHYSICAL_EDUCATION = {
    "1.0 Ball Games": {10: ["Football", "Netball", "Volleyball", "Handball", "Basketball",
                            "Rugby"]},
    "2.0 Athletics": {10: ["Sprints", "Long Jump", "Javelin", "Shot Put"]},
    "3.0 Optional Sports": {10: ["Hockey", "Swimming", "Softball", "Baseball5", "Cricket",
                                 "Table Tennis", "Tennis", "Badminton"]},
}

_KB_COMMUNITY_SERVICE = {
    "1.0 Citizenship": {10: ["Concept of CSL", "Community Needs", "Leadership Development"]},
    "2.0 Life Skills": {10: ["Intercultural Competence", "Self-Awareness in the Community",
                             "Conflict Resolution"]},
    "3.0 Action Research": {10: ["Introduction to Action Research", "Problem Identification",
                                "Implementation Process"]},
    "4.0 Social Entrepreneurship": {10: ["Social Entrepreneurship Process",
                                         "Opportunity Identification",
                                         "Social Enterprise Planning", "Resource Mobilisation"]},
}

# Every learning area named in the KICD senior designs, whether or not its sub-strands have
# been transcribed. Listing them all keeps the subject grid truthful — a Grade 10 student
# who does Physics sees Physics. The ones a design has been read for fill themselves in from
# data/curriculum/kicd_strands.json; the rest open on a page that says plainly that the list
# is still being added.
_KB_PENDING = [
    ("mathematics", "Mathematics/Advanced Mathematics", "sigma", "#74A98D"),
    ("physics", "Physics", "magnet", "#D9A648"),
    ("geography", "Geography", "globe", "#74A98D"),
    ("history_and_citizenship", "History and Citizenship", "landmark", "#BFB09C"),
    ("english", "English", "book", "#E2492F"),
    ("kiswahili", "Kiswahili or Kenya Sign Language", "speech", "#F4EBDD"),
    ("agriculture", "Agriculture", "sprout", "#BFB09C"),
    ("home_science", "Home Science", "hand", "#FF6B57"),
    ("sports_science", "Sports Science", "target", "#74A98D"),
    ("visual_performing_arts", "Visual and Performing Arts", "layers", "#E2492F"),
    ("religious_education", "Christian Religious Education", "cross", "#BFB09C"),
]

KCBE_SUBJECTS = [
    {"slug": "kb_chemistry", "name": "Chemistry", "icon": "flask", "accent": "#FF6B57",
     "live": False, "topics": _topics("kb_chemistry", _KB_CHEMISTRY)},
    {"slug": "kb_biology", "name": "Biology", "icon": "leaf", "accent": "#BFB09C",
     "live": False, "topics": _topics("kb_biology", _KB_BIOLOGY)},
    {"slug": "kb_business_studies", "name": "Business Studies", "icon": "briefcase",
     "accent": "#E2492F", "live": False, "topics": _topics("kb_business_studies", _KB_BUSINESS)},
    {"slug": "kb_computer_science", "name": "Computer Studies", "icon": "monitor",
     "accent": "#BFB09C", "live": False,
     "topics": _topics("kb_computer_science", _KB_COMPUTER_SCIENCE)},
    {"slug": "kb_physical_education", "name": "Physical Education", "icon": "target",
     "accent": "#74A98D", "live": False,
     "topics": _topics("kb_physical_education", _KB_PHYSICAL_EDUCATION)},
    {"slug": "kb_community_service_learning", "name": "Community Service Learning",
     "icon": "hand", "accent": "#D9A648", "live": False,
     "topics": _topics("kb_community_service_learning", _KB_COMMUNITY_SERVICE)},
] + [{"slug": "kb_" + s, "name": n, "icon": i, "accent": a, "live": False,
      "topics": _topics("kb_" + s)}
     for (s, n, i, a) in _KB_PENDING]


# --- CBC Junior School (KJSEA, Grades 7-9) ----------------------------------------------
#
# Nine learning areas since the July 2024 rationalisation cut the earlier twelve; every
# learner sits all of them (Pre-Technical Studies absorbed Business Studies, Computer
# Studies and Home Science) plus one of CRE, IRE or HRE.

_KJ_INTEGRATED_SCIENCE = {
    "1.0 Scientific Investigation": {7: ["Introduction to Integrated Science",
                                         "Laboratory Safety",
                                         "Laboratory Apparatus and Instruments"]},
    "2.0 Mixtures, Elements and Compounds": {
        7: ["Mixtures", "Acids, Bases and Indicators"],
        8: ["Elements and Compounds", "Physical and Chemical Changes", "Classes of Fire"],
        9: ["Structure of the Atom", "Metals and Alloys", "Water Hardness"]},
    "3.0 Living Things and the Environment": {
        7: ["Human Reproductive System", "Human Excretory System"],
        8: ["The Cell", "Movement of Materials In and Out of the Cell",
            "Reproduction in Human Beings"],
        9: ["Nutrition in Plants", "Nutrition in Animals", "Reproduction in Plants",
            "Interdependence of Life"]},
    "4.0 Force and Energy": {
        7: ["Electrical Energy", "Magnetism"],
        8: ["Transformation of Energy", "Pressure"],
        9: ["Curved Mirrors", "Waves"]},
}

_KJ_PRETECHNICAL = {
    "1.0 Foundations of Pre-Technical Studies": {
        7: ["Introduction to Pre-Technical Studies", "Safety in the Immediate Environment",
            "Computer Concepts"],
        8: ["Fire Safety", "Data Safety"],
        9: ["Safety on Raised Platforms", "Handling Hazardous Substances",
            "Self-Exploration and Career Development"]},
    "2.0 Communication in Pre-Technical Studies": {
        7: ["Introduction to Drawing", "Free-hand Sketching", "ICT Tools in Communication"],
        8: ["Plane Geometry", "Dimensioning", "Plain Scale Drawing", "Visual Programming"],
        9: ["Oblique Projection", "Visual Programming"]},
    "3.0 Materials for Production": {
        7: ["Introduction to Materials", "Metallic Materials", "Non-Metallic Materials"],
        8: ["Composite Materials", "Ceramics"],
        9: ["Wood", "Handling Waste Materials"]},
    "4.0 Tools and Production": {
        7: ["Measuring and Marking-Out Tools", "Computer Hardware"],
        8: ["Cutting Tools", "Computer Software"],
        9: ["Holding Tools", "Driving Tools", "Project"]},
    "5.0 Entrepreneurship": {
        7: ["Introduction to Entrepreneurship", "Production Unit", "Financial Goals"],
        8: ["Bookkeeping", "Income and Budgeting", "Marketing Goods and Services",
            "Distribution of Goods and Services"],
        9: ["Financial Services", "Government and Business", "Business Plan"]},
}

_KJ_AGRICULTURE_NUTRITION = {
    "1.0 Conservation of Resources": {
        7: ["Controlling Soil Pollution", "Constructing Water Retention Structures",
            "Conserving Food Nutrients", "Growing Trees"],
        8: ["Soil Conservation Measures", "Water Harvesting and Storage"],
        9: ["Conserving Animal Feed: Hay", "Conserving Leftover Food", "Integrated Farming"]},
    "2.0 Food Production Processes": {
        7: ["Crop Establishment", "Selected Crop Management Practices",
            "Preparing Animal Products", "Cooking Food"],
        8: ["Kitchen and Backyard Gardening", "Poultry Rearing in a Fold",
            "Crop Pest and Disease Control", "Preparation of Animal Products",
            "Preserving Animal Products", "Cooking: Preparing a Balanced Meal"],
        9: ["Organic Gardening", "Storage of Crop Produce", "Cooking: Using Flour Mixtures"]},
    "3.0 Hygiene Practices": {
        7: ["Hygiene in Rearing Animals", "Laundry: Loose Coloured Items"],
        8: ["Cleaning the Kitchen"],
        9: ["Cleaning Waste Disposal Facilities",
            "Disinfecting Clothings and Household Articles"]},
    "4.0 Production Techniques": {
        7: ["Knitting Skills", "Constructing Framed Suspended Garden",
            "Adding Value to Crop Produce", "Making Homemade Soap"],
        8: ["Sewing Skills: Constructing Household Items",
            "Constructing Innovative Animal Waterer", "ICT Support Services"],
        9: ["Grafting in Plants", "Homemade Sun Dryer"]},
}

_KJ_SOCIAL_STUDIES = {
    "1.0 Social Studies and Personal/Career Development": {
        7: ["Self-Exploration", "Social Entrepreneurial Opportunities"],
        8: ["Self-Improvement", "Self-Esteem Assessment"],
        9: ["Pathway Choices", "Pre-career Support Systems"]},
    "2.0 Community Service-Learning": {
        7: ["Community Service-Learning Project"],
        8: ["Community Service-Learning Project"],
        9: ["Community Service-Learning Project"]},
    "3.0 People and Relationships": {
        7: ["Human Origin", "Early Civilisation", "Slavery and Servitude",
            "Developments in Medium of Trade", "Diversity and Interpersonal Relationships",
            "Peaceful Coexistence"],
        8: ["Scientific Theory about Human Origin", "Early Civilisations",
            "Trans-Saharan Slave Trade", "Population Growth in Africa",
            "Diversity and Interpersonal Skills", "Peaceful Conflict Resolutions"],
        9: ["Socio-Economic Practices of Early Humans",
            "Indigenous Knowledge Systems in African Societies", "Poverty Reduction",
            "Population Structure", "Peaceful Conflict Resolution", "Healthy Relationships"]},
    "4.0 Natural and Historic Built Environments": {
        7: ["Historical Information", "Historical Development of Agriculture",
            "Maps and Map Work", "Earth and the Solar System", "Weather", "Fieldwork"],
        8: ["Map Reading and Interpretation", "Weather and Climate", "Vegetation in Africa",
            "Historical Sites and Monuments in Africa"],
        9: ["Topographical Maps", "Internal Land Forming Processes",
            "Multipurpose River Projects in Africa",
            "Management and Conservation of the Environment",
            "World Heritage Sites in Africa"]},
    "5.0 Political Development and Governance": {
        7: ["Political Development in Africa", "The Constitution of Kenya", "Human Rights",
            "African Diasporas", "Citizenship"],
        8: ["The Constitution of Kenya", "Human Rights", "Citizenship"],
        9: ["The Constitution of Kenya", "Civic Engagement in Governance",
            "Kenya's Bill of Rights", "Cultural Globalisation"]},
}

_KJ_CREATIVE_ARTS_SPORTS = {
    "1.0 Foundations of Creative Arts and Sports": {
        7: ["Introduction to Creative Arts and Sports", "Components of Creative Arts and Sports"],
        8: ["Roles of Creative Arts and Sports", "Components of Creative Arts and Sports"],
        9: ["Careers in Creative Arts and Sports", "Components of Creative Arts and Sports"]},
    "2.0 Creating and Performing in Creative Arts and Sports": {
        7: ["Drawing and Painting", "Rhythm", "Javelin and Sculpture", "Melody", "Handball",
            "Descant Recorder", "Storytelling and Animation", "Football", "Kenyan Folk Song",
            "Indigenous Kenyan Craft - Bead work"],
        8: ["Drawing and Painting", "Rhythm", "Athletics and Montage", "Melody", "Netball",
            "Fabric Decoration", "Descant Recorder", "Verse", "Volleyball", "Kenyan Folk Dance",
            "Indigenous Kenyan Craft - Basketry"],
        9: ["Drawing and Painting", "Rhythm", "Athletics and Mosaic", "Melody", "Rugby",
            "Photography", "Descant Recorder", "Play", "Basketball and Logo Design",
            "Indigenous Kenyan Craft"]},
    "3.0 Appreciation in Creative Arts and Sports": {
        7: ["Analysis of Creative Arts and Sports"],
        8: ["Analysis of Creative Arts and Sports"],
        9: ["Analysis of Creative Arts and Sports"]},
}

KJSEA_SUBJECTS = [
    {"slug": "kj_integrated_science", "name": "Integrated Science", "icon": "flask",
     "accent": "#FF6B57", "live": False,
     "topics": _topics("kj_integrated_science", _KJ_INTEGRATED_SCIENCE)},
    {"slug": "kj_pretechnical_studies", "name": "Pre-Technical Studies", "icon": "monitor",
     "accent": "#D9A648", "live": False,
     "topics": _topics("kj_pretechnical_studies", _KJ_PRETECHNICAL)},
    {"slug": "kj_agriculture_nutrition", "name": "Agriculture and Nutrition", "icon": "sprout",
     "accent": "#74A98D", "live": False,
     "topics": _topics("kj_agriculture_nutrition", _KJ_AGRICULTURE_NUTRITION)},
    {"slug": "kj_social_studies", "name": "Social Studies", "icon": "landmark",
     "accent": "#BFB09C", "live": False,
     "topics": _topics("kj_social_studies", _KJ_SOCIAL_STUDIES)},
    {"slug": "kj_creative_arts_sports", "name": "Creative Arts and Sports", "icon": "layers",
     "accent": "#E2492F", "live": False,
     "topics": _topics("kj_creative_arts_sports", _KJ_CREATIVE_ARTS_SPORTS)},
] + [{"slug": "kj_" + s, "name": n, "icon": i, "accent": "#BFB09C", "live": False,
      "topics": _topics("kj_" + s)} for (s, n, i) in [
          ("english", "English", "book"),
          ("kiswahili", "Kiswahili or Kenya Sign Language", "speech"),
          ("mathematics", "Mathematics", "sigma"),
          ("religious_education", "Religious Education", "cross")]]


# --- CBC Upper Primary (KPSEA, Grades 4-6) ----------------------------------------------
#
# Eight learning areas. KNEC merged them into five KPSEA papers in 2024 — Mathematics,
# English, Science and Technology, Kiswahili, and one combined Creative Arts plus Social
# Studies paper — and dropped Religious Education from the exam, though it stays taught.

KPSEA_SUBJECTS = [
    {"slug": "kp_" + s, "name": n, "icon": i, "accent": a, "live": False,
     "topics": _topics("kp_" + s)}
    for (s, n, i, a) in [
        ("english", "English", "book", "#E2492F"),
        ("kiswahili", "Kiswahili", "speech", "#F4EBDD"),
        ("mathematics", "Mathematics", "sigma", "#74A98D"),
        ("science_technology", "Science and Technology", "flask", "#FF6B57"),
        ("agriculture_nutrition", "Agriculture and Nutrition", "sprout", "#BFB09C"),
        ("social_studies", "Social Studies", "landmark", "#D9A648"),
        ("creative_arts", "Creative Arts", "layers", "#E2492F"),
        ("religious_education", "Religious Education", "cross", "#BFB09C"),
    ]]


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
        "slug": "kcbe",
        "name": "KCBE",
        "full_name": "Kenya Certificate of Basic Education",
        "country": "Kenya",
        "system": "CBC",
        "grades": KCBE_GRADES,
        "subjects": KCBE_SUBJECTS,
        # The senior phase grew out of a "specialisation pathway" design that the October
        # 2025 Basic Education Regulations cancelled, so content is organised by grade and
        # strand rather than by a student's chosen pathway.
        "note": "Grades 10-12 under CBC. First KCBE sitting: 2028.",
    },
    {
        "slug": "kjsea",
        "name": "KJSEA",
        "full_name": "Kenya Junior School Certificate Examination",
        "country": "Kenya",
        "system": "CBC",
        "grades": KJSEA_GRADES,
        "subjects": KJSEA_SUBJECTS,
        "note": "Grades 7-9 under CBC. Nine compulsory learning areas at Junior School.",
    },
    {
        "slug": "kpsea",
        "name": "KPSEA",
        "full_name": "Kenya Primary School Education Assessment",
        "country": "Kenya",
        "system": "CBC",
        "grades": KPSEA_GRADES,
        "subjects": KPSEA_SUBJECTS,
        "note": "Grades 4-6 under CBC. KNEC has assessed five papers since 2024.",
    },
    {
        "slug": "kcse",
        "name": "KCSE",
        "full_name": "Kenya Certificate of Secondary Education",
        "country": "Kenya",
        "system": "8-4-4",
        "grades": KCSE_FORMS,
        "subjects": SUBJECTS,
        # 8-4-4 and CBC run side by side until the old system retires; the Form 4 class
        # sitting in 2027 is the last, so this list cannot be deleted yet.
        "note": "8-4-4. The final KCSE sitting is November 2027.",
    },
    {
        "slug": "sat",
        "name": "SAT",
        "full_name": "SAT (Digital)",
        "country": "Global",
        "system": "US",
        "grades": (),          # no school year — an SAT candidate is not in a Kenyan form
        "subjects": SAT_SUBJECTS,
    },
]


# ---------------- Lookups ----------------

def curricula():
    return [{"slug": c["slug"], "name": c["name"], "full_name": c["full_name"],
             "country": c["country"], "system": c.get("system", ""),
             "subject_count": len(c["subjects"]), "grades": c.get("grades", ()),
             "year_word": year_word_for(c["slug"]), "note": c.get("note", "")}
            for c in CURRICULA]


def grades_for(cur_slug):
    """The year choices a qualification offers; empty when it has none (SAT)."""
    return (get_curriculum(cur_slug) or {}).get("grades", ())


def year_word_for(cur_slug):
    """The word in front of the number — read off the qualification's own year labels so
    the two can never drift apart, and empty when there is no school year to name."""
    grades = grades_for(cur_slug)
    return grades[0].split()[0] if grades else ""


def year_number(label):
    """The number inside a year label. 'Form 3' and 'Grade 11' only become comparable as
    3 and 11, which is what decides which group of topics a student opens on."""
    digits = re.sub(r"\D", "", label or "")
    return int(digits) if digits else None


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

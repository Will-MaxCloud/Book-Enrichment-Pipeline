"""
Genius Book Analysis v3.2 — Full taxonomy integration.
New genre, sub-genre, theme, and category master lists.
Categories added as new output field (top 3 per book).
"""

from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER THEMES
# ═══════════════════════════════════════════════════════════════════════════════

MASTER_THEMES = [
    "identity and self-discovery", "coming of age", "self-acceptance",
    "transformation and change", "inner conflict", "destiny vs free will",
    "duality of human nature", "masks and hidden selves",
    "love and romance", "friendship", "family bonds", "betrayal",
    "loyalty and devotion", "loss and grief", "forgiveness",
    "parent-child relationships", "sibling relationships", "rivalry",
    "power and corruption", "class and social inequality", "justice and injustice",
    "oppression and resistance", "war and conflict", "politics and governance",
    "revolution and rebellion", "colonialism and imperialism",
    "good vs evil", "moral ambiguity", "sacrifice and selflessness",
    "revenge and retribution", "guilt and redemption", "honor and duty",
    "temptation", "consequences of choices",
    "truth and deception", "knowledge and ignorance", "education and learning",
    "science and discovery", "art and creativity", "language and communication",
    "humanity vs nature", "technology and progress", "isolation and loneliness",
    "freedom and captivity", "home and belonging",
    "exile and displacement", "control and manipulation",
    "meaning of life", "death and mortality", "time and memory",
    "fate and prophecy", "hope and despair", "faith and doubt",
    "dreams and ambition", "legacy and inheritance", "disillusionment",
    "magic and the supernatural", "light vs darkness",
    "ancient evil", "prophecy and legend",
    "gender and sexuality", "race and ethnicity", "tradition vs modernity",
    "cultural identity", "immigration and migration",
    "wealth and materialism", "fame and celebrity",
    "madness and sanity", "addiction",
]
MASTER_THEMES_SET = {t.lower() for t in MASTER_THEMES}


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER CATEGORIES (discovery tags, top 3 per book)
# ═══════════════════════════════════════════════════════════════════════════════

MASTER_CATEGORIES = [
    # Content/concept
    "magic system", "prophecy", "time travel", "artificial intelligence",
    "multiverse or parallel worlds", "zombies or undead", "vampires",
    "werewolves or shapeshifters", "ghosts or spirits", "demons", "dragons",
    "gods and mythology", "pirates", "robots or mechs", "superpowers",
    "secret society", "conspiracy", "dystopian society", "post-apocalyptic world",
    "war setting", "courtroom drama", "medical setting", "sports focus",
    "music or art focus", "culinary or food focus", "true events based",
    "debut novel", "series starter", "standalone", "short read", "long read",
    # Vibe
    "cozy", "dark and gritty", "feel-good", "tearjerker", "mind-bending",
    "edge-of-your-seat", "laugh-out-loud", "haunting", "whimsical",
    "nostalgic", "inspirational", "unsettling", "bittersweet", "epic in scope",
    # Setting
    "set in school or university", "set in space", "historical setting",
    "small town", "big city", "isolated setting", "prison or captivity",
    "military setting", "royal court", "underground or criminal world",
    "wilderness or nature", "workplace setting", "set in the real world",
    "imaginary world",
    # Narrative style
    "unreliable narrator", "multiple perspectives", "dual timeline",
    "epistolary or found documents", "nonlinear timeline", "frame narrative",
    "stream of consciousness", "story within a story",
    # Plot hooks
    "chosen one", "quest", "heist", "tournament or competition", "survival",
    "revenge plot", "rescue mission", "political intrigue",
    "undercover or disguise", "treasure hunt", "race against time",
    "locked room or closed circle", "whodunit", "mystery box",
    "reluctant partnership", "underdog story", "rebellion or uprising",
    # Relationship tropes
    "enemies to lovers", "friends to lovers", "forbidden love", "love triangle",
    "slow burn romance", "found family", "brothers in arms", "rivalry",
    "mentor and student", "star-crossed lovers", "second chance romance",
    "marriage of convenience",
    # Representation
    "LGBTQ+", "diverse cast", "neurodivergent character",
    "disability representation", "cross-cultural", "indigenous voices",
    "feminist", "own voices",
    # Character tags
    "strong female lead", "strong male lead", "anti-hero protagonist",
    "morally grey characters", "ensemble cast", "child or teen protagonist",
    "elderly protagonist", "non-human protagonist", "animal companion",
    "dual protagonists", "reluctant hero", "villain protagonist",
]
MASTER_CATEGORIES_SET = {c.lower() for c in MASTER_CATEGORIES}


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER SUB-GENRES
# ═══════════════════════════════════════════════════════════════════════════════

MASTER_SUB_GENRES = [
    # Action & Adventure
    "survival", "espionage", "military adventure", "treasure hunt",
    "heist & caper", "naval & maritime adventure",
    # Children's & YA
    "early childhood", "middle grade", "young adult",
    # Contemporary
    "upmarket fiction", "commercial fiction", "satirical fiction",
    "slice of life",
    # Drama & Plays
    "tragedy", "comedy", "musical theatre", "absurdist drama",
    "history plays", "melodrama", "farce",
    # Fantasy
    "high & epic fantasy", "urban fantasy", "portal fantasy",
    "grimdark fantasy", "low fantasy", "sword & sorcery",
    "heroic fantasy", "gaslamp fantasy",
    # Folklore & Mythology
    "fables", "myths", "fairy tales", "legends", "tall tales",
    "nursery rhymes",
    # Graphic Novels & Manga
    "superhero", "shonen", "shojo", "seinen", "josei",
    "graphic memoir", "alternative & underground",
    # Historical Fiction
    "biographical fiction", "alternate history", "period epics",
    "naval historical", "social history fiction",
    # Horror
    "gothic horror", "psychological horror", "paranormal horror",
    "slasher & visceral horror", "cosmic horror", "body horror", "folk horror",
    # Literary Fiction
    "modernism", "post-modernism", "magical realism", "autofiction",
    "existential fiction", "post-colonial literature",
    # Classics
    "ancient & classical literature", "medieval & renaissance classics",
    "18th & 19th century literature", "20th century classics",
    # Mystery
    "cozy mystery", "hardboiled", "police procedural", "whodunit",
    "legal mystery", "medical mystery", "amateur sleuth",
    "locked room mystery", "noir",
    # Poetry
    "lyric poetry", "narrative poetry", "epic poetry", "dramatic poetry",
    "prose poetry", "spoken word", "formal poetry",
    # Romance
    "contemporary romance", "historical romance", "paranormal romance",
    "fantasy romance", "regency romance", "romantic suspense",
    # Science Fiction
    "space opera", "hard science fiction", "soft science fiction",
    "cyberpunk", "steampunk", "military science fiction",
    "dystopian", "post-apocalyptic", "time travel",
    # Short Story
    "single-author collections", "multi-author anthologies",
    "flash fiction collections",
    # Thriller & Suspense
    "psychological thriller", "legal thriller", "medical thriller",
    "political thriller", "techno-thriller", "crime thriller",
    "domestic thriller",
    # Westerns
    "classic western", "revisionist western", "frontier stories",
    "cattle drive westerns", "modern western",
    # Women's Fiction
    "family saga", "relationship fiction", "domestic fiction",
    "contemporary women's fiction",
    # Religious & Inspirational
    "biblical fiction", "christian fiction", "amish fiction",
    "spiritual & metaphysical fiction", "jewish fiction", "islamic fiction",
    # Non-fiction sub-genres
    "art history", "art criticism & theory", "individual artists & monographs",
    "technique & instruction", "architecture", "design",
    "photography theory & technique", "photography collections",
    "autobiography", "memoir", "biography", "historical biography",
    "professional & career biography", "diaries, journals & letters",
    "management & leadership", "economics", "finance & investing",
    "marketing & sales", "entrepreneurship", "business history",
    "industries & professions",
    "regional & ethnic cuisine", "baking & desserts", "special diet & nutrition",
    "culinary history", "beverages & mixology",
    "professional cooking & technique", "food writing & memoirs",
    "fiber arts & textiles", "woodworking & manual crafts",
    "interior design & home decor", "gardening & landscaping",
    "antiques & collectibles", "home improvement & maintenance",
    "test preparation", "dictionaries & encyclopedias", "atlases & maps",
    "teaching & pedagogy", "language learning", "writing & research guides",
    "personal essays", "literary collections",
    "journalism & cultural criticism", "speeches & addresses",
    "medical science", "nutrition & dieting", "exercise & physical fitness",
    "mental health & psychology", "alternative & holistic medicine",
    "addiction & recovery",
    "ancient history", "medieval history", "modern history",
    "military history", "social & cultural history",
    "political history", "world history",
    "satire", "parody", "comedic essays", "jokes & riddles",
    "comics & cartoons",
    "political ideologies", "international relations",
    "legal theory & systems", "public policy", "human rights", "civil rights",
    "ecology", "conservation", "zoology & wildlife", "botany",
    "climate science", "natural history",
    "child development", "parenting & upbringing", "family dynamics",
    "adoption & foster care", "special needs parenting",
    "ethics", "metaphysics & epistemology", "logic",
    "comparative religion", "theology",
    "eastern philosophy", "western philosophy",
    "physical sciences", "biological sciences",
    "astronomy & space science", "computer science & ai",
    "engineering & applied sciences", "mathematics",
    "personal productivity", "emotional well-being",
    "interpersonal communication", "career & professional development",
    "creativity & innovation",
    "sociology", "anthropology", "archaeology", "psychology",
    "linguistics", "cultural studies",
    "team sports", "individual sports", "outdoor activities & recreation",
    "sports history & biography", "coaching & athletics",
    "regional & city guides", "travel memoirs & narratives",
    "adventure & specialty travel", "cultural & heritage travel",
    "criminal case studies", "forensic science & investigations",
    "organized crime", "cold cases", "white collar crime",
]
MASTER_SUB_GENRES_SET = {sg.lower() for sg in MASTER_SUB_GENRES}


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE ANCHORS
# ═══════════════════════════════════════════════════════════════════════════════

SCORE_ANCHORS = {
    "prominence": {
        1: "Mentioned once or twice, peripheral to the text",
        3: "Recurring but secondary; supports other themes",
        5: "Clearly present throughout; a recognizable thread",
        7: "Central to the work; shapes major sections or arcs",
        10: "The dominant organizing principle of the entire text",
    },
    "character_importance": {
        1: "Mentioned briefly, minimal role",
        3: "Supporting character with some development",
        5: "Significant character with clear arc or function",
        7: "Major character; essential to the narrative",
        10: "Protagonist or central figure; the work revolves around them",
    },
    "tone": {
        1: "Light, easy, comfort read; cheerful throughout (Winnie the Pooh, cozy mysteries)",
        3: "Mostly light with some serious moments; generally upbeat (Harry Potter early books)",
        5: "Balanced — mix of light and heavy; serious but not bleak (Percy Jackson, Hunger Games)",
        7: "Heavy, emotionally impactful, dark themes dominate (Game of Thrones, The Road)",
        10: "Extremely dark, bleak, devastating throughout (Blood Meridian, A Little Life)",
    },
    "readability": {
        1: "Very hard; dense vocabulary, long complex sentences, requires expertise (Ulysses, Hegel)",
        3: "Challenging; above-average vocabulary, complex structure (Infinite Jest, Moby Dick)",
        5: "Average; standard prose for engaged adult readers (literary fiction, most novels)",
        7: "Easy; clear simple prose, short chapters, quick read (Hunger Games, Dan Brown)",
        10: "Effortless; very simple words, very short sentences (Diary of a Wimpy Kid, early readers)",
    },
    "violence": {
        1: "No violence whatsoever; completely kid-friendly (Paddington, Winnie the Pooh)",
        3: "Mild conflict; implied or off-page, nothing graphic (Harry Potter 1-3, Narnia)",
        5: "Moderate violence; some fight scenes, injuries described (Percy Jackson, Hunger Games)",
        7: "Significant graphic violence; frequent combat, blood, death (Game of Thrones)",
        10: "Extreme persistent violence; gory, brutal throughout (Blood Meridian, American Psycho)",
    },
    "age_target": {
        1: "Very young children (ages 4-6); picture book level (Goodnight Moon)",
        3: "Middle grade (ages 8-12); simple themes, no adult content (Percy Jackson, Narnia)",
        5: "Young adult (ages 13-17); some mature themes (Hunger Games, Divergent)",
        7: "Adult (18+); complex themes, some strong content (Game of Thrones, literary fiction)",
        10: "Mature adult only; strong sexual or violent themes, complex language (A Little Life)",
    },
    "pace": {
        1: "Extremely slow; meditative, minimal action, real slow burner (Proust, Walden)",
        3: "Leisurely; character-driven, gradual development, few big events (Normal People)",
        5: "Moderate; balanced between action and reflection (most literary fiction)",
        7: "Fast; page-turner, frequent events, hard to put down (Hunger Games, Da Vinci Code)",
        10: "Insanely fast; not a moment to breathe, relentless action throughout (Jack Reacher)",
    },
    "worldbuilding": {
        1: "No setting elaboration; generic or unspecified backdrop (most contemporary realism)",
        3: "Light setting; familiar world with basic atmosphere, minimal description",
        5: "Moderate; distinct setting with some rules, culture, or geography developed",
        7: "Rich; detailed world with history, systems, and vivid descriptions (Harry Potter)",
        10: "Exhaustive; deeply descriptive, fully realized world (Lord of the Rings, Dune)",
    },
    "humor": {
        1: "No humor whatsoever; completely serious throughout (The Road, Schindler's List)",
        3: "Occasional light moments; a few wry observations",
        5: "Regular humor; comedic moments woven throughout (Harry Potter)",
        7: "Very funny; humor is a defining feature (Hitchhiker's Guide, Good Omens)",
        10: "Maximum comedy; couldn't fit more humor in (Discworld, P.G. Wodehouse)",
    },
    "romance": {
        1: "No romantic elements whatsoever (Lord of the Flies, The Road)",
        3: "Minor romantic subplot; brief tension or attraction",
        5: "Notable romance; meaningful relationship development (Hunger Games)",
        7: "Romance is a major driver of plot and motivation (Pride and Prejudice, Twilight)",
        10: "Maximum romance; love story is everything (The Notebook, romance genre novels)",
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class Genre(str, Enum):
    # Fiction
    ACTION_AND_ADVENTURE = "action_and_adventure"
    CHILDRENS_AND_YOUNG_ADULT = "childrens_and_young_adult"
    CONTEMPORARY_FICTION = "contemporary_fiction"
    DRAMA_AND_PLAYS = "drama_and_plays"
    FANTASY = "fantasy"
    FOLKLORE_AND_MYTHOLOGY = "folklore_and_mythology"
    GRAPHIC_NOVELS_AND_MANGA = "graphic_novels_and_manga"
    HISTORICAL_FICTION = "historical_fiction"
    HORROR = "horror"
    LITERARY_FICTION = "literary_fiction"
    CLASSICS = "classics"
    MYSTERY = "mystery"
    POETRY = "poetry"
    ROMANCE = "romance"
    SCIENCE_FICTION = "science_fiction"
    SHORT_STORY = "short_story"
    THRILLER_AND_SUSPENSE = "thriller_and_suspense"
    WESTERNS = "westerns"
    WOMENS_FICTION = "womens_fiction"
    RELIGIOUS_AND_INSPIRATIONAL = "religious_and_inspirational"
    # Non-fiction
    ARTS_AND_PHOTOGRAPHY = "arts_and_photography"
    BIOGRAPHIES_AND_MEMOIRS = "biographies_and_memoirs"
    BUSINESS_AND_ECONOMICS = "business_and_economics"
    COOKBOOKS_AND_FOOD = "cookbooks_and_food"
    CRAFTS_HOBBIES_AND_HOME = "crafts_hobbies_and_home"
    EDUCATION_AND_REFERENCE = "education_and_reference"
    ESSAYS_AND_ANTHOLOGIES = "essays_and_anthologies"
    HEALTH_FITNESS_AND_WELLNESS = "health_fitness_and_wellness"
    HISTORY = "history"
    HUMOR = "humor"
    LAW_AND_POLITICS = "law_and_politics"
    NATURE_AND_ENVIRONMENT = "nature_and_environment"
    PARENTING_AND_FAMILY = "parenting_and_family"
    PHILOSOPHY_AND_RELIGION = "philosophy_and_religion"
    SCIENCE_AND_TECHNOLOGY = "science_and_technology"
    SELF_HELP = "self_help"
    SOCIAL_SCIENCES = "social_sciences"
    SPORTS_AND_OUTDOORS = "sports_and_outdoors"
    TRAVEL = "travel"
    TRUE_CRIME = "true_crime"
    OTHER = "other"


class CharacterArchetype(str, Enum):
    UNLIKELY_HERO = "unlikely_hero"
    ANTI_HERO = "anti_hero"
    CHOSEN_ONE = "chosen_one"
    MENTOR = "mentor"
    TRICKSTER = "trickster"
    REBEL = "rebel"
    EVERYMAN = "everyman"
    TRAGIC_HERO = "tragic_hero"
    VILLAIN = "villain"
    SIDEKICK = "sidekick"
    LOVE_INTEREST = "love_interest"
    WISE_ELDER = "wise_elder"
    OUTCAST = "outcast"
    WARRIOR = "warrior"
    DETECTIVE = "detective"
    SURVIVOR = "survivor"
    ROYAL = "royal"
    GODS_OR_MYTHICAL = "gods_or_mythical"
    CHILD_OR_TEEN = "child_or_teen"
    NARRATOR_OBSERVER = "narrator_observer"
    OTHER = "other"


class AgeCategory(str, Enum):
    """Character age bracket. None / missing is a valid state ('unknown')."""
    CHILD = "child"          # roughly 0-12
    TEEN = "teen"            # roughly 13-17
    YOUNG_ADULT = "young_adult"  # roughly 18-29
    ADULT = "adult"          # roughly 30-59
    ELDERLY = "elderly"      # roughly 60+
    AGELESS = "ageless"      # immortals, gods, AIs, supernatural beings, etc.


class POVType(str, Enum):
    FIRST_PERSON = "first_person"
    SECOND_PERSON = "second_person"
    THIRD_PERSON_LIMITED = "third_person_limited"
    THIRD_PERSON_OMNISCIENT = "third_person_omniscient"
    MULTIPLE_POV = "multiple_pov"
    UNRELIABLE_NARRATOR = "unreliable_narrator"
    EPISTOLARY = "epistolary"


class TimePeriod(str, Enum):
    PREHISTORIC = "prehistoric"
    ANCIENT_WORLD = "ancient_world"
    MEDIEVAL = "medieval"
    RENAISSANCE = "renaissance"
    EARLY_MODERN = "early_modern"
    EIGHTEENTH_CENTURY = "18th_century"
    NINETEENTH_CENTURY = "19th_century"
    EARLY_20TH_CENTURY = "early_20th_century"
    MID_20TH_CENTURY = "mid_20th_century"
    LATE_20TH_CENTURY = "late_20th_century"
    CONTEMPORARY = "contemporary"
    NEAR_FUTURE = "near_future"
    FAR_FUTURE = "far_future"
    TIMELESS = "timeless_or_unspecified"
    MULTIPLE_PERIODS = "multiple_time_periods"


class SettingType(str, Enum):
    URBAN = "urban"
    SUBURBAN = "suburban"
    RURAL = "rural"
    WILDERNESS = "wilderness"
    SMALL_TOWN = "small_town"
    COASTAL_OR_ISLAND = "coastal_or_island"
    DESERT = "desert"
    ARCTIC_OR_TUNDRA = "arctic_or_tundra"
    MOUNTAINOUS = "mountainous"
    UNDERGROUND = "underground"
    UNDERWATER = "underwater"
    SPACE = "space"
    OTHER_PLANET = "other_planet"
    FANTASY_REALM = "fantasy_realm"
    POST_APOCALYPTIC = "post_apocalyptic"
    INSTITUTIONAL = "institutional"
    AT_SEA = "at_sea"
    ON_THE_ROAD = "on_the_road"
    DOMESTIC = "domestic"
    VIRTUAL_OR_DIGITAL = "virtual_or_digital"


class ContentFlag(str, Enum):
    DEATH = "death"
    GRAPHIC_VIOLENCE = "graphic_violence"
    SEXUAL_CONTENT = "sexual_content"
    SEXUAL_VIOLENCE = "sexual_violence"
    SUBSTANCE_ABUSE = "substance_abuse"
    MENTAL_HEALTH = "mental_health"
    SELF_HARM = "self_harm"
    ABUSE = "abuse"
    STRONG_LANGUAGE = "strong_language"
    DISCRIMINATION = "discrimination"
    WAR = "war"
    ANIMAL_HARM = "animal_harm"
    CHILD_ENDANGERMENT = "child_endangerment"
    BODY_HORROR = "body_horror"
    TRAUMA = "trauma"
    GRIEF = "grief"
    KIDNAPPING = "kidnapping"
    SLAVERY = "slavery"
    NONE = "none"


class ReadingExperience(str, Enum):
    ACTION_PACKED = "action_packed"
    TENSE = "tense"
    IMMERSIVE = "immersive"
    THOUGHT_PROVOKING = "thought_provoking"
    EMOTIONALLY_HEAVY = "emotionally_heavy"
    FEEL_GOOD = "feel_good"
    PAGE_TURNER = "page_turner"
    SLOW_BURN = "slow_burn"
    UNPREDICTABLE = "unpredictable"
    ATMOSPHERIC = "atmospheric"
    HEARTWARMING = "heartwarming"
    DISTURBING = "disturbing"
    INSPIRATIONAL = "inspirational"
    NOSTALGIC = "nostalgic"
    EDUCATIONAL = "educational"
    CINEMATIC = "cinematic"
    COZY = "cozy"
    BITTERSWEET = "bittersweet"
    MYSTERIOUS = "mysterious"
    EPIC_IN_SCOPE = "epic_in_scope"
    WHIMSICAL = "whimsical"


class HumorType(str, Enum):
    SATIRE = "satire"
    IRONY = "irony"
    SLAPSTICK = "slapstick"
    WORDPLAY = "wordplay"
    DRY_WIT = "dry_wit"
    ABSURDIST = "absurdist"
    DARK_HUMOR = "dark_humor"
    OBSERVATIONAL = "observational"
    PARODY = "parody"
    NONE = "none"


class BookType(str, Enum):
    FICTION = "fiction"
    NON_FICTION = "non_fiction"
    ESSAY_COLLECTION = "essay_collection"
    POETRY = "poetry"
    MEMOIR = "memoir"
    ACADEMIC = "academic"
    HYBRID = "hybrid"


# ═══════════════════════════════════════════════════════════════════════════════
# NON-FICTION ENUMS
# ═══════════════════════════════════════════════════════════════════════════════
# These enums support the NonFictionInfo block on BookAnalysis. They are used
# only when a book is detected as non-fiction. See NONFICTION_DESIGN.md for the
# full design rationale.

class BookSubType(str, Enum):
    """Sub-type of non-fiction book. Determines which addendum (if any) applies."""
    SELF_HELP = "self_help"
    MEMOIR_BIOGRAPHY = "memoir_biography"
    HISTORY_NARRATIVE = "history_narrative"
    ACADEMIC_TEXTBOOK = "academic_textbook"
    POPULAR_SCIENCE = "popular_science"
    PHILOSOPHY_RELIGION = "philosophy_religion"
    BUSINESS_ECONOMICS = "business_economics"
    HEALTH_FITNESS = "health_fitness"
    COOKING_FOOD = "cooking_food"
    TRAVEL_NATURE = "travel_nature"
    TRUE_CRIME = "true_crime"
    OTHER = "other"


class TargetAudience(str, Enum):
    """Who is this book for?"""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    GENERAL_READER = "general_reader"
    SPECIALIST = "specialist"


class StructureType(str, Enum):
    """How is the book organized? Affects whether readers can skip around."""
    LINEAR_ARGUMENT = "linear_argument"
    EPISODIC_CHAPTERS = "episodic_chapters"
    CASE_STUDIES = "case_studies"
    REFERENCE = "reference"
    WORKBOOK = "workbook"
    MIXED = "mixed"


class ToneRegister(str, Enum):
    """Writing voice / tone for non-fiction. Replaces fiction's tone scale."""
    ACADEMIC = "academic"
    CONVERSATIONAL = "conversational"
    INSPIRATIONAL = "inspirational"
    SOBERING = "sobering"
    WITTY = "witty"
    DENSE = "dense"
    BREEZY = "breezy"


class ConclusionType(str, Enum):
    """How does the book conclude? Replaces sad_ending/cliffhanger for non-fiction."""
    OPTIMISTIC = "optimistic"
    CAUTIONARY = "cautionary"
    OPEN_ENDED = "open_ended"
    DEFINITIVE = "definitive"
    PROVOCATIVE = "provocative"
    HOPEFUL = "hopeful"


# ── Addendum-specific enums ────────────────────────────────────────────────────

class CommitmentLevel(str, Enum):
    """For self-help books: how much effort does this require?"""
    QUICK_READ = "quick_read"
    CASUAL_APPLICATION = "casual_application"
    SERIOUS_PRACTICE = "serious_practice"


class NarrativeShape(str, Enum):
    """For memoirs/biographies: the narrative arc."""
    TRIUMPH = "triumph"
    CAUTIONARY = "cautionary"
    WITNESS = "witness"
    SURVIVAL = "survival"
    COMING_OF_AGE = "coming_of_age"
    OTHER = "other"


class SubjectRelationship(str, Enum):
    """For memoirs/biographies: author's relationship to subject."""
    AUTOBIOGRAPHICAL = "autobiographical"
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    SCHOLARLY = "scholarly"


class HistoricalPerspective(str, Enum):
    """For history books: whose perspective is centered?"""
    GREAT_FIGURES = "great_figures"
    EVERYDAY_PEOPLE = "everyday_people"
    SPECIFIC_COMMUNITY = "specific_community"
    GLOBAL = "global"


class AcademicLevel(str, Enum):
    """For textbooks: intended educational level."""
    UNDERGRADUATE = "undergraduate"
    GRADUATE = "graduate"
    PROFESSIONAL = "professional"
    INTRO_SURVEY = "intro_survey"


class PhilosophyFocus(str, Enum):
    """For philosophy/religion books: primary orientation."""
    THEORETICAL = "theoretical"
    PRACTICAL = "practical"
    DEVOTIONAL = "devotional"
    HISTORICAL = "historical"


class BusinessAudienceRole(str, Enum):
    """For business books: who is the target role?"""
    FOUNDER = "founder"
    MANAGER = "manager"
    INDIVIDUAL_CONTRIBUTOR = "individual_contributor"
    GENERAL = "general"


class HealthEvidenceBasis(str, Enum):
    """For health/fitness books: what kind of evidence backs the claims?"""
    CLINICAL_RESEARCH = "clinical_research"
    PRACTITIONER_EXPERIENCE = "practitioner_experience"
    ANECDOTAL = "anecdotal"
    MIXED = "mixed"


class RecipeDifficulty(str, Enum):
    """For cookbooks: skill level expected of the reader."""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    MIXED = "mixed"


class CookbookPurpose(str, Enum):
    """For cookbooks: what role does the book serve?"""
    RECIPE_COLLECTION = "recipe_collection"
    TECHNIQUE = "technique"
    FOOD_NARRATIVE = "food_narrative"
    DIETARY_PROGRAM = "dietary_program"


class TravelStyle(str, Enum):
    """For travel books: what style of travel?"""
    ADVENTURE = "adventure"
    CULTURAL = "cultural"
    NATURE = "nature"
    LUXURY = "luxury"
    BUDGET = "budget"
    MIXED = "mixed"


class TrueCrimeCaseType(str, Enum):
    """For true crime books: scope of cases covered."""
    SINGLE_CASE = "single_case"
    MULTIPLE_CASES = "multiple_cases"
    PATTERN_ANALYSIS = "pattern_analysis"


class TrueCrimeResolution(str, Enum):
    """For true crime books: how the case stands."""
    SOLVED = "solved"
    UNSOLVED = "unsolved"
    COLD_CASE = "cold_case"
    ONGOING = "ongoing"


class TrueCrimePerspective(str, Enum):
    """For true crime books: from whose viewpoint."""
    JOURNALIST = "journalist"
    LAW_ENFORCEMENT = "law_enforcement"
    FAMILY = "family"
    ACADEMIC = "academic"
    PERPETRATOR = "perpetrator"


# ═══════════════════════════════════════════════════════════════════════════════
# SUB-MODELS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Non-fiction sub-type addendums ─────────────────────────────────────────────
# Each addendum holds 3-4 sub-type-specific fields. A non-fiction book has at
# most ONE addendum populated, based on its detected BookSubType. All addendum
# fields use Optional types so the model still validates when fields cannot be
# determined from the source text.

class SelfHelpAddendum(BaseModel):
    """Self-help, how-to, and instructional books."""
    skill_or_outcome: Optional[str] = Field(
        default=None, description="What the reader will be able to do after reading")
    has_exercises: Optional[bool] = Field(
        default=None, description="True if the book contains exercises, prompts, or worksheets")
    commitment_level: Optional[CommitmentLevel] = Field(default=None)


class MemoirBiographyAddendum(BaseModel):
    """Memoirs, autobiographies, and biographies."""
    life_period_covered: Optional[str] = Field(
        default=None, description="What span of the subject's life is covered")
    narrative_shape: Optional[NarrativeShape] = Field(default=None)
    subject_relationship: Optional[SubjectRelationship] = Field(default=None)


class HistoryNarrativeAddendum(BaseModel):
    """Histories and narrative non-fiction (including journalism)."""
    historical_period: Optional[str] = Field(
        default=None, description="The time period the book focuses on")
    geographic_focus: Optional[str] = Field(
        default=None, description="The region, country, or area covered")
    perspective_centered: Optional[HistoricalPerspective] = Field(default=None)


class AcademicTextbookAddendum(BaseModel):
    """Academic texts, textbooks, and reference works."""
    discipline: Optional[str] = Field(
        default=None, description="Field of study (e.g., 'biochemistry', 'literary theory')")
    level: Optional[AcademicLevel] = Field(default=None)
    has_exercises: Optional[bool] = Field(
        default=None, description="True if includes problem sets, exercises, or review questions")


class PopularScienceAddendum(BaseModel):
    """Popular science, nature writing, science journalism."""
    scientific_domain: Optional[str] = Field(
        default=None, description="The branch of science covered (e.g., 'cosmology', 'neuroscience')")
    accessibility_score: Optional[int] = Field(
        default=None, ge=1, le=10,
        description="1=requires deep prior knowledge; 10=fully accessible to any reader")
    is_cutting_edge: Optional[bool] = Field(
        default=None, description="True if it covers recent or developing science")


class PhilosophyReligionAddendum(BaseModel):
    """Philosophy, religion, and spirituality books."""
    tradition: Optional[str] = Field(
        default=None,
        description="Tradition or school (e.g., 'analytic philosophy', 'Zen Buddhism', 'Catholic theology')")
    focus: Optional[PhilosophyFocus] = Field(default=None)


class BusinessEconomicsAddendum(BaseModel):
    """Business, economics, leadership, and management books."""
    domain: Optional[str] = Field(
        default=None,
        description="Specific area (e.g., 'leadership', 'strategy', 'behavioral economics')")
    audience_role: Optional[BusinessAudienceRole] = Field(default=None)


class HealthFitnessAddendum(BaseModel):
    """Health, fitness, nutrition, and wellness books."""
    focus_area: Optional[str] = Field(
        default=None,
        description="Specific health domain (e.g., 'nutrition', 'strength training', 'sleep', 'mental health')")
    evidence_basis: Optional[HealthEvidenceBasis] = Field(default=None)


class CookingFoodAddendum(BaseModel):
    """Cookbooks and food writing."""
    cuisine_type: Optional[str] = Field(
        default=None, description="Cuisine or food tradition (e.g., 'French', 'plant-based', 'BBQ')")
    recipe_difficulty: Optional[RecipeDifficulty] = Field(default=None)
    book_purpose: Optional[CookbookPurpose] = Field(default=None)


class TravelNatureAddendum(BaseModel):
    """Travel writing and nature/wilderness writing."""
    location_focus: Optional[str] = Field(
        default=None, description="Primary location or region covered")
    travel_style: Optional[TravelStyle] = Field(default=None)


class TrueCrimeAddendum(BaseModel):
    """True crime books."""
    case_type: Optional[TrueCrimeCaseType] = Field(default=None)
    resolution: Optional[TrueCrimeResolution] = Field(default=None)
    investigation_perspective: Optional[TrueCrimePerspective] = Field(default=None)


class NonFictionInfo(BaseModel):
    """
    Container for non-fiction-specific analysis fields.

    Holds the 7 common-core fields that apply to ALL non-fiction books, plus
    11 Optional addendum slots — exactly ONE of which is populated based on
    the detected `sub_type`. The remaining addendum slots stay None.

    Example for a self-help book:
      NonFictionInfo(
          thesis="...",
          target_audience=TargetAudience.GENERAL_READER,
          ...,
          sub_type=BookSubType.SELF_HELP,
          self_help=SelfHelpAddendum(skill_or_outcome="...", ...),
          # all other addendum slots remain None
      )

    For fiction books, this entire object is None on BookAnalysis (set in A4).
    """
    # ── Common core (populated for ALL non-fiction books) ─────────────────
    thesis: str = Field(
        description="1-2 sentence statement of the book's central claim, argument, or stated purpose")
    target_audience: TargetAudience = Field(
        description="Who this book is for")
    prerequisites: list[str] = Field(
        default_factory=list,
        description="What the reader should already know. Empty list = no prereqs needed")
    structure_type: StructureType = Field(
        description="How the book is organized; affects whether readers can skip around")
    tone_register: list[ToneRegister] = Field(
        min_length=1, max_length=3,
        description="Writing voice (1-3 values, e.g. academic, conversational, sobering)")
    practical_vs_theoretical: int = Field(
        ge=1, le=10,
        description="1=pure theory/concepts; 10=pure actionable how-to")
    conclusion_type: ConclusionType = Field(
        description="How the book concludes; replaces sad_ending/cliffhanger for non-fiction")

    # ── Sub-type classification ───────────────────────────────────────────
    sub_type: BookSubType = Field(
        description="Which sub-type this book is; determines which addendum slot is populated")

    # ── Addendum slots (exactly ONE populated based on sub_type) ──────────
    # Field name matches the sub_type value (e.g. sub_type=self_help → self_help slot filled)
    self_help: Optional[SelfHelpAddendum] = Field(default=None)
    memoir_biography: Optional[MemoirBiographyAddendum] = Field(default=None)
    history_narrative: Optional[HistoryNarrativeAddendum] = Field(default=None)
    academic_textbook: Optional[AcademicTextbookAddendum] = Field(default=None)
    popular_science: Optional[PopularScienceAddendum] = Field(default=None)
    philosophy_religion: Optional[PhilosophyReligionAddendum] = Field(default=None)
    business_economics: Optional[BusinessEconomicsAddendum] = Field(default=None)
    health_fitness: Optional[HealthFitnessAddendum] = Field(default=None)
    cooking_food: Optional[CookingFoodAddendum] = Field(default=None)
    travel_nature: Optional[TravelNatureAddendum] = Field(default=None)
    true_crime: Optional[TrueCrimeAddendum] = Field(default=None)
    # Note: sub_type="other" has NO addendum slot — only common core is populated


# ── Existing sub-models ────────────────────────────────────────────────────────

class Theme(BaseModel):
    name: str = Field(description="Must be from the master theme list")
    prominence: int = Field(ge=1, le=10)

class Character(BaseModel):
    name: str
    importance: int = Field(ge=1, le=10)
    gender: str = Field(description="male, female, non-binary, or unknown")
    archetypes: list[CharacterArchetype] = Field(min_length=1, max_length=3)
    arc_summary: str
    age_category: Optional[AgeCategory] = Field(default=None)

class SettingInfo(BaseModel):
    primary_location: str
    time_period: TimePeriod
    setting_type: SettingType
    real_or_fictional: str
    additional_locations: list[str] = Field(default_factory=list)

class HumorProfile(BaseModel):
    humor_density: int = Field(ge=1, le=10)
    primary_humor_types: list[HumorType] = Field(min_length=1, max_length=3)

class ContentRatings(BaseModel):
    # ── Universal ratings (apply to both fiction and non-fiction) ─────────
    tone: int = Field(ge=1, le=10)
    readability: int = Field(ge=1, le=10)
    pace: int = Field(ge=1, le=10)
    age_target: int = Field(ge=1, le=10)
    # ── Fiction-specific ratings (default to 1 for non-fiction books) ─────
    # Non-fiction Pass 2 doesn't populate these. They default to 1
    # (minimum) so the JSON shape stays consistent across all books.
    violence: int = Field(default=1, ge=1, le=10)
    worldbuilding: int = Field(default=1, ge=1, le=10)
    humor: int = Field(default=1, ge=1, le=10)
    romance: int = Field(default=1, ge=1, le=10)

class ComputedStats(BaseModel):
    total_words: int
    total_pages: int
    avg_sentence_length: float
    avg_words_per_page: float
    estimated_read_time_hours: float


# ═══════════════════════════════════════════════════════════════════════════════
# CHUNK-LEVEL ANALYSIS — FULL (Pass 1 quality mode)
# ═══════════════════════════════════════════════════════════════════════════════

class ChunkAnalysis(BaseModel):
    chunk_index: int
    chunk_label: str
    word_count: int
    themes_detected: list[str]
    theme_prominences: dict[str, int]
    characters_present: list[Character]
    humor_density: int = Field(ge=1, le=10)
    humor_types: list[HumorType]
    tone: int = Field(ge=1, le=10)
    readability_score: int = Field(ge=1, le=10)
    violence_level: int = Field(ge=1, le=10)
    pace_score: int = Field(ge=1, le=10)
    romance_level: int = Field(ge=1, le=10)
    worldbuilding_level: int = Field(ge=1, le=10)
    content_flags: list[ContentFlag]
    notable_observations: str


# ═══════════════════════════════════════════════════════════════════════════════
# CHUNK-LEVEL ANALYSIS — SLIM (Pass 1 fast mode, Haiku-viable)
# ═══════════════════════════════════════════════════════════════════════════════

class SlimChunkAnalysis(BaseModel):
    """Minimal chunk data — just scores, names, flags, summary."""
    chunk_index: int
    chunk_label: str
    word_count: int
    tone: int = Field(ge=1, le=10)
    readability: int = Field(ge=1, le=10)
    violence: int = Field(ge=1, le=10)
    pace: int = Field(ge=1, le=10)
    worldbuilding: int = Field(ge=1, le=10)
    humor: int = Field(ge=1, le=10)
    romance: int = Field(ge=1, le=10)
    character_names: list[str] = Field(default_factory=list)
    content_flags: list[ContentFlag] = Field(default_factory=list)
    summary: str = Field(description="One sentence summary of this section")


# ═══════════════════════════════════════════════════════════════════════════════
# HOLISTIC ANALYSIS (Pass 2)
# ═══════════════════════════════════════════════════════════════════════════════

class HolisticAnalysis(BaseModel):
    book_type: BookType
    genre: Genre
    sub_genres: list[str] = Field(min_length=1, max_length=4)
    title: str
    author: str
    pov_types: list[POVType] = Field(min_length=1, max_length=3)
    pov_notes: str
    setting: SettingInfo
    reading_experience: list[ReadingExperience] = Field(min_length=1, max_length=4)
    categories: list[str] = Field(min_length=1, max_length=3)
    sad_ending: bool
    cliffhanger: bool
    ending_notes: str
    age_target: int = Field(ge=1, le=10)
    content_flags: list[ContentFlag]
    overall_summary: str
    character_arcs: dict[str, str]
    character_genders: dict[str, str] = Field(default_factory=dict)
    character_archetypes: dict[str, list[str]] = Field(default_factory=dict, description="Archetypes per character")
    character_ages: dict[str, str] = Field(default_factory=dict, description="Age category per character")
    ranked_themes: list[str] = Field(default_factory=list, description="Top 10 themes ranked most important first")
    ranked_characters: list[str] = Field(default_factory=list, description="Top 8 characters ranked most important first")
    humor_types: list[HumorType] = Field(min_length=1, max_length=3)


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LEVEL OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

class BookMetadata(BaseModel):
    title: str
    author: str
    book_type: BookType
    genre: Genre
    sub_genres: list[str]
    publisher: Optional[str] = Field(default=None)
    publish_year: Optional[int] = Field(default=None)
    language: str = Field(default="English")
    isbn: Optional[str] = Field(default=None)

class BookAnalysis(BaseModel):
    # ── Universal fields (apply to both fiction and non-fiction) ──────────
    metadata: BookMetadata
    themes: list[Theme]
    categories: list[str] = Field(description="Top 3 discovery tags from master list")
    setting: SettingInfo = Field(
        default_factory=lambda: SettingInfo(
            primary_location="",
            time_period=TimePeriod.TIMELESS,
            setting_type=SettingType.DOMESTIC,
            real_or_fictional="real",
        ),
        description="Setting info. Real for memoirs/history/travel; minimal default for self-help/philosophy/etc.")
    ratings: ContentRatings
    computed_stats: ComputedStats
    reading_experience: list[ReadingExperience]
    content_flags: list[ContentFlag]
    overall_summary: str
    analysis_version: str = Field(default="4.0.0")

    # ── Fiction-specific fields (empty defaults for non-fiction) ──────────
    # These keep JSON shape consistent across fiction/non-fiction books.
    # Non-fiction Pass 2 doesn't populate them; they take these defaults.
    characters: list[Character] = Field(default_factory=list)
    humor: HumorProfile = Field(
        default_factory=lambda: HumorProfile(
            humor_density=1,
            primary_humor_types=[HumorType.NONE],
        ))
    pov: list[POVType] = Field(default_factory=list)
    pov_notes: str = Field(default="")
    sad_ending: bool = Field(default=False)
    cliffhanger: bool = Field(default=False)
    ending_notes: str = Field(default="")

    # ── Non-fiction-specific field (None for fiction books) ───────────────
    non_fiction_info: Optional[NonFictionInfo] = Field(
        default=None,
        description="Populated for non-fiction books only; None for fiction.")

    @field_validator("themes")
    @classmethod
    def themes_sorted(cls, v):
        return sorted(v, key=lambda t: t.prominence, reverse=True)

    @field_validator("characters")
    @classmethod
    def characters_sorted(cls, v):
        return sorted(v, key=lambda c: c.importance, reverse=True)
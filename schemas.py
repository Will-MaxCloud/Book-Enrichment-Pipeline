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
# SUB-MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class Theme(BaseModel):
    name: str = Field(description="Must be from the master theme list")
    prominence: int = Field(ge=1, le=10)

class Character(BaseModel):
    name: str
    role: str
    importance: int = Field(ge=1, le=10)
    gender: str = Field(description="male, female, non-binary, or unknown")
    archetypes: list[CharacterArchetype] = Field(min_length=1, max_length=3)
    arc_summary: str
    age_category: Optional[str] = Field(default=None)

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
    tone: int = Field(ge=1, le=10)
    readability: int = Field(ge=1, le=10)
    violence: int = Field(ge=1, le=10)
    age_target: int = Field(ge=1, le=10)
    pace: int = Field(ge=1, le=10)
    worldbuilding: int = Field(ge=1, le=10)
    humor: int = Field(ge=1, le=10)
    romance: int = Field(ge=1, le=10)

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

class BookAnalysis(BaseModel):
    metadata: BookMetadata
    themes: list[Theme]
    categories: list[str] = Field(description="Top 3 discovery tags from master list")
    characters: list[Character]
    setting: SettingInfo
    ratings: ContentRatings
    computed_stats: ComputedStats
    humor: HumorProfile
    pov: list[POVType]
    pov_notes: str
    reading_experience: list[ReadingExperience]
    sad_ending: bool
    cliffhanger: bool
    ending_notes: str
    content_flags: list[ContentFlag]
    overall_summary: str
    analysis_version: str = Field(default="4.0.0")

    @field_validator("themes")
    @classmethod
    def themes_sorted(cls, v):
        return sorted(v, key=lambda t: t.prominence, reverse=True)

    @field_validator("characters")
    @classmethod
    def characters_sorted(cls, v):
        return sorted(v, key=lambda c: c.importance, reverse=True)
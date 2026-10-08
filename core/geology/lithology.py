"""Rock type catalog with render-oriented properties, and lithology text classification.

Geological maps describe rocks with words ("Lavas and basaltic pyroclasts, trachytic and
phonolytic", "Middle Triassic limestone"). This module turns those descriptions into
:class:`RockProfile` entries that carry what a renderer needs: a natural (not cartographic)
color, surface roughness, relative strength, how the rock weathers and breaks, bedding
thickness and typical block size.

Colors are approximate sRGB appearances of weathered outcrops, meant as starting points;
the cartographic colors returned by map services are deliberately not used for rendering.
Relative hardness follows the qualitative ordering of rock mass strength used in
geomorphology (Selby 1980): it decides which rocks form cliffs and coarse blocks and
which weather into fine debris and gentle slopes.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

# Weathering / fracture styles understood by the material and scatter builders
WEATHERING_STYLES = (
    "columnar",   # Cooling joints: hexagonal prisms (basalt flows, some trachytes)
    "blocky",     # Orthogonal joints: cubic / prismatic blocks (granite, sandstone, quartzite)
    "rounded",    # Spheroidal weathering: corestones and rounded boulders (granite, gabbro)
    "platy",      # Thin slabs along bedding or flow banding (phonolite, shale, flagstones)
    "vesicular",  # Gas bubbles and clinker: rough, porous lava (scoria, aa lava)
    "granular",   # Loose grains and lapilli (pyroclasts, ash, sand, gravel)
    "karst",      # Solution pitting, fluting and pavements (limestone, dolomite)
    "foliated",   # Wavy foliation and crenulation (schist, gneiss, slate)
)


@dataclass(frozen=True)
class RockProfile:
    """Appearance and mechanical behavior of a rock type."""
    key: str
    label: str
    rock_class: str                 # igneous / sedimentary / metamorphic / unconsolidated
    color: Tuple[float, float, float]           # sRGB 0..1, weathered surface
    color_alt: Tuple[float, float, float]       # sRGB 0..1, secondary tone (fresh face, oxidation, banding)
    roughness: float                # Shader roughness 0..1
    hardness: float                 # Relative rock mass strength 0 (loose) .. 1 (very strong)
    weathering: str                 # One of WEATHERING_STYLES
    bedding_m: float                # Typical layer / banding thickness in meters (0 = massive)
    clast_size_m: float             # Typical loose block size in meters (talus, boulders)


def _hex(code: str) -> Tuple[float, float, float]:
    code = code.lstrip("#")
    return tuple(int(code[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _p(key, label, rock_class, color, color_alt, roughness, hardness, weathering, bedding_m, clast_size_m):
    return RockProfile(key, label, rock_class, _hex(color), _hex(color_alt), roughness, hardness, weathering, bedding_m, clast_size_m)


ROCK_PROFILES: Dict[str, RockProfile] = {p.key: p for p in (
    # Volcanic
    _p("basalt", "Basalt", "igneous", "#3d3b39", "#5b4438", 0.80, 0.85, "columnar", 0.0, 1.2),
    _p("scoria", "Scoria / Clinker", "igneous", "#5a3427", "#2e2a28", 0.95, 0.35, "vesicular", 0.0, 0.3),
    _p("andesite", "Andesite", "igneous", "#6b6661", "#7d6a5c", 0.75, 0.80, "platy", 0.0, 1.0),
    _p("trachyte", "Trachyte", "igneous", "#8a8478", "#9c8b70", 0.70, 0.75, "platy", 0.0, 1.0),
    _p("phonolite", "Phonolite", "igneous", "#7d8075", "#a59b84", 0.65, 0.80, "platy", 0.0, 0.8),
    _p("rhyolite", "Rhyolite", "igneous", "#b8a796", "#c9a98c", 0.70, 0.80, "platy", 0.0, 0.9),
    _p("obsidian", "Obsidian", "igneous", "#19191b", "#2b2523", 0.15, 0.70, "blocky", 0.0, 0.5),
    _p("pumice", "Pumice / Lapilli", "igneous", "#c3b69d", "#a8977c", 0.95, 0.10, "granular", 0.0, 0.05),
    _p("tuff", "Tuff / Ignimbrite", "igneous", "#c2b296", "#a88f6e", 0.90, 0.35, "blocky", 2.0, 0.6),
    _p("volcanic", "Volcanic rock", "igneous", "#4f4b47", "#6b5546", 0.80, 0.75, "blocky", 0.0, 1.0),
    # Plutonic
    _p("granite", "Granite", "igneous", "#b7aca3", "#c9a99a", 0.65, 0.90, "rounded", 0.0, 2.5),
    _p("diorite", "Diorite", "igneous", "#8b8984", "#6f6d69", 0.65, 0.90, "rounded", 0.0, 2.0),
    _p("gabbro", "Gabbro", "igneous", "#45433f", "#5a5047", 0.65, 0.90, "rounded", 0.0, 2.0),
    _p("peridotite", "Peridotite / Serpentinite", "igneous", "#4f5a45", "#7a6a4b", 0.70, 0.60, "blocky", 0.0, 1.0),
    # Sedimentary
    _p("limestone", "Limestone", "sedimentary", "#c8c1b2", "#a39a89", 0.75, 0.70, "karst", 0.8, 1.5),
    _p("dolomite", "Dolostone", "sedimentary", "#d9d1c1", "#bcae95", 0.75, 0.75, "karst", 1.5, 2.0),
    _p("marl", "Marl", "sedimentary", "#b3a790", "#9d9480", 0.90, 0.25, "platy", 0.3, 0.2),
    _p("sandstone", "Sandstone", "sedimentary", "#c09a6b", "#a45a3c", 0.85, 0.60, "blocky", 1.0, 1.5),
    _p("conglomerate", "Conglomerate", "sedimentary", "#a2907a", "#7e6e5f", 0.90, 0.55, "blocky", 2.0, 1.2),
    _p("shale", "Shale / Mudstone", "sedimentary", "#5f5a54", "#7b6e5e", 0.85, 0.25, "platy", 0.1, 0.1),
    _p("siltstone", "Siltstone", "sedimentary", "#857c70", "#6c6458", 0.85, 0.40, "platy", 0.3, 0.3),
    _p("chert", "Chert", "sedimentary", "#6c5a4d", "#8d7a69", 0.60, 0.90, "blocky", 0.1, 0.3),
    _p("evaporite", "Gypsum / Evaporite", "sedimentary", "#e2dccf", "#c8bda9", 0.80, 0.15, "karst", 0.5, 0.3),
    _p("coal", "Coal", "sedimentary", "#202020", "#383430", 0.60, 0.20, "platy", 0.5, 0.2),
    # Metamorphic
    _p("quartzite", "Quartzite", "metamorphic", "#ddd4c8", "#c4a993", 0.60, 0.95, "blocky", 0.5, 1.5),
    _p("marble", "Marble", "metamorphic", "#e6e2db", "#c9c4ba", 0.50, 0.70, "karst", 0.0, 1.5),
    _p("schist", "Schist", "metamorphic", "#76726a", "#8f8470", 0.70, 0.50, "foliated", 0.05, 0.6),
    _p("gneiss", "Gneiss", "metamorphic", "#8a857e", "#5a5651", 0.70, 0.85, "foliated", 0.2, 1.8),
    _p("slate", "Slate / Phyllite", "metamorphic", "#4a4c50", "#5d5a55", 0.60, 0.60, "platy", 0.02, 0.4),
    # Unconsolidated surface deposits
    _p("alluvium", "Alluvium / River gravel", "unconsolidated", "#8e8272", "#a39581", 0.90, 0.05, "granular", 0.0, 0.3),
    _p("sand", "Sand / Dunes", "unconsolidated", "#c9b28a", "#b39770", 0.95, 0.02, "granular", 0.0, 0.0),
    _p("clay", "Clay / Silt", "unconsolidated", "#9b8670", "#7d6b58", 0.95, 0.05, "granular", 0.0, 0.0),
    _p("till", "Glacial till / Moraine", "unconsolidated", "#8a8070", "#77706a", 0.90, 0.15, "granular", 0.0, 1.5),
    _p("colluvium", "Colluvium / Scree", "unconsolidated", "#80766a", "#6b625a", 0.90, 0.15, "granular", 0.0, 0.5),
)}

# Fallbacks by broad class (also used when a description has no recognizable rock name)
CLASS_FALLBACK = {
    "igneous": "volcanic",
    "sedimentary": "sandstone",
    "metamorphic": "gneiss",
    "unconsolidated": "alluvium",
}

# Keywords searched in lithology names / unit descriptions, most specific first.
# Several languages appear in national surveys (IGME in Spanish, BRGM in French...).
KEYWORDS: Sequence[Tuple[str, str]] = (
    ("obsidian", "obsidian"), ("obsidiana", "obsidian"),
    ("pumice", "pumice"), ("pómez", "pumice"), ("pomez", "pumice"), ("lapilli", "pumice"),
    ("scoria", "scoria"), ("escoria", "scoria"), ("clinker", "scoria"), ("cinder", "scoria"),
    ("pyroclast", "scoria"), ("piroclast", "scoria"),
    ("ignimbrite", "tuff"), ("ignimbrita", "tuff"), ("tuff", "tuff"), ("toba", "tuff"), ("ash", "tuff"),
    ("phonolit", "phonolite"), ("phonolyt", "phonolite"), ("fonolit", "phonolite"),
    ("trachy-basalt", "basalt"), ("trachybasalt", "basalt"),
    ("trachyt", "trachyte"), ("traquit", "trachyte"),
    ("basalt", "basalt"), ("basált", "basalt"), ("basanit", "basalt"), ("dolerite", "basalt"), ("diabase", "basalt"),
    ("andesit", "andesite"), ("dacit", "andesite"),
    ("rhyolit", "rhyolite"), ("riolit", "rhyolite"), ("felsic volcanic", "rhyolite"),
    ("granodiorit", "granite"), ("granit", "granite"), ("pegmatit", "granite"), ("aplit", "granite"),
    ("monzonit", "diorite"), ("syenit", "diorite"), ("diorit", "diorite"), ("tonalit", "diorite"),
    ("gabbro", "gabbro"), ("norite", "gabbro"),
    ("serpentin", "peridotite"), ("peridotit", "peridotite"), ("ultramafic", "peridotite"), ("ophiolit", "peridotite"),
    ("quartzit", "quartzite"), ("cuarcita", "quartzite"),
    ("marble", "marble"), ("mármol", "marble"), ("marmol", "marble"),
    ("slate", "slate"), ("pizarra", "slate"), ("phyllit", "slate"), ("filita", "slate"),
    ("schist", "schist"), ("esquist", "schist"),
    ("gneiss", "gneiss"), ("gneis", "gneiss"), ("migmatit", "gneiss"), ("amphibolit", "gneiss"),
    ("dolomit", "dolomite"), ("dolostone", "dolomite"),
    ("marl", "marl"), ("marga", "marl"),
    ("limestone", "limestone"), ("caliza", "limestone"), ("calcaire", "limestone"), ("carbonate", "limestone"),
    ("chalk", "limestone"), ("travertin", "limestone"),
    ("gypsum", "evaporite"), ("yeso", "evaporite"), ("evaporit", "evaporite"), ("halite", "evaporite"), ("salt", "evaporite"),
    ("conglomerat", "conglomerate"), ("breccia", "conglomerate"), ("brecha", "conglomerate"),
    ("chert", "chert"), ("sílex", "chert"), ("radiolarit", "chert"),
    ("coal", "coal"), ("lignite", "coal"), ("carbón", "coal"),
    ("arenite", "sandstone"), ("arkose", "sandstone"), ("greywacke", "sandstone"), ("graywacke", "sandstone"),
    ("sandstone", "sandstone"), ("arenisca", "sandstone"),
    ("siltstone", "siltstone"), ("limolita", "siltstone"),
    ("shale", "shale"), ("mudstone", "shale"), ("claystone", "shale"), ("argilit", "shale"), ("lutita", "shale"),
    ("moraine", "till"), ("morrena", "till"), ("till", "till"), ("glacial", "till"),
    ("colluvi", "colluvium"), ("talus", "colluvium"), ("scree", "colluvium"), ("derrubio", "colluvium"),
    ("alluvi", "alluvium"), ("aluvia", "alluvium"), ("gravel", "alluvium"), ("grava", "alluvium"), ("fluvial", "alluvium"),
    ("dune", "sand"), ("duna", "sand"), ("sand", "sand"), ("arena", "sand"), ("eolian", "sand"),
    ("clay", "clay"), ("arcilla", "clay"), ("silt", "clay"), ("loess", "clay"), ("mud", "clay"),
    ("lava", "basalt"), ("mafic", "basalt"),
    ("plutonic", "granite"), ("intrusive", "granite"),
    ("volcan", "volcanic"),
    ("metamorphic", "gneiss"), ("metasediment", "schist"),
    ("unconsolidated", "alluvium"), ("quaternary deposit", "alluvium"),
    ("siliciclastic", "sandstone"), ("sedimentary", "sandstone"),
    ("igneous", "volcanic"),
)


# Broad words that only name a rock family; ignored when a specific rock is also mentioned
GENERIC_WORDS = frozenset((
    "lava", "mafic", "plutonic", "intrusive", "volcan", "metamorphic", "metasediment",
    "unconsolidated", "quaternary deposit", "siliciclastic", "sedimentary", "igneous", "carbonate",
))


def classify_lithology(*texts: str) -> List[Tuple[str, float]]:
    """Find rock profiles mentioned in lithology texts, as ``[(profile_key, weight), ...]``.

    Keys are ordered by first mention and weighted 1, 1/2, 1/3... (normalized to sum 1),
    as map descriptions list the dominant lithology first. Keywords only match at the
    start of a word. Returns an empty list when nothing is recognized.
    """
    text = " ".join(t for t in texts if t).lower()
    consumed = [False] * len(text)
    first_mention: Dict[str, int] = {}
    specific_keys = set()
    for word, key in KEYWORDS:
        start = text.find(word)
        while start >= 0:
            at_word_start = start == 0 or not text[start - 1].isalpha()
            span = range(start, start + len(word))
            if at_word_start and not any(consumed[i] for i in span):
                for i in span:
                    consumed[i] = True
                first_mention[key] = min(start, first_mention.get(key, start))
                if word not in GENERIC_WORDS:
                    specific_keys.add(key)
            start = text.find(word, start + 1)
    keys = sorted(first_mention, key=first_mention.get)
    specific = [k for k in keys if k in specific_keys] or keys
    raw = [1.0 / (i + 1) for i in range(len(specific))]
    total = sum(raw)
    return [(k, w / total) for k, w in zip(specific, raw)]


def profile_for(key: str) -> RockProfile:
    return ROCK_PROFILES.get(key) or ROCK_PROFILES[CLASS_FALLBACK.get(key, "volcanic")]


# Shader parameters per weathering style:
#   fracture = (blocky joints, columnar prisms, layering, rounded corestones)
#   surface  = (vesicles / pits, karst fluting, loose granular texture)
STYLE_PARAMETERS: Dict[str, Tuple[Tuple[float, float, float, float], Tuple[float, float, float]]] = {
    "columnar": ((0.2, 1.0, 0.0, 0.0), (0.1, 0.0, 0.1)),
    "blocky": ((1.0, 0.0, 0.2, 0.0), (0.0, 0.0, 0.1)),
    "rounded": ((0.4, 0.0, 0.0, 1.0), (0.0, 0.0, 0.2)),
    "platy": ((0.4, 0.0, 1.0, 0.0), (0.0, 0.0, 0.1)),
    "vesicular": ((0.2, 0.0, 0.0, 0.0), (1.0, 0.0, 0.3)),
    "granular": ((0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "karst": ((0.6, 0.0, 0.5, 0.0), (0.0, 1.0, 0.0)),
    "foliated": ((0.3, 0.0, 1.0, 0.0), (0.0, 0.0, 0.2)),
}


def rock_style_parameters(profile: RockProfile) -> Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float], float, float]:
    """Continuous shader inputs for a rock: (fracture RGBA, surface RGBA, joint spacing m, bedding m).

    ``surface`` alpha carries the shader roughness. Bedded rocks always show some layering.
    """
    fracture, surface = STYLE_PARAMETERS[profile.weathering]
    joint_m = max(0.2, profile.clast_size_m)
    bedding_m = profile.bedding_m if profile.bedding_m > 0 else joint_m
    if profile.bedding_m > 0:
        fracture = (fracture[0], fracture[1], max(fracture[2], 0.6), fracture[3])
    return fracture, (*surface, profile.roughness), joint_m, bedding_m

"""Rule-based tagging of FDA food registrations for the salty-snack category.

v1 is deliberately transparent keyword rules (no model calls), so every tag can be
traced to a word in the registered product or brand name. An LLM pass can refine
the ambiguous ones later.
"""
import re

# Ordered: first match wins. Patterns run on "PRODUCT_NAME | BRAND_NAME" upper-cased.
SNACK_TYPES = [
    ("Chicharon & pork rinds", r"CHICHAR+ON|PORK RIND|PORK CRACKLING|CRACKLINGS"),
    ("Prawn, fish & seafood crackers", r"PRAWN CRACKER|SHRIMP (CHIP|CRACKER|SNACK)|FISH CRACKER|KROPEK|SQUID (SNACK|CHIP|CRACKER)|FISH SKIN"),
    ("Seaweed snacks", r"SEAWEED SNACK|ROASTED SEAWEED|LAVER SNACK|NORI SNACK|CRISPY SEAWEED"),
    ("Popcorn", r"POPCORN|POP CORN"),
    ("Potato chips & crisps", r"POTATO (CHIP|CRISP|STICK|STRAW|SNACK)|\bCRISPS\b.*POTATO|POTATO.*\bCRISPS?\b"),
    ("Corn & tortilla chips", r"TORTILLA|NACHO|CORN (CHIP|CRISP|SNACK)|CORNICK|CORN BITS"),
    ("Extruded & multigrain snacks", r"\bCURLS?\b|\bPUFFS?\b|\bRINGS?\b.*SNACK|EXTRUDED|CHEESE BALLS?|CORN PUFF|MULTIGRAIN SNACK"),
    ("Rice crackers & pretzels", r"RICE CRACKER|SENBEI|PRETZEL|RICE CRISP SNACK"),
    ("Fruit & vegetable chips", r"(BANANA|CASSAVA|KAMOTE|SWEET POTATO|TARO|VEGETABLE|VEGGIE|KALE|APPLE|JACKFRUIT|COCONUT|MUSHROOM) (CHIPS?|CRISPS?)"),
    ("Nuts, peanuts & seeds", r"PEANUTS?\b|CASHEW|ALMONDS?\b|PISTACHIO|MIXED NUTS|MACADAMIA|(SUNFLOWER|PUMPKIN|WATERMELON) SEEDS?|GREEN PEAS SNACK|BROAD BEANS|CORNIK"),
    ("Other chips & savory snacks", r"\bCHIPS?\b|\bCRISPS?\b|SAVOU?RY SNACK|SNACK FOOD|\bSNACKS?\b"),
]

# Words that mean the record is not a finished salty snack.
EXCLUDE = re.compile(
    r"CHOCO(LATE)? ?CHIPS?|CHOC CHIP|COOKIE|ICE CREAM|BEVERAGE|\bDRINK|JUICE|\bSAUCE|SPREAD|PEANUT BUTTER|"
    r"\bOIL\b|MILK|BAKING|PREMIX|\bCAKE|BREAD|\bMIX\b.*FLOUR|FLOUR|NOODLE|CEREAL BAR|GRANOLA|"
    r"SUPPLEMENT|CAPSULE|TABLET|PET FOOD|DOG|CAT FOOD|COFFEE|\bTEA\b|CANDY|GUMMY|JELLY|MARSHMALLOW|"
    r"PASTR(Y|IES)|CROISSANT|CEREAL|CUSTARD|CHOCOLATE|TURR[OÓ]N|NOUGAT|BROWNIE|WAFER|BISCUIT|COOKIES"
)
INGREDIENT = re.compile(r"PELLET|SEASONING|FLAVOU?R(ING)?\b.*(POWDER|LIQUID)|\bFLAVOU?R\b\s*(FHI|#|\d)|POWDER")

FLAVOURS = [
    ("Cheese", r"CHEES|CHEDDAR|PARMESAN|NACHO CHEESE"),
    ("Sour cream & onion", r"SOUR CREAM|CREAM (AND|&) ONION"),
    ("Barbecue", r"BARBE?CUE|\bBBQ\b"),
    ("Spicy / hot / chili", r"SPICY|\bHOT\b|CHIL[IL]|JALAPE|SRIRACHA|BUFFALO|MALA\b|PERI ?PERI|FLAMIN"),
    ("Salted egg", r"SALTED EGG"),
    ("Garlic", r"GARLIC"),
    ("Sweet / honey / caramel", r"HONEY|CARAMEL|SWEET(?! POTATO| CORN)|SUGAR|MAPLE|BUTTERSCOTCH"),
    ("Butter", r"BUTTER(?!SCOTCH)"),
    ("Truffle", r"TRUFFLE"),
    ("Korean-inspired", r"KOREAN|KIMCHI|GOCHUJANG|BULGOGI|TTEOKBOKKI|HONEY BUTTER"),
    ("Seaweed / nori", r"SEAWEED|NORI|LAVER"),
    ("Salt & vinegar", r"VINEGAR"),
    ("Filipino dishes", r"ADOBO|SISIG|LECHON|SINIGANG|KARE|BICOL|INASAL|BAGOONG|CALAMANSI|UBE|LONGGANISA"),
    ("Meat & seafood", r"CHICKEN|BEEF|PORK|BACON|SHRIMP|PRAWN|CRAB|SQUID|FISH|TUNA"),
    ("Sweet corn", r"SWEET CORN"),
    ("Pizza", r"PIZZA"),
    ("Wasabi", r"WASABI"),
    ("Lime / citrus", r"\bLIME\b|LEMON|CALAMANSI|TOM YUM"),
    ("Original / salted", r"ORIGINAL|PLAIN|LIGHTLY SALTED|\bSALTED\b(?! EGG)|SEA SALT|CLASSIC"),
]

CLAIMS = [
    ("Baked / air-fried", r"\bBAKED\b|AIR[- ]?FRIED|NOT FRIED|NON[- ]FRIED"),
    ("Less salt / sodium", r"LESS SALT|LOW SODIUM|REDUCED (SALT|SODIUM)|LOW SALT|NO SALT"),
    ("High protein", r"PROTEIN"),
    ("Whole grain / fibre", r"WHOLE ?GRAIN|MULTIGRAIN|WHOLE WHEAT|FIBER|FIBRE|OAT"),
    ("Gluten-free", r"GLUTEN[- ]FREE"),
    ("Vegan / plant-based", r"VEGAN|PLANT[- ]BASED"),
    ("Organic / non-GMO", r"ORGANIC|NON[- ]GMO"),
    ("No MSG / natural", r"NO MSG|MSG[- ]FREE|ALL NATURAL|NO PRESERVATIVE"),
    ("Keto / low carb", r"KETO|LOW CARB"),
]

# Parent-group mapping by registered company name. Unlisted companies keep their own name.
COMPANY_GROUPS = [
    ("URC", r"UNIVERSAL ROBINA"),
    ("Liwayway (Oishi)", r"LIWAYWAY"),
    ("Monde Nissin", r"MONDE NISSIN"),
    ("Regent Foods", r"REGENT FOOD"),
    ("Leslie Corporation", r"\bLESLIE"),
    ("WL Foods", r"W\.? ?L\.? FOOD"),
    ("Mondelez", r"MONDELEZ"),
    ("PepsiCo", r"PEPSICO|PEPSI[- ]COLA"),
]

_SUFFIX = re.compile(r"[,.]?\s+(INCORPORATED|INC|CORPORATION|CORP|CO|COMPANY|LTD|LIMITED|OPC|PHILS?|PHILIPPINES)\.?$")
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(KG|KILOGRAMS?|G|GRAMS?|GM)\b")


def _first(rules, text):
    for label, pattern in rules:
        if re.search(pattern, text):
            return label
    return None


def _all(rules, text):
    return [label for label, pattern in rules if re.search(pattern, text)]


def company_group(company):
    name = (company or "").upper()
    for label, pattern in COMPANY_GROUPS:
        if re.search(pattern, name):
            return label
    clean = name.strip()
    for _ in range(3):
        clean = _SUFFIX.sub("", clean).strip()
    return clean.title() if clean else "Unknown"


def pack_grams(text):
    m = _WEIGHT.search(text)
    if not m:
        return None
    value = float(m.group(1))
    return value * 1000 if m.group(2).startswith("K") else value


IMPORTER = re.compile(
    r"SUPERMARKET|DUTY ?FREE|IMPORT|TRADING|DISTRIBUT|MARKET SUPPLY|ENTERPRISE|\bMART\b|STORE|"
    r"HEALTHY OPTIONS|PUREGOLD|ROBINSONS|RUSTAN|SHOPWISE|LANDERS|HARD DISCOUNT|WHOLESALE|GROCER"
)

APPLICATION_KIND = {
    "Initial": "New",
    "Re-application": "Re-application",
    "Reapplication": "Re-application",
    "Re-Issuance": "Re-application",
    "Renewal": "Renewal",
    "Renewal Data Capture": "Renewal",
    "Automatic Renewal": "Renewal",
    "Amendment": "Amendment",
    "Automatic Amendment": "Amendment",
    "I want to cancel this authorization": "Cancellation",
}

_STOP = {"FLAVORED", "FLAVOURED", "FLAVOR", "FLAVOUR", "WITH", "AND", "THE", "OF", "IN", "A"}


def product_key(group, brand_name, product_name):
    """Same product registered several times (per plant, re-filed) collapses to one key."""
    def words(s):
        return sorted({w for w in re.findall(r"[A-Z0-9]+", (s or "").upper()) if w not in _STOP})
    return "|".join([group, " ".join(words(brand_name)), " ".join(words(product_name))])


def company_type(company):
    return "Importer / retailer" if IMPORTER.search((company or "").upper()) else "Manufacturer / brand owner"


def tag(product_name, brand_name, risk_type, company_name):
    text = (product_name or "").upper()
    full = f"{product_name or ''} | {brand_name or ''}".upper()
    group = company_group(company_name)
    is_raw = (risk_type or "").lower().startswith("raw")
    excluded = bool(EXCLUDE.search(text))
    snack_type = None if excluded else _first(SNACK_TYPES, text)
    is_input = bool(snack_type or re.search(r"CHIPS?|SNACK", text)) and (is_raw or bool(INGREDIENT.search(full)))
    is_snack = bool(snack_type) and not is_input
    return {
        "is_snack": is_snack,
        "is_snack_input": is_input,
        "snack_type": snack_type if is_snack else None,
        "flavours": _all(FLAVOURS, text) if is_snack else [],
        "claims": _all(CLAIMS, text) if is_snack else [],
        "pack_g": pack_grams(text) if is_snack else None,
        "company_group": group,
        "company_type": company_type(company_name),
    }

"""
Shared utilities: schema loading, ditto propagation, field normalization.
See CLAUDE.md for verified data quality findings.
"""
import json
from paths import SCHEMAS_DIR

BIRTHPLACE_COLUMN = {1850:"Birth Place",1860:"Birth Place",1950:"Birth Place",1870:"Birthplace",1880:"Birthplace",1900:"Birthplace",1910:"Birthplace",1920:"Birthplace",1930:"Birthplace",1940:"Birthplace"}
FATHER_BIRTHPLACE_COLUMN = {1880:"Father's Birthplace",1900:"Father's Birthplace",1910:"Father's Birthplace",1920:"Father's Birthplace",1930:"Father's Birthplace",1940:"Father's Birth Place",1950:"Father Birth Place"}
GENDER_COLUMN = {1920: "Sex"}
HAS_LINE_NUMBER = {1850:True,1860:False,1870:True,1880:True,1900:True,1910:True,1920:True,1930:True,1940:True,1950:True}
VALID_RACE = {1850:["White","Black","Mulatto"],1860:["White","Black","Mulatto","Indian (Native American)"],1870:["White","Black","Mulatto"],1880:["White","Black","Mulatto","Filipino"],1900:["White","Black","Mulatto","Chinese","Mexican (Latino)"],1910:["White","Black","Mulatto","Octoroon","Mexican (Latino)","Other"],1920:["White","Black","Mulatto","Mexican (Latino)"],1930:["White","Black","Negro (Black)","Mulatto","Mexican (Latino)"],1940:["White","Negro (Black)"],1950:["White","Negro (Black)","Chinese","W0","WO"]}
RACE_NORMALIZE_MAP = {1850:{"W":"White","B":"Black","Mu":"Mulatto","Mul":"Mulatto"},1860:{"W":"White","B":"Black","Mu":"Mulatto","Mul":"Mulatto","In":"Indian (Native American)","Ind":"Indian (Native American)"},1870:{"W":"White","B":"Black","Mu":"Mulatto","Mul":"Mulatto"},1880:{"W":"White","B":"Black","Mu":"Mulatto","Mul":"Mulatto","Fil":"Filipino"},1900:{"W":"White","B":"Black","Mu":"Mulatto","Mul":"Mulatto","Ch":"Chinese","Mex":"Mexican (Latino)"},1910:{"W":"White","B":"Black","Mu":"Mulatto","Mul":"Mulatto","Ot":"Octoroon","Mex":"Mexican (Latino)"},1920:{"W":"White","B":"Black","Mu":"Mulatto","Mul":"Mulatto","Mex":"Mexican (Latino)"},1930:{"W":"White","B":"Black","Mu":"Mulatto","Mul":"Mulatto","Mex":"Mexican (Latino)","Neg":"Negro (Black)"},1940:{"W":"White","Ne":"Negro (Black)","Neg":"Negro (Black)"},1950:{"W":"White","Neg":"Negro (Black)","Ch":"Chinese"}}
VALID_GENDER = ["Male","Female"]
VALID_MARITAL_STATUS = {1880:["Married","Single","Widowed","Widower","Divorced","Na"],1900:["Married","Single","Widowed","Divorced"],1910:["Married","Single","Widowed","Divorced"],1920:["Married","Single","Widowed","Divorced"],1930:["Married","Single","Widowed","Divorced"],1940:["Married","Single","Widowed","Divorced"],1950:["Married","Never Married (Single)","Widowed","Divorced","Separated"]}
MARITAL_STATUS_NORMALIZE_MAP = {
    1950: {"Mar": "Married", "M": "Married", "Wd": "Widowed", "D": "Divorced",
           "Sep": "Separated", "S": "Never Married (Single)", "Nev": "Never Married (Single)"},
    "default": {"Mar": "Married", "M": "Married", "Wd": "Widowed", "W": "Widowed",
                "D": "Divorced", "S": "Single", "Sep": "Separated"},
}

COMPARE_FIELDS_1950 = [
    "Street Name", "House Number", "Dwelling Number", "Surname", "Given Name",
    "Relation to Head of House", "Race", "Gender", "Age", "Marital Status",
    "Birth Place", "Occupation", "Industry", "Worker Class",
]
PRIORITY_FIELDS_1950 = ["Race", "Gender", "Surname", "Given Name", "Age",
                        "Relation to Head of House", "Birth Place"]

def load_column_schema(year: int) -> dict:
    schema_path = SCHEMAS_DIR / "columns_by_decade.json"
    if not schema_path.exists():
        return {}
    with open(schema_path) as f:
        return json.load(f).get(str(year), {})

def normalize_marital_status(raw: str, year: int) -> str:
    if not raw: return raw
    raw = raw.strip()
    decade_map = MARITAL_STATUS_NORMALIZE_MAP.get(year, MARITAL_STATUS_NORMALIZE_MAP["default"])
    if raw in decade_map: return decade_map[raw]
    for v in VALID_MARITAL_STATUS.get(year, []):
        if raw.lower() == v.lower(): return v
    return raw

def propagate_dittos(records, surname_field="Surname", birthplace_field="Birthplace"):
    prev_surname, prev_birthplace = None, None
    ditto_markers = [None, "", "——", '"', "''", "ditto", "do", "Do", "DO"]
    for rec in records:
        surname = rec.get(surname_field)
        if surname in ditto_markers:
            if prev_surname: rec[surname_field] = prev_surname
        else: prev_surname = surname
        birthplace = rec.get(birthplace_field)
        if birthplace in ditto_markers:
            if prev_birthplace: rec[birthplace_field] = prev_birthplace
        else: prev_birthplace = birthplace
    return records

def normalize_race(raw: str, year: int) -> str:
    if not raw: return raw
    raw = raw.strip()
    decade_map = RACE_NORMALIZE_MAP.get(year, {})
    normalized_codes = {key.casefold(): value for key, value in decade_map.items()}
    if raw.casefold() in normalized_codes: return normalized_codes[raw.casefold()]
    for v in VALID_RACE.get(year, []):
        if raw.lower() == v.lower(): return v
    return raw

def normalize_gender(raw: str) -> str:
    if not raw: return raw
    raw = raw.strip().upper()
    if raw in ["M","MALE"]: return "Male"
    if raw in ["F","FEMALE"]: return "Female"
    return raw

def gender_column_for_year(year: int) -> str:
    return GENDER_COLUMN.get(year, "Gender")

def birthplace_column_for_year(year: int) -> str:
    return BIRTHPLACE_COLUMN.get(year, "Birthplace")

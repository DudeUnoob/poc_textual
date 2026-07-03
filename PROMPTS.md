# Census Extraction Prompts

Prompts to use with Claude claude-sonnet-4-6 vision API for each census decade.
Save each decade block as `prompts/{year}.txt`. Save the system prompt as `prompts/system.txt`.

---

## prompts/system.txt (shared across all decades)

```
You are a specialized historical census transcription assistant. You read
handwritten U.S. Census record images and extract structured data precisely.

RULES — follow these exactly:
1. Transcribe EXACTLY what is written. Do not correct spelling of names.
2. If a field is blank or empty, use null.
3. If handwriting is truly illegible after careful inspection, use "[illegible]".
   Do NOT guess an illegible field.
4. Ditto marks (——, ", ditto) mean "same as the row above" — write out the
   actual value, do not write the ditto mark itself.
5. "No one at home", "Vacant", or similar — include as a record with
   line_number filled and all person fields null.
6. Return ONLY valid JSON. No explanation, no markdown fences, no preamble.
7. Race and marital status: transcribe the LITERAL mark on the page as written
   (e.g. "W" stays "W", "Neg" stays "Neg", "Mar" stays "Mar"). Do NOT expand
   abbreviations into full words and do NOT map to any external category
   system. The research team's ground-truth spreadsheets use a controlled
   vocabulary that differs from decade to decade and sometimes encodes
   information not visible on the form at all (e.g. surname-based ethnic
   sub-codes) — that normalization happens in a separate downstream step,
   not during transcription. Your only job here is faithful transcription.
8. Gender/Sex: write "Male" or "Female" (these ARE consistent across decades
   and directly correspond to the "M"/"F" mark, so full-word expansion here
   is safe and matches ground truth exactly).
```

---

## prompts/1950.txt

```
This is a page from the 1950 U.S. Census of Population and Housing (Form P1).
State: Texas, County: Bastrop. Enumeration District: 11-2A.

The form has two sections:
  - MAIN RECORDS (lines 1–30): extract all of these
  - SAMPLE LINES (bottom section, separate grid): label these with
    line_number as "S1", "S2", etc.

For each numbered line in the MAIN RECORDS section, extract these fields
using EXACTLY these key names (these match the research team's spreadsheet
columns exactly):

  "Line Number"         integer
  "Street Name"         string or null
  "House Number"        integer or null
  "Dwelling Number"     integer or null
  "Surname"             string or null (written once per household; propagate from above for dashes/ditto marks)
  "Given Name"          string or null
  "Relation to Head of House"  string (Head, Wife, Son, Daughter, Lodger, Father, Mother, etc.)
  "Race"                string — transcribe EXACTLY what letter/word is written on the
                         form (e.g. "W", "Neg"). Do NOT expand or guess a fuller
                         category. Normalization to the research team's vocabulary
                         happens in a separate step, not here.
  "Gender"               string (Male or Female)
  "Age"                 integer or null
  "Marital Status"      string as abbreviated on form (Mar, Wd, D, Sep, S) or null
  "Birth Place"         string (state name or country) or null
  "Occupation"          string or null
  "Industry"            string or null
  "Worker Class"        string (P, G, O, NP as marked) or null

Return a JSON array of objects, one per line.

NOTE: This decade's ground truth also uses a sub-code ("W0"/"WO") applied to
some White-coded individuals based on Spanish-language surnames. That code is
NOT visible anywhere on the physical form and cannot be transcribed from the
image — do not attempt to guess it. Just transcribe what's written ("W").
```

---

## prompts/1940.txt

```
This is a page from the 1940 U.S. Census population schedule.
State: Texas, County: Bastrop.

For each numbered line, extract using EXACTLY these key names:

  "Line Number"              integer
  "Street Name"              string or null
  "Surname"                  string or null
  "Given Name"               string or null
  "Relation to Head of House" string
  "Gender"                   string (Male or Female)
  "Race"                     string — transcribe the letter/word as written (e.g. "W", "Ne").
                              Ground truth for this decade only contains White and
                              Negro (Black) — do not expand, just transcribe the raw mark.
  "Age"                      integer or null
  "Estimated Birth Year"     integer or null
  "Marital Status"           string or null
  "Birthplace"               string or null
  "Father's Birth Place"     string or null
  "Mother's Birth Place"     string or null
  "Occupation"               string or null
  "Industry"                 string or null
  "Worker Class"             string or null
  "Weeks Worked"             integer or null
  "Income"                   integer or null

Return a JSON array.
```

---

## prompts/1930.txt

```
This is a page from the 1930 U.S. Census population schedule.
State: Texas, County: Bastrop.

NOTE: "Mexican" first appears as a racial category in 1930. Write it as "Mexican".

For each numbered line, extract using EXACTLY these key names:

  "Line Number"           integer
  "Surname"               string or null
  "Given Name"            string or null
  "Relation to Head"      string
  "Gender"                string (Male or Female)
  "Race"                  string — transcribe the letter/word as written. Ground truth
                           for this decade contains White, Black, Negro (Black), Mulatto,
                           and Mexican (Latino) -- note both "Black" and "Negro (Black)"
                           appear inconsistently in the ground truth itself; transcribe
                           exactly what's on the form and let normalization sort it out.
  "Age"                   integer or null
  "Estimated Birth Year"  integer or null
  "Marital Status"        string or null
  "Age at First Marriage" integer or null
  "Birthplace"            string or null
  "Father's Birthplace"   string or null
  "Mother's Birthplace"   string or null
  "Language Spoken"       string or null
  "Occupation"            string or null
  "Industry"              string or null

Return a JSON array.
```

---

## prompts/1920.txt

```
This is a page from the 1920 U.S. Census population schedule.
State: Texas, County: Bastrop.

For each numbered line, extract using EXACTLY these key names:

  "Line Number"           integer
  "Surname"               string or null
  "Given Name"            string or null
  "Relationship"          string
  "Home Free or Mortgaged" string or null
  "Sex"                   string (Male or Female)
  "Race"                  string — transcribe the letter/word as written. Ground truth
                           for this decade contains White, Black, Mulatto, and
                           Mexican (Latino) -- transcribe exactly what's on the form.
  "Age"                   integer or null
  "Estimated Birth Year"  integer or null
  "Marital Status"        string or null
  "Birthplace"            string or null
  "Father's Birthplace"   string or null
  "Mother's Birthplace"   string or null
  "Citizenship"           string or null
  "Occupation"            string or null
  "Industry"              string or null

Return a JSON array.
```

---

## prompts/1910.txt

```
This is a page from the 1910 U.S. Census population schedule.
State: Texas, County: Bastrop.

NOTE: "Mulatto" is still a valid race category in 1910.

For each numbered line, extract using EXACTLY these key names:

  "Line Number"                      integer
  "Street Name"                      string or null
  "Surname"                          string or null
  "Given Name"                       string or null
  "Relationship"                     string
  "Gender"                           string (Male or Female)
  "Race"                             string — transcribe the letter/word as written.
                                      Ground truth for this decade contains White, Black,
                                      Mulatto, Octoroon, Mexican (Latino), and Other --
                                      "Octoroon" is a real category used in this decade's
                                      data, don't discard it as an error if you see it.
  "Age"                              integer or null
  "Estimated Birth Year"             integer or null
  "Marital Status"                   string or null
  "Number of Years of Present Marriage" integer or null
  "Birthplace"                       string or null
  "Father's Birthplace"              string or null
  "Mother's Birthplace"              string or null
  "Occupation"                       string or null
  "Industry"                         string or null

Return a JSON array.
```

---

## prompts/1900.txt

```
This is a page from the 1900 U.S. Census population schedule.
State: Texas, County: Bastrop.

NOTE: 1900 records birth MONTH and YEAR separately instead of age.

For each numbered line, extract using EXACTLY these key names:

  "Line Number"                          integer
  "Family Number"                        integer or null
  "Street"                               string or null
  "House Number"                         integer or null
  "Number of Dwelling in Order of Visitation" integer or null
  "Surname"                              string or null
  "Given Name"                           string or null
  "Relationship"                         string
  "Race"                                 string — transcribe the letter/word as written.
                                          Ground truth for this decade contains White,
                                          Black, Mulatto, Chinese, and Mexican (Latino).
  "Gender"                               string (Male or Female)
  "Birth Month"                          string (Jan, Feb, Mar... Dec) or null
  "Birth Year"                           integer or null
  "Age"                                  integer or null
  "Marital Status"                       string or null
  "Years Married"                        integer or null
  "Number of Children Born"              integer or null
  "Number of Children Living"            integer or null
  "Birthplace"                           string or null
  "Father's Birthplace"                  string or null
  "Mother's Birthplace"                  string or null
  "Occupation"                           string or null
  "Can Read"                             string (Y or N) or null
  "Can Write"                            string (Y or N) or null
  "Can Speak English"                    string (Y or N) or null
  "House Owned or Rented"                string (Own or Rent) or null
  "Farm or House"                        string (F or H) or null

Return a JSON array.
```

---

## prompts/1880.txt

```
This is a page from the 1880 U.S. Census population schedule.
State: Texas, County: Bastrop.

NOTE: 1880 is the FIRST census to include a "Relation to Head of House" column.
      "Mulatto" is still a valid race category.

For each numbered line, extract using EXACTLY these key names:

  "Line Number"               integer
  "Surname"                   string or null
  "Given Name"                string or null
  "Race"                      string — transcribe the letter/word as written. Ground
                              truth for this decade contains White, Black, Mulatto,
                              and Filipino.
  "Gender"                    string (Male or Female)
  "Age"                       integer or null
  "Birth Month"               string or null
  "Birth Year"                integer or null
  "Relation to Head of House" string
  "Birthplace"                string or null
  "Father's Birthplace"       string or null
  "Mother's Birthplace"       string or null
  "Marital Status"            string or null
  "Occupation"                string or null

Return a JSON array.
```

---

## prompts/1870.txt

```
This is a page from the 1870 U.S. Census population schedule.
State: Texas, County: Bastrop.

NOTE: No "Relationship" column exists in 1870.
      "Mulatto" is a valid race category.

For each numbered line, extract using EXACTLY these key names:

  "Line Number"            integer
  "Surname"                string or null
  "Given Name"             string or null
  "Age"                    integer or null
  "Birth Year"             integer or null (calculate as ~1870 minus age)
  "Gender"                 string (Male or Female)
  "Race"                   string — transcribe the letter/word as written. Ground
                           truth for this decade contains only White, Black, and Mulatto.
  "Birthplace"             string or null
  "Father of Foreign Birth" string (Y or N) or null
  "Mother of Foreign Birth" string (Y or N) or null
  "Occupation"             string or null
  "Real Estate Value"      integer or null
  "Personal Estate Value"  integer or null

Return a JSON array.
```

---

## prompts/1860.txt

```
This is a page from the 1860 U.S. Census population schedule.
State: Texas, County: Bastrop.

NOTE: No "Relationship" column, and NO "Line Number" column exists in this
decade's ground truth at all (confirmed absent from all 17 sheets in the
workbook) -- do not include a line_number field for 1860, it has no
counterpart to compare against. Race values observed: White, Black, Mulatto,
Indian (Native American).
If this appears to be a SLAVE SCHEDULE (separate form listing enslaved people
without names), add a top-level field "schedule_type": "slave" and use fields:
age, sex, race, fugitive_from_state, deaf_dumb_blind_insane_idiotic.

For REGULAR schedule, extract using EXACTLY these key names:

  "Dwelling Number"          integer or null
  "Family Number"            integer or null
  "Given Name"               string or null
  "Surname"                  string or null
  "Age"                      integer or null
  "Birth Year"               integer or null
  "Gender"                   string (Male or Female)
  "Race"                     string — transcribe the letter/word as written. Ground
                             truth for this decade contains White, Black, Mulatto,
                             and Indian (Native American).
  "Occupation"               string or null
  "Real Estate Value"        integer or null
  "Personal Estate Value"    integer or null
  "Birth Place"              string or null
  "Married Within Year"      string (Y or N) or null
  "Attended School"          string (Y or N) or null
  "Cannot Read, Write"       string (Y or N) or null
  "Disability Condition"     string or null

Return a JSON array.
```

---

## prompts/1850.txt

```
This is a page from the 1850 U.S. Census population schedule.
State: Texas, County: Bastrop.

NOTE: No "Relationship" column. Earliest census in this project.
If this is a SLAVE SCHEDULE, add "schedule_type": "slave" at top level.

For REGULAR schedule, extract using EXACTLY these key names:

  "Dwelling Number"       integer or null
  "Family Number"         integer or null
  "Given Name"            string or null
  "Surname"               string or null
  "Age"                   integer or null
  "Birth Date"            integer or null (birth year)
  "Gender"                string (Male or Female)
  "Race"                  string — transcribe the letter/word as written. Ground
                          truth for this decade contains only White, Black, and Mulatto.
  "Occupation"            string or null
  "Value of Real Estate"  integer or null
  "Birth Place"           string or null
  "Married within the Year" string (Y or N) or null
  "Attended School"       string (Y or N) or null
  "Cannot Read, Write"    string (Y or N) or null
  "Condition"             string or null

Return a JSON array.
```

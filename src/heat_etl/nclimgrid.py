"""Parse nClimGrid-Daily county area-average files (tmax-YYYYMM-cty-scaled.csv).

Row layout (no header, comma-separated, 37 fields):
    cty, <NCEI state code><county code>, "<ST>: <name>", YYYY, MM, TMAX, day1 ... day31
Values are degrees C, written as %8.2f. Days that don't exist (Feb 30, Apr 31) are -999.99.

PITFALL: the 5-digit county ID uses NCEI's state numbering, NOT FIPS.
Texas is NCEI state 41 (FIPS 48). The 3-digit county part matches the FIPS county code,
so 41453 (NCEI) == 48453 (FIPS, Travis County).
"""
import calendar
import re

MISSING = -999.99
NCEI_TO_FIPS_STATE = {"41": "48"}           # Texas only; extend if the study grows
STATE_ABBR = {"41": "TX"}
FILENAME_RE = re.compile(r"(?P<var>tmax|tmin|tavg|prcp)-(?P<yyyymm>\d{6})-cty-(?P<status>scaled|prelim)\.csv(\.gz)?$")


class NClimGridFormatError(ValueError):
    pass


def parse_filename(key):
    m = FILENAME_RE.search(key)
    if not m:
        raise NClimGridFormatError(f"Not an nClimGrid county file: {key}")
    if m["status"] != "scaled":
        raise NClimGridFormatError(f"{key}: use 'scaled' files; 'prelim' values are not final")
    return m["var"], int(m["yyyymm"][:4]), int(m["yyyymm"][4:])


def parse_county_file(text, ncei_state="41", expect=None):
    """Return {fips: [tmax_day1, ..., tmax_dayN]} for one state and one month.

    Missing days are None. Only real calendar days are returned (N = days in month).
    expect = (variable, year, month) from the filename, checked against every row.
    """
    out = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        f = [x.strip() for x in line.split(",")]
        if len(f) != 37 or f[0] != "cty":
            raise NClimGridFormatError(f"line {line_no}: expected 37 fields starting 'cty', got {len(f)}")
        ncei_id = f[1]
        if not ncei_id.startswith(ncei_state):
            continue
        year, month, var = int(f[3]), int(f[4]), f[5].lower()
        if expect and (var, year, month) != expect:
            raise NClimGridFormatError(f"line {line_no}: row is {var} {year}-{month:02d}, file says {expect}")
        if not f[2].startswith(STATE_ABBR[ncei_state] + ":"):
            raise NClimGridFormatError(f"line {line_no}: {ncei_id} labelled {f[2]!r}")

        n_days = calendar.monthrange(year, month)[1]
        values = []
        for v in f[6:6 + n_days]:
            x = float(v)
            values.append(None if x <= MISSING + 0.001 else x)
        fips = NCEI_TO_FIPS_STATE[ncei_state] + ncei_id[2:]
        if fips in out:
            raise NClimGridFormatError(f"duplicate county {fips}")
        out[fips] = values
    return out

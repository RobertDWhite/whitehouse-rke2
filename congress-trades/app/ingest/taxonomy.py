"""Static maps: SIC code -> broad sector, and congressional committee -> oversight sectors.
Used for sector tagging (SEC) and the conflict-of-interest signal."""


def sector_from_sic(sic):
    """Map a 4-digit SIC code to a broad sector bucket."""
    if not sic:
        return None
    try:
        n = int(str(sic)[:4])
    except (ValueError, TypeError):
        return None
    ranges = [
        (100, 999, "Agriculture"),
        (1000, 1499, "Energy"),          # mining/oil/gas extraction
        (1500, 1799, "Industrials"),     # construction
        (2000, 2199, "Consumer Staples"),
        (2200, 2399, "Consumer Discretionary"),
        (2400, 2799, "Industrials"),
        (2800, 2899, "Materials"),       # chemicals
        (2830, 2836, "Healthcare"),      # pharma (overlaps; checked below)
        (2900, 2999, "Energy"),
        (3000, 3399, "Materials"),
        (3400, 3599, "Industrials"),
        (3570, 3579, "Technology"),      # computers
        (3600, 3699, "Technology"),      # electronics
        (3700, 3799, "Industrials"),     # transport equip
        (3800, 3899, "Healthcare"),      # medical instruments
        (4000, 4799, "Industrials"),     # transportation
        (4800, 4899, "Communications"),
        (4900, 4999, "Utilities"),
        (5000, 5999, "Consumer Discretionary"),  # retail/wholesale
        (6000, 6499, "Financials"),
        (6500, 6799, "Real Estate"),
        (7000, 7299, "Consumer Discretionary"),
        (7370, 7379, "Technology"),      # software/IT services
        (7300, 7399, "Industrials"),
        (8000, 8099, "Healthcare"),
        (2833, 2836, "Healthcare"),
        (3826, 3829, "Healthcare"),
    ]
    # pharma/biotech & software get priority over the broad bands above
    if 2833 <= n <= 2836 or 8731 <= n <= 8734:
        return "Healthcare"
    if 7370 <= n <= 7379:
        return "Technology"
    if 3570 <= n <= 3579 or 3670 <= n <= 3679:
        return "Technology"
    for lo, hi, name in ranges:
        if lo <= n <= hi:
            return name
    return "Other"


# committee name substring -> oversight sectors (a pragmatic prior, not exhaustive)
COMMITTEE_SECTORS = {
    "armed services": ["Industrials"],          # defense contractors sit in Industrials SIC
    "defense": ["Industrials"],
    "financial services": ["Financials", "Real Estate"],
    "banking": ["Financials", "Real Estate"],
    "energy": ["Energy", "Utilities"],
    "natural resources": ["Energy", "Materials"],
    "commerce": ["Technology", "Communications", "Consumer Discretionary"],
    "science": ["Technology", "Communications"],
    "health": ["Healthcare"],
    "agriculture": ["Agriculture", "Consumer Staples"],
    "transportation": ["Industrials"],
    "homeland security": ["Industrials", "Technology"],
    "intelligence": ["Industrials", "Technology"],
    "ways and means": ["Financials", "Healthcare"],  # tax + healthcare policy
    "finance": ["Financials", "Healthcare"],
}


def committee_sectors(committee_names):
    out = set()
    for name in committee_names or []:
        low = (name or "").lower()
        for key, sectors in COMMITTEE_SECTORS.items():
            if key in low:
                out.update(sectors)
    return sorted(out)

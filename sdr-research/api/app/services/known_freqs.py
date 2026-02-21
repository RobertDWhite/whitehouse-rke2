"""
Static lookup table of well-known frequencies.

Frequencies are stored in Hz with a match tolerance. The first match wins,
so list more specific entries (narrow-band) before broader band ranges.
"""

from typing import Optional


# (center_hz, tolerance_hz, label)
_KNOWN: list[tuple[float, float, str]] = [
    # ── NOAA Weather Radio ────────────────────────────────────────────────
    (162_400_000, 3_000, "NOAA WX-1"),
    (162_425_000, 3_000, "NOAA WX-2"),
    (162_450_000, 3_000, "NOAA WX-3"),
    (162_475_000, 3_000, "NOAA WX-4"),
    (162_500_000, 3_000, "NOAA WX-5"),
    (162_525_000, 3_000, "NOAA WX-6"),
    (162_550_000, 3_000, "NOAA WX-7"),

    # ── Amateur — 2m ──────────────────────────────────────────────────────
    (144_200_000, 3_000, "2m SSB Calling"),
    (144_390_000, 3_000, "APRS 2m"),
    (146_520_000, 5_000, "2m National Simplex Calling"),
    (146_580_000, 5_000, "2m FM Simplex"),
    (147_555_000, 5_000, "2m FM Simplex"),

    # ── Amateur — 70cm ────────────────────────────────────────────────────
    (432_100_000, 3_000, "70cm SSB Calling"),
    (433_920_000, 5_000, "70cm FM Simplex"),
    (446_000_000, 5_000, "70cm National Simplex Calling"),
    (446_500_000, 5_000, "70cm FM Simplex"),

    # ── Amateur — 1.25m ───────────────────────────────────────────────────
    (223_500_000, 5_000, "1.25m National Simplex Calling"),

    # ── ISS ───────────────────────────────────────────────────────────────
    (145_800_000, 5_000, "ISS Voice Downlink"),
    (437_550_000, 5_000, "ISS Packet"),
    (145_825_000, 3_000, "ISS APRS"),

    # ── Aviation ──────────────────────────────────────────────────────────
    (121_500_000, 5_000, "Aviation Distress (Guard)"),
    (122_750_000, 5_000, "Aviation Multicom"),
    (123_025_000, 5_000, "Aviation Unicom"),
    (123_450_000, 5_000, "Aviation Air-to-Air"),

    # ── Marine VHF ───────────────────────────────────────────────────────
    (156_800_000, 5_000, "Marine Ch 16 (Distress/Calling)"),
    (156_300_000, 5_000, "Marine Ch 6 (Safety)"),
    (157_050_000, 5_000, "Marine Ch 22A (USCG Working)"),

    # ── FRS/GMRS simplex ─────────────────────────────────────────────────
    (462_562_500, 5_000, "FRS/GMRS Ch 1"),
    (462_587_500, 5_000, "FRS/GMRS Ch 2"),
    (462_612_500, 5_000, "FRS/GMRS Ch 3"),
    (462_637_500, 5_000, "FRS/GMRS Ch 4"),
    (462_662_500, 5_000, "FRS/GMRS Ch 5"),
    (462_687_500, 5_000, "FRS/GMRS Ch 6"),
    (462_712_500, 5_000, "FRS/GMRS Ch 7"),
    (467_562_500, 5_000, "FRS Ch 8"),
    (467_587_500, 5_000, "FRS Ch 9"),
    (467_612_500, 5_000, "FRS Ch 10"),
    (467_637_500, 5_000, "FRS Ch 11"),
    (467_662_500, 5_000, "FRS Ch 12"),
    (467_687_500, 5_000, "FRS Ch 13"),
    (467_712_500, 5_000, "FRS Ch 14"),
    (462_550_000, 5_000, "GMRS Ch 15 (Calling)"),
    (462_575_000, 5_000, "GMRS Ch 16"),
    (462_600_000, 5_000, "GMRS Ch 17"),
    (462_625_000, 5_000, "GMRS Ch 18"),
    (462_650_000, 5_000, "GMRS Ch 19"),
    (462_675_000, 5_000, "GMRS Ch 20"),
    (462_700_000, 5_000, "GMRS Ch 21"),
    (462_725_000, 5_000, "GMRS Ch 22"),

    # ── MURS ─────────────────────────────────────────────────────────────
    (151_820_000, 5_000, "MURS Ch 1"),
    (151_880_000, 5_000, "MURS Ch 2"),
    (151_940_000, 5_000, "MURS Ch 3"),
    (154_570_000, 5_000, "MURS Ch 4"),
    (154_600_000, 5_000, "MURS Ch 5"),
]


def lookup_known_freq(frequency_hz: float) -> Optional[str]:
    """Return a human label for a known frequency, or None if unrecognized."""
    for center, tolerance, label in _KNOWN:
        if abs(frequency_hz - center) <= tolerance:
            return label
    return None

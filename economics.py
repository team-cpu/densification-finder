"""
Residual land value for a single parcel — the arithmetic behind Block C.

    Landwert = Verkaufserlös der neuen Flächen
             − Baukosten
             − Baunebenkosten
             − Abbruchkosten des bestehenden Gebäudes
             − Finanzierungskosten
             − Gewinn / Risiko

Kept out of the interface for two reasons. It has to produce exactly one
calculation path that both the screen and the exported PDF render, so the
document cannot disagree with what was on screen; and every step of it is
arithmetic worth testing without starting Streamlit.

Every default here is a published benchmark with its source attached, never an
invented number, and every one of them is marked *unbestätigt* until Philipp
names the figure he actually calculates with. The interface shows that mark, so
a benchmark can never be mistaken for his own assumption.

The result is the residual value of the ADDITIONAL floor area only. It is not a
valuation of the parcel: the existing building keeps a value of its own, which
this deliberately does not estimate.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Benchmark:
    """A default the user can override, and where it comes from."""

    value: float
    unit: str
    source: str
    #: False until Philipp confirms the figure. Rendered as a warning, because a
    #: cited median is still not the number a developer prices a project with.
    confirmed: bool = False
    url: str = ""
    note: str = ""

    @property
    def provenance(self) -> str:
        mark = "" if self.confirmed else " — mit Philipp zu bestätigen"
        return f"{self.source}{('; ' + self.note) if self.note else ''}{mark}"


#: Screening defaults. Sources checked 2026-08-18.
BENCHMARKS = {
    "sale_price_chf_m2": Benchmark(
        7300,
        "CHF/m² Verkaufsfläche",
        "RealAdvisor, Median Wohnungen Kanton Aargau CHF 7'331/m² "
        "(Spanne 4'496–12'658), Stand August 2026",
        url="https://realadvisor.ch/de/immobilienpreise-pro-m2/kanton-aargau",
        note="Bestandsmarkt über den ganzen Kanton, nicht Neubau an dieser Lage",
    ),
    "construction_chf_m2": Benchmark(
        2600,
        "CHF/m² Verkaufsfläche",
        "AKTIVA AG Neubau-Benchmarks nach Schätzerhandbuch / Wüest Partner: "
        "Eigentum CHF 2'600/m² HNF, Renditeobjekt CHF 2'050/m² HNF (BKP 2)",
        url="https://aktiva.swiss/immobilien-benchmarks/",
        note="ohne Einstellhalle, dort rund CHF 40'000 pro Platz",
    ),
    "demolition_chf_m2": Benchmark(
        150,
        "CHF/m² bestehende Geschossfläche",
        # The weakest of these defaults, and the only one derived rather than
        # quoted. No Swiss source that can actually be opened states a price per
        # m² of floor area; the "CHF 80–180 per m³" that circulates in search
        # results traces back to no page that carries it — every candidate
        # either lacks the figure, 403s or 404s. Do not re-add it from a search
        # snippet.
        #
        # What ofri does publish is a total against a stated building size,
        # which is what makes a rate derivable at all: CHF 18'000–28'000 for a
        # 120 m² single-family house, itemised down to demolition, disposal and
        # preparation — CHF 150–233/m². The low end is taken deliberately: the
        # 120 m² of the source is living area, while this multiplies the GWR
        # floor area (footprint × storeys), which is the larger number.
        "Abgeleitet aus CHF 18'000–28'000 für ein 120-m²-Einfamilienhaus "
        "(ofri.ch, Stand 30.08.2023) = CHF 150–233/m²; hier das untere Ende",
        url="https://www.ofri.ch/kosten/hausabbruch",
        note="Schadstoffanalyse bei Bauten vor 1990 kommt dazu und ist hier nicht gerechnet",
    ),
    "ancillary_pct": Benchmark(
        15.0, "% der Baukosten", "Vorgabe aus dem Auftrag (Standardwert 15%)",
        confirmed=True,
    ),
    "profit_pct": Benchmark(
        15.0, "% des Verkaufserlöses", "Vorgabe aus dem Auftrag (Standardwert 15%)",
        confirmed=True,
    ),
    "financing_pct": Benchmark(
        3.0,
        "% der Erstellungskosten",
        "Grobannahme: rund 18 Monate Bauzeit zu rund 4% auf durchschnittlich "
        "halbem Kapital",
        note="im Auftrag ausdrücklich als grob und optional bezeichnet",
    ),
    "sale_area_pct": Benchmark(
        80.0,
        "% der Geschossfläche",
        "Erfahrungswert Wohnbau: Haupt-/Wohnfläche liegt bei rund 70–80% der "
        "Geschossfläche",
        note="Das Potenzial ist anrechenbare Geschossfläche, nicht Wohnfläche",
    ),
}

#: A rule of thumb the brief names, not a planning standard.
SQM_PER_UNIT = 90


@dataclass(frozen=True)
class Step:
    """One line of the calculation path, as shown on screen and in the PDF."""

    label: str
    formula: str
    value: float
    unit: str = "CHF"
    #: "area" | "revenue" | "cost" | "result" — drives sign and emphasis only.
    kind: str = "cost"


def chf(value: Optional[float]) -> str:
    """Swiss thousands separator, no decimals. These are screening figures; a
    residual land value printed to the franc would claim a precision that the
    inputs — a canton-wide median and an estimated existing floor area — do not
    have."""
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", "’")


def units(potential_gf: float, sqm_per_unit: float = SQM_PER_UNIT) -> Optional[float]:
    """Possible dwellings from a floor-area potential. None when the assumed
    unit size is zero or negative, which is a division rather than an answer."""
    if not sqm_per_unit or sqm_per_unit <= 0:
        return None
    return potential_gf / sqm_per_unit


def residual(
    potential_gf: float,
    sale_area_pct: float,
    sale_price_chf_m2: float,
    construction_chf_m2: float,
    ancillary_pct: float,
    existing_gf: float,
    demolition_chf_m2: float,
    financing_pct: float,
    profit_pct: float,
    demolish: bool = True,
) -> list[Step]:
    """The full path from floor-area potential to residual land value.

    Returns every intermediate step rather than the final number alone: the
    people this is for adjust assumptions, and an assumption cannot be adjusted
    if only the total is visible.

    Revenue and construction cost are both reckoned per m² of saleable area,
    which is how the published benchmark for construction is expressed — a cost
    per m² of main usable area already carries the circulation it needs. Mixing
    the bases (revenue on saleable area, cost on gross floor area) would quietly
    overstate the land value by the difference between them.
    """
    sale_area = potential_gf * sale_area_pct / 100.0
    revenue = sale_area * sale_price_chf_m2
    construction = sale_area * construction_chf_m2
    ancillary = construction * ancillary_pct / 100.0
    demolition = existing_gf * demolition_chf_m2 if demolish else 0.0
    financing = (construction + ancillary + demolition) * financing_pct / 100.0
    profit = revenue * profit_pct / 100.0
    land = revenue - construction - ancillary - demolition - financing - profit

    steps = [
        Step(
            "Verkaufsfläche",
            f"{chf(potential_gf)} m² GF × {sale_area_pct:.0f}%",
            sale_area,
            "m²",
            "area",
        ),
        Step(
            "Verkaufserlös",
            f"{chf(sale_area)} m² × CHF {chf(sale_price_chf_m2)}/m²",
            revenue,
            kind="revenue",
        ),
        Step(
            "− Baukosten (BKP 2)",
            f"{chf(sale_area)} m² × CHF {chf(construction_chf_m2)}/m²",
            -construction,
        ),
        Step(
            "− Baunebenkosten",
            f"{ancillary_pct:.0f}% der Baukosten",
            -ancillary,
        ),
        Step(
            "− Abbruchkosten",
            (
                f"{chf(existing_gf)} m² Bestand × CHF {chf(demolition_chf_m2)}/m²"
                if demolish and existing_gf
                else "kein Abbruch gerechnet"
            ),
            -demolition,
        ),
        Step(
            "− Finanzierungskosten",
            f"{financing_pct:.1f}% auf Bau-, Nebenkosten und Abbruch",
            -financing,
        ),
        Step(
            "− Gewinn / Risiko",
            f"{profit_pct:.0f}% des Verkaufserlöses",
            -profit,
        ),
        Step(
            "= Residualer Landwert",
            "Erlös abzüglich aller Kosten",
            land,
            kind="result",
        ),
    ]
    return steps


def land_value(steps: list[Step]) -> float:
    """The bottom line of a path built by `residual`."""
    return steps[-1].value


def per_square_metre(steps: list[Step], parcel_area: float) -> Optional[float]:
    """Residual land value per m² of parcel — the figure that compares directly
    with the land-price reference already shown in the result table."""
    if not parcel_area:
        return None
    return land_value(steps) / parcel_area


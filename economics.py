"""
Residual land value for a single parcel — the arithmetic behind Block C.

    Landwert = Verkaufserlös der neuen Flächen
             − Baukosten
             − Baunebenkosten
             − Abbruchkosten des bestehenden Gebäudes
             − Finanzierungskosten
             − Reserve / Unvorhergesehenes

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
import re
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
        # Reckoned on the whole floor area, so the label has to say so: the
        # benchmark is quoted per m² HNF, which is the narrower measure. Left
        # on the published figure rather than adjusted upward by a factor
        # nobody has confirmed — it is one of the numbers still marked for
        # Philipp, and this is the note he needs to see when he names his own.
        "CHF/m² Geschossfläche",
        "AKTIVA AG Neubau-Benchmarks nach Schätzerhandbuch / Wüest Partner: "
        "Eigentum CHF 2'600/m² HNF, Renditeobjekt CHF 2'050/m² HNF (BKP 2)",
        url="https://aktiva.swiss/immobilien-benchmarks/",
        note="ohne Einstellhalle, dort rund CHF 40'000 pro Platz; Quelle je m² HNF, "
             "hier auf die ganze Geschossfläche gerechnet",
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
    "reserve_pct": Benchmark(
        15.0,
        "% der Kosten",
        "Philipp, 18.08.2026: 10–15% auf die Kostenschätzung für Unvorhergesehenes "
        "— langer Winter, Auseinandersetzungen mit Nachbarn und Ähnliches",
        confirmed=True,
        note="im Auftrag als «Gewinnmarge» bezeichnet und dort auf den Erlös bezogen; "
             "gerechnet wird auf die Kosten, wie besprochen",
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
        "Philipp, 18.08.2026: Verkaufspreis auf 80% der Geschossfläche, "
        "Baukosten auf 100%",
        confirmed=True,
        note="wirkt deshalb nur auf den Erlös, nicht auf die Baukosten",
    ),
}

#: A rule of thumb the brief names, not a planning standard.
SQM_PER_UNIT = 90



#: The calculation, written once.
#:
#: Each rule carries the expression that produces its number — and that same
#: expression string is what gets evaluated. The formula shown on hover is
#: therefore not a description of the arithmetic maintained beside it; it is the
#: arithmetic. Change a rule and the tooltip changes with it, because there is
#: nothing else to change.
#:
#: `effect` says how the row enters the total: "+" earns, "−" costs, "" is an
#: intermediate quantity that later rows refer to, "=" is the result.
@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    expr: str
    unit: str = "CHF"
    effect: str = "−"


PATH = (
    Rule("verkaufsflaeche", "Verkaufsfläche",
         "potenzial_gf * verkaufsflaechenanteil / 100", "m²", ""),
    Rule("verkaufserloes", "Verkaufserlös",
         "verkaufsflaeche * verkaufspreis", "CHF", "+"),
    # On the full floor area, not the saleable share: the construction cost is
    # incurred for everything that gets built, while only part of it is sold.
    Rule("baukosten", "Baukosten (BKP 2)",
         "potenzial_gf * baukosten_pro_m2", "CHF", "−"),
    Rule("baunebenkosten", "Baunebenkosten",
         "baukosten * baunebenkosten_prozent / 100", "CHF", "−"),
    Rule("abbruchkosten", "Abbruchkosten",
         "bestand_gf * abbruchkosten_pro_m2 * abbruch", "CHF", "−"),
    Rule("finanzierung", "Finanzierungskosten",
         "(baukosten + baunebenkosten + abbruchkosten) * finanzierung_prozent / 100",
         "CHF", "−"),
    # Not a developer's margin: a contingency on the cost estimate, for the long
    # winter and the neighbour who objects.
    Rule("reserve", "Reserve / Unvorhergesehenes",
         "(baukosten + baunebenkosten + abbruchkosten + finanzierung)"
         " * reserve_prozent / 100", "CHF", "−"),
    Rule("landwert", "Residualer Landwert",
         "verkaufserloes - baukosten - baunebenkosten - abbruchkosten"
         " - finanzierung - reserve", "CHF", "="),
)

#: What each input is called inside the formulas, so the control can say which
#: symbol it feeds rather than leaving the reader to guess.
INPUTS = {
    "potenzial_gf": "Potenzial (m² GF)",
    "verkaufsflaechenanteil": "Verkaufsflächenanteil (%)",
    "verkaufspreis": "Verkaufspreis (CHF/m²)",
    "baukosten_pro_m2": "Baukosten (CHF/m²)",
    "baunebenkosten_prozent": "Baunebenkosten (%)",
    "bestand_gf": "bestehende Geschossfläche (m²)",
    "abbruchkosten_pro_m2": "Abbruchkosten (CHF/m²)",
    "abbruch": "Abbruch ja/nein (1/0)",
    "finanzierung_prozent": "Finanzierung (%)",
    "reserve_prozent": "Reserve (%)",
}

_NAME = re.compile(r"[a-z_][a-z0-9_]*")


@dataclass(frozen=True)
class Step:
    """One line of the calculation path, as shown on screen and in the PDF."""

    label: str
    formula: str      # the expression with the numbers filled in
    value: float
    unit: str = "CHF"
    kind: str = "cost"
    #: The expression as written in `PATH`, symbols and all.
    expr: str = ""


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


def used_in(symbol: str) -> list[str]:
    """Which steps an input actually feeds. Read off the expressions, so a
    control can say where its number goes without anyone maintaining a list."""
    return [rule.label for rule in PATH if symbol in _NAME.findall(rule.expr)]


def substitute(expr: str, values: dict) -> str:
    """The same expression with every symbol replaced by what it stood for."""
    def swap(match):
        name = match.group(0)
        if name not in values:
            return name
        return chf(values[name]) if abs(values[name]) >= 1000 else f"{values[name]:g}"
    return _NAME.sub(swap, expr)


def evaluate(inputs: dict) -> list[Step]:
    """Run `PATH` over the given inputs.

    The expressions are evaluated rather than reimplemented, which is the whole
    point: there is one statement of each formula, and both the number and the
    text that explains it come out of it. `eval` sees no builtins and only the
    values computed so far, so a rule can reach a previous rule and nothing else.
    """
    values = dict(inputs)
    steps = []
    for rule in PATH:
        value = eval(rule.expr, {"__builtins__": {}}, values)  # noqa: S307 — our own literals
        values[rule.key] = value
        kind = {"+": "revenue", "=": "result", "": "area"}.get(rule.effect, "cost")
        steps.append(Step(
            label=("− " if rule.effect == "−" else "= " if rule.effect == "=" else "")
                  + rule.label,
            formula=substitute(rule.expr, values),
            value=-value if rule.effect == "−" else value,
            unit=rule.unit,
            kind=kind,
            expr=rule.expr,
        ))
    return steps


def residual(
    potential_gf: float,
    sale_area_pct: float,
    sale_price_chf_m2: float,
    construction_chf_m2: float,
    ancillary_pct: float,
    existing_gf: float,
    demolition_chf_m2: float,
    financing_pct: float,
    reserve_pct: float,
    demolish: bool = True,
) -> list[Step]:
    """The path from floor-area potential to residual land value.

    Kept as a named signature over `evaluate` so the interface has one obvious
    call, and the formulas stay in `PATH` where the tooltip reads them.

    Two things were settled by Philipp on 18.08.2026 and are visible in `PATH`:
    the sale price is reckoned on 80% of the floor area while the construction
    cost is reckoned on all of it, and the 15% is a contingency on the cost
    estimate rather than a margin on revenue.
    """
    return evaluate({
        "potenzial_gf": potential_gf,
        "verkaufsflaechenanteil": sale_area_pct,
        "verkaufspreis": sale_price_chf_m2,
        "baukosten_pro_m2": construction_chf_m2,
        "baunebenkosten_prozent": ancillary_pct,
        "bestand_gf": existing_gf,
        "abbruchkosten_pro_m2": demolition_chf_m2,
        "abbruch": 1 if demolish else 0,
        "finanzierung_prozent": financing_pct,
        "reserve_prozent": reserve_pct,
    })


def land_value(steps: list[Step]) -> float:
    """The bottom line of a path built by `residual`."""
    return steps[-1].value


def per_square_metre(steps: list[Step], parcel_area: float) -> Optional[float]:
    """Residual land value per m² of parcel — the figure that compares directly
    with the land-price reference already shown in the result table."""
    if not parcel_area:
        return None
    return land_value(steps) / parcel_area

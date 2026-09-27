#!/usr/bin/env python3
"""Obligacje skarbowe, inflacja i stopa NBP — źródło danych aplikacji
(PLAN.md, kroki 9.2 i 9.3, wariant Ź1).

Czyta strony serii z obligacjeskarbowe.pl (adres `kodMMRR`, gdzie MMRR to
miesiąc i rok **wykupu**), plik CSV GUS i archiwum stóp NBP, i zapisuje:

    {"version": 2, "updated": "2026-09-27",
     "bonds": {"EDO": {"2026-09": {"firstYearBp": 535, "marginBp": 200}},
               "COI": {...}, "ROR": {...}, ...},
     "edo": {...},            # to samo co bonds.EDO — dla wersji aplikacji
                              # sprzed 9.3, które czytają tylko ten klucz
     "cpi": {"2026-08": 340, ...},
     "nbp": [{"from": "2026-03-05", "bp": 375}, ...]}

`firstYearBp` to stopa pierwszego okresu (roku albo miesiąca), `marginBp`
marża do inflacji albo stopy NBP; przy TOS (stała stopa) marży nie ma.

Tylko biblioteka standardowa, żeby GitHub Actions nie musiał niczego
instalować. Seria, której strona nie istnieje albo nie ma stopy, jest
pomijana — skrypt nigdy nie zgaduje.
"""

import datetime as dt
import html as html_lib
import json
import re
import sys
import time
import urllib.error
import urllib.request

OFFER = "https://www.obligacjeskarbowe.pl/oferta-obligacji/"

# Kod: (ścieżka na stronie, lata do wykupu, czy potrzebna marża).
BONDS = {
    "ROR": ("obligacje-roczne-ror", 1, True),
    "DOR": ("obligacje-2-letnie-dor", 2, True),
    "TOS": ("obligacje-3-letnie-tos", 3, False),
    "COI": ("obligacje-4-letnie-coi", 4, True),
    "EDO": ("obligacje-10-letnie-edo", 10, True),
    "ROS": ("obligacje-6-letnie-ros", 6, True),
    "ROD": ("obligacje-12-letnie-rod", 12, True),
}

# Dwa brzmienia strony: nowsze „Oprocentowanie: 5,35% w pierwszym…"
# i starsze „W pierwszym rocznym okresie odsetkowym wynosi 1,70%" (wtedy
# po „Oprocentowanie:" nie ma liczby — pierwszą byłaby marża).
FIRST_NEW = re.compile(r"Oprocentowanie: (\d+,\d{2})%")
FIRST_OLD = re.compile(
    r"[Ww] pierwszym (?:rocznym |miesięcznym )?okresie odsetkowym "
    r"(?:oprocentowanie )?wynosi (\d+,\d{2})%"
)
MARGIN = re.compile(r"marża (\d+,\d{2})%|NBP\s*\+\s*(\d+,\d{2})%")
SALE = re.compile(r"Sprzedaż: (\d{2})\.(\d{2})\.(\d{4})")

GUS_PAGE = (
    "https://stat.gov.pl/obszary-tematyczne/ceny-handel/wskazniki-cen/"
    "wskazniki-cen-towarow-i-uslug-konsumpcyjnych-pot-inflacja-/"
    "miesieczne-wskazniki-cen-towarow-i-uslug-konsumpcyjnych-od-1982-roku/"
)
GUS_YOY = "Analogiczny miesiąc poprzedniego roku = 100"
NBP_ARCHIVE = "https://static.nbp.pl/dane/stopy/stopy_procentowe_archiwum.xml"


def bp(text):
    whole, frac = text.split(",")
    return int(whole) * 100 + int(frac)


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def fetch(code, year, month):
    """Warunki serii [code] sprzedawanej w [year]-[month] albo None."""
    path, years, needs_margin = BONDS[code]
    url = f"{OFFER}{path}/{code.lower()}{month:02d}{(year + years) % 100:02d}/"
    try:
        html = get(url).decode("utf-8", "replace")
    except urllib.error.URLError:
        return None
    html = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_lib.unescape(html)))
    first = FIRST_NEW.search(html) or FIRST_OLD.search(html)
    margin = MARGIN.search(html)
    if not first or (needs_margin and not margin):
        return None
    # Strona serii może istnieć dla innego miesiąca sprzedaży niż zakładany
    # — wtedy dane nie należą do tego klucza.
    sale = SALE.search(html)
    if sale and (int(sale.group(3)), int(sale.group(2))) != (year, month):
        return None
    return {
        "firstYearBp": bp(first.group(1)),
        "marginBp": bp(margin.group(1) or margin.group(2)) if margin else 0,
    }


def fetch_cpi(since_year):
    """Inflacja r/r z GUS po miesiącach, w punktach bazowych ponad 100
    (103,4 -> 340). Od drugiego roku EDO płaci marżę plus tę liczbę.

    Link do pliku ma w nazwie numer wersji, więc szukamy go na stronie."""
    page = get(GUS_PAGE).decode("utf-8", "replace")
    link = re.search(r'href="([^"]+\.csv)"', page)
    if not link:
        sys.exit("Brak pliku CSV na stronie GUS — zmienił się układ?")
    rows = get("https://stat.gov.pl" + link.group(1)).decode("cp1250")
    cpi = {}
    for line in rows.splitlines():
        cols = line.split(";")
        if len(cols) < 6 or cols[2] != GUS_YOY or not cols[5]:
            continue
        year, month = int(cols[3]), int(cols[4])
        if year < since_year:
            continue
        whole, _, frac = cols[5].partition(",")
        index = int(whole) * 100 + int((frac + "00")[:2])
        cpi[f"{year}-{month:02d}"] = index - 10000
    return cpi


def fetch_nbp(since_year):
    """Stopa referencyjna NBP od dnia, w punktach bazowych — ROR i DOR
    płacą ją plus marżę."""
    xml = get(NBP_ARCHIVE).decode("utf-8-sig")
    pairs = re.findall(
        r'obowiazuje_od="([\d-]+)">\s*<pozycja\s+id="ref"\s+'
        r'oprocentowanie="([\d,]+)"',
        xml,
    )
    if not pairs:
        sys.exit("Brak stóp w archiwum NBP — zmienił się układ?")
    rates = [{"from": day, "bp": bp(rate)} for day, rate in pairs]
    # Ostatnia zmiana przed okresem też jest potrzebna — obowiązuje dalej.
    earlier = [r for r in rates if r["from"] < f"{since_year}-01-01"]
    return earlier[-1:] + [r for r in rates if r["from"] >= f"{since_year}-01-01"]


def main(out_path, since_year=2016):
    today = dt.date.today()
    # Następny miesiąc bywa ogłoszony pod koniec bieżącego.
    last = (today.year + (today.month // 12), today.month % 12 + 1)
    bonds = {}
    for code in BONDS:
        terms = {}
        year, month = since_year, 1
        while (year, month) <= last:
            rate = fetch(code, year, month)
            if rate:
                terms[f"{year}-{month:02d}"] = rate
            time.sleep(0.2)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        bonds[code] = terms
    if not bonds["EDO"]:
        sys.exit("Brak danych — strona zmieniła układ?")
    cpi = fetch_cpi(since_year - 1)
    nbp = fetch_nbp(since_year)
    # Te same dane zostawiają starą datę — aplikacja porównuje `updated`
    # i bez tego pobierałaby codziennie ten sam plik, a repo dostawałoby
    # codziennie pusty commit.
    updated = today.isoformat()
    try:
        with open(out_path, encoding="utf-8") as previous:
            old = json.load(previous)
        if (old.get("bonds"), old.get("cpi"), old.get("nbp")) == (bonds, cpi, nbp):
            updated = old["updated"]
    except (OSError, ValueError, KeyError):
        pass
    with open(out_path, "w", encoding="utf-8") as out:
        json.dump(
            {
                "version": 2,
                "updated": updated,
                "bonds": bonds,
                "edo": bonds["EDO"],
                "cpi": cpi,
                "nbp": nbp,
            },
            out,
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
    counts = ", ".join(f"{code} {len(terms)}" for code, terms in bonds.items())
    print(f"{counts}; CPI {len(cpi)}, NBP {len(nbp)} -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "savings_data.json")

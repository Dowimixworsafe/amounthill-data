#!/usr/bin/env python3
"""Stopy obligacji EDO po miesiącach sprzedaży — źródło danych aplikacji
(PLAN.md, krok 9.2, wariant Ź1).

Czyta strony serii z obligacjeskarbowe.pl (adres `edoMMRR`, gdzie MMRR to
miesiąc i rok **wykupu**, czyli sprzedaż + 10 lat) i zapisuje JSON:

    {"version": 1, "updated": "2026-09-27",
     "edo": {"2026-09": {"firstYearBp": 535, "marginBp": 200}, ...},
     "cpi": {"2026-08": 340, ...}}

Inflacja (r/r, w punktach bazowych) przychodzi z pliku CSV GUS.

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

BASE = "https://www.obligacjeskarbowe.pl/oferta-obligacji/obligacje-10-letnie-edo/edo{:02d}{:02d}/"
# Dwa brzmienia strony: nowsze „6,00% w pierwszym rocznym okresie…"
# i starsze „W pierwszym rocznym okresie odsetkowym wynosi 1,70%".
FIRST = re.compile(
    r"(\d+,\d{2})%\s*w pierwszym rocznym okresie odsetkowym"
    r"|W pierwszym rocznym okresie odsetkowym wynosi (\d+,\d{2})%"
)
MARGIN = re.compile(r"(\d+,\d{2})%\s*\+\s*inflacja")
SALE = re.compile(r"Sprzedaż: (\d{2})\.(\d{2})\.(\d{4})")

GUS_PAGE = (
    "https://stat.gov.pl/obszary-tematyczne/ceny-handel/wskazniki-cen/"
    "wskazniki-cen-towarow-i-uslug-konsumpcyjnych-pot-inflacja-/"
    "miesieczne-wskazniki-cen-towarow-i-uslug-konsumpcyjnych-od-1982-roku/"
)
GUS_YOY = "Analogiczny miesiąc poprzedniego roku = 100"


def bp(text):
    whole, frac = text.split(",")
    return int(whole) * 100 + int(frac)


def fetch(year, month):
    """Stopa serii sprzedawanej w [year]-[month] albo None."""
    maturity = (year + 10) % 100
    url = BASE.format(month, maturity)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", "replace")
    except urllib.error.URLError:
        return None
    html = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_lib.unescape(html)))
    first, margin = FIRST.search(html), MARGIN.search(html)
    if not first or not margin:
        return None
    # Strona serii może istnieć dla innego miesiąca sprzedaży niż zakładany
    # — wtedy dane nie należą do tego klucza.
    sale = SALE.search(html)
    if sale and (int(sale.group(3)), int(sale.group(2))) != (year, month):
        return None
    return {
        "firstYearBp": bp(first.group(1) or first.group(2)),
        "marginBp": bp(margin.group(1)),
    }


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


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


def main(out_path, since_year=2016):
    today = dt.date.today()
    # Następny miesiąc bywa ogłoszony pod koniec bieżącego.
    last = (today.year + (today.month // 12), today.month % 12 + 1)
    edo = {}
    year, month = since_year, 1
    while (year, month) <= last:
        rate = fetch(year, month)
        if rate:
            edo[f"{year}-{month:02d}"] = rate
        time.sleep(0.3)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if not edo:
        sys.exit("Brak danych — strona zmieniła układ?")
    cpi = fetch_cpi(since_year - 1)
    # Te same dane zostawiają starą datę — aplikacja porównuje `updated`
    # i bez tego pobierałaby codziennie ten sam plik, a repo dostawałoby
    # codziennie pusty commit.
    updated = today.isoformat()
    try:
        with open(out_path, encoding="utf-8") as previous:
            old = json.load(previous)
        if old.get("edo") == edo and old.get("cpi") == cpi:
            updated = old["updated"]
    except (OSError, ValueError, KeyError):
        pass
    with open(out_path, "w", encoding="utf-8") as out:
        json.dump(
            {
                "version": 1,
                "updated": updated,
                "edo": edo,
                "cpi": cpi,
            },
            out,
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
    print(f"{len(edo)} miesięcy EDO, {len(cpi)} miesięcy CPI -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "savings_data.json")

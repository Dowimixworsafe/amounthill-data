#!/usr/bin/env python3
"""Kursy ETF-ów i walut — źródło danych skarbonek z ETF-em (PLAN.md,
krok 9.4, warianty ETF-E1, L2 i Z1).

Czyta listę instrumentów z `etf_list.txt`, kursy zamknięcia z Yahoo
Finance (`v8/finance/chart`, bez klucza) i kursy średnie walut z tabeli A
NBP, i zapisuje:

    {"version": 1, "updated": "2026-10-04", "historyDays": 400,
     "instruments": {
       "VWCE.DE": {"name": "Vanguard FTSE All-World (akumulujący)",
                   "group": "swiat", "currency": "EUR", "exchange": "XETRA",
                   "closes": {"2026-10-02": 1711400, ...}}, ...},
     "fx": {"EUR": {"2026-10-02": 43745, ...}, "USD": {...}, ...}}

Kursy to liczby całkowite w **dziesięciotysięcznych** jednostki waluty
(171,14 EUR = 1711400; IS04.DE kosztuje 2,5802 EUR, więc dwa miejsca po
przecinku nie wystarczą), kursy NBP tak samo (4,3745 zł = 43745) — jak
punkty bazowe przy obligacjach: bez ułamków w JSON-ie. Notowania w pensach
(`GBp`) przeliczamy na funty, więc w pliku waluta to zawsze kod ISO.

Instrument, którego Yahoo akurat nie oddało, **zostaje z poprzedniego
pliku** — jedna zła odpowiedź nie kasuje mu kursów. Skrypt kończy się
błędem (mail z GitHuba), dopiero gdy nie wyszła ponad połowa listy albo
NBP — wtedy aplikacje dalej liczą na ostatnim dobrym pliku.

Tylko biblioteka standardowa, żeby GitHub Actions nie musiał niczego
instalować.
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
NBP = "https://api.nbp.pl/api/exchangerates/rates/a/"

# Ile dni wstecz trzymamy. Starczy na podpowiedź liczby jednostek przy
# zakupie sprzed roku; starszy zakup wpisuje się z potwierdzenia brokera.
HISTORY_DAYS = 400

# NBP oddaje najwyżej 93 dni na zapytanie.
NBP_CHUNK_DAYS = 93

LIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_list.txt")


def get_json(url, attempts=3):
    """JSON spod [url] albo None przy 404; inne błędy próbuje jeszcze raz."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
        time.sleep(2 * (attempt + 1))
    return None


def read_list(path=LIST_PATH):
    """[(ticker, nazwa, grupa)] z pliku listy; # i puste linijki pomija."""
    items = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ticker, name, group = (part.strip() for part in line.split("|"))
            items.append((ticker, name, group))
    return items


def units4(value):
    """Kwota w dziesięciotysięcznych jednostki: 171.14 → 1711400."""
    return int(round(value * 10000))


def parse_chart(data):
    """(waluta, giełda, {data: kurs}) z odpowiedzi `chart` albo None.

    Dzień notowania liczymy w strefie giełdy (`gmtoffset`), nie w UTC —
    sesja w Tokio albo Nowym Jorku inaczej wypadłaby o dzień obok. Dni bez
    kursu (`null` przy zawieszeniu notowań) pomijamy.
    """
    result = (data or {}).get("chart", {}).get("result") or []
    if not result:
        return None
    result = result[0]
    meta = result["meta"]
    currency = meta.get("currency")
    divisor = 1
    if currency == "GBp":  # pensy — SSAC.L 9325,0 to 93,25 GBP
        currency, divisor = "GBP", 100
    offset = dt.timedelta(seconds=meta.get("gmtoffset", 0))
    stamps = result.get("timestamp") or []
    quotes = result.get("indicators", {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []
    days = {}
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        day = (dt.datetime.fromtimestamp(stamp, dt.timezone.utc) + offset).date()
        days[day.isoformat()] = units4(close / divisor)
    if not currency or not days:
        return None
    return currency, meta.get("fullExchangeName") or meta.get("exchangeName"), days


def fetch_instrument(ticker, since, until):
    """Kursy dzienne [ticker] od [since] do [until] (daty)."""
    start = int(dt.datetime.combine(since, dt.time(), dt.timezone.utc).timestamp())
    end = int(dt.datetime.combine(until + dt.timedelta(days=1), dt.time(), dt.timezone.utc).timestamp())
    # Zakres dat, nie `range=max` — przy długim zakresie Yahoo oddaje
    # dane miesięczne zamiast dziennych.
    url = f"{YAHOO}{ticker}?period1={start}&period2={end}&interval=1d"
    return parse_chart(get_json(url))


def fetch_fx(code, since, until):
    """{data: kurs średni} waluty [code] z tabeli A NBP."""
    days = {}
    start = since
    while start <= until:
        end = min(start + dt.timedelta(days=NBP_CHUNK_DAYS - 1), until)
        data = get_json(f"{NBP}{code.lower()}/{start}/{end}/?format=json")
        # 404 = w tym okienku nie było ani jednej tabeli (np. same święta).
        for rate in (data or {}).get("rates", []):
            days[rate["effectiveDate"]] = units4(rate["mid"])
        start = end + dt.timedelta(days=1)
    return days


def load_previous(path):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def main(out_path, today=None):
    today = today or dt.date.today()
    since = today - dt.timedelta(days=HISTORY_DAYS)
    previous = load_previous(out_path).get("instruments", {})

    instruments, failed = {}, []
    for ticker, name, group in read_list():
        try:
            fetched = fetch_instrument(ticker, since, today)
        except Exception as error:  # noqa: BLE001 — jeden ticker nie przerywa reszty
            print(f"{ticker}: {error}", file=sys.stderr)
            fetched = None
        if fetched is None:
            failed.append(ticker)
            if ticker in previous:
                instruments[ticker] = {**previous[ticker], "name": name, "group": group}
            continue
        currency, exchange, closes = fetched
        instruments[ticker] = {
            "name": name,
            "group": group,
            "currency": currency,
            "exchange": exchange,
            "closes": closes,
        }
        time.sleep(0.5)  # grzecznie wobec nieoficjalnego API

    if failed:
        print(f"Bez nowych kursów: {', '.join(failed)}", file=sys.stderr)
    if len(failed) * 2 > len(instruments) + len(failed):
        sys.exit("Brak kursów dla ponad połowy listy — Yahoo zmieniło API?")

    currencies = sorted({item["currency"] for item in instruments.values()} - {"PLN"})
    fx = {code: fetch_fx(code, since, today) for code in currencies}
    empty = [code for code, days in fx.items() if not days]
    if empty:
        sys.exit(f"Brak kursów NBP dla {', '.join(empty)}")

    # `updated` zmienia się tylko razem z danymi, żeby workflow nie
    # commitował pliku, w którym nowa jest sama data.
    old = load_previous(out_path)
    data = {
        "version": 1,
        "updated": old.get("updated", today.isoformat()),
        "historyDays": HISTORY_DAYS,
        "instruments": instruments,
        "fx": fx,
    }
    if {k: v for k, v in data.items() if k != "updated"} != {
        k: v for k, v in old.items() if k != "updated"
    }:
        data["updated"] = today.isoformat()

    with open(out_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        file.write("\n")
    closes = sum(len(item["closes"]) for item in instruments.values())
    print(f"{len(instruments)} instrumentów, {closes} kursów, waluty: {', '.join(currencies)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "etf_data.json")

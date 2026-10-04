# amounthill-data

Dane rynkowe dla aplikacji Amounthill: warunki obligacji skarbowych (ROR, DOR, TOS, COI, EDO, ROS, ROD) z obligacjeskarbowe.pl, inflacja r/r z GUS i stopa referencyjna NBP. Plik `savings_data.json` aktualizuje codziennie GitHub Actions (`fetch_bonds.py`).

Kursy ETF-ów: dzienne kursy zamknięcia instrumentów z `etf_list.txt` z Yahoo Finance (ostatnie 400 dni) i kursy średnie walut z tabeli A NBP. Plik `etf_data.json` aktualizuje GitHub Actions od poniedziałku do piątku wieczorem (`fetch_etf.py`, workflow „Kursy ETF").

Umiesc tutaj szablony:
1) skills.png — naglowek panelu Skills (jak na screenie), w naturalnych kolorach.
2) Znaki do odczytu wartosci (czarny znak na bialym tle, przyciete ciasno):
   0.png 1.png 2.png 3.png 4.png 5.png 6.png 7.png 8.png 9.png
   comma.png percent.png lparen.png rparen.png

Skrypt:
- szuka skills.png na zrzucie okna, wyznacza polozenie panelu i na tej podstawie
  oblicza offsety dla wierszy Experience i Level.
- do odczytu wartosci uzywa najpierw szablonow znakow (naturalny rozmiar),
  potem fallback OCR.

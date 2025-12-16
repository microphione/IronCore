"""
Konfiguracja bota.

Ustaw tutaj:
- MANUAL_REGION: absolutne wspolrzedne panelu Skills (left, top, width, height) albo None,
- MANUAL_EXPERIENCE_OFFSET / MANUAL_LEVEL_OFFSET: offsety etykiet wzgledem lewego gornego rogu panelu,
  w formacie (dx, dy, width, height). Używane do wyznaczenia pozycji Experience/Level po znalezieniu naglowka skills.png.
"""

# Przykład (odkomentuj i uzupełnij):
MANUAL_REGION = (100, 100, 220, 200)
MANUAL_EXPERIENCE_OFFSET = (10, 20, 100, 4 )
MANUAL_LEVEL_OFFSET = (10, 40, 70, 4)

MANUAL_REGION = None
MANUAL_EXPERIENCE_OFFSET = None
MANUAL_LEVEL_OFFSET = None

# Czy zapisywać wycięte fragmenty Experience/Level (debug)?
SAVE_DEBUG_CROPS = False
# Ścieżki do zapisów (relative do katalogu projektu)
DEBUG_EXP_PATH = "debug_experience.png"
DEBUG_LEVEL_PATH = "debug_level.png"

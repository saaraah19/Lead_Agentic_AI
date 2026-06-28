# test_booking.py
from dotenv import load_dotenv
load_dotenv()  # <-- DOIT être en premier

from booking import get_available_slots, confirm_booking

# Test de récupération des créneaux
print("🔍 Récupération des créneaux disponibles...")
slots = get_available_slots()
print("Créneaux disponibles :")
for s in slots:
    print(f"  {s['index']}: {s['display']}")


# Tester une réservation (simulation)
result = confirm_booking(
    slot_index=1,
    lead_email="test@example.com",
    lead_name="Test User",
    lead_need="Découverte",
    language="fr"
)
print(result["message"])
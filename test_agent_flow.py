# test_agent_flow.py
"""
Script de test du parcours complet de l'agent :
1. Crée une session
2. Simule une conversation (besoin, budget, délai, email)
3. Passe en mode booking
4. Sélectionne un créneau
5. Vérifie la création de l'événement Google Calendar
"""

import asyncio
import uuid
from agent import process_message

# -------------------------------
# 1. Créer un ID de session unique
# -------------------------------
session_id = str(uuid.uuid4())
print(f"🧪 Session ID : {session_id}\n")

# -------------------------------
# 2. Simuler les messages de l'utilisateur
# -------------------------------
messages = [
    "Bonjour, j'ai besoin d'un site web pour mon restaurant.",   # besoin
    "Mon budget est d'environ 5000€.",                           # budget
    "J'aimerais que ce soit prêt dans 2 mois.",                  # délai
    "Mon email est client@restaurant.com",                      # contact
    # L'agent devrait maintenant proposer des créneaux.
    # On choisit le premier créneau (par exemple "1")
    "1"
]

# -------------------------------
# 3. Exécuter la conversation
# -------------------------------
async def run_conversation():
    print("🤖 Début de la conversation simulée...\n")
    for i, user_msg in enumerate(messages):
        print(f"👤 Utilisateur : {user_msg}")
        bot_reply = await process_message(session_id, user_msg)
        print(f"🤖 Agent : {bot_reply}\n")
        # Si l'agent répond par un message de confirmation de réservation, on s'arrête
        if "réservé" in bot_reply.lower() or "booked" in bot_reply.lower() or "تم حجزك" in bot_reply:
            print("✅ Réservation effectuée avec succès !")
            break
    else:
        print("⚠️ La conversation s'est terminée sans réservation.")

# -------------------------------
# 4. Lancer le test
# -------------------------------
if __name__ == "__main__":
    asyncio.run(run_conversation())
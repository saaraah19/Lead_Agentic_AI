#UN FICHIER TEST DE CONNEXION AVEC GOOGLE CALENDAR

import os
from dotenv import load_dotenv
load_dotenv()

from google.oauth2 import service_account
from googleapiclient.discovery import build
import json

# 1. Lire les variables d'environnement
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")

# 2. Charger les credentials
creds_dict = json.loads(creds_json)
creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

# 3. Créer le service Calendar
service = build("calendar", "v3", credentials=creds)

# 4. Tester l'accès en listant les 10 prochains événements
print("🔍 Test de connexion au calendrier...")
try:
    events = service.events().list(
        calendarId=CALENDAR_ID,
        maxResults=10,
        orderBy="startTime",
        singleEvents=True
    ).execute()

    items = events.get("items", [])
    if not items:
        print("✅ Connexion réussie, mais aucun événement à venir.")
    else:
        print(f"✅ Connexion réussie ! {len(items)} événements trouvés :")
        for event in items:
            start = event["start"].get("dateTime", event["start"].get("date"))
            print(f"   - {start} : {event.get('summary', 'Sans titre')}")

except Exception as e:
    print(f"❌ Erreur : {e}")
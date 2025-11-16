import json
import google.auth
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import requests
import os

# Path to your service account file
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# FCM v1 endpoint
FCM_ENDPOINT = (
    "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
)


def get_access_token():
    """
    Generates an OAuth2 access token using the service account key.
    """
    scopes = ["https://www.googleapis.com/auth/firebase.messaging"]
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=scopes
    )
    credentials.refresh(Request())
    return credentials.token


def send_push_v1(project_id: str, token: str, title: str, body: str):
    """
    Sends a push notification using FCM HTTP v1 API.
    """

    access_token = get_access_token()

    url = FCM_ENDPOINT.format(project_id=project_id)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
    }

    payload = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "webpush": {
                "fcm_options": {"link": "https://airnav-compound.work.gd/"}
            },
        }
    }

    response = requests.post(url, headers=headers, json=payload)
    return response.status_code, response.text

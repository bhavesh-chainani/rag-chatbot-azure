import asyncio
import os

from azure.identity.aio import AzureDeveloperCliCredential
from msgraph import GraphServiceClient
from msgraph.generated.models.application import Application
from msgraph.generated.models.public_client_application import PublicClientApplication
from msgraph.generated.models.spa_application import SpaApplication
from msgraph.generated.models.web_application import WebApplication

from auth_common import get_application, test_authentication_enabled
from load_azd_env import load_azd_env


async def main():
    load_azd_env()
    if not test_authentication_enabled():
        print("Not updating authentication.")
        exit(0)

    auth_tenant = (os.getenv("AZURE_AUTH_TENANT_ID") or os.getenv("AZURE_TENANT_ID") or "").strip()
    credential = AzureDeveloperCliCredential(tenant_id=auth_tenant)

    scopes = ["https://graph.microsoft.com/.default"]
    graph_client = GraphServiceClient(credentials=credential, scopes=scopes)

    uri = os.getenv("BACKEND_URI")
    client_app_id = os.getenv("AZURE_CLIENT_APP_ID", None)
    if client_app_id:
        client_object_id = await get_application(graph_client, client_app_id)
        if client_object_id:
            print(f"Updating redirect URIs for client app ID {client_app_id}...")
            existing_app = await graph_client.applications.by_application_id(client_object_id).get()

            existing_spa_redirects = []
            if existing_app and existing_app.spa and existing_app.spa.redirect_uris:
                existing_spa_redirects = existing_app.spa.redirect_uris

            existing_web_redirects = []
            if existing_app and existing_app.web and existing_app.web.redirect_uris:
                existing_web_redirects = existing_app.web.redirect_uris

            # Redirect URIs need to be relative to the deployed application.
            # Preserve manually-added values (e.g. custom domains) to avoid
            # deleting them on each `azd up`.
            default_spa_redirects = [
                "http://localhost:50505/redirect",
                "http://localhost:5173/redirect",
                f"{uri}/redirect",
            ]
            default_web_redirects = [
                f"{uri}/.auth/login/aad/callback",
            ]

            merged_spa_redirects = sorted(set(default_spa_redirects + existing_spa_redirects))
            merged_web_redirects = sorted(set(default_web_redirects + existing_web_redirects))

            app = Application(
                public_client=PublicClientApplication(redirect_uris=[]),
                spa=SpaApplication(redirect_uris=merged_spa_redirects),
                web=WebApplication(redirect_uris=merged_web_redirects),
            )
            await graph_client.applications.by_application_id(client_object_id).patch(app)
            print(f"Application update for client app id {client_app_id} complete.")


if __name__ == "__main__":
    asyncio.run(main())

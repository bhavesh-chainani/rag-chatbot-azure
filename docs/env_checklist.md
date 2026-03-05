# Environment variables checklist (after `azd down` / before `azd up`)

Use this list to set **azd** environment variables for your setup: **OpenAI (not Azure OpenAI)** + **user upload** + **auth** + **Cosmos chat history**.

Set secrets with `azd env set <NAME> <value>` so they are not stored in `.env` in plain text. Non-secrets can go in `.env` or `azd env set`.

---

## You must set (required for your setup)

### OpenAI (public API)

| Variable | Required | Notes |
|----------|----------|--------|
| `OPENAI_HOST` | Yes | Set to `openai` for public OpenAI API. |
| `OPENAI_API_KEY` | Yes | Your OpenAI API key (secret — use `azd env set`). |
| `OPENAI_ORGANIZATION` | Yes | Your OpenAI org ID (e.g. `org-...`). |
| `AZURE_OPENAI_CHATGPT_MODEL` | Yes | Model name, e.g. `gpt-4o-mini`, `gpt-4.1-mini`, `gpt-5-mini`. |

Leave **empty or unset** when using public OpenAI: `AZURE_OPENAI_SERVICE`, `AZURE_OPENAI_CHATGPT_DEPLOYMENT`, `AZURE_OPENAI_EMB_DEPLOYMENT`, `AZURE_OPENAI_EMB_MODEL_NAME`, `AZURE_OPENAI_EMB_DIMENSIONS` (backend uses defaults for embeddings when on `OPENAI_HOST=openai` if needed).

### User upload

| Variable | Required | Notes |
|----------|----------|--------|
| `USE_USER_UPLOAD` | Yes | Set to `true`. |
| `AZURE_USERSTORAGE_ACCOUNT` | Yes | ADLS Gen2 storage account for user uploads (e.g. `userstrgwz4n7fu7xdu`). |
| `AZURE_USERSTORAGE_CONTAINER` | Yes | Container name (e.g. `user-content`). |
| `AZURE_ENFORCE_ACCESS_CONTROL` | Yes | Must be `true` when user upload is enabled. |

### Auth (login)

| Variable | Required | Notes |
|----------|----------|--------|
| `AZURE_USE_AUTHENTICATION` | Yes | Set to `true`. |
| `AZURE_TENANT_ID` | Yes | Azure AD tenant ID. |
| `AZURE_CLIENT_APP_ID` | Yes | App registration (client) application ID. |
| `AZURE_CLIENT_APP_SECRET` | Yes | Client app secret (secret — use `azd env set`). |
| `AZURE_SERVER_APP_ID` | Yes | App registration (server/backend) application ID. |
| `AZURE_SERVER_APP_SECRET` | Yes | Server app secret (secret — use `azd env set`). |
| `AZURE_AUTH_TENANT_ID` | Optional | Same as `AZURE_TENANT_ID` if not using a different tenant for auth. |

### Cosmos DB chat history

| Variable | Required | Notes |
|----------|----------|--------|
| `USE_CHAT_HISTORY_COSMOS` | Yes | Set to `true`. |
| `AZURE_COSMOSDB_ACCOUNT` | Yes | Cosmos account name (e.g. `cosmos-rgwz4n7fu7xdu`). |
| `AZURE_CHAT_HISTORY_DATABASE` | Yes | Database name (e.g. `chat-database`). |
| `AZURE_CHAT_HISTORY_CONTAINER` | Yes | Container name (e.g. `chat-history-v2`). |
| `AZURE_CHAT_HISTORY_VERSION` | Optional | e.g. `cosmosdb-v2` if your app expects it. |

### Azure resources (search + storage — often set by Bicep from other vars)

| Variable | Required | Notes |
|----------|----------|--------|
| `AZURE_SEARCH_SERVICE` | Yes | Search service name (e.g. `gptkb-rgwz4n7fu7xdu`). |
| `AZURE_SEARCH_INDEX` | Yes | Index name (e.g. `gptkbindex`). |
| `AZURE_STORAGE_ACCOUNT` | Yes | Storage for app/content (e.g. `strgwz4n7fu7xdu`). |
| `AZURE_STORAGE_CONTAINER` | Yes | Content container (e.g. `content`). |
| `AZURE_RESOURCE_GROUP` | Yes | Resource group (e.g. `rg-rag-chatbot-final`). |
| `AZURE_LOCATION` | Yes | e.g. `eastus`. |
| `AZURE_ENV_NAME` | Yes | e.g. `rag-chatbot-final`. |

---

## Optional (you can leave unset or default)

- **Speech**: `AZURE_SPEECH_SERVICE_ID`, `AZURE_SPEECH_SERVICE_LOCATION` — only if you enable Azure speech output.
- **Document Intelligence**: `AZURE_DOCUMENTINTELLIGENCE_SERVICE`, `AZURE_DOCUMENTINTELLIGENCE_RESOURCE_GROUP` — only for ingestion/parsing.
- **Embeddings (OpenAI host)**: Backend can use defaults; if you set `AZURE_OPENAI_EMB_MODEL_NAME` / `AZURE_OPENAI_EMB_DIMENSIONS`, they’re used when applicable.
- **Cloud ingestion / skills**: `DOCUMENT_EXTRACTOR_SKILL_*`, `FIGURE_PROCESSOR_SKILL_*`, `TEXT_PROCESSOR_SKILL_*` — only for cloud ingestion.
- **Other**: `AZURE_IMAGESTORAGE_CONTAINER`, `AZURE_VISION_ENDPOINT`, `AZURE_CONTENTUNDERSTANDING_ENDPOINT`, `AZURE_OPENAI_*` deployment/sku/version vars when using public OpenAI.

---

## What you don’t need in `.env` (set by azd/infra or after deploy)

- `BACKEND_URI` — set after deploy or by frontend config.
- `SERVICE_BACKEND_IMAGE_NAME`, `SERVICE_BACKEND_RESOURCE_EXISTS` — set by deployment.
- `AZURE_SUBSCRIPTION_ID` — from `az login` / azd.
- `OPENAI_AI_KEY` — duplicate of `OPENAI_API_KEY`; use `OPENAI_API_KEY` only.
- Resource IDs like `AZURE_SEARCH_USER_ASSIGNED_IDENTITY_RESOURCE_ID` — from Bicep if using managed identity.

---

## Security

- **Do not commit** `.env` or any file containing `OPENAI_API_KEY`, `AZURE_CLIENT_APP_SECRET`, or `AZURE_SERVER_APP_SECRET`. Add `.env` to `.gitignore` if needed.
- Prefer `azd env set OPENAI_API_KEY "sk-..."` (and same for other secrets) so values live in azd’s env store instead of `.env`.

---

## Quick “minimal” set for your setup

After `azd down`, ensure at least these are set (values are examples):

```bash
# OpenAI (public API)
azd env set OPENAI_HOST openai
azd env set OPENAI_API_KEY "sk-..."      # secret
azd env set OPENAI_ORGANIZATION "org-..."
azd env set AZURE_OPENAI_CHATGPT_MODEL gpt-5-mini

# User upload
azd env set USE_USER_UPLOAD true
azd env set AZURE_USERSTORAGE_ACCOUNT userstrgwz4n7fu7xdu
azd env set AZURE_USERSTORAGE_CONTAINER user-content
azd env set AZURE_ENFORCE_ACCESS_CONTROL true

# Auth
azd env set AZURE_USE_AUTHENTICATION true
azd env set AZURE_TENANT_ID "<tenant-id>"
azd env set AZURE_CLIENT_APP_ID "<client-app-id>"
azd env set AZURE_CLIENT_APP_SECRET "..."   # secret
azd env set AZURE_SERVER_APP_ID "<server-app-id>"
azd env set AZURE_SERVER_APP_SECRET "..."   # secret

# Cosmos chat history
azd env set USE_CHAT_HISTORY_COSMOS true
azd env set AZURE_COSMOSDB_ACCOUNT cosmos-rgwz4n7fu7xdu
azd env set AZURE_CHAT_HISTORY_DATABASE chat-database
azd env set AZURE_CHAT_HISTORY_CONTAINER chat-history-v2
```

All other Azure resource names (search, storage, resource group, location, env name) must match what you’ll provision with `azd up` — either set them explicitly or ensure `main.parameters.json` / Bicep get them from your existing env (e.g. `AZURE_SEARCH_SERVICE`, `AZURE_STORAGE_ACCOUNT`, `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `AZURE_ENV_NAME`).

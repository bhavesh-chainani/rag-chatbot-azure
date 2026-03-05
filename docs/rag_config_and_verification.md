# RAG configuration and verification

This doc explains how the app connects to the RAG database (Azure AI Search) and how to confirm the chatbot is reading from it.

---

## Required .env variables for RAG

The backend loads environment variables from your **default azd env** file (e.g. `.azure/<env-name>/.env`) when you run the app locally (see `app/backend/main.py` and `load_azd_env.py`). For the model to read from your RAG database, these must be set:

| Variable | Purpose | Your current value |
|----------|---------|--------------------|
| `AZURE_SEARCH_SERVICE` | Azure AI Search service name (no URL) | e.g. `gptkb-rgwz4n7fu7xdu` |
| `AZURE_SEARCH_INDEX` | Index name where documents are stored | e.g. `gptkbindex` |
| `AZURE_SEARCH_FIELD_NAME_EMBEDDING` | Name of the vector field in the index | e.g. `embedding3` (must match your index schema) |
| `AZURE_STORAGE_ACCOUNT` | Storage account for content (used for citations) | e.g. `strgwz4n7fu7xdu` |
| `AZURE_STORAGE_CONTAINER` | Container that holds the source files | e.g. `content` |

Optional but important if you use vector search:

| Variable | Purpose | Notes |
|----------|---------|--------|
| `AZURE_OPENAI_EMB_MODEL_NAME` | Embedding model name | Must match how the index was built (e.g. `text-embedding-3-large`) |
| `AZURE_OPENAI_EMB_DIMENSIONS` | Embedding dimensions | Must match index (e.g. `3072` for text-embedding-3-large) |

When using **public OpenAI** (`OPENAI_HOST=openai`), the backend still needs an embedding model for query vectorization; it uses the same model/dimensions from env. The index must have been built with the **same** embedding model and dimensions, and the field name must match `AZURE_SEARCH_FIELD_NAME_EMBEDDING`.

Optional overrides for index field names (defaults are fine for standard prepdocs index):

| Variable | Default | Purpose |
|----------|---------|---------|
| `KB_FIELDS_CONTENT` | `content` | Field in the index that holds the document text |
| `KB_FIELDS_SOURCEPAGE` | `sourcepage` | Field that holds the source page/file reference |

---

## No API key for Search

The app uses **Azure credential** (not an API key) to talk to Azure AI Search:

- **Locally**: `AzureDeveloperCliCredential` — you must be logged in with `az login` or `azd auth login` so the backend can access the search service.
- **On Azure**: Managed identity is used.

So you do **not** need `AZURE_SEARCH_KEY` or similar in `.env`. Ensure your Azure user/identity has **Search Index Data Reader** (or equivalent) on the search service.

---

## How to verify the model is reading from the RAG database

### 1. Check “Supporting content” in the chat UI

After you ask a question:

1. Open the **analysis panel** (e.g. click the chart/thought-process icon next to the answer).
2. Open the **“Supporting Content”** tab.

- If you see **one or more sources** (snippets from documents with filenames), the app **is** querying the index and passing results to the model. The model should be using that context (if the prompt is source-grounded).
- If **Supporting content is empty**, then either:
  - The search returned **no results** (index empty, wrong index, or query/embedding mismatch), or
  - There was an error calling Search (check backend logs).

### 2. Check backend logs

When you send a chat message, the backend:

1. Calls Azure AI Search (with the query or a rewritten query).
2. Sends the retrieved “sources” to the LLM.

Look for:

- Errors mentioning `SearchClient`, `search`, or `Azure AI Search` — indicates a connection or permission problem.
- Logs that show how many results were returned (if your app logs that).

### 3. Confirm the index has your data

The app can only “read” what’s in the index. If the index is empty or was built from other data (e.g. sample docs), you won’t see your employment law content.

- **Local ingestion**: Run `scripts/prepdocs.sh` (or `prepdocs.ps1`) so that files in the `data/` folder (including your `employment_law_golden_set.json` or exported content) are uploaded to blob storage and indexed into `AZURE_SEARCH_INDEX`. Use the same `.env` (or same azd env) so `AZURE_SEARCH_SERVICE`, `AZURE_SEARCH_INDEX`, and `AZURE_SEARCH_FIELD_NAME_EMBEDDING` match.
- **Cloud ingestion**: Ensure the indexer has run successfully and that the container referenced by `AZURE_STORAGE_CONTAINER` (and any data source used by the indexer) contains the employment law data.

You can also check in the Azure portal: **Azure AI Search** → your service → **Indexes** → open `AZURE_SEARCH_INDEX` → **Search explorer** and run a simple query to see if documents (and the expected text) are present.

---

## Checklist

- [ ] `.env` (or default azd env) has `AZURE_SEARCH_SERVICE` and `AZURE_SEARCH_INDEX` set correctly.
- [ ] `AZURE_SEARCH_FIELD_NAME_EMBEDDING` matches the vector field name in the index (e.g. `embedding3`).
- [ ] Embedding dimensions and model match how the index was built (e.g. 3072 for `text-embedding-3-large`).
- [ ] Logged in with `az login` or `azd auth login` when running the backend locally.
- [ ] Index actually contains your RAG data (run prepdocs or confirm cloud indexer + blob content).
- [ ] After a query, “Supporting content” in the UI shows at least one source when you expect a match.

If all of the above are true and the model still says it has “no sources,” the problem is likely in the **prompt** (model ignoring sources) or **retrieval quality** (query not matching the right chunks). If “Supporting content” is empty, fix **connection, index name, embedding config, or index population** first.

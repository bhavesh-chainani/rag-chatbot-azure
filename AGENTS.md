# Instructions for Coding Agents

This file contains instructions for developers working on the Azure Search and OpenAI demo application. It covers the overall code layout, how to add new data, how to add new azd environment variables, how to add new developer settings, and how to add tests for new features.

Always keep this file up to date with any changes to the codebase or development process.
If necessary, edit this file to ensure it accurately reflects the current state of the project.

## Overall code layout

* app: Contains the main application code, including frontend and backend.
  * app/backend: Contains the Python backend code, written with Quart framework.
    * app/backend/approaches: Contains the different approaches
      * app/backend/approaches/approach.py: Base class for all approaches
      * app/backend/approaches/chatreadretrieveread.py: Chat approach, includes query rewriting step first
      * app/backend/approaches/promptmanager.py: Manages loading and rendering of Jinja2 prompt templates
      * app/backend/approaches/prompts/query_rewrite.system.jinja2: Jinja2 template used to rewrite the query based off search history into a better search query
      * app/backend/approaches/prompts/chat_query_rewrite_tools.json: Tools used by the query rewriting prompt
      * app/backend/approaches/prompts/chat_answer.system.jinja2: Jinja2 template for the system message used by the Chat approach — implements the auto-extract triage flow (see "Triage approach" section below)
      * app/backend/approaches/prompts/chat_answer.user.jinja2: Jinja2 template for the user message used by the Chat approach, including sources
    * app/backend/prepdocslib: Contains the document ingestion library used by both local and cloud ingestion
      * app/backend/prepdocslib/blobmanager.py: Manages uploads to Azure Blob Storage
      * app/backend/prepdocslib/cloudingestionstrategy.py: Builds the Azure AI Search indexer and skillset for the cloud ingestion pipeline
      * app/backend/prepdocslib/csvparser.py: Parses CSV files
      * app/backend/prepdocslib/embeddings.py: Generates embeddings for text and images using Azure OpenAI
      * app/backend/prepdocslib/figureprocessor.py: Generates figure descriptions for both local ingestion and the cloud figure-processor skill
      * app/backend/prepdocslib/fileprocessor.py: Orchestrates parsing and chunking of individual files
      * app/backend/prepdocslib/filestrategy.py: Strategy for uploading and indexing files (local ingestion)
      * app/backend/prepdocslib/htmlparser.py: Parses HTML files
      * app/backend/prepdocslib/integratedvectorizerstrategy.py: Strategy using Azure AI Search integrated vectorization
      * app/backend/prepdocslib/jsonparser.py: Parses JSON files
      * app/backend/prepdocslib/listfilestrategy.py: Lists files from local filesystem or Azure Data Lake
      * app/backend/prepdocslib/mediadescriber.py: Interfaces for describing images (Azure OpenAI GPT-4o, Content Understanding)
      * app/backend/prepdocslib/page.py: Data classes for pages, images, and chunks
      * app/backend/prepdocslib/parser.py: Base parser interface
      * app/backend/prepdocslib/pdfparser.py: Parses PDFs using Azure Document Intelligence or local parser
      * app/backend/prepdocslib/searchmanager.py: Manages Azure AI Search index creation and updates
      * app/backend/prepdocslib/servicesetup.py: Shared service setup helpers for OpenAI, embeddings, blob storage, etc.
      * app/backend/prepdocslib/strategy.py: Base strategy interface for document ingestion
      * app/backend/prepdocslib/textparser.py: Parses plain text and markdown files
      * app/backend/prepdocslib/textprocessor.py: Processes text chunks for cloud ingestion (merges figures, generates embeddings)
      * app/backend/prepdocslib/textsplitter.py: Splits text into chunks using different strategies
    * app/backend/app.py: The main entry point for the backend application.
    * app/backend/query_router.py: Query relevance router; classifies whether the user question is in scope (e.g. legal enquiries) before invoking RAG, to reduce latency and avoid unnecessary search/LLM calls for irrelevant queries.
  * app/functions: Azure Functions used for cloud ingestion custom skills (document extraction, figure processing, text processing). Each function bundles a synchronized copy of `prepdocslib`; run `python scripts/copy_prepdocslib.py` to refresh the local copies if you modify the library.
  * app/frontend: Contains the React frontend code, built with TypeScript, built with vite.
    * app/frontend/src/api: Contains the API client code for communicating with the backend.
    * app/frontend/src/components: Contains the React components for the frontend.
    * app/frontend/src/locales: Contains the translation files for internationalization.
      * app/frontend/src/locales/da/translation.json: Danish translations
      * app/frontend/src/locales/en/translation.json: English translations
      * app/frontend/src/locales/es/translation.json: Spanish translations
      * app/frontend/src/locales/fr/translation.json: French translations
      * app/frontend/src/locales/it/translation.json: Italian translations
      * app/frontend/src/locales/ja/translation.json: Japanese translations
      * app/frontend/src/locales/nl/translation.json: Dutch translations
      * app/frontend/src/locales/ptBR/translation.json: Portuguese translations
      * app/frontend/src/locales/tr/translation.json: Turkish translations
    * app/frontend/src/pages: Contains the main pages of the application
* infra: Contains the Bicep templates for provisioning Azure resources.
* tests: Contains the test code, including e2e tests, app integration tests, and unit tests.

## Query router (optional)

When enabled, the app checks each user message for relevance to the bot’s scope (e.g. legal enquiries) before running retrieval and the chat approach. Irrelevant queries get a short “I handle …” style reply without hitting the RAG pipeline, which reduces latency and cost.

* **Enable**: set `QUERY_ROUTER_ENABLED=true` in the environment (default: false). The deployed backend gets this from Bicep; run `azd env set QUERY_ROUTER_ENABLED true` then **provision and redeploy** so the container receives it.
* **Verify**: call `GET /config` and confirm `queryRouterEnabled` is `true`; if `false`, the router is off and every query goes to RAG.
* **Fast path**: obvious greetings (e.g. "hello", "hi", "hey") are always treated as out-of-scope without calling the LLM; see `query_router.OBVIOUS_NON_QUERIES` and `is_obvious_non_query()`.
* **Scope text**: `QUERY_ROUTER_SCOPE_DESCRIPTION` (default: "legal enquiries based on our knowledge base") is used in the classifier prompt.
* **Out-of-scope message**: `QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE` overrides the reply shown when the query is classified as out of scope.
* **Bypass**: the frontend can send `overrides.skip_query_router: true` (e.g. from Developer Settings) to force RAG for that request.

The current router implementation is heuristic-only (keyword and short-message checks), so there is no extra LLM call on the router path. It is designed to be fail-open for substantive user messages so legitimate legal queries still proceed to RAG.

## Triage approach (auto-extract)

The system prompt in `chat_answer.system.jinja2` implements an **auto-extract triage flow** designed for interns with zero legal knowledge who have a applicant sitting right in front of them. The key innovation over a simple sequential Q&A is:

1. **Topic match**: The intern types a paragraph describing the applicant's situation. The RAG pipeline retrieves Golden Set entries, and the LLM matches the best entry.
2. **Auto-extract**: The LLM reads the intern's paragraph and checks every Part B triage question from the matched entry. Questions whose answers are already present in the paragraph (explicitly or by clear implication) are marked as answered.
3. **Smart routing**: The LLM follows the `branching_logic` with all extracted answers to determine how far down the decision tree it can get:
   - **All needed questions answered** → routes immediately (OUTPUT A) with a script the intern reads aloud
   - **Some answered, more needed** → shows what it gathered + asks exactly ONE follow-up question (OUTPUT B)
   - **Nothing extractable** → brief Part A briefing + first question (OUTPUT C)
   - **No matching entry** → Unclear handling (OUTPUT D)
4. **Follow-up turns**: As the intern provides answers, the system updates its map and either asks the next required question or gives the final routing recommendation.
5. **Multi-topic handling**: When a applicant's situation spans multiple legal areas (e.g. criminal charge AND divorce), the system identifies all matching entries, prioritizes them, and handles each sequentially. After completing routing for the first topic (OUTPUT A), it transitions automatically to the next topic, re-using any facts already gathered so it never re-asks known answers.

**Workstream-specific binding flows** (STEP 2b in `chat_answer.system.jinja2`): **Route letters are not global** across golden entries. **§1 GEN3-T02** (*CLAS + Urgent concurrent*): run **GEN3-T06** before **GEN3-T02 Q3**; **GEN3-T02 Q3 = No** → **GEN3-T04** (no T02 Q4–Q6); **T02 Q3 = Not Sure** → **Route F** only. **§2 GEN3-T03** (*FJSS + Urgent concurrent*): run **GEN3-T06** before **GEN3-T03 Q2**; **GEN3-T03 Q2 = No, foreigner** → **GEN3-T03 Q4** (not T04); **GEN3-T03 Q4 = No** (foreigner path) → **GEN3-T04** (no T03 Q5). When **GEN3-T06** is nested under T02 or T03 for those routes, **resume the parent** at the question stated in the parent’s `routing` text — do not treat GEN3-T06’s “return to GEN3-T01” as replacing that resume point. **§3 GEN3-T01** (*first contact*): **Q2** “calling on behalf” → must check **able to self-help** before proceeding (if able → **Route B**, if not → Q3, if unclear → clarify once then Route B); **Q3 = Not Sure** → ask **personal capacity vs business** clarify **once**, if still unsure → **Route F** (no Q3 loop); **Q3 = Yes** → determine **nonprofit vs for-profit** before routing to C or D. **§4 GEN3-T06** (*urgent*): Q2/Q3 `if_not_sure` = concurrent routing (output Route **and** continue to next question in the same response). **GEN3-T01** hands off Route G → **GEN3-T02**, Route H → **GEN3-T03**, Route I → **GEN3-T04** (ids in `data/pbsg_golden_set_by_id/`).

**Deterministic workflow graph:** The LLM may identify candidate topics, urgency, vulnerability, corrections, or ambiguity, but it must not decide route letters, next questions, handoff targets, parent resume points, or final recommendations. Once a workflow or overlay is triggered, `PBSGWorkflowGraph` / `PBSGRoutingEngine` in `app/backend/pbsg_triage_state.py` owns execution against the Golden Set. Cross-topic links such as **GEN3-T01 Route G/H/I → GEN3-T02/T03/T04**, **GEN3-T02 Route D → GEN3-T06 → GEN3-T02 Q3**, **GEN3-T03 Route A → GEN3-T06 → GEN3-T03 Q2**, and standalone **GEN3-T06 Route D → GEN3-T01 Q1** are deterministic backend policies.

**Urgent and vulnerability handling:** **GEN3-T06** is a deterministic urgent stream. Standalone **GEN3-T06 Route D** returns to **GEN3-T01 Q1** for ordinary first-contact triage, but nested GEN3-T06 under **GEN3-T02** or **GEN3-T03** resumes the parent stream as described above. **GEN3-T13** is handled by deterministic cue routing in the backend: confirmed minor → Route B; unsafe/no shelter/basic needs → trigger GEN3-T06 first; active violence/FSC/social worker/two or more vulnerability cues → Route A; one non-severe cue → Route C; unclear severity → Route A for staff assessment. Keep these as code rules with tests, not prompt-only behavior.

Design principles:
- **Bare greetings / no situation text** (e.g. intern only says "hi"): the model must **not** open **`GEN3-T13`** or other specialty entries as the primary topic — default to **`GEN3-T01`** with OUTPUT C (first-contact Q1) when that JSON is in sources. With **`QUERY_ROUTER_ENABLED=true`**, obvious greetings are also short-circuited before RAG (see Query router).
- Every response must be **scannable in under 15 seconds** (applicant is right there)
- Questions are phrased as **scripts the intern reads verbatim** to the applicant
- Routing recommendations include **exact words to say** to the applicant
- Never asks a question whose answer is already known
- Stops asking questions the moment branching logic allows a valid route
- When multiple topics are detected, handles higher-priority topics first (capital offences > urgent matters > criminal > matrimonial > civil > other)
- When the current pending `branching_logic` question has a small finite answer set, the backend may attach `context.quick_reply` metadata so the frontend can render selectable response buttons. The assistant markdown stays unchanged for eval compatibility; button clicks are sent back as normal user messages, and free-text input remains available.

The four output formats (A/B/C/D) all preserve the `**Selected Entry:**` anchor and `Route <letter>` labels needed by the eval script (`evals/pbsg_golden_set_eval.py`).

Terminal routing outputs are rendered as structured intern-facing route cards by the backend, not as one long quoted route paragraph. Keep `routing` as the canonical source/prose from the Golden Set. Every GEN3 route should have display-ready copy in `routing_structured` keyed by route label, for example `routing_structured["Route E"]`. Each card may include `name`, `script`, `needs_to_know`, `access`, `prepare`, `intern_steps`, and `caveats`. The backend renderer prefers `routing_structured` when present and falls back to parsing the existing `routing` prose when absent. Do not replace `routing` with first-person display copy; keep policy prose traceable and put intern-facing wording in `routing_structured`. The `script` field is the read-aloud applicant wording and must not contain internal instructions like "Inform applicant", "Share about", "Take down", or "Forward the details"; put operational steps in `intern_steps`.

## Adding new data

New files should be added to the `data` folder, and then either run scripts/prepdocs.sh or scripts/prepdocs.ps1 to ingest the data.

For the Pro Bono SG golden set, **`data/pbsg_golden_set_by_id/<ENTRY_ID>.json`** is the canonical structured data (one object per file) for **ingestion** and **evaluation**. If the source Word document changes, regenerate all entry files with:

```shell
python scripts/build_pbsg_golden_set_json.py
```

By default that script reads **`data/2026.04.16 PBSG_Golden_Set_General_Enquiries_v3.docx`** (GEN3 topic ids such as `GEN3-T01`) and overwrites the JSON files under `data/pbsg_golden_set_by_id/`. To rebuild from the older domain golden set Word file instead, run:

```shell
python scripts/build_pbsg_golden_set_json.py --legacy
```

(`--legacy` uses `data/PBSG_Golden_Set_Complete_v2.docx` and the `XXX-NN | topic` header layout.) The script requires macOS `textutil` to convert `.docx` to plain text. It also normalizes known source typos (e.g. handoff ids `GEN3-T-FAM` / `GEN3-T-CIV` → `GEN3-T03` / `GEN3-T04`, and **GEN3-T03** Q4 Not Sure — foreigner path must clarify a **Singapore Citizen child under 21**, not the caller’s nationality, which **Q2** already covers).

`scripts/build_pbsg_golden_set_json.py` preserves existing `routing_structured` blocks when regenerating JSON from the Word document. If you add or edit structured route cards manually, run the builder only after those fields have been saved in `data/pbsg_golden_set_by_id/<ENTRY_ID>.json`; regeneration will carry them forward.

**Do not index the Word file for RAG:** keep the `.docx` in `data/` for regeneration if you like, but `prepdocs.sh` / `prepdocs.ps1` skip the golden-set source Word files so Azure Search only gets the structured JSON (avoid duplicate, messier chunks from the document extractor). Skipped basenames include `PBSG_Golden_Set_Complete_v2.docx`, `2026_03_31_PBSG_Golden_Set_v3_MCA.docx`, and `2026.04.16 PBSG_Golden_Set_General_Enquiries_v3.docx`. `prepdocs.py` also skips Microsoft Office lock files (basenames starting with `~$`, e.g. `~$26.04.16 … .docx`) so an open Word document does not break ingestion or `azd` postprovision. For any other build-only `.docx`, pass `--exclude YourFile.docx` to `prepdocs.py`.

The deployed backend container also needs the Golden Set JSON files at runtime for the deterministic PBSG transition guard. `azd` prebuild/prepackage hooks run `scripts/sync_backend_golden_set.py` from `app/backend` to mirror the canonical files into `app/backend/data/pbsg_golden_set_by_id/` before packaging the backend Docker context. If you run a manual Docker build from `app/backend`, run that sync script first.

## Adding a new azd environment variable

An azd environment variable is stored by the azd CLI for each environment. It is passed to the "azd up" command and can configure both provisioning options and application settings.
When adding new azd environment variables, update:

1. infra/main.parameters.json : Add the new parameter with a Bicep-friendly variable name and map to the new environment variable
1. infra/main.bicep: Add the new Bicep parameter at the top, and add it to the `appEnvVariables` object
1. .azdo/pipelines/azure-dev.yml: Add the new environment variable under `env` section
1. .github/workflows/azure-dev.yml: Add the new environment variable under `env` section

You may also need to update:

1. app/backend/prepdocs.py: If the variable is used in the ingestion script, retrieve it from environment variables here. Not always needed.
1. app/backend/app.py: If the variable is used in the backend application, retrieve it from environment variables in setup_clients() function. Not always needed.

## Adding a new setting to "Developer Settings" in RAG app

When adding a new developer setting, update:

* frontend:
  * app/frontend/src/api/models.ts : Add to ChatAppRequestOverrides
  * app/frontend/src/components/Settings.tsx : Add a UI element for the setting
  * app/frontend/src/locales/*/translations.json: Add a translation for the setting label/tooltip for all languages
  * app/frontend/src/pages/chat/Chat.tsx: Add the setting to the component, pass it to Settings

* backend:
  * app/backend/approaches/chatreadretrieveread.py :  Retrieve from overrides parameter
  * app/backend/app.py: Some settings may need to be sent down in the /config route.

## When adding tests for a new feature

All tests are in the `tests` folder and use the pytest framework.
There are three styles of tests:

* e2e tests: These use playwright to run the app in a browser and test the UI end-to-end. They are in e2e.py and they mock the backend using the snapshots from the app tests. (Before running e2e tests, make sure to run `npm run build` in app/frontend first to build the frontend code.)
* app integration tests: Mostly in test_app.py, these test the app's API endpoints and use mocks for services like Azure OpenAI and Azure Search.
* unit tests: The rest of the tests are unit tests that test individual functions and methods. They are in test_*.py files.

When adding a new feature, add tests for it in the appropriate file.
If the feature is a UI element, add an e2e test for it.
If it is an API endpoint, add an app integration test for it.
If it is a function or method, add a unit test for it.
Use mocks from tests/conftest.py to mock external services. Prefer mocking at the HTTP/requests level when possible.

When you're running tests, make sure you activate the .venv virtual environment first:

```shell
source .venv/bin/activate
```

To check for coverage, run the following command:

```shell
pytest --cov --cov-report=annotate:cov_annotate
```

Open the cov_annotate directory to view the annotated source code. There will be one file per source file. If a file has 100% source coverage, it means all lines are covered by tests, so you do not need to open the file.

For each file that has less than 100% test coverage, find the matching file in cov_annotate and review the file.

If a line starts with a ! (exclamation mark), it means that the line is not covered by tests. Add tests to cover the missing lines.

## Sending pull requests

When sending pull requests, make sure to follow the PULL_REQUEST_TEMPLATE.md format.

## Upgrading dependencies

### Python backend dependencies

To upgrade a particular package in the backend, use the following command, replacing `<package-name>` with the name of the package you want to upgrade:

```shell
cd app/backend && uv pip compile requirements.in -o requirements.txt --python-version 3.10 --upgrade-package <package-name>
```

After upgrading, run tests to verify compatibility:

```shell
source .venv/bin/activate
pytest tests/
```

### npm frontend dependencies

To upgrade a particular package in the frontend:

1. **Navigate to the frontend directory**:

   ```shell
   cd app/frontend
   ```

2. **Upgrade the package** (replace `<package-name>` with the package you want to upgrade):

   ```shell
   npm install <package-name>@latest
   ```

3. **Build the frontend** to verify the upgrade works:

   ```shell
   npm run build
   ```

4. **Run all tests** to ensure nothing broke:

   ```shell
   # Run e2e tests from the root directory
   cd ../..
   source .venv/bin/activate
   pytest tests/e2e.py
   ```

5. **Commit changes** if the upgrade is successful:

   ```shell
   git add package.json package-lock.json
   git commit -m "chore: upgrade <package-name> to <version>"
   ```

**Important notes for frontend upgrades**:

* When upgrading React or related core packages, you may need to upgrade multiple packages together (e.g., `react`, `react-dom`, `@types/react`, `@types/react-dom`)
* Some upgrades may require code changes for API compatibility - check the package's changelog
* For major version upgrades of UI libraries like Fluent UI or MSAL, review breaking changes carefully. Manual tests are required for any MSAL changes since the E2E tests do not cover authentication flows.
* If npm reports peer dependency conflicts, the `.npmrc` file has `legacy-peer-deps=true` which allows the install to proceed. This is currently needed because `react-helmet-async` declares peer dependencies on React 17/18, but works fine with React 19.

## Checking Python type hints

To check Python type hints, use the following command:

```shell
ty check
```

Note that we do not currently enforce type hints in the tests folder, as it would require adding a lot of `# type: ignore` comments to the existing tests.
We only enforce type hints in the main application code and scripts.

## Python code style

Do not use single underscores in front of "private" methods or variables in Python code. We do not follow that convention in this codebase, since this is an application and not a library.

## Deploying the application

To deploy the application, use the `azd` CLI tool. Make sure you have the latest version of the `azd` CLI installed. Then, run the following command from the root of the repository:

```shell
azd up
```

That command will BOTH provision the Azure resources AND deploy the application code.

If you only changed the Bicep templates and want to re-provision the Azure resources, run:

```shell
azd provision
```

If you only changed the application code and want to re-deploy the code, run:

```shell
azd deploy
```

If you are using cloud ingestion and only want to deploy individual functions, run the necessary deploy commands, for example:

```shell
azd deploy document-extractor
azd deploy figure-processor
azd deploy text-processor
```

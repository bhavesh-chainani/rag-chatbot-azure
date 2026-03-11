## Developer onboarding

This guide explains how to onboard a new developer to this project and its Azure environment.

The examples below assume there is an existing shared Azure Developer CLI (`azd`) environment named `rag-chatbot-8mar` and a corresponding resource group `rg-rag-chatbot-8mar`. Adjust names if your setup is different.

---

## 1. Prerequisites for new developers

- **Accounts**
  - **Azure account** in the same tenant as the project.
  - **Git account** with access to the repository (GitHub or Azure DevOps, depending on where this repo is hosted).
- **Local tools**
  - `azd` (Azure Developer CLI)
  - Azure CLI (`az`)
  - Python 3.10+ and `pip`
  - Node.js 20+
  - Git

Developers can also use GitHub Codespaces or Dev Containers instead of a full local setup (see `README.md`).

---

## 2. Granting access (project owner steps)

- **Source control**
  - Add the developer to the Git repo with at least **write** / **contribute** permissions.

- **Azure permissions**
  - In the Azure Portal, locate the **resource group** that hosts this environment (for example `rg-rag-chatbot-8mar`).
  - Open **Access control (IAM)** → **Add role assignment**.
  - Assign an appropriate role to the developer’s Entra ID user (search by their company email):
    - Common choice: **Contributor** on the resource group.
    - Alternatively, use more granular roles if your organization requires them.

If you use Entra ID groups, add the developer to the appropriate group(s) instead of granting per-user access.

---

## 3. Attaching to an existing azd environment

Developers should **not** receive `.env` files with secrets by email or chat. Instead, they attach to an existing azd environment and let `azd` pull configuration from Azure.

From the repo root:

```bash
# Log in to Azure
azd auth login

# (Optional) Ensure the correct subscription is active if needed
az account set --subscription <subscription-id>

# Pull the shared environment configuration (example name)
azd env refresh -e rag-chatbot-8mar
```

This creates or updates `.azure/rag-chatbot-8mar/.env` on the developer’s machine using values stored in Azure.

If you prefer **per-developer environments**, each developer can instead run:

```bash
azd auth login
azd up -e <dev-env-name>
```

This will provision a new resource group and environment for that developer (ensure their Azure role assignment allows this).

---

## 4. Local development setup

After attaching to an azd environment:

```bash
git clone <repo-url>
cd rag-chatbot-azure

# (Recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Backend dependencies
pip install -r app/backend/requirements.txt

# Frontend dependencies
cd app/frontend
npm install
cd ../..
```

To run the development server (after `azd up` has succeeded for at least one environment):

```bash
azd auth login  # if not already logged in
./app/start.sh  # Linux/Mac
# or
./app/start.ps1 # Windows
```

See `docs/localdev.md` for additional options (hot reload, debugging, etc.).

---

## 5. Typical day‑to‑day workflows

- **Deploying changes to the shared environment**

```bash
azd env select rag-chatbot-8mar
azd deploy
```

- **Running tests**
  - Activate the virtual environment:

    ```bash
    source .venv/bin/activate
    ```

  - Run Python tests:

    ```bash
    pytest
    ```

  - Run frontend tests / build (from `app/frontend`):

    ```bash
    npm test      # if configured
    npm run build
    ```

---

## 6. Security and secrets

- Do **not** commit `.azure/**/.env` or any other secret-containing files to source control.
- Do **not** share `.env` content over email or chat.
- When rotating secrets (client app secrets, keys, etc.), update them in the appropriate Azure resource and let `azd env refresh` propagate changes to developers.

---

## 7. Quick checklist for onboarding a new developer

- **Project owner**
  - **Add repo access** for the developer.
  - **Assign Azure RBAC** on the project resource group (or add them to an appropriate group).
  - Tell them the **azd environment name** (for example `rag-chatbot-8mar`) and **subscription ID**.

- **New developer**
  - Install tools (`azd`, Azure CLI, Python, Node.js, Git).
  - Clone the repo and run `azd auth login`.
  - Run `azd env refresh -e <shared-env-name>` or `azd up -e <dev-env-name>`.
  - Set up Python and Node dependencies.
  - Run the app locally or deploy with `azd deploy` as appropriate.


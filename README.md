<<<<<<< HEAD
=======
# IR Chatbot — DSX REST wrapper

FastAPI backend: thin entry [`server.py`](server.py) → [`backend/`](backend/) (see [`server_legacy.py`](server_legacy.py) for the old monolith) + React UI ([`ir-chatbot-app/`](ir-chatbot-app/)) for filing Incident Reports via 3DEXPERIENCE **Coal Porter** (DSxR&D) REST APIs.

## Quick start

```bash
pip install -r requirements.txt
cp env.example .env   # set DSX_BASE_URL, DSX_CREDENTIALS, and DSX_UI_BASE_URL
uvicorn server:app --reload --reload-dir backend --port 3001   # sessions are in memory: a restart signs you out

cd ir-chatbot-app
npm install
npm run dev
```

Log in with your **DSX application password (API key)**, not your Windows password.

## Release-first filing flow

1. **Login** → hello and **main menu**: create IR (saved form or manual), or **set target release** by typing the release name/code (resolves `rel_eno_id` via Coal Porter `GET /releases` — no brand/program picker).
2. **Resolve context** — `POST /api/dsx/resolve-release` stores `rel_eno_id` and version fields on the dashboard; optional hierarchy APIs remain for detection program pickers elsewhere in the flow.
3. **Pick a saved IR form** (e.g. Translate_IR from `/rest/ui/v1/formtemplates`) or **start without a saved form**.
4. **Edit** feature, people, detection version, title, and description in the chat (dashboard release always overrides template release OIDs).
5. **File** — `POST /incidentfamilies` with resolved physical IDs.

| Step | App API | Coal Porter / UI API |
|------|---------|----------------------|
| Set release | `POST /api/dsx/resolve-release` (name → `rel_eno_id`); optional `resolve-release-context` if using hierarchy | `GET /releases` (search); `/programs?brand=` not required for target release |
| Load template | `GET /api/saved-ir-forms` | `GET /rest/ui/v1/formtemplates` (`DSX_UI_BASE_URL`) |
| Re-link OIDs | `POST /api/resolve-ir-references` | Release/program/feature resolution helpers |
| Create IR | `POST /api/create-ir` | `POST …/incidentfamilies` |

Check `GET /api/health` for `release_hierarchy_configured`, `dsx_ui_configured`, `dsx_api_docs_url`, and `dsx_navigator_base`.

## DSX dev API reference (`dsxdev-online`)

| Purpose | URL |
|---------|-----|
| **Coal Porter OpenAPI** (REST contract) | `https://dsxdev-online.dsone.3ds.com/enovia/rest/devops/v1/api-docs` |
| **Coal Porter REST base** (this app) | `DSX_BASE_URL` → `.../rest/devops/v1` |
| **Saved form templates** | `DSX_UI_BASE_URL` → `.../rest/ui/v1/formtemplates` |
| **3DEXPERIENCE Navigator** (browse objects by ID) | `DSX_WEB_BASE_URL` + `/common/emxNavigator.jsp?physicalId={PHYSICAL_ID}` |

Navigator is the UI you use to **confirm** a release or filed IR. It is not a REST catalog — implement and audit calls against **api-docs**, not emxNavigator.jsp.

Broader Coal Porter background: internal **3DSwym Cloud Web Services (CoalPorter)** wiki; your tenant’s paths and form fields are always defined in **dsxdev api-docs**.

### Chatbot → upstream mapping

| Chatbot feature | App route | Upstream (dsxdev) | OpenAPI / notes |
|-----------------|-----------|-------------------|-----------------|
| File IR | `POST /api/create-ir` | `POST /rest/devops/v1/incidentfamilies` (multipart) | `incidentfamilies` POST — fields match `CreateIRRequest` / `REQUIRED_FIELDS` in `server.py` |
| List my IRs | `GET /api/my-incidents` | `GET /incidentfamilies` | `state`, `owner` query variants |
| Target release | `POST /api/dsx/resolve-release` | `GET /releases` (name/q/search/code) | Physical ID or release title/code; Navigator via `DSX_WEB_BASE_URL` |
| Saved forms | `GET /api/saved-ir-forms` | `GET /rest/ui/v1/formtemplates` | UI REST (not in devops OpenAPI) |
| Open object in browser | filed IR link, release chip | `emxNavigator.jsp?physicalId=` | `GET /api/dsx/navigator-url?physical_id=` |

After a DSX upgrade, re-check `api-docs` for `incidentfamilies` POST form parameter names and compare with `_normalize_create_payload` in [`server.py`](server.py).

## `.env` (Coal Porter + templates)

```env
DSX_BASE_URL=https://dsxdev-online.dsone.3ds.com/enovia/rest/devops/v1
DSX_UI_BASE_URL=https://dsxdev-online.dsone.3ds.com/enovia/rest/ui/v1
DSX_WEB_BASE_URL=https://dsxdev-online.dsone.3ds.com/enovia
DSX_CREDENTIALS=your_login:your_api_key

DSX_PARENT_BRAND_NAME=3DEXPERIENCE Platform
DSX_SERVICES_PATH=/brands
DSX_PRODUCT_SERVICES_PATH=/services
DSX_PROGRAMS_PATH=/programs
DSX_RELEASES_PATH=/releases/{program_id}
DSX_PROGRAMS_SERVICE_PARAM=service
DSX_RELEASES_PROGRAM_PARAM=program
```

If the release picker returns empty lists, tune these paths for your tenant and confirm `release_hierarchy_configured` is true in health.

### Naming: service vs release (not brand)

Target release in an IR is the Coal Porter **release** object (`rel_eno_id`). The wizard walks the hierarchy DSX exposes:

| Step | Meaning | Example |
|------|---------|---------|
| Brand (optional) | Scope for listing product services | `3DEXPERIENCE Platform`, `3DExp` |
| Product service | Product line you file against | `3DEXPERIENCEAIAssistantInfra` |
| Program | Build / stream under that service | `PRG…` or program title |
| Release | Fix version for the IR | `1.9x` (release title/code) |

A label like **`3DEXPERIENCEAIAssistantInfra-1.9x`** is usually **service + release stream**, not a brand. Set **`DSX_PARENT_BRAND_NAME`** in `.env` to the brand title from `GET /api/dsx/brands` so the app skips manual brand pick and opens the **product service** list. Then pick the Infra service, its program, and the **1.9x** release level.

## Typical flow

1. Login → set **target release** on the dashboard
2. **Path A**: Pick saved form → review / edit sections → file IR
3. **Path B**: Start without saved form → answer questions → file IR
>>>>>>> 54129c9 (first commit)
# IR-Assistant

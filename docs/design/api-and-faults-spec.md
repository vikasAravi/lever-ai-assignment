# GLPI APIs and proxy faults: brain spec

| | |
|---|---|
| Spec version | 0.1 (draft) |
| GLPI | 11.0.12 |
| Date | 2026-11-02 |
| Scope | Shared core for every agent: which API each tool uses, how reads and writes behave on the wire, and how each proxy fault is handled. Companion to [`onboarding-brain-spec.md`](onboarding-brain-spec.md). |

**Status.** Facts were read in GLPI 11.0.12 source, the seed dump and `proxy/proxy.py`. **None has been checked against a running GLPI yet**; section 7 lists the live checks owed. Legacy-API facts (section 2) were read directly. High-level-API facts (section 3) were collected by two research helpers reading the same tag; their file:line citations are kept, but they have not been re-checked one by one.

Source paths refer to `https://github.com/glpi-project/glpi/tree/11.0.12`. Line numbers are approximate; the function name is the anchor.

---

## 1. Decisions

| Id | Decision |
|---|---|
| D-API-1 | **Every tool uses the legacy REST API (`/apirest.php`).** The high-level API is disabled in the seed, has no OAuth clients, needs real passwords, and has no route for profile assignments, other users' emails or ticket ↔ item links (section 3). Enabling it would mean changing GLPI's configuration. |
| D-API-2 | **Read → act → read back.** Every decision-making read (exists? visible in X? did the write land?) is `GET /search/{type}` after switching the session to the target entity **without sub-entities**. Read by id is used only for detail on a record `/search` already returned (e.g. a user's profile assignments). Collection GETs are never used for decisions. |
| D-API-3 | **Session and fault handling** as in section 5: three checks before an empty answer counts as "none", re-login and re-scope after expiry, bounded retries, a 30 s per-call timeout plus a per-tool budget, one session per role, no decoy sessions, and every fault in the decision log. |

---

## 2. Legacy API (`/apirest.php`)

### 2.1 Sessions and scope

| Id | Fact | Source |
|---|---|---|
| A1 | `initSession` with `App-Token` + `user_token` logs in through `Auth::login`, which calls `Session::init`, the same path as a UI login. The starting scope follows the brain spec's V6 rule: onboarding.bot → Root and everything below; inventory.bot → Engineering. | `Glpi/Api/API.php::initSession` (~291); `Auth.php::login` (~1128, `Session::init` ~1212) |
| A2 | The session token is the PHP session id, encrypted with the instance key and base64-encoded. It is sent back as the `Session-Token` header (GLPI also accepts a `session_token` query parameter). | `API::initSession`; `API::retrieveSession` |
| A3 | Requests open the PHP session **read-only**, except `initSession`, `killSession`, `changeActiveEntities` and `changeActiveProfile` (or a request with `session_write=true`). Side effects on the session from other calls are not persisted. | `API::getRessourcesWithSessionWrite` (~3522); `APIRest` (~166-173) |
| A4 | **`changeActiveEntities` failure answers HTTP 200 with body `false`**; success is 200 `true`. | `API::changeActiveEntities` (~444); `APIRest` (~190-192, `returnResponse` defaults to 200) |
| A5 | `entities_id` is passed through `intval()`, so a non-numeric value becomes **0 (Root)**. `is_recursive` must be a JSON boolean, otherwise 400. A missing `entities_id` means `'all'`. | `API::changeActiveEntities` |
| A6 | `GET /getActiveEntities` returns an object `{"active_entity":{"id", "active_entity_recursive", "active_entities":[…]}}`. Its genuine answer is never an empty array. | `API::getActiveEntities` (~516) |
| A7 | Seed settings: API enabled; login by credentials and by user token both enabled; `list_limit` 15; `version` 11.0.12; `url_base` `http://localhost:8080`. | seed `glpi_configs` |

### 2.2 Reads

| Id | Fact | Source |
|---|---|---|
| A8 | **Read by id** (`GET /{type}/{id}`): not found → `404 ERROR_ITEM_NOT_FOUND`; exists but no right or out of scope → `403 ERROR_RIGHT_MISSING`. It **returns trashed records**. | `API::getItem` (~646); `messageNotfoundError`, `messageRightError` |
| A9 | **Collection list** (`GET /{type}`): needs the type's READ right (else 403). Owned types are filtered with `getEntitiesRestrictCriteria(..., maybeRecursive())`, the same rule as the UI including records shared down from ancestors. Trash is hidden by default (`is_deleted=false`). **Templates are not hidden.** | `API::getItems` (~1146, ~1214, ~1320-1340) |
| A10 | **`GET /User` has no entity filter at all**: users are not entity-assigned, so the collection returns every user the READ right allows. The UI scopes users through profile assignments. | `API::getItems`; `User::isEntityAssign` returns false |
| A11 | Collection paging: default range `0-14` (the session's list limit); `206` with `Content-Range: start-end/total` when more remain; a start beyond the total → `400 ERROR_RANGE_EXCEED_TOTAL`. A genuinely empty collection is `200 []`. | `API::getItems`; `APIRest` (~316-330) |
| A12 | **Search** (`GET /search/{type}`) runs `Search::getDatas`, the engine behind the UI's lists: same entity rule, trash and templates hidden, users scoped through profile assignments. This is the API's version of "the listing the way the UI lists it". | `API::searchItems` (~1643); `Glpi/Search/Provider/SQLProvider.php` |
| A13 | Search criteria refer to fields by numeric **search-option ids** (version-specific; listed by `GET /listSearchOptions/{type}`). Text "contains" is a substring match; `^value$` anchors it; matching ignores case (collation). Linked fields come back as display text; the id column (option 2) is requested explicitly. | `API::searchItems`; `SQLProvider::makeTextSearchValue` |
| A14 | The search body is always an **object**: `{"totalcount", "count", "sort", "order", "data", …}`, with 200 when complete and 206 plus `Content-Range` when paged. A zero-result search is `{"totalcount":0, …}`, never a bare `[]`. An internal failure is `500 ERROR_SQL`. | `API::searchItems` (~1744-1760); `APIRest` (~221-243) |
| A15 | `GET /getGlpiConfig` returns `Config::getSafeConfig()` (the whole config minus secrets), which should include `version`. Planned startup version check. | `API::getGlpiConfig` (~614); `Config::getSafeConfig` |

### 2.3 Writes

| Id | Fact | Source |
|---|---|---|
| A16 | **Create** (`POST /{type}` with `{"input": {...}}`) checks `can(-1, CREATE, input)` first. Success: `201 {"id": N, "message": ""}` plus a `Location` header. Bulk input (an array): partial failure `207`, all failed `400 ERROR_GLPI_ADD`. Single failure: `400 ERROR_GLPI_ADD` with GLPI's message. | `API::createItems` (~1902, ~1930); `APIRest` (~346-363) |
| A17 | A database error inside `add()` (e.g. a duplicate key) is caught and its message returned as-is: **raw MySQL text including the SQL statement**. | `API::createItems` catches `RuntimeException` (~1946); `DBmysql::doQuery` |
| A18 | **Update** (`PUT`/`PATCH /{type}/{id}`) checks `can(id, UPDATE)`. Success: `200 [{"<id>": true, "message": ""}]`; failures `400 ERROR_GLPI_UPDATE` or `207 ERROR_GLPI_PARTIAL_UPDATE`. | `API::updateItems` (~2033) |
| A19 | **Delete** takes `force_purge`. Types without a trash are always purged and the PURGE right is checked; otherwise DELETE (trash) or PURGE depending on `force_purge`. | `API::deleteItems` (~2153, ~2190-2210) |
| A20 | A write needs the record's entity **in** the active set (brain spec V4), so a session scoped to X can't edit a record shared down from X's parent. | `CommonDBTM::checkEntity()` |

### 2.4 Errors

GLPI's error body is a JSON **array**: `["ERROR_CODE", "message; view documentation in your browser at …"]` (`API::returnError`, ~2889).

| Code | Status | When |
|---|---|---|
| `ERROR_SESSION_TOKEN_MISSING` | 400 | no token |
| `ERROR_SESSION_TOKEN_INVALID` | 401 | GLPI doesn't recognise the session |
| `ERROR_RIGHT_MISSING` | 403 | missing right or out of scope |
| `ERROR_ITEM_NOT_FOUND` | 404 | no such record |
| `ERROR_BAD_ARRAY` | 400 | malformed input |
| `ERROR_RANGE_EXCEED_TOTAL` | 400 | page start beyond total |
| `ERROR_SQL` | 500 | search failed inside GLPI |
| `ERROR_GLPI_ADD` / `_UPDATE` / `_DELETE` | 400 | write refused (permission or business rule; the message tells which) |
| `ERROR_GLPI_PARTIAL_ADD` / `_UPDATE` / `_DELETE` | 207 | bulk write partly refused |

A permission refusal on a write is a **400 with the message** "You don't have permission to perform this action.", not a 403. Tools classify refusals by code **and** message, never by status alone.

---

## 3. High-level API (`/api.php`): why it is not used

| Id | Fact | Source (as reported) |
|---|---|---|
| H1 | Disabled in the seed (`enable_hlapi = 0`): every route except `/token` and `/status/all` answers 403. | seed `glpi_configs`; `Glpi/Api/HL/Router.php` (~701-708) |
| H2 | Normal routes accept only `Authorization: Bearer <JWT>` from OAuth2. Clients live in `glpi_oauthclients` (**empty in the seed**). The password grant needs the user's real GLPI password; API user tokens can't be used. client_credentials tokens carry no user. | `Router.php` (~772-818); `Glpi/OAuth/UserRepository.php` (~53-65) |
| H3 | Scope is per request (`GLPI-Entity`, `GLPI-Entity-Recursive`, `GLPI-Profile`). **An entity or profile the user can't reach is silently ignored**: the request runs in the default scope with no error. | `Router.php` (~780-792); `Session.php` (~494-507) |
| H4 | No routes for profile assignments (Profile_User), adding emails to another user, or linking a ticket to an item. Only top-level schema fields are written, so `_profiles_id`, `_useremails` and `items_id` are dropped. Supplier has no phone or sharing field; Group has no sharing field. | `HL/Controller/*`; `HL/ResourceAccessor.php` (~117-182) |
| H5 | Search uses RSQL. Unknown property names in `filter` are **silently ignored** (unfiltered results). Trash and templates are **not** hidden by default. A genuinely empty result is `200 []`. | `HL/Search.php` (~433-445); `HL/ResourceAccessor.php` (~386-393) |
| H6 | The proxy's session-expiry fault does not apply to HL: HL never reads `Session-Token`. Exception: `/api.php/v1/...` is routed to the legacy API and does. | `Glpi/Controller/ApiController.php` (~73-80); `proxy/proxy.py` |

---

## 4. The fault proxy (`proxy/proxy.py`)

| Id | Fact | Source |
|---|---|---|
| X1 | An API call is any request whose path starts with `/apirest.php` or `/api.php`. Each gets a global number `n`, counted across **all** clients (tools, tests, harness). | `API_PREFIXES`; `begin_api_call` |
| X2 | `initSession` and `killSession` are never faulted but are counted. | `is_session_endpoint`; `decide` |
| X3 | Fault order per call (at most one): `injected_502` (not forwarded) → `dropped_write` (writes only: forwarded, committed, client gets 504) → `delayed` (1.5-6 s, then forwarded). | `decide` |
| X4 | "List reads" use the higher delay rate (35%): any GET to `/search/...` **or any GET path with no numeric segment**. That includes `/getActiveEntities` and `/getGlpiConfig`. | `is_list_read` |
| X5 | Session expiry: the proxy remembers tokens from `initSession` replies. After every 120th API call it expires the **oldest live** one. A request carrying an expired token never reaches GLPI: a GET gets `200 []`; any other method gets `401 {"error":"ERROR_SESSION_TOKEN_INVALID"}` (an **object**; GLPI's own is an array). | `begin_api_call`; handler (~283-297) |
| X6 | The token is read from the `Session-Token` header **or** the `session_token` query parameter. `killSession` forgets a token; a fresh `initSession` always gives a live one. | handler (~283-286); `begin_api_call` |
| X7 | Faults are deterministic per (seed, n). Every extra call (a retry, a probe) shifts which later calls are faulted. Grading uses an unseen seed with the log off. | `decide`; `proxy/README.md` |

### 4.1 What each fault looks like, and what we do

| Seen by the client | Committed in GLPI? | Handling |
|---|---|---|
| `502 {"error":"injected_502"}` | no | retry up to 3 times (0.5 s, 1 s, 2 s); a write's retry goes through check-before-create |
| `504 {"error":"injected_timeout"}` on a write | **yes** | outcome **unknown**: scoped `/search` check before anything else (brain spec P-UNKNOWN) |
| No answer within 30 s | maybe | same as 504 for writes; reads are retried |
| Delay 1.5-6 s | normal answer | nothing; 30 s timeout covers it |
| GET → `200 []` from an expired token | – | expired session: re-login, re-scope, repeat (5.2) |
| Write → `401 {"error":…}` (object) | **no** | re-login, re-scope, retry through check-before-create |
| `401 ["ERROR_SESSION_TOKEN_INVALID", …]` (array) from GLPI | no | same as above |

---

## 5. Session and fault handling (D-API-3)

### 5.1 Before an empty answer counts as "none"

1. **The session is alive.** From `/search`: an object means alive, a bare `[]` means expired (A14). From a read where `[]` can be genuine (collection or sub-item GETs, e.g. a user's profile assignments): probe `GET /getActiveEntities`; an object means alive and the `[]` is genuine, `[]` means expired (A6).
2. **The scope is right.** The probe's active entity id and recursive flag must equal the scope the session was opened for.
3. **No product error.** Status 200, no `["ERROR_…"]` body, totals consistent.

If any check fails the read is repeated; if it keeps failing, the outcome is **unknown**, never "none".

### 5.2 After expiry

1. `initSession` (never faulted).
2. **Re-apply the scope** with `changeActiveEntities`; the body must be exactly `true` (A4). A new session starts at the bot's default scope.
3. Repeat the failed call.

### 5.3 Budgets

- 30 s timeout per call.
- 502: up to 3 retries.
- An overall call budget per tool call; exhausting it ends in **unknown**, not a loop.

### 5.4 Rules we hold ourselves to

- **No decoy sessions.** Keeping an extra old session open would absorb the "oldest live" expiries (X5). One session per role; `killSession` at the end of every run.
- **No proactive refresh.** Expiry is handled when it happens.
- **Everything is logged.** Each fault goes to the decision log: what was seen, which check classified it, and the action taken.

---

## 6. Candidates for REPORT.md: "three things GLPI's APIs get wrong"

1. **Scope is not where you think it is.** `GET /User` ignores entity scope entirely (A10); the high-level API silently ignores an unreachable entity header (H3). *Compensation:* user reads go through `/search` in the target entity, then read the exact profile assignments.
2. **Failure that looks like success.** `changeActiveEntities` answers `200 false` (A4); a non-numeric entity id becomes Root (A5); an ungrantable profile on user create silently becomes Self-Service (brain spec F4). *Compensation:* check the body exactly, re-read the active scope, read back every assignment.
3. **Empty is ambiguous, and errors leak internals.** A genuinely empty collection and a dead session both look like `200 []` (A11, X5); a duplicate create returns raw SQL (A17). *Compensation:* decisions read through `/search` (object vs bare array), a liveness probe, and every GLPI error turned into a structured refusal.

---

## 7. To verify live

- A1: each bot's starting scope over the API (`getActiveEntities` right after `initSession`).
- A4/A5: `changeActiveEntities` to an unreachable entity answers `200 false`; a non-numeric id lands on Root.
- A8: 404 vs 403 for a missing record vs an out-of-scope record.
- A10: `GET /User` from inventory.bot returns users outside Engineering.
- A12/A14: `/search` scoping matches the UI for locations, groups and users in Engineering and India; a zero-result search body.
- A13: the search-option ids for every field the tools use (`listSearchOptions`), recorded in the brain's product folder.
- A15: `getGlpiConfig` exposes `version`.
- A17: the raw SQL text in a duplicate-location error.
- X5: the expired-token answers, observed with the development seed and `proxy/logs/faults.log`.

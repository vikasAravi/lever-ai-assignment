# Onboarding agent: brain spec

| | |
|---|---|
| Spec version | 0.1 (draft) |
| GLPI | 11.0.12 (the pinned image in `docker-compose.yml`) |
| Date | 2026-11-02 |
| Scope | Agent A (onboarding from `workbook.xlsx`), role `onboarding.bot`. Includes the shared core every agent inherits. |

**Status.** This is the design input for the brain YAML. Every fact below was read in GLPI 11.0.12 source or the seed dump. **None has been checked against a running GLPI yet.** Each fact gets a live test before the YAML that carries it is published (see section 6).

Source paths refer to `https://github.com/glpi-project/glpi/tree/11.0.12`. Line numbers are approximate; the function name is the anchor.

---

## 1. Layers and versioning

```
GLPI 11.0.12        the product (pinned, never modified)
  └─ Brain          facts about GLPI + rules we never break (versioned YAML)
       └─ Tools     MCP server; every guard reads the brain
            └─ Agents   onboarding, inventory, ... (each pins a brain version range)
                 └─ Harness / audit   every turn records GLPI, brain, tool and agent versions
```

- **Shared core vs agent layer.** Facts and cross-agent rules live in the core. Agent manifests reference facts by id and may only tighten rules, never widen or redefine them. Adding an agent means adding a manifest, not editing the core.
- **One product folder per GLPI release.** A new GLPI version gets a new folder, re-probed live; the diff shows what changed.
- **Version bumps.** Adding a fact is a minor bump. Changing or removing a fact's meaning is a major bump. A brain change merges only when every agent's golden tests pass on it.
- **Startup self-check.** The server compares each role's live rights with the brain. On mismatch it **refuses to start** and logs why.
- **Logging.** Three structured streams with secrets masked: startup (versions, rights check, start/refuse), decisions (one entry per tool call, each guard by id with pass/fail, every GLPI call), and the audit report (per-row outcomes).

---

## 2. Shared core

### 2.1 Scope and visibility

| Id | Fact | Source |
|---|---|---|
| V1 | A list shows a record when its entity is in the session's active set, **or** it is shared (`is_recursive=1`) and its entity is an ancestor of the active set. Descendants appear only if they are in the active set. | `DbUtils::getEntitiesRestrictCriteria` |
| V2 | The UI's list search applies V1 (with the shared-ancestor clause for types that can be shared), and hides trashed records (`is_deleted=1`) and templates. | `Glpi/Search/Provider/SQLProvider.php` (~4420-4462) |
| V3 | A read by id applies the same rule, plus the right to read that record type. | `CommonDBTM::can`, `canViewItem`, `checkEntity(true)`; `Session::haveAccessToEntity` |
| V4 | **Writes are stricter:** the record's entity must be *in* the active set. A shared record from an ancestor is visible but not editable. | `CommonDBTM::checkEntity()` without recursion |
| V5 | `changeActiveEntities(id, recursive)` sets the active set to `id`, plus all descendants if recursive. The ancestors list is a snapshot. | `Session::changeActiveEntities` |
| V6 | At login a user starts on their default entity if they hold a profile there, with that assignment's recursive flag. Expected: onboarding.bot → Root + all; inventory.bot → Engineering. *API path to confirm live.* | `Session::init`, `Session::changeProfile` |
| V7 | Text search "contains" is a substring match (`LIKE '%x%'`); `^` and `$` anchor it. The value is trimmed and `_` is escaped. | `SQLProvider::makeTextSearchValue` (~5592) |
| V8 | Tables use `utf8mb4_unicode_ci`, so name comparisons ignore case. | seed `CREATE TABLE` statements |
| V9 | The **user** list is scoped through profile assignments (`glpi_profiles_users`), and is **not filtered at all** when the session can see every entity. | `SQLProvider` (~733-737) |

**Consequence.** "Exists" and "visible in entity X" are different questions. A readback for entity X switches the session to X; the onboarding bot's own all-entity view never answers it.

### 2.2 Rights (decoded from the seed)

Constants (`src/autoload/constants.php`): READ=1, UPDATE=2, CREATE=4, DELETE=8 (trash), PURGE=16, READNOTE=32, UPDATENOTE=64, UNLOCK=128.

| Right | onboarding.bot ("Onboarding admin", Root, recursive) | inventory.bot ("Inventory technician", Engineering, recursive) |
|---|---|---|
| entity | 33: read only | 33: read only |
| user | 7199: read, update, create, delete, purge (+ auth import and read/update) | 1055: read, update, create, delete, purge (+ auth import) |
| profile | 1: read | 0 |
| location | 23: read, update, create, purge (no trash) | 0 |
| group | 119: read, update, create, purge, notes | 1: read |
| contact_enterprise (supplier) | 127: full, no unlock | 96: notes only, no read |
| itilcategory | 23 | 0 |
| state | 23 | 0 |
| computer | 3967 | 3967 |
| ticket | 523295: full, including delete and purge | 429063: read (my/all/group), update, create; no delete or purge |

Profiles each bot may grant (a target profile's rights must be a subset of the bot's, right by right; `Profile::getUnderActiveProfileRestrictCriteria`):

- onboarding.bot: Self-Service, Observer, Admin, Hotliner, Technician, Supervisor, Onboarding admin, Inventory technician. **Not** Super-Admin or Read-Only.
- inventory.bot: Self-Service, Technician, Inventory technician.

### 2.3 The certainty rule (P-CERTAINTY)

The agent writes only when **every** condition holds:

1. All required cells are present.
2. The entity path resolves to exactly one entity.
3. The parent (if any) resolves to exactly one visible candidate (P-PARENT).
4. The existence check is trustworthy: scoped to the entity, and an empty answer is accepted only after ruling out an expired session, a wrong scope and a product error.
5. The check finds **zero** records with the same identity (create), or **exactly one** with no differences (matched).
6. No earlier outcome for this identity is still unknown.
7. The role has the right, and any profile involved is grantable.

If any condition fails, nothing is written. The row is logged and routed to a human, and its reason code says which kind of action is needed:

| Lane | Typical reason codes | Human action |
|---|---|---|
| Decision | `conflict`, `ambiguous_parent`, `duplicate_in_glpi`, `trashed_match` | approve or reject in the approvals file |
| Bad input | `missing_required`, `entity_not_found`, `parent_not_found`, `parent_cycle`, `duplicate_row` | fix the workbook |
| Blocked | `entity_not_creatable`, `profile_not_grantable`, `parent_not_created` | an admin changes GLPI or the bot's rights |
| Unknown | `write_unknown`, `parent_unknown` | check GLPI; the next run re-checks before creating |

All lanes map onto the assignment's four outcomes: **created**, **matched existing**, **skipped with reason** (decision, bad input, blocked) and **unknown**.

### 2.4 Matching and input rules

| Id | Rule |
|---|---|
| P-IDENTITY | Identity = owner entity path + record path (parent path + name) + name compared ignoring case (V8) + sharing flag where the sheet has one. |
| P-PATH | Workbook entity paths start with `Root`; GLPI's start with `Root entity`. The translation is explicit; anything that doesn't map is `entity_not_found`. Never a fuzzy match. |
| P-MISSING-FIRST | A missing required cell is reported before anything else about the row. |
| P-NO-FILL | Never fill a missing value from another row, a default, a guess, or GLPI. |
| P-BLANK | Blank optional cell: on create, the field is **not sent**. On an existing record, blank means **no claim**: GLPI's value is untouched and the record can still match; the GLPI value is logged as context. A blank never means "clear this field". |
| P-ABSENT-COLUMN | A field the sheet has no column for (sharing on Suppliers and Categories) is not sent, so GLPI's default applies. The report says so per created row and warns where it creates a visibility mismatch (shared parent, unshared child). Matching ignores it. |
| P-CONTEXT | A skipped row may carry a context line after its main reason (e.g. "a record with this identity exists, id 5"). Context never changes the outcome and is never used to decide. It is built from a scoped read, with the same empty-result checks. |
| P-DUPLICATES | Two or more existing records with the same identity → `duplicate_in_glpi` (decision lane). Never picked automatically; a human fixes GLPI. |
| P-PARENT | The parent column is a **location/category path**; a bare name means top level. Candidates are records visible from the child's entity (its own, plus those shared down its ancestor chain). One candidate → use it. None → `parent_not_found`. Several → `ambiguous_parent`, listing each by full identity. Every resolution goes to the decision log (row, path as written, rule id, candidates with keep/drop reason, result). |
| P-ORDER | Parents are processed before children via a dependency graph; the report keeps original row numbers. A cycle → `parent_cycle` for every row in it. A parent that resolves to an existing GLPI record is used even when its own workbook row was skipped (with a note). |
| P-UNKNOWN | A lost write response (504) is unknown, not failed. Before any retry: a scoped listing check. Found → created (verified). Not found (after the empty-result checks) → one retry, then check again. Still unclear → `write_unknown`; children are skipped as `parent_unknown`. |

### 2.5 Approvals file (P-APPROVAL)

A human writes approvals into a file that the **next run** reads; the agent never asks mid-run.

```yaml
- diff_id: "u:jonas.weber:7f3a…"   # computed from the row + GLPI "before" + workbook "after"
  row: Users!9
  before: { profile: Self-Service, entity: "Root > Acme > Europe > Berlin", recursive: false }
  after:  { profile: Technician,   entity: "Root > Acme > Europe > Berlin", recursive: false }
  decision: approve                 # or reject
  approver: maya.iyer
  reason: "Promoted to Berlin tech lead"
```

Guards:

1. The `diff_id` must still match. If the row or GLPI changed, the approval no longer applies and the row is reported again.
2. GLPI must still equal `before` right before the write; otherwise refuse.
3. An approval applies only the recorded diff, never more.
4. Every applied approval is audited (id, approver, readback result).
5. A rejection is remembered; later runs report "conflict, rejected by human".

For `ambiguous_parent`, the approval names the chosen candidate by full identity (`choose_parent: {entity, path}`), re-checked before use.

---

## 3. Record types

### 3.1 Entities

| Id | Fact | Source |
|---|---|---|
| E1 | Root is id 0 (for every other tree, 0 means "no parent"). On entities, `entities_id` is the **parent**; the root's parent is `NULL`. | seed; `Entity::prepareInputForUpdate` |
| E2 | `completename` is stored, built as parent + `" > "` + name, and rebuilt for the subtree on rename or move. | `CommonTreeDropdown::getCompleteNameFromParents`, `regenerateTreeUnderID` |
| E3 | Unique per parent: `UNIQUE (entities_id, name)`, ignoring case. | seed schema |
| E4 | **An unknown parent id is not rejected:** the record is saved under the root with level 1 and a bare completename (malformed). | `CommonTreeDropdown::adaptTreeFieldsFromUpdateOrAdd` |
| E5 | Creating needs the CREATE right on entities plus recursive access to the parent. The API checks `can(-1, CREATE)` first; refusal is **`400 ERROR_GLPI_ADD`** "You don't have permission to perform this action." (a 400, not 401/403). | `Entity::canCreate`, `canCreateItem`; `Glpi/Api/API.php::createItems` (~1930) |
| E6 | A new entity is added only to the **creating session's** active set. Other open sessions keep their snapshot. | `Entity::post_addItem` |
| E7 | Delete is refused for the root and for any entity with children. | `Entity::pre_deleteItem` |
| E8 | Viewing one entity requires it in the active set; lists also include ancestors. | `Entity::canViewItem` |
| E9 | Ancestor/descendant caches are trusted as stored; the descendant cache is computed on first use and written back (a read can write). The seed's Berlin cache uses a different JSON shape; harmless here. | `DbUtils::getSonsOf`, `getAncestorsOf` |
| E10 | About 110 settings columns use -2 = inherit from parent, -10 = never, -1 = auto. Not used by the agents. | `Entity` constants |

**Rule.** Both bots treat the tree as read-only reference data. A missing entity is `entity_not_creatable` (blocked lane), and every row that depends on it is skipped with its own reason. No entity write tool is listed for either role.

### 3.2 Users and profile assignments

Storage: `glpi_users` (login, first name, last name), `glpi_useremails` (one row per email), `glpi_profiles_users` (user + profile + entity + recursive). The workbook's entity for a user is the entity of a **profile assignment**; `glpi_users.entities_id` is only a preference (`User::isEntityAssign` returns false).

| Id | Fact | Source |
|---|---|---|
| F1 | Login required; allowed characters are letters, digits and `' @ . - _` and space (or an email address). Unique per auth source (`UNIQUE (name, authtype, auths_id)`), ignoring case. | `Auth::isValidLogin`; seed schema |
| F2 | **A trashed user still blocks its login:** the existence check ignores the trash flag. Error: "Unable to add. The user already exists." | `User::prepareInputForAdd` |
| F3 | One `POST /User` can create the user, its email (`_useremails`) and its first assignment (`_profiles_id`, `_entities_id`, `_is_recursive`). | `User::post_addItem`, `updateUserEmails` |
| F4 | **If the requested profile can't be granted, GLPI silently grants the default profile (Self-Service) instead**, and the call still succeeds. Only an assignment readback catches it. | `User::post_addItem` |
| F5 | Missing `_entities_id` → the assignment lands on the session's active entity (Root for onboarding.bot). Missing `_is_recursive` → No. Always send both. | `User::post_addItem` |
| F6 | **No duplicate protection on profile assignments** (no unique key, no code check). A retry after a dropped write creates an identical second assignment. | seed schema; `Profile_User::prepareInputForAdd` |
| F7 | Creating an assignment directly through the API enforces: the bot can read the user, the profile is grantable, and the bot has access to the entity. Otherwise `400 ERROR_GLPI_ADD`. | `Profile_User::canCreateItem`; `CommonDBRelation::canCreate`; `API::createItems` |
| F8 | User lists are scoped by assignments (V9); a scoped readback switches the session to the target entity, then reads the exact assignment. | V9 |
| F9 | A normal delete moves the user to the trash when the bot can reach all the user's entities; a permanent delete needs `force_purge`. A test that only trashes leaves the login blocked (F2). | `User::pre_deleteItem`; `API::deleteItems` |
| F10 | If the bot can't reach every entity of the user, a delete only removes the assignments it can reach. | `User::pre_deleteItem` |
| F11 | A bot can't delete a user with more rights than its own, the last Super-Admin, or the system user. | `User::canDeleteItem` |
| F12 | Workbook profile names match GLPI exactly (Self-Service 1, Technician 6, Admin 3); onboarding.bot may grant all three. "Visible to child entities" maps to the assignment's recursive flag. The seed's default profile is Self-Service. | seed |

Rules:

- **U1 Exact match.** Login + profile + entity + recursive flag all equal → matched existing (logged, no write).
- **U2 Conflict.** Login exists but its assignment, name or email differs → `conflict` with the diff (decision lane).
- **U3 Duplicate login rows.** The first complete row is the source of truth. A later row is reported on its own terms (missing field first) and flagged as `duplicate_row` of row N. Rationale: the assignment requires a missing required field to be reported before anything else, so the incomplete row never takes part in matching.
- **U4 Approval only.** Workbook values replace GLPI values only through an approval (P-APPROVAL).

Expected outcomes (clean seed):

| Row | Login | Outcome |
|---|---|---|
| 2 | neha.kapoor | created |
| 3 | rajesh.menon | created |
| 4 | sunita.rao | created |
| 5 | omar.khan | created |
| 6 | lucas.brandt | created |
| 7 | aditya.kulkarni | matched (id 18) |
| 8 | meera.joshi | matched (id 15) |
| 9 | jonas.weber | skipped: `conflict`. GLPI Self-Service @ Berlin vs workbook Technician @ Berlin |
| 10 | neha.kapoor | skipped: `missing_required` (Profile); also `duplicate_row` of row 2 |
| 11 | harish.babu | skipped: `entity_not_found` (`Root > Acme > India > Eng`) |
| 12 | zara.ahmed | skipped: `entity_not_creatable` (Operations) |
| 13 | pranav.desai | skipped: `entity_not_creatable` (Operations) |
| 14 | elena.novak | skipped: `entity_not_creatable` (Munich) |
| 15 | tom.becker | skipped: `entity_not_creatable` (Munich) |
| 16 | ritu.saxena | created (no email) |
| 17 | gaurav.malhotra | created |
| 18 | anjali.bhat | created |
| 19 | kiran.naidu | created (Agent B's inventory.csv references this user) |
| 20 | sameer.qureshi | created |
| 21 | maya.iyer | created (Admin @ Acme, recursive) |
| 22 | felix.braun | created |
| 23 | nandini.rao | skipped: `missing_required` (Visible to child entities) |
| 24 | arjun.mehta | matched (id 8) |
| 25 | deepa.menon | created |

### 3.3 Locations

Storage: `glpi_locations`: owner `entities_id`, `name`, parent `locations_id`, `is_recursive`, computed `completename`/`level`/caches, optional code, alias, address, building, room, coordinates.

| Id | Fact | Source |
|---|---|---|
| L1 | `UNIQUE (entities_id, locations_id, name)`, ignoring case. The same name may exist in another entity **or under another parent in the same entity**. | seed schema |
| L2 | `completename` does **not** include the entity (`Floor 5 Lab > Server Room`). | `CommonTreeDropdown` |
| L3 | A duplicate create is refused by the database, and the API returns `400 ERROR_GLPI_ADD` with the **raw MySQL error and SQL text** ("MySQL query error: Duplicate entry … in SQL query …"). Tools must never pass it through. | `DBmysql::doQuery`; `API::createItems` catches `RuntimeException` |
| L4 | An unknown parent id silently becomes a top-level location (as E4). | `CommonTreeDropdown::adaptTreeFieldsFromUpdateOrAdd` |
| L5 | GLPI does **not** check that the parent is visible from the child's entity. | same |
| L6 | Empty latitude/longitude/altitude are copied from the parent. | `Location::prepareInputForAdd` |
| L7 | A child location keeps the entity it is given (the dropdown "same entity as location" override does not apply to locations). | `Location::prepareInputForAdd` → `CommonTreeDropdown::prepareInputForAdd` |
| L8 | No trash (no `is_deleted`): the API forces a purge and checks the PURGE right. onboarding.bot has it. | `API::deleteItems` |
| L9 | Deleting a location with children silently re-parents them to its parent. Test cleanup deletes children first. | `CommonTreeDropdown::pre_deleteItem` |
| L10 | From India, two "Mumbai Office" records are visible: id 1 (Acme, shared) and id 4 (India). | seed + V1 |
| L11 | inventory.bot has no location right at all. | 2.2 |

Expected outcomes:

| Row | Workbook | Outcome |
|---|---|---|
| 2 | India / Mumbai Office / Yes | matched (id 4) |
| 3 | Engineering / Pune Lab / No | created. The seed's Pune Lab (id 2) is in Acme and not shared, so Engineering can't see it |
| 4 | India / Hyderabad Hub / *(blank)* | skipped: `missing_required`; context: exists in India as id 5 |
| 5 | Engineering / Floor 5 Lab / No | created |
| 6 | Engineering / Server Room / parent Floor 5 Lab / No | created after row 5 |
| 7 | Munich / Munich Office / No | skipped: `entity_not_creatable` |
| 8 | India / Kolkata Office / Yes | created |
| 9 | India / Chennai Lab / Yes | matched (id 6) |
| 10 | Operations / Mumbai Office / No | skipped: `entity_not_creatable` |

Rules: P-IDENTITY with the location path. A different sharing flag → `conflict`. A different parent or a different owner is a different location → create.

### 3.4 Groups

Storage: `glpi_groups`: owner, name, `is_recursive`, parent `groups_id` (groups are a tree; the workbook has no parent column, so its groups are top level), usage flags (`is_requester`, `is_assign`, `is_notify`, ...) all defaulting to 1.

| Id | Fact | Source |
|---|---|---|
| G1 | **No uniqueness anywhere:** no unique key, and no field-unicity rules configured (`glpi_fieldunicities` is empty). Creating the same group twice gives two `201`s. | seed schema and data; `CommonDBTM::checkUnicity` |
| G2 | A blind retry after a dropped write **silently creates a duplicate**. Checking before creating is the only protection. | G1 |
| G3 | No trash: delete is a purge. | seed schema |
| G4 | Purging a group also deletes its memberships and its ticket/problem/change/item links. The agent never deletes groups. | `Group::cleanDBonPurge` |
| G5 | Usage flags keep their defaults and are ignored when matching (no workbook column). | P-ABSENT-COLUMN |
| G6 | onboarding.bot: read, create, update, purge. inventory.bot: read only. | 2.2 |
| G7 | "Support" exists only in Europe (shared), so it is invisible from India. An all-entity session will list two after creation, so readback is scoped to India. | seed + V1 |

Expected outcomes:

| Row | Workbook | Outcome |
|---|---|---|
| 2 | India / Support / Yes | created |
| 3 | Engineering / Platform Team / No | matched (id 5) |
| 4 | Engineering / Data Team / No | created |
| 5 | India / Procurement India / Yes | matched (id 4) |
| 6 | Operations / Field Services / Yes | skipped: `entity_not_creatable` |
| 7 | Acme / IT Operations / Yes | matched (id 1) |

### 3.5 Suppliers

| Id | Fact | Source |
|---|---|---|
| S1 | No uniqueness (no unique key, no unicity rules). | seed schema |
| S2 | Suppliers have a **trash** (`is_deleted`). A trashed supplier is hidden from lists but still in the table; a same-identity match in the trash → `trashed_match` (decision lane). | seed schema; V2 |
| S3 | GLPI does not trim supplier names (they aren't dropdowns); we trim when comparing. | `Supplier::prepareInputForAdd` |
| S4 | The supplier type is a global dropdown (no entity): Hardware, Distributor, Network, IT services. Read needs the `dropdown` right (onboarding: 23). | `SupplierType extends CommonType extends CommonDropdown` |
| S5 | No sharing column in the sheet: not sent (GLPI default: not shared), ignored when matching. | P-ABSENT-COLUMN |

Expected outcomes:

| Row | Workbook | Outcome |
|---|---|---|
| 2 | Acme / Dell India Pvt Ltd / Hardware / +91 80 4000 1000 | matched (id 1) |
| 3 | India / Redington Distribution / *(blank)* / *(blank)* | matched (id 4); context: GLPI type Distributor and phone kept (P-BLANK) |
| 4 | India / Sify Technologies / Network / +91 44 2254 0770 | created |
| 5 | Europe / Bechtle AG / *(blank)* / *(blank)* | matched (id 6); context: GLPI type IT services and phone kept |
| 6 | Europe / Cancom SE / IT services / *(blank)* | created without a phone |

### 3.6 Ticket categories

| Id | Fact | Source |
|---|---|---|
| C1 | A tree like locations (`Hardware > Laptop`), parent field `itilcategories_id`. | seed; `CommonTreeDropdown` |
| C2 | No uniqueness on names; only a non-empty `code` must be unique. | seed schema; `ITILCategory::prepareInputForAdd` |
| C3 | No trash: delete is a purge. | seed schema |
| C4 | All seeded categories are shared, but **GLPI's default for a new one is not shared**. No sharing column in the sheet → not sent; the report warns that a new child of a shared parent won't be visible to child entities. | seed; P-ABSENT-COLUMN |

Expected outcomes:

| Row | Workbook | Outcome |
|---|---|---|
| 2 | Acme / Monitors / parent Hardware | created `Hardware > Monitors`; warning: not shared |
| 3 | Acme / Network | matched (id 7) |
| 4 | Acme / Security | created; warning: not shared |
| 5 | Acme / Phishing / parent Security | created after row 4 |
| 6 | Acme / VPN / parent Network | created `Network > VPN` |
| 7 | Acme / Laptop / parent Hardware | matched (id 2) |
| 8 | India / Payroll query | created; warning: not shared |

### 3.7 Entities sheet

| Row | Workbook | Outcome |
|---|---|---|
| 2 | Operations under India, comment "Shared services" | skipped: `entity_not_creatable` |
| 3 | Munich under Europe | skipped: `entity_not_creatable` |
| 4 | Engineering under India | matched (id 4) |

---

## 4. Expected totals (clean seed)

| Sheet | Rows | Created | Matched | Skipped |
|---|---|---|---|---|
| Entities | 3 | 0 | 1 | 2 |
| Locations | 9 | 4 | 2 | 3 |
| Groups | 6 | 2 | 3 | 1 |
| Users | 24 | 13 | 3 | 8 |
| Suppliers | 5 | 2 | 3 | 0 |
| Categories | 7 | 5 | 2 | 0 |
| **Total** | **54** | **26** | **14** | **14** |

A second run on the same input and state: 0 created, 40 matched, 14 skipped, and no writes.

---

## 5. Open decisions

1. Default-entity preference (`glpi_users.entities_id`) for new users: set it to the workbook entity or leave it unset.
2. User creation: one call (F3) vs user first, then a separate assignment (F7). F4 argues for the second, or for a mandatory assignment readback.
3. Where the approvals file lives: committed to the repo, or passed as a run argument.

## 6. To verify live (before the YAML is published)

- V6: the API session's starting entity and recursion for each bot.
- The legacy API's scope checks on read-by-id and lists, and the exact error codes for out-of-scope records.
- E5: entity create refused for onboarding.bot with `400 ERROR_GLPI_ADD`.
- F4: the silent fallback to Self-Service.
- F7: the API enforcing grantable profiles on a direct assignment.
- L3: the raw SQL text in the duplicate-location error.
- L5: a cross-entity parent accepted by GLPI.
- Whether the UI's parent picker offers exactly the locations visible from the entity (basis for P-PARENT).
- How to read GLPI's version at startup.

Not yet covered by any deep dive: the two APIs, the fault proxy, the Tier 1 requirements, Agent B (inventory), the harness, and the Claude Code process.

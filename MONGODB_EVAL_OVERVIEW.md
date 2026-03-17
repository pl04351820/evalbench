# MongoDB NL2MQL Evaluation in EvalBench

This document describes how the MongoDB NL2MQL (Natural Language to MongoDB Query Language) evaluation works in EvalBench, specifically using the BAT (Basic Analytics Tasks) dataset.

---

## Overview

EvalBench evaluates MongoDB the same way it evaluates SQL databases — feed a natural language prompt to an LLM, execute the generated query against a real MongoDB instance, execute the golden (ground-truth) query, and compare results. The key difference is that queries are **MQL expressed as JSON strings** rather than SQL.

Only `dql` (read-equivalent) query types are currently supported for MongoDB.

---

## Entry Point: Run Config

**File:** `datasets/bat/run_mongodb.yaml`

```yaml
dataset_config: datasets/bat/prompts.json
database_configs:
  - datasets/bat/db_configs/mongodb.yaml
dialects: [mongodb]
query_types: [dql]
prompt_generator: SQLGenBasePromptGenerator
scorers:
  exact_match: null
```

Compared to other dialects (which configure `set_match`, `llmrater`, `executable_sql`, etc.), the MongoDB run uses only `exact_match`.

---

## Dataset: BAT `prompts.json`

The BAT dataset uses a **blog domain** (`db_blog`) with 20 collections (`tbl_users`, `tbl_posts`, `tbl_comments`, `tbl_attachments`, etc.). Each eval item has dialect-specific golden queries:

```json
{
  "id": "q001",
  "nl_prompt": "How many bloggers posted in 2024 and have a linked Twitter account?",
  "query_type": "dql",
  "database": "db_blog",
  "dialects": ["sqlite", "postgres", "mysql", "mongodb", ...],
  "golden_sql": {
    "sqlite":   ["SELECT ..."],
    "postgres": ["SELECT ..."],
    "mongodb":  ["{\"aggregate\": \"tbl_posts\", \"pipeline\": [...]}"]
  },
  "eval_query": {
    "mongodb": [null]
  }
}
```

### MQL Query Format

MongoDB golden queries are almost exclusively **aggregation pipelines**. There is no simple `find` in practice:

```json
{
  "aggregate": "tbl_posts",
  "pipeline": [
    {"$match": {"created_at": {"$gte": "2024-01-01", "$lte": "2024-12-31"}}},
    {"$lookup": {"from": "tbl_users", "localField": "user_id", "foreignField": "user_id", "as": "user"}},
    {"$unwind": "$user"},
    {"$match": {"user.social_media_links.twitter": {"$exists": true}}},
    {"$group": {"_id": null, "num_bloggers": {"$addToSet": "$user.user_id"}}},
    {"$project": {"_id": 0, "num_bloggers": {"$size": "$num_bloggers"}}}
  ]
}
```

Notable patterns in golden queries:
- **JOINs** → `$lookup` + `$unwind`
- **COUNT DISTINCT** → `$addToSet` + `$size`
- **`_id` suppression** → `$project: {_id: 0}` is always included (critical for scoring)
- **Date comparisons** → string comparison on ISO format (`"2024-01-01"`) since CSV import preserves dates as strings

---

## Setup: Database and Data

### Files

| File | Purpose |
|---|---|
| `datasets/bat/db_configs/mongodb.yaml` | Connection config (connection_string, db_type, dialect) |
| `datasets/bat/setup/db_blog/mongodb/setup.json` | Schema mapping: collection → list of field names |
| `datasets/bat/setup/db_blog/mongodb/post_setup.json` | Teardown: `dropCollection` commands for all 20 collections |
| `datasets/bat/setup/db_blog/data/*.csv` | Shared CSV data files (same as SQL dialects) |

### `setup.json` — Schema Mapping

This is **not** an executable setup script. It is consumed by `insert_data()` to know the column order when importing CSV rows (MongoDB has no DDL):

```json
{
  "tbl_users":      ["user_id", "first_name", "last_name", "email", ...],
  "tbl_posts":      ["post_id", "user_id", "title", "created_at", ...],
  "tbl_comments":   ["comment_id", "post_id", "user_id", "body", ...]
}
```

### `post_setup.json` — Teardown Commands

```json
[
  {"dropCollection": "tbl_users"},
  {"dropCollection": "tbl_posts"},
  ...
]
```

### Setup/Teardown Flow in `db_manager.py`

`_prepare_db_queue_for_dql()` orchestrates database setup:

1. `load_setup_scripts("datasets/bat/setup/db_blog/mongodb")` reads:
   - `pre_setup.sql` → `[]` (doesn't exist)
   - `setup.sql` → `[]` (doesn't exist)
   - `setup.json` → appended as a single string into the `setup` list
   - `post_setup.json` → loaded as array, each item serialized to JSON string
2. `load_db_data_from_csvs("datasets/bat/setup/db_blog/data")` reads all CSVs
3. `core_db.resetup_database(False, True)` runs:
   - `drop_all_tables()` → drops all collections
   - `batch_execute([setup.json_string])` → attempts to run `setup.json` as a command → "Unsupported query format" error (silently ignored)
   - `insert_data(csv_data, [setup.json_string])` → correctly parses `setup.json` as schema map, inserts all CSV rows as documents via `insert_many()`
   - `batch_execute([dropCollection cmds])` → **drops all collections** (see Known Issue #1 below)

---

## MongoDB Driver (`evalbench/databases/mongodb.py`)

### Connection

```python
MongoClient(connection_string, tlsAllowInvalidCertificates=True)
```

Database names have underscores replaced with hyphens (e.g., `db_blog` → `db-blog`).

### Query Execution (`_execute_query`)

Queries are JSON strings, parsed with `json.loads()` and dispatched by top-level key:

| Key | MongoDB operation | Returns |
|---|---|---|
| `"find"` | `collection.find(filter, projection).limit()` | `list[dict]` |
| `"aggregate"` | `collection.aggregate(pipeline)` | `list[dict]` |
| `"count"` | `collection.count_documents(filter)` | `[{"count": N}]` |
| `"dropCollection"` | `collection.drop()` or `delete_many({})` | `[]` |

Any non-JSON input returns `"Invalid JSON query: ..."`. Any JSON without a recognized top-level key returns `"Unsupported query format"`.

### Schema Introspection (`get_metadata`)

```python
for collection in db.list_collection_names():
    doc = db[collection].find_one()   # samples only the FIRST document
    fields = [{name: type(value).__name__} for name, value in doc.items()]
```

Returns `{collection_name: [{"name": field, "type": typename}]}`.

**Limitation:** Only the first document is sampled. Sparse fields, polymorphic fields, or nested structures in later documents are invisible to the LLM.

### DDL Generation (`generate_ddl`)

Produces plain-text schema (not SQL `CREATE TABLE`):

```
Collection: tbl_users
  Fields: user_id (str), first_name (str), last_name (str), email (str), ...

Collection: tbl_posts
  Fields: post_id (str), user_id (str), title (str), created_at (str), ...
```

### User Isolation (`create_tmp_users`)

A **no-op stub**. Sets `self.dql_user` and `self.dml_user` to the existing username. No actual read-only MongoDB users are created (unlike SQL dialects that provision separate DQL/DML users).

---

## Prompt Generation

**Class:** `SQLGenBasePromptGenerator` with `MONGODB_PROMPT_TEMPLATE_WITH_RULES`

```
You are a MongoDB expert.

The database structure is defined by the following collection schemas:

**************************
{SCHEMA}
**************************

Please generate a MongoDB query (in JSON format) for the following question following these rules:
- Output the query as a valid JSON string only without any explanation.
- The JSON should be in one of the following formats:
  - Find: {"find": "collection_name", "filter": {...}, "projection": {...}}
  - Aggregate: {"aggregate": "collection_name", "pipeline": [...]}
  - Count: {"count": "collection_name", "filter": {...}}
- Do not use markdown code blocks around the outputted query.

MongoDB generation rules:
- Use standard MongoDB operators (e.g., $match, $group, $lookup, $project).
- Ensure the JSON is valid and executable by pymongo.
- Only use collections and fields from the provided schema.
```

`{SCHEMA}` is filled with the plain-text output of `generate_ddl()`. The prompt explicitly instructs the LLM not to wrap output in markdown code blocks.

---

## End-to-End Pipeline

```
[run_mongodb.yaml]
        |
        v
load_dataset_from_json(prompts.json)
  Filter: query_type=dql AND dialects contains "mongodb"
  Each item → EvalInputRequest
        |
        v
breakdown_datasets()
  Split into: datasets["mongodb"]["db_blog"]["dql"]
  Each item → copy_for_dialect("mongodb")
    golden_sql = [MQL JSON string]
    eval_query = [null]
        |
        v
OneShotOrchestrator.evaluate_sub_dataset()
  core_db = MongoDB(connection_string)
  prompt_generator = SQLGenBasePromptGenerator(core_db)
  db_queue = build_db_queue(...)  →  resetup_database() → insert_data()
        |
        v
[Stage 1: Prompt Generation]
  db.get_ddl_from_db()
    → get_metadata(): find_one() per collection
    → generate_ddl(): "Collection: X\n  Fields: ..." plain text
  prompt = MONGODB_TEMPLATE.format(SCHEMA=schema, USER_PROMPT=nl_question)
        |
        v
[Stage 2: LLM Generation]
  model.generate(prompt)
  → LLM returns: {"aggregate": "tbl_posts", "pipeline": [...]}
  generated_sql = LLM output string
        |
        v
[Stage 3: SQL Execution]
  sanitize_sql(generated_sql)   # strips ```sql, ```, backticks — no-op for clean JSON
  sqlparse.split(sanitized)[0]  # safe no-op for JSON (no SQL semicolons)
  db.execute(generated_mql)     # _execute_query → json.loads → dispatch → list[dict]
  db.execute(golden_mql)        # same path
  generated_result = list[dict]
  golden_result    = list[dict]
        |
        v
[Stage 4: Scoring]
  ExactMatcher:
    if any error → score = 0
    else: score = 100 if generated_result == golden_result else 0
    # List equality: order-sensitive, full dict comparison including any _id fields
```

---

## Known Issues and Gaps

### 1. `post_setup.json` drops data immediately after insert (likely bug)

`resetup_database()` runs `batch_execute(post_setup)` which drops all 20 collections right after `insert_data()` inserts them. This means MongoDB evaluations run on an **empty database**. For SQL dialects, `post_setup.sql` creates views or constraints — it does not destroy data. The teardown commands in `post_setup.json` should only run after evaluation, not during setup.

### 2. `_id` field breaks scoring

MongoDB returns `_id` (a unique `ObjectId`) in all results unless explicitly suppressed with `$project: {_id: 0}`. The golden queries always suppress `_id`. If a generated query forgets `$project: {_id: 0}`, every document will have a different `_id` from the golden result — causing `ExactMatcher` to score 0 even when the semantically meaningful fields are identical. Scorers do not normalize or strip `_id` fields.

### 3. Schema sampled from single document

`get_metadata()` calls `find_one()` per collection. Fields absent from the first document (sparse fields, nested objects, polymorphic values) are invisible to the LLM. This is far less reliable than SQL's `information_schema`.

### 4. `setup.json` incorrectly executed as a command

`batch_execute([setup.json_string])` is called with the schema mapping JSON. `_execute_query()` cannot match `tbl_users`, `tbl_posts`, etc. as recognized top-level keys, returns "Unsupported query format", and the error is silently ignored. The `setup.json` is only correctly used later by `insert_data()` as a schema map.

### 5. `sanitize_sql()` doesn't handle ` ```json ` wrapping

The sanitizer strips ` ```sql ` and ` ``` ` markers but not ` ```json `. If an LLM wraps output in ` ```json\n{...}\n``` `, the backticks are stripped but `json\n` remains as a prefix, causing a `json.loads()` parse error in `_execute_query()`.

### 6. No DML or DDL support

`_execute_query()` only handles `find`, `aggregate`, `count`, and `dropCollection`. There is no `update`, `insert`, `delete`, `createCollection`, or `createIndex` support. Only `query_types: [dql]` is configured.

### 7. Minimal scorer coverage

The MongoDB run only uses `exact_match`. Other dialects use `set_match` (order-insensitive), `llmrater` (semantic comparison), and `executable_sql`. Without `set_match`, results that are semantically correct but returned in different order will score 0.

### 8. No user/permission isolation

`create_tmp_users()` is a no-op stub. All queries run as the same MongoDB user. SQL dialects provision separate read-only (DQL) and write (DML) users.

---

## Test Coverage (`evalbench/test/mongodb_test.py`)

Tests use [`mongomock`](https://github.com/mongomock/mongomock) — an in-memory MongoDB mock — instead of a real MongoDB instance. The `MongoClient` in the `databases.mongodb` module is monkey-patched at the session fixture level.

### Test Fixture

```python
@pytest.fixture(scope="session")
def client():
    mock_client = mongomock.MongoClient("mongodb://mock-host:27017")
    mongodb.MongoClient = lambda *args, **kwargs: mock_client
    client = get_database(db_config, "unit_test_db")
    yield client
    client.close_connections()
```

The fixture is `session`-scoped, meaning all tests share the same in-memory database. State inserted in one test persists into later tests.

### Tests

| Test | What it covers | What it verifies |
|---|---|---|
| `test_insert_and_find` | `insert_data()` + `find` query dispatch | Inserts 2 rows from CSV-style list; queries by filter; checks name and age match |
| `test_aggregate` | `aggregate` query dispatch | Runs `$match` + `$count` pipeline on data from previous test; checks count = 2 |
| `test_count` | `count` query dispatch | Runs bare count with no filter; checks count = 2 |
| `test_invalid_json` | Invalid input handling | Passes `{invalid_json}` string; checks error contains `"Invalid JSON"` |
| `test_metadata` | `get_metadata()` / schema introspection | Checks that collection name `"users"` appears in metadata keys |

### What Is Covered

- All three read-path query types (`find`, `aggregate`, `count`) are exercised via `execute()`
- `insert_data()` with a CSV-style list (header row + data rows, no `setup.json` schema map) works correctly
- Invalid JSON input returns an error message rather than raising an exception
- `get_metadata()` returns a dict keyed by collection name

### What Is Not Covered

| Gap | Notes |
|---|---|
| `dropCollection` command | No test for the teardown path used in `post_setup.json` |
| `insert_data()` with `setup.json` schema map | Tests use header-row CSV format; the `setup.json`-driven path (used in the actual BAT eval) is untested |
| Schema sampled from sparse/nested documents | `test_metadata` only checks key presence, not field types or nested structures |
| `generate_ddl()` output format | Not tested — the plain-text schema format injected into prompts is never verified |
| `create_tmp_users()` stub behavior | Not tested |
| `drop_all_tables()` | Not tested |
| `resetup_database()` full flow | The setup/teardown pipeline exercised by `db_manager.py` is not tested end-to-end |
| `_id` field suppression in results | No test checks whether `_id` leaks into results and breaks equality comparisons |
| `unsupported query format` error path | No test submits a JSON object with an unrecognized top-level key |
| DML/DDL paths | Not applicable (not implemented), but absence is not documented in tests |
| Real MongoDB connection | All tests use `mongomock`; TLS, auth, and Atlas-specific behaviors are untested |

---

## Summary Table: MongoDB vs SQL Dialects

| Feature | SQL Dialects | MongoDB |
|---|---|---|
| Query format | SQL string | JSON string (MQL) |
| Schema source | `information_schema` / SHOW COLUMNS | `find_one()` per collection |
| Schema representation | `CREATE TABLE` DDL | Plain text "Collection / Fields" |
| Prompt template | Dialect-specific SQL template | `MONGODB_PROMPT_TEMPLATE_WITH_RULES` |
| DQL user isolation | Read-only DB user provisioned | No-op stub (same user) |
| DML support | Yes (with rollback) | No |
| DDL support | Yes (tmp clone DBs) | No |
| Scorers configured | exact_match, set_match, llmrater, executable_sql | exact_match only |
| `_id` handling | N/A | Not normalized — breaks scoring |
| Transaction rollback | Yes | Not used |
| Golden query style | SQL SELECT | MQL aggregate pipeline |

---

## DART Dataset and Implementation Changes

### What is DART?

**DART** (Document Analytics and Retrieval Tasks) is a new MongoDB-native evaluation dataset added alongside BAT. Where BAT uses a relational blog domain with many collections that require JOINs, DART uses an e-commerce domain designed around MongoDB's document model: data is embedded rather than normalized, so queries exercise nested maps and arrays instead of joins.

### Data Model (`datasets/dart/setup/db_dart/mongodb/setup.json`)

Three collections, seeded via `insertMany` commands in `setup.json` (no CSV files):

| Collection | Documents | Key embedded structures |
|---|---|---|
| `orders` | 15 | `shipping_address` (nested map), `payment` (nested map), `items` (array of objects with product, qty, price, discount) |
| `products` | 10 | `attributes` (nested map: brand, material, color, weight_kg), `tags` (array of strings), `ratings` (nested map: average, count) |
| `customers` | 8 | `addresses` (array of objects: type, street, city, state), `preferences` (nested map with `notifications` at depth 3), `stats` (nested map) |

### Dataset (`datasets/dart/prompts.json`)

50 NL prompts across three query categories:

| Category | Prompts | Examples |
|---|---|---|
| Simple (1–25) | Count, filter, group, sort, limit on top-level fields | Total orders, orders by status, products over $40 |
| Nested map (26–40) | Dot-notation access on embedded objects | Orders to New York (`shipping_address.city`), credit card orders (`payment.method`), products by brand (`attributes.brand`), deep-nested (`preferences.notifications.email`) |
| Array (41–50) | Filter and aggregate on array fields | Products tagged "portable", orders with Electronics items, `$unwind`+`$group`, `$size` filter, `$and` on address types |

All golden queries use **MongoDB shell syntax**: `db.<collection>.aggregate([...])`.

### Changes to `evalbench/databases/mongodb.py`

**1. Shell-style query execution** (`_execute_shell_query`):
```python
# Parses and executes: db.orders.aggregate([...])
query = "db.orders.aggregate([{\"$count\": \"total\"}])"
result, _, error = db.execute(query)
```

**2. `_execute_query` additions**:
- `db.` prefix → dispatches to shell parser
- JSON array input → iterates and executes each command (enables `setup.json` seeding)
- `{"insertMany": "collection", "documents": [...]}` → `collection.insert_many(documents)`

**3. Recursive schema introspection** (`_extract_fields`):
- Nested objects produce dotted-path fields: `shipping_address.city (str)`
- Arrays of objects produce `[]` sub-fields: `items[].unit_price (float)`
- Arrays of scalars show element type: `tags (array<str>)`
- Provides the LLM with accurate field paths for dot-notation queries

### Changes to `evalbench/generators/prompts/sqlgenbase.py`

Updated `MONGODB_PROMPT_TEMPLATE_WITH_RULES` to:
- Instruct shell-style output: `db.<collection>.aggregate([...])`
- Prefer aggregate pipelines as the canonical format
- Always suppress `_id` with `{"_id": 0}` in `$project`
- Document dot-notation rules for nested and array fields

### Seeding Flow (how `setup.json` replaces CSVs)

```
setup.json = [
  {"insertMany": "orders", "documents": [...]},
  {"insertMany": "products", "documents": [...]},
  {"insertMany": "customers", "documents": [...]}
]

resetup_database():
  drop_all_tables()                        # clear existing data
  batch_execute([setup.json as string])    # _execute_query parses list
    → for each insertMany command:         # inserts nested documents
        collection.insert_many(documents)
  insert_data({}, ...)                     # no-op (no CSV data)
  # no post_setup.json → data persists ✓
```

This fixes the BAT post_setup bug: DART has no `post_setup.json`, so data is never wiped after seeding.

### Run Config (`datasets/dart/run_mongodb.yaml`)

```yaml
dataset_config: datasets/dart/prompts.json
database_configs:
  - datasets/dart/db_configs/mongodb.yaml
dialects: [mongodb]
query_types: [dql]
scorers:
  exact_match: null
  set_match: null       # added vs BAT (order-insensitive comparison)
  executable_sql: null  # added vs BAT (checks generated query runs without error)
```

### New Test Coverage (`evalbench/test/mongodb_test.py`)

Five new test classes added alongside the original tests:

| Class | Tests | Covers |
|---|---|---|
| `TestMongoDBShellFormat` | 5 | Shell-style `aggregate`, `match`+`project`, `group`, invalid JSON args, unsupported method |
| `TestNestedDocumentQueries` | 7 | `shipping_address.city`, `payment.method/status`, `attributes.brand`, `preferences.notifications.email` (depth 3), `ratings.average` threshold, `$group` on nested field |
| `TestArrayFieldQueries` | 8 | `tags` membership, `items.category`, `items.discount` comparison, `$unwind`+`$group`, `$size` filter, `preferred_categories`, `$and` on address types |
| `TestInsertManyCommand` | 2 | Single `insertMany`, array of `insertMany` commands |
| `TestGetMetadataRecursive` | 4 | Dotted paths for nested objects, `[]` notation for array-of-objects, `array<str>` type, depth-3 nested fields |

### Environment Variable

Set `DART_MONGODB_CONNECTION_STRING` to your MongoDB connection string before running:

```bash
export DART_MONGODB_CONNECTION_STRING="mongodb+srv://user:pass@cluster.mongodb.net"
EVAL_CONFIG=datasets/dart/run_mongodb.yaml evalbench/run.sh
```

# EvalBench Codebase Overview

## What is EvalBench?

EvalBench is a flexible evaluation framework for measuring the quality of GenAI workflows on **database-specific tasks**, with a primary focus on **NL2SQL** (Natural Language to SQL). It:

- Sends natural language prompts to an LLM (Gemini, Claude, etc.)
- Receives generated SQL queries
- Executes them against real databases
- Scores the results against golden (ground-truth) queries
- Produces structured reports (CSV, BigQuery) and a local web viewer dashboard

It supports DQL (SELECT), DML (INSERT/UPDATE/DELETE), and DDL (CREATE/ALTER/DROP) query types, and can run evaluations across multiple database engines in parallel.

---

## Deployment Modes

The system has three deployment modes:

| Mode | Entry Point | Description |
|---|---|---|
| **Standalone CLI** | `evalbench/run.sh` → `evalbench/evalbench.py` | Primary local mode |
| **gRPC Service** | `evalbench/eval_server.py` | Deployed as a stateful microservice |
| **Web Viewer** | `viewer/main.py` | Mesop web app for browsing results |

---

## Directory Structure

```
evalbench/
├── evalbench/               # Main Python source package
│   ├── evalbench.py         # CLI entry point: eval() and run_suite()
│   ├── eval_server.py       # gRPC server entry point
│   ├── eval_service.py      # gRPC EvalServicer implementation
│   ├── run.sh               # Shell wrapper, reads $EVAL_CONFIG env var
│   │
│   ├── evaluator/           # Orchestration & evaluation loop
│   ├── databases/           # DB abstraction layer (8+ engines)
│   ├── dataset/             # Data loading and EvalInput types
│   ├── generators/          # Prompt builders and LLM model drivers
│   ├── scorers/             # Scoring/comparison strategies
│   ├── work/                # Work item classes (parallelizable units)
│   ├── mp/                  # ThreadPoolExecutor-based work scheduler
│   ├── reporting/           # CSV, BigQuery reporters
│   ├── evalproto/           # Protobuf definitions and generated Python code
│   └── util/                # Config, rate limiting, sanitization utilities
│
├── datasets/                # Evaluation datasets and run configs
│   ├── bat/                 # "Basic Analytics Tasks" dataset
│   ├── bird/                # BIRD benchmark
│   ├── bird-interact/       # BIRD-Interact multi-turn benchmark
│   └── model_configs/       # Model configs (Gemini, Claude, etc.)
│
├── viewer/                  # Local result viewer (Mesop web app)
├── evalbench_service/       # Kubernetes/Docker deployment for gRPC server
└── docs/                    # Configuration documentation
```

---

## Main Workflow: CLI Mode

### Step-by-step Flow

```
run.sh ($EVAL_CONFIG)
  └── evalbench.py::eval()
        ├── load_yaml_config()          → parse run config YAML
        ├── set_session_configs()       → normalize to session dict
        ├── load_dataset_from_json()    → {dql: [...], dml: [...], ddl: [...]}
        ├── get_orchestrator()          → select orchestrator by type
        ├── orchestrator.evaluate()     → run 4-stage pipeline
        ├── orchestrator.process()      → write temp JSON output
        └── get_reporters()             → write CSV / BigQuery results
```

### The 4-Stage Evaluation Pipeline

Each `EvalInput` flows through four sequential, internally-parallelized stages managed by `MPRunner` (a `ThreadPoolExecutor` wrapper):

```
EvalInput
   │
   ▼
[Stage 1: Prompt Generation]  (SQLPromptGenWork)
   • Fetch DB schema via db.get_ddl_from_db()
   • Format dialect-specific system prompt + NL question
   │
   ▼
[Stage 2: SQL Generation]  (SQLGenWork)
   • Call model_generator.generate(prompt)
   • Hit Vertex AI (Gemini or Claude) API
   • Return raw SQL string
   │
   ▼
[Stage 3: SQL Execution]  (SQLExecWork)
   • Sanitize SQL (strip markdown fences)
   • Execute generated SQL on real DB
   • Execute golden SQL on real DB
   • DQL → read-only, reuse connection
   • DML → rollback after execution
   • DDL → use isolated temp clone DB
   │
   ▼
[Stage 4: Scoring]  (ScorerWork)
   • Run all configured scorers
   • Each produces score (0–100) + explanation
   • Append ComparisonResult dicts to output
```

### Orchestrator Variants

| Orchestrator | Config Value | Use Case |
|---|---|---|
| `OneShotOrchestrator` | `oneshot` | Single-prompt NL2SQL (default) |
| `InteractOrchestrator` | `interact` | Multi-turn disambiguation (BIRD-Interact) |
| `DataAgentOrchestrator` | `dataagent` | ADK DataAgent with simulated user |
| `AgentOrchestrator` | `geminicli` | Gemini CLI subprocess with simulated user |

The `OneShotOrchestrator` slices the dataset by `(dialect, database, query_type)` triplets and evaluates multiple triplets in parallel using an outer `ThreadPoolExecutor` (default 4 workers), each running the inner 4-stage pipeline.

---

## Key Abstractions

| Class | File | Role |
|---|---|---|
| `DB` | `databases/db.py` | Abstract DB base. Defines `execute()`, `batch_execute()`, `get_metadata()`, `create_tmp_database()`, etc. |
| `QueryGenerator` | `generators/models/generator.py` | Abstract base for LLM inference. Wraps `generate_internal()` with rate limiting. |
| `PromptGenerator` | `generators/prompts/generator.py` | Abstract base for prompt construction. Defines `setup()` and `generate(item)`. |
| `Orchestrator` | `evaluator/orchestrator.py` | Abstract base. Defines `evaluate(dataset)` and `process() → (job_id, run_time, results, scores)`. |
| `Comparator` | `scorers/comparator.py` | Abstract base. Defines `compare(...) → Tuple[float, str]` (score 0–100, explanation). |
| `ComparisonResult` | `scorers/comparator.py` | Data container for one scorer's output: comparator, score, logs, error. |
| `Reporter` | `reporting/report.py` | Abstract base. Defines `store(df, STORETYPE)` and `print_dashboard_links()`. |
| `EvalInputRequest` | `dataset/evalinput.py` | Container for one eval item: id, nl_prompt, query_type, database, dialects, golden_sql (per-dialect), tags, etc. Also handles proto serialization. |
| `MPRunner` | `mp/mprunner.py` | Thin `ThreadPoolExecutor` wrapper; holds a futures list. |
| `Work` | `work/work.py` | Abstract base with `run() → Any`. Each pipeline stage subclasses this. |
| `SessionManager` | `util/sessionmgr.py` | In-memory session store for the gRPC server (TTL-based reaper thread). |

---

## Database Layer

Database support is organized around a common abstract `DB` class in `databases/db.py`. Supported engines:

- **Relational**: SQLite, PostgreSQL, MySQL, SQL Server, AlloyDB, AlloyDB Omni
- **Cloud**: BigQuery, Spanner
- **NoSQL**: Bigtable, MongoDB

The `get_database()` factory in `databases/__init__.py` maps `db_type` strings from config to the right class.

### DB Connection Queue Strategy

Rather than sharing a single connection, a `Queue[DB]` is built per batch. Workers `get()` a connection, use it, then `put()` it back. Per-type strategies:

- **DQL**: Shared read-only connection; reused concurrently
- **DML**: Shared DML connection; each query is rolled back after execution
- **DDL**: N temporary clone databases created (one per concurrent worker), independently set up from scratch, dropped after the run

---

## Generators (Models + Prompts)

### Model Generators (`generators/models/`)

| Generator | Config Value | Backend |
|---|---|---|
| `GeminiGenerator` | `gcp_vertex_gemini` | Vertex AI (google-genai) |
| `ClaudeGenerator` | `gcp_vertex_claude` | Vertex AI (anthropic[vertex]) |
| `GeminiCLIGenerator` | `gemini_cli` | Gemini CLI subprocess |
| `QueryDataGenerator` | `querydata` | External data agent API endpoint |
| `AlloyDBNLGenerator` | `alloydb_ai_nl` | AlloyDB AI NL2SQL endpoint |
| `NOOPGenerator` | `noop` | Passthrough (input as output) |

### Prompt Generators (`generators/prompts/`)

- `SQLGenBasePromptGenerator`: Builds dialect-specific SQL generation system prompts with schema context
- `InteractSystemPromptGenerator` / `InteractUserPromptGenerator`: Multi-turn disambiguation prompts
- `SimulatedUserPromptGenerator`: Prompt for the LLM-backed simulated user in agent evals
- `PassthroughPromptGenerator`: NOOP; passes input through as-is

---

## Scorers

Scorers are fully composable — any combination can be listed in the run config. Each produces an independent score (0 or 100) plus a textual explanation.

| Scorer | What it measures |
|---|---|
| `ExactMatcher` | Exact result set match |
| `SetMatcher` | Set equality (BIRD execution accuracy) |
| `RecallMatcher` | Recall of golden rows in generated results |
| `LLMRater` | LLM-as-judge (fast SetMatcher first, then LLM if mismatch) |
| `GeneratedQueryRegexpMatcher` | Regex match on generated SQL text |
| `ReturnedSQL` | Checks that SQL was returned (not just comments) |
| `ExecutableSQL` | Checks that generated SQL executes without error |
| `TrajectoryMatcher` | Jaccard/Levenshtein similarity on tool-call trajectories |
| `GoalCompletionRate` | LLM rates whether agent accomplished the conversation goal |
| `BehavioralMetrics` | LLM evaluates agent behavioral quality |
| `TurnCount` | Number of conversation turns |
| `EndToEndLatency` | Total wall-clock latency |
| `ToolCallLatency` | Per-tool-call latency |
| `TokenConsumption` | Token usage metric |

The `analyze_result()` function in `reporting/analyzer.py` aggregates all scorer outputs into a per-metric summary of pass percentages.

**LLMRater short-circuit**: First runs `SetMatcher` as a fast exact-match check; only if that fails does it invoke the (expensive) LLM judge. On LLM failure, a second call runs `ERROR_CATEGORIZATION_PROMPT` to classify the error type.

---

## Configuration System

Configuration is organized in three layers of YAML files. Values support environment variable interpolation via `pyaml_env` (e.g., `!ENV ${EVAL_GCP_PROJECT_ID}`).

### Run Config (`example_run_config.yaml`)

```yaml
dataset_config: path/to/dataset.json
database_configs:
  - path/to/sqlite.yaml
  - path/to/postgres.yaml
dialects: [sqlite, postgres]      # filter
query_types: [dql]                # filter: dql, dml, ddl
model_config: path/to/model.yaml
prompt_generator: SQLGenBasePromptGenerator
scorers:
  SetMatcher: {}
  LLMRater:
    model_config: path/to/rater_model.yaml
reporting:
  csv:
    output_directory: results/
  bigquery:
    gcp_project_id: my-project
runners:
  eval_runners: 4
  sqlexec_runners: 8
```

### DB Config (e.g., `datasets/bat/db_configs/sqlite.yaml`)

```yaml
db_type: sqlite        # sqlite, postgres, mysql, bigquery, spanner, ...
dialect: sqlite
database_path: path/to/db.sqlite
max_executions_per_minute: 60
```

### Model Config (e.g., `datasets/model_configs/gemini_2.5_pro_model.yaml`)

```yaml
generator: gcp_vertex_gemini
vertex_model: gemini-2.5-pro
gcp_project_id: !ENV ${EVAL_GCP_PROJECT_ID}
gcp_region: us-central1
execs_per_minute: 60
max_attempts: 3
```

### Suite Config (for multi-run sweeps)

```yaml
name: "Full Evaluation Suite"
runs:
  - name: "Run 1"
    config_path: path/to/run_config_1.yaml
  - name: "Run 2"
    config_path: path/to/run_config_2.yaml
```

---

## Dataset Format

Each eval dataset is a JSON file containing lists of eval items grouped by query type:

```json
{
  "dql": [
    {
      "id": "q001",
      "nl_prompt": "How many orders were placed last month?",
      "query_type": "dql",
      "database": "sales_db",
      "dialects": ["sqlite", "postgres"],
      "golden_sql": {
        "sqlite": ["SELECT COUNT(*) FROM orders WHERE ..."],
        "postgres": ["SELECT COUNT(*) FROM orders WHERE ..."]
      },
      "tags": ["aggregation", "date_filter"]
    }
  ],
  "dml": [...],
  "ddl": [...]
}
```

---

## Reporting

Two reporter implementations:

- **CsvReporter** (`reporting/csv.py`): Writes multiple CSV files to `results/` directory — `evals.csv`, `scores.csv`, per-run summaries, and a `config.json`
- **BigQueryReporter** (`reporting/bqstore.py`): Appends results to a BigQuery dataset; prints a Looker Studio dashboard URL

Both implement the abstract `Reporter` base and are instantiated from the `reporting` section of the run config.

---

## Notable Design Patterns

**Staged parallel pipeline**: Four sequential phases, each internally parallelized via `ThreadPoolExecutor`. While one batch waits for LLM responses, another batch's DB executions can proceed.

**Work pattern**: Each pipeline stage is a `Work` subclass with a `run()` method, submitted to `MPRunner`. Makes stages independently testable and swappable.

**Plugin scorers**: Any combination of scorers can be configured; each runs independently and produces its own score. This allows fine-grained analysis across multiple quality dimensions simultaneously.

**DDL isolation**: DDL queries get N temporary clone databases (one per concurrent worker), named `tmp_{db_name}_{random_key}`, created and destroyed per run to prevent permanent schema mutations.

**Redis caching**: Both the DB layer and `LLMRater` support optional Redis caching, keyed on a hash of `(query, model_config)`. Prevents redundant LLM calls or DB queries on repeated runs.

**gRPC service mode**: `EvalServicer` + `SessionManagerInterceptor` allow deployment as a stateful microservice. Clients stream `EvalInputRequest` protos and receive a `job_id` back; sessions have TTL-based cleanup.

**Dialect-aware golden SQL**: Each eval item stores per-dialect SQL variants under `golden_sql: {postgres: [...], sqlite: [...]}`. The `copy_for_dialect()` method selects the right variant during dataset preprocessing.

---

## Testing

Tests are in `evalbench/test/` using Python's `unittest` framework with `unittest.mock`:

- `evalbench_test.py`: Unit tests for `eval()` and `run_suite()`, mocking all external dependencies (orchestrator, YAML loading, dataset loading, reporters)
- `mongodb_test.py`, `bigtable_test.py`, `spanner_test.py`: DB connector tests
- `sessionmgr_test.py`: gRPC session manager tests

Test sessions and linting are managed via `noxfile.py` and run on Cloud Build CI (`cloudbuild.yaml`).

# Research: SIO v2

## Decision 1: SpecStory File Format

**Decision**: Parse SpecStory files as markdown with conversation blocks delimited by `## Human:` / `## Assistant:` headers, with ISO timestamps in filenames.

**Rationale**: SpecStory files at `~/.specstory/history/` follow a consistent pattern: filename encodes session start time (`2026-02-25_14-04-27Z-topic.md`), content alternates Human/Assistant blocks. Tool calls appear as code blocks with specific markers.

**Alternatives considered**:
- Parse as raw text — loses structure
- Use regex only — brittle against format changes
- Use HybridRAG — too heavy for simple extraction

## Decision 2: JSONL Transcript Format

**Decision**: Parse Claude JSONL transcripts line-by-line, extracting message objects with `role`, `content`, and tool call metadata.

**Rationale**: JSONL files at `~/.claude/projects/*/*.jsonl` contain one JSON object per line. Each object has `role` (human/assistant/tool_result), `content`, and for tool calls: `tool_name`, `tool_input`, potentially `error`. ISO timestamps on each message.

**Alternatives considered**:
- Read session.json only — lacks tool call detail
- Use Claude API to re-analyze — violates local-only constraint

## Decision 3: Embedding-Based Clustering

**Decision**: Use fastembed (all-MiniLM-L6-v2) to embed error messages, then cluster by cosine similarity with threshold 0.80.

**Rationale**: Exact string matching misses paraphrased errors. Embedding similarity catches "file not found" ≈ "no such file or directory" ≈ "path does not exist". The 384-dim model is fast and runs locally. Already available from v1 embeddings provider.

**Alternatives considered**:
- TF-IDF + k-means — less semantic understanding
- LLM-based clustering — too expensive for local-only
- Edit distance — misses semantic similarity

## Decision 4: Suggestion Home File

**Decision**: Write suggestions to `~/.sio/suggestions.md` as ranked markdown, readable by both humans and by a session-start script.

**Rationale**: Markdown is human-readable, git-trackable, and parseable. A session-start hook or CLAUDE.md instruction can reference this file to surface suggestions when user opens a new session.

**Alternatives considered**:
- JSON file — less human-readable
- SQLite only — not easily surfaced at session start
- Push notification — no mechanism in CLI tools

## Decision 5: Cron vs Systemd

**Decision**: Support both cron and systemd timer, with cron as default (wider compatibility).

**Rationale**: Most developer machines have cron. WSL2 has it via `cron` service. macOS has launchd but cron works too. Provide `sio schedule install` that writes crontab entries.

**Alternatives considered**:
- systemd only — not available on macOS
- Background daemon — heavier, harder to debug
- Manual-only — defeats the passive analysis purpose

## Decision 6: Reuse Strategy for v1 Core

**Decision**: Extend v1's core modules in-place rather than replacing them. Add v2 tables to existing schema.py, v2 queries to existing queries.py, v2 config keys to existing config.py, v2 CLI commands to existing main.py.

**Rationale**: v1 has 274 passing tests. Replacing modules risks breaking existing functionality. Extending preserves v1's test coverage while adding v2 capabilities. The v1 infrastructure (embeddings, arena, config) is directly needed by v2.

**Alternatives considered**:
- Fork v1 modules — code duplication, maintenance burden
- Replace entirely — breaks 274 existing tests, wastes working code
- Separate package — unnecessary complexity for a single-developer tool

# Tutorial: Using Claude Code Sub‑Agents via Anthropic API / SDK

### Overview

Claude Code includes support for **sub‑agents** (task-specific specialist agents) that operate in their own context windows, with tailored prompts and tool permissions. This memo focuses on **Python + CLI workflows**, which are fully supported as of July 29, 2025 ([Reddit][1], [Anthropic][2]).

---

## A) Installation & Setup

**CLI Installation** (required for sub‑agent capability):

```bash
npm install -g @anthropic-ai/claude-code
```

**Python SDK**:

```bash
pip install claude-code-sdk
```

The SDK operates by invoking Claude Code as a subprocess—it does not yet expose native methods for managing or invoking sub‑agents programmatically ([Anthropic][3]).

**Authenticate**:
Set `ANTHROPIC_API_KEY` as an environment variable or configure via SDK/CLI methods.

**Optional**: Set up local install to avoid permission issues:

```bash
claude migrate-installer
```

(Recommended when global npm prefixes are not user-writable) ([Anthropic][4]).

---

## B) Defining Sub‑Agent Configuration

**File Locations**:

* Project‑level: `.claude/agents/`
* User‑level: `~/.claude/agents/`
  Project‑level agents take precedence if there’s a naming conflict ([Anthropic][2]).

**File format**:

```markdown
---
name: code-reviewer
description: Review recent code changes for security and style.
tools: Read, Write, Bash
---

You are the "code-reviewer" agent. Your job:
- Read changed files
- Identify security issues or style violations
- Provide clear feedback and suggestions
```

 *If `tools:` is omitted, the agent inherits all available tools.* Use explicit lists for least privilege design ([Anthropic][2]).

---

## C) Creating and Managing Sub‑Agents via CLI

Run:

```
claude
```

Inside the interactive shell:

```text
/agents
```

Follow UI prompts to create, edit, delete agents. You can paste your .md file or use auto-generated templates then customize. Sub‑agents created here are immediately available for invocation ([Anthropic][2]).

**Known Windows bug**: In CLI v1.0.62 on Windows, `.claude/agents/` files may not appear in `/agents` list—even though they exist—due to a known issue filed July 28, 2025 ([GitHub][5], [GitHub][6]).

---

## D) Invoking Agents via CLI

Example:

```text
> Use the code-reviewer sub-agent to analyze my latest changes
```

Claude Code will delegate to your named agent automatically, per prompt instructions and agent description ([Reddit][7]).

---

## E) Invoking Agents via Python SDK

```python
import anyio
from claude_code_sdk import query, ClaudeCodeOptions

async def main():
    options = ClaudeCodeOptions(
        cwd="myproject",
        max_turns=3,
        allowed_tools=["Read", "Bash"]
    )
    prompt = "Use the code-reviewer agent on the last commit"
    messages = []
    async for msg in query(prompt=prompt, options=options):
        messages.append(msg)
    for m in messages:
        print(m.text)

anyio.run(main)
```

**Note**: SDK does not support explicit agent orchestration. This pattern works because the CLI honors the agent prompt; actual invocation still happens via subprocesses launched in the background ([Anthropic][3], [GitHub][8]).

---

## F) Orchestrating Master Agent Workflows

Use a coordinator system prompt to **sequence or parallelize** tasks:

```
> Planner agent: propose feature architecture.
> Code-generator agent: implement based on outline.
> Code-reviewer agent: audit implementation for standards and security.
```

This modular pattern reduces context bleed and allows each agent to focus on a narrow task. Community examples include OODA‑loop agents and orchestrator patterns ([Reddit][1]).

**Trade‑offs**:

* Parallel invocation increases token usage and latency.
* For faster performance, prefer **serial sequencing** unless parallelism is critical ([Anthropic][3]).

---

## G) Considerations & Known Issues

* **Token & latency**: More agents = more tokens spent. Serial workflows often more efficient ([Reddit][7], [Reddit][1]).
* **Agent naming conflict hack**: Some users report Claude may rewrite prompts or interfere when agent names contain meaningful terms like "review." Placeholder or numeric names may avoid this unintended behavior ([Reddit][9]).
* **Custom hooks support**: It’s unclear whether sub‑agents respect hooks configured in `settings.json`. Testing recommended ([Reddit][7]).
* **Model override per agent**: Currently unsupported; the agent selection UI does not allow specifying a different Claude model version per agent ([Reddit][7]).

---

## H) Best Practices & Extensions

* Use a **`CLAUDE.md` baseline** file at project root to seed global context across agents (e.g. company style guides, coding standards) ([Reddit][10], [Medium][11]).
* Use `.claude/settings.json` with `/config` command to centrally manage tool permissions, telemetry, and environment settings across agents.
* Design agent system prompts to **return a final summarized output**, so that orchestration logic (CLI or SDK) can consume a concise result.

---

## I) Summary & Action Recommendations

✅ **Confirmed capabilities**:

* Sub‑agents work via CLI and Python SDK (via subprocess).
* Defined using Markdown/YAML in `.claude/agents/` or user folder.
* Tool access correctly scoped with optional `tools:`.

⚠️ **Current limitations**:

* Python SDK does **not** yet support agent orchestration APIs.
* No model-level override per agent.
* Known Windows CLI display bug around agent listing.
* Potential agent prompt rewrites based on name semantics.

---

## Example Agent Definitions

**Planner Sub‑Agent (`.claude/agents/planner.md`):**

```markdown
---
name: planner
description: Create a feature architecture plan before coding.
tools: Read
---

As the planner agent:
- Review feature request or code context
- Propose architecture: components, dependencies, interfaces
- Output in structured format (JSON or bullet list)
```

**Code‑Generator Agent:**

```markdown
---
name: code-generator
description: Implement code based on planner outline
tools: Read, Write, Bash
---

You are a build expert:
- Take planner outline
- Generate and commit code
- Provide implementation summary and tests
```

**Orchestrator Prompt:**

```text
Use planner to draft architecture. Then invoke code-generator. Finally invoke code-reviewer. Collect results from each. Respond with combined summary:
- Plan
- Code implementation notes
- Review feedback
```

_____

[1]: https://www.reddit.com/r/ClaudeAI/comments/1m8yt48/claude_code_now_supports_subagents_so_i_tried/?utm_source=chatgpt.com "Claude Code now supports subagents, so I tried something fun, (I ..."
[2]: https://docs.anthropic.com/en/docs/claude-code/sub-agents?utm_source=chatgpt.com "Subagents - Anthropic API"
[3]: https://docs.anthropic.com/en/docs/claude-code/sdk?utm_source=chatgpt.com "Claude Code SDK - Anthropic API"
[4]: https://docs.anthropic.com/en/docs/claude-code/troubleshooting?utm_source=chatgpt.com "Troubleshooting - Anthropic API"
[5]: https://github.com/anthropics/claude-code/issues/4623?utm_source=chatgpt.com "Missing Agents in CLI List Despite Directory Creation · Issue #4623 ..."
[6]: https://github.com/anthropics/claude-code/issues/4626?utm_source=chatgpt.com "[BUG] · Issue #4626 · anthropics/claude-code - GitHub"
[7]: https://www.reddit.com/r/ClaudeAI/comments/1m8ik5l/claude_code_now_supports_custom_agents/?utm_source=chatgpt.com "Claude Code now supports Custom Agents : r/ClaudeAI - Reddit"
[8]: https://github.com/anthropics/claude-code-sdk-python/issues/92?utm_source=chatgpt.com "Is subagents supported in claude code python sdk? #92 - GitHub"
[9]: https://www.reddit.com/r/ClaudeAI/comments/1ma4obp/claude_code_sub_agents_not_working_as_expected/?utm_source=chatgpt.com "Claude Code sub agents not working as expected - Reddit"
[10]: https://www.reddit.com/r/ClaudeCode/?utm_source=chatgpt.com "r/ClaudeCode - Reddit"
[11]: https://medium.com/vibe-coding/99-of-developers-havent-seen-claude-code-sub-agents-it-changes-everything-c8b80ed79b97?utm_source=chatgpt.com "99% of Developers Haven't Seen Claude Code Sub Agents (It ..."

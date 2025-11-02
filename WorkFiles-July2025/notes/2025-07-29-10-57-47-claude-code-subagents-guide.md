# Claude Code Subagents Guide

**Date Created:** 2025-07-29 10:57:47  
**Topic:** Claude Code Subagents  
**Tags:** #claude-code #subagents #ai-development #automation

## Overview

Claude Code subagents are specialized AI assistants that can be invoked to handle specific types of tasks. They enable more efficient problem-solving by providing task-specific configurations with customized system prompts, tools, and separate context windows.

## Key Features

### 1. Independent Context Windows
Each subagent operates in its own context, preventing pollution of the main conversation and keeping it focused on high-level objectives.

### 2. Parallel Execution
Claude Code can run up to 10 subagents in parallel, enabling complex multi-faceted problem solving.

### 3. Task-Specific Expertise
Subagents can be fine-tuned with detailed instructions for specific domains, leading to higher success rates on designated tasks.

## Creating Subagents

### File Structure
```markdown
---
name: your-sub-agent-name
description: Description of when this subagent should be invoked
tools: tool1, tool2, tool3  # Optional - inherits all tools if omitted
---

Your subagent's system prompt goes here.
```

### Storage Locations
- **Project-level:** `.claude/agents/` directory
- **User-level:** `~/.claude/agents/` directory

*Note: Project-level subagents take precedence over user-level when names conflict.*

## Best Practices from Web Research

### 1. Use Subagents Early in Complex Tasks
Deploy subagents early in conversations to preserve context availability without sacrificing efficiency. They're particularly useful for:
- Verifying details
- Investigating specific questions
- Parallel analysis of complex problems

### 2. Leverage Parallel Processing
Example use case: Spawning 4 sub-tasks with different expert personas to analyze the same problem from multiple angles.

### 3. Clear Context Management
- Call `/clear` frequently to maintain predictable behavior
- Create fresh prompts for different question types
- Avoid overly long conversation threads

### 4. Use Proactive Language
Include phrases like "use PROACTIVELY" or "MUST BE USED" in your description field to encourage automatic invocation.

### 5. Generate Initial Subagents with Claude
Start by having Claude generate your initial subagent, then iterate to customize it for your specific needs.

## Resource Considerations

⚠️ **Important:** Subagents consume significantly more tokens than traditional interactions:
- Each subagent maintains independent context windows
- Sessions with 3 active subagents typically use 3-4x more tokens
- Plan resource usage accordingly for extended development sessions

## Invoking Subagents

### Automatic Invocation
Claude will automatically use appropriate subagents when it recognizes matching tasks based on their descriptions.

### Explicit Invocation
Request specific subagents in your commands:
- "Use the test-runner subagent to fix failing tests"
- "Have the code-reviewer subagent look at my recent changes"
- "Ask the debugger subagent to investigate this error"

## Currently Available Subagents in This Project

1. **web-search-agent**: Performs web searches and returns summarized factual responses
2. **task-notes**: Creates timestamped markdown notes in the /notes directory

## Suggested Subagents for the Agento Project

### 1. OpenTelemetry Guardian (`otel-guardian`)
**Purpose:** Monitor and protect OpenTelemetry instrumentation during code changes
```markdown
---
name: otel-guardian
description: Validates that OpenTelemetry instrumentation remains intact after code modifications. Use after any code changes.
tools: Read, Grep, Bash
---

You are an OpenTelemetry integrity guardian. Your role is to:
- Verify all spans are properly created and closed
- Check that trace context propagation is maintained
- Ensure all attributes follow semantic conventions
- Validate OTLP exporter configuration remains intact
- Alert if any telemetry is accidentally removed or broken
```

### 2. AI Pipeline Orchestrator (`pipeline-orchestrator`)
**Purpose:** Manage the execution of the three-module AI pipeline
```markdown
---
name: pipeline-orchestrator
description: Orchestrates the execution of the Agento pipeline modules in sequence with proper error handling
tools: Bash, Read, Write
---

You coordinate the execution of the Agento pipeline:
1. Verify environment setup (API keys, OpenTelemetry collector)
2. Execute modules in sequence: 1_01 → 1_06 → 2_Revise
3. Monitor trace.context propagation between modules
4. Handle errors and provide clear status updates
5. Save pipeline execution logs with timestamps
```

### 3. Goal Structure Validator (`goal-validator`)
**Purpose:** Validate and enhance project_goal.json structures
```markdown
---
name: goal-validator
description: Validates goal JSON structure and suggests improvements for better AI processing
tools: Read, Write, Edit
---

You ensure goal structures are optimized for AI processing:
- Validate JSON syntax and required fields
- Suggest enhancements for clarity and specificity
- Add metadata for better traceability
- Ensure goals align with expected pipeline input format
```

### 4. Token Usage Analyzer (`token-analyzer`)
**Purpose:** Analyze OpenTelemetry traces to provide token usage insights
```markdown
---
name: token-analyzer
description: Analyzes token usage from OpenTelemetry data to optimize costs and performance
tools: Read, Grep, Bash
---

You analyze AI token usage patterns:
- Extract token counts from OpenTelemetry attributes
- Calculate costs per model and operation
- Identify optimization opportunities
- Generate usage reports with visualizations
- Suggest prompt engineering improvements
```

### 5. Plan Quality Reviewer (`plan-reviewer`)
**Purpose:** Review generated plans for completeness and actionability
```markdown
---
name: plan-reviewer
description: Reviews AI-generated plans for quality, completeness, and actionability
tools: Read, Write
---

You are a strategic planning expert who reviews AI-generated plans:
- Check for SMART goals (Specific, Measurable, Achievable, Relevant, Time-bound)
- Identify gaps or missing considerations
- Suggest improvements for clarity and execution
- Create summary reports of plan quality
```

### 6. Development Environment Helper (`env-helper`)
**Purpose:** Manage environment setup and troubleshooting
```markdown
---
name: env-helper
description: Assists with environment setup, dependency management, and troubleshooting
tools: Bash, Read, Edit
---

You help maintain the development environment:
- Verify all dependencies are installed
- Check API key configurations
- Troubleshoot OpenTelemetry collector issues
- Update requirements.txt when needed
- Create setup scripts for new developers
```

## Implementation Tips

1. **Start Simple:** Begin with one or two subagents and expand as needed
2. **Test Thoroughly:** Verify subagents work correctly before relying on them
3. **Document Usage:** Keep notes on which subagents are most effective
4. **Monitor Performance:** Track token usage and adjust strategies accordingly
5. **Iterate Frequently:** Refine subagent prompts based on actual usage patterns

## Conclusion

Claude Code subagents transform complex development workflows by enabling specialized, parallel AI assistance. For the Agento project, they can provide targeted support for OpenTelemetry integrity, pipeline orchestration, and quality assurance, making the development process more efficient and reliable.
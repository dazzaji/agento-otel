# Agento Claude Code Subagent Integration Plan

**Date Created:** 2025-07-29 11:05:00  
**Topic:** Integrating Claude Code Subagents into 1_WithSubAgent.py  
**Tags:** #agento #claude-code #subagents #architecture #planning

## Executive Summary

This plan outlines multiple options for integrating Claude Code subagents into the Agento Module 1 (Goal → Plan Structure). The integration leverages the Anthropic API via the claude-code-sdk Python package to invoke specialized subagents for enhanced plan generation, validation, and success criteria refinement.

## Current Module Analysis

### Existing Functionality
- **Single AI Agent**: Uses Google Gemini 1.5 Pro for plan structure generation
- **Basic Validation**: Uses Pydantic for JSON structure validation
- **OpenTelemetry**: Comprehensive tracing of AI operations
- **Output**: Generates plan_structure.json with steps and evaluation criteria

### Identified Enhancement Opportunities
1. Success criteria lack technical actionability
2. No multi-perspective plan review
3. Limited validation of plan feasibility
4. Single model dependency (Gemini only)

## Proposed Subagents

### 1. Success Criteria Optimizer (`success-criteria-optimizer`)
```markdown
---
name: success-criteria-optimizer
description: Transforms vague success criteria into SMART, technically measurable objectives with data validation methods
tools: Read, Write
---

You are an expert in creating actionable, measurable success criteria for AI and technical systems. Your role:
- Transform vague success measures into SMART objectives
- Define specific metrics, data points, and validation methods
- Create both human-readable and system-verifiable criteria
- Include technical implementation hints for measurement systems
- Ensure criteria can be tracked via telemetry/logging
```

### 2. Plan Validator (`plan-validator`)
```markdown
---
name: plan-validator
description: Validates plan structure for completeness, feasibility, and technical coherence
tools: Read
---

You validate AI-generated plans by checking:
- Logical flow and dependencies between steps
- Resource requirements and constraints
- Timeline feasibility
- Risk identification
- Missing prerequisites or assumptions
- Technical implementation challenges
```

### 3. Multi-Model Synthesizer (`multi-model-synthesizer`)
```markdown
---
name: multi-model-synthesizer
description: Coordinates multiple AI models to generate diverse perspectives on plan structure
tools: Read, Write
---

You orchestrate multiple AI perspectives:
- Analyze different model outputs for consensus/divergence
- Identify unique insights from each model
- Synthesize best elements into unified plan
- Document model-specific strengths utilized
- Create confidence scores for plan elements
```

## Integration Options

### Option 1: Replace Existing Tasks

**Implementation**: Replace Gemini plan generation with subagent orchestration

```python
async def generate_plan_with_subagents(goal: str) -> Optional[Dict]:
    """Replace single Gemini call with multi-agent approach"""
    
    # 1. Use multi-model-synthesizer to coordinate plan generation
    options = ClaudeCodeOptions(
        cwd=os.getcwd(),
        max_turns=5,
        allowed_tools=["Read", "Write"]
    )
    
    prompt = f"""Use the multi-model-synthesizer agent to create a plan for: {goal}
    Coordinate responses from multiple AI models and synthesize the best plan structure."""
    
    plan = await invoke_subagent(prompt, options)
    
    # 2. Validate with plan-validator
    validation_prompt = f"""Use the plan-validator agent to review this plan:
    {json.dumps(plan, indent=2)}"""
    
    validation = await invoke_subagent(validation_prompt, options)
    
    # 3. Optimize success criteria
    criteria_prompt = f"""Use the success-criteria-optimizer to enhance success measures:
    Current plan: {json.dumps(plan, indent=2)}"""
    
    optimized_plan = await invoke_subagent(criteria_prompt, options)
    
    return optimized_plan
```

**Benefits**:
- Leverages multiple AI perspectives
- Built-in validation and optimization
- More robust success criteria

**Trade-offs**:
- Higher latency (3x subagent calls)
- Increased token usage
- Requires SDK integration

### Option 2: Add New Tasks

**Implementation**: Keep Gemini generation, add subagent enhancements

```python
def enhance_plan_with_subagents(plan: Dict) -> Dict:
    """Enhance existing plan with subagent capabilities"""
    
    with tracer.start_as_current_span(
        "agento.subagent.enhancement",
        attributes={"openinference.span.kind": OIKind.CHAIN.value}
    ):
        # 1. Enhance success criteria only
        enhanced_criteria = enhance_success_criteria(plan)
        plan["Success_Measures"] = enhanced_criteria["measures"]
        plan["Success_Validation"] = enhanced_criteria["validation_methods"]
        
        # 2. Add feasibility assessment
        feasibility = assess_plan_feasibility(plan)
        plan["Feasibility_Score"] = feasibility["score"]
        plan["Risk_Factors"] = feasibility["risks"]
        
        # 3. Add implementation hints
        implementation = generate_implementation_hints(plan)
        plan["Implementation_Guide"] = implementation
        
    return plan
```

**New Functions**:
```python
async def enhance_success_criteria(plan: Dict) -> Dict:
    """Use success-criteria-optimizer to enhance measures"""
    prompt = f"""Use the success-criteria-optimizer agent to transform these success measures 
    into technically actionable criteria with validation methods:
    
    Original measures: {json.dumps(plan['Success_Measures'], indent=2)}
    Goal context: {plan['Original_Goal']}
    
    Provide:
    1. SMART objectives for each measure
    2. Technical metrics and data points
    3. Validation methods (automated where possible)
    4. OpenTelemetry attributes to track
    """
    
    return await invoke_subagent(prompt, get_subagent_options())

async def assess_plan_feasibility(plan: Dict) -> Dict:
    """Use plan-validator for feasibility assessment"""
    prompt = f"""Use the plan-validator agent to assess this plan's feasibility:
    {json.dumps(plan, indent=2)}
    
    Return a structured assessment with:
    - Overall feasibility score (0-100)
    - Risk factors by step
    - Missing prerequisites
    - Resource requirements
    """
    
    return await invoke_subagent(prompt, get_subagent_options())
```

**Benefits**:
- Preserves existing functionality
- Selective enhancement
- Lower latency than full replacement

**Trade-offs**:
- Still relies on single model for core generation
- May have inconsistencies between original and enhanced parts

### Option 3: Hybrid Approach (Recommended)

**Implementation**: Parallel generation with synthesis

```python
async def generate_hybrid_plan(goal: str) -> Dict:
    """Generate plan using both Gemini and Claude subagents in parallel"""
    
    with tracer.start_as_current_span(
        "agento.hybrid.generation",
        attributes={"openinference.span.kind": OIKind.CHAIN.value}
    ):
        # Parallel execution
        gemini_task = asyncio.create_task(generate_plan_structure(goal))
        subagent_task = asyncio.create_task(generate_subagent_plan(goal))
        
        gemini_plan, subagent_plan = await asyncio.gather(
            gemini_task, subagent_task
        )
        
        # Synthesize best of both
        synthesized = await synthesize_plans(gemini_plan, subagent_plan)
        
        # Always enhance success criteria
        enhanced = await enhance_success_criteria(synthesized)
        
        return enhanced
```

**Key Integration Points**:

1. **Subagent Invocation Helper**:
```python
async def invoke_subagent(prompt: str, options: ClaudeCodeOptions) -> Dict:
    """Invoke Claude Code subagent and parse response"""
    messages = []
    async for msg in query(prompt=prompt, options=options):
        messages.append(msg)
    
    # Parse response and extract JSON
    response_text = " ".join(m.text for m in messages)
    return parse_json_response(response_text)
```

2. **OpenTelemetry Integration**:
```python
def trace_subagent_call(agent_name: str, operation: str):
    """Decorator for subagent tracing"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(
                f"agento.subagent.{agent_name}",
                kind=SpanKind.CLIENT,
                attributes={
                    "openinference.span.kind": OIKind.AGENT.value,
                    "agento.subagent.name": agent_name,
                    "agento.subagent.operation": operation,
                }
            ) as span:
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR))
                    raise
        return wrapper
    return decorator
```

## Implementation Requirements

### 1. Dependencies
```python
# Add to requirements.txt
claude-code-sdk>=0.1.0
anyio>=3.0.0
```

### 2. Environment Setup
```python
# Add to .env
ANTHROPIC_API_KEY=your_key_here
CLAUDE_CODE_MAX_TURNS=10
```

### 3. Subagent Files
Create in `.claude/agents/`:
- `success-criteria-optimizer.md`
- `plan-validator.md`
- `multi-model-synthesizer.md`

### 4. Module Updates
```python
# Add imports
import anyio
from claude_code_sdk import query, ClaudeCodeOptions

# Add configuration
ENABLE_SUBAGENTS = os.getenv("ENABLE_SUBAGENTS", "true").lower() == "true"
SUBAGENT_TIMEOUT = int(os.getenv("SUBAGENT_TIMEOUT", "300"))
```

## Success Criteria Enhancement Focus

### Current Problem
Existing success criteria like "Clear project roadmap created" are too vague for:
- LLMs to evaluate completion
- Technical systems to measure
- Automated validation

### Solution via success-criteria-optimizer
Transform each criterion into:

1. **Quantifiable Metrics**
   - Original: "Clear project roadmap created"
   - Enhanced: 
     - "Project roadmap document exists at `./outputs/roadmap.md`"
     - "Contains minimum 5 milestones with dates"
     - "Each milestone has 3+ measurable deliverables"
     - "Gantt chart generated at `./outputs/timeline.png`"

2. **Validation Methods**
   ```json
   {
     "validation": {
       "automated": [
         "file_exists('./outputs/roadmap.md')",
         "markdown_sections_count >= 5",
         "deliverables_count >= 15"
       ],
       "llm_evaluation": [
         "Clarity score > 0.8",
         "Completeness score > 0.9"
       ]
     }
   }
   ```

3. **OpenTelemetry Attributes**
   ```json
   {
     "telemetry_tracking": {
       "agento.roadmap.exists": "boolean",
       "agento.roadmap.milestones": "integer",
       "agento.roadmap.deliverables": "integer",
       "agento.roadmap.clarity_score": "float"
     }
   }
   ```

## Testing Strategy

### 1. Unit Tests
```python
def test_subagent_invocation():
    """Test basic subagent call"""
    result = await invoke_subagent(
        "Use success-criteria-optimizer to enhance: 'Project completed'",
        ClaudeCodeOptions(max_turns=1)
    )
    assert "validation" in result
    assert "metrics" in result
```

### 2. Integration Tests
```python
def test_full_pipeline():
    """Test complete plan generation with subagents"""
    plan = await generate_hybrid_plan("Build a web app")
    assert plan["Success_Validation"] is not None
    assert all(
        "automated" in v 
        for v in plan["Success_Validation"].values()
    )
```

### 3. Performance Benchmarks
- Measure latency increase
- Track token usage
- Monitor OpenTelemetry span counts

## Rollout Plan

### Phase 1: Development (Week 1)
1. Install claude-code-sdk
2. Create subagent markdown files
3. Implement invoke_subagent helper
4. Add OpenTelemetry tracing

### Phase 2: Testing (Week 2)
1. Test individual subagents
2. Benchmark performance
3. Validate enhanced success criteria
4. Test error handling

### Phase 3: Integration (Week 3)
1. Implement hybrid approach
2. Add feature flags
3. Update documentation
4. Create monitoring dashboard

### Phase 4: Optimization (Week 4)
1. Tune subagent prompts
2. Optimize parallel execution
3. Implement caching
4. Add retry logic

## Monitoring & Observability

### New OpenTelemetry Spans
- `agento.subagent.{name}` - Each subagent call
- `agento.enhancement.success_criteria` - Criteria optimization
- `agento.validation.plan` - Plan validation
- `agento.synthesis.multi_model` - Plan synthesis

### New Attributes
- `agento.subagent.name` - Which subagent was called
- `agento.subagent.operation` - What operation performed
- `agento.subagent.token_usage` - Tokens consumed
- `agento.enhancement.criteria_count` - Number of criteria enhanced
- `agento.validation.score` - Validation score (0-100)

## Risk Mitigation

### 1. API Rate Limits
- Implement exponential backoff
- Use async for parallel calls
- Cache subagent responses

### 2. Increased Costs
- Monitor token usage via OpenTelemetry
- Set per-operation token limits
- Use feature flags for gradual rollout

### 3. Latency Concerns
- Parallel execution where possible
- Timeout configuration
- Fallback to Gemini-only mode

## Conclusion

The hybrid approach (Option 3) is recommended as it:
1. Preserves existing functionality
2. Adds multi-model perspectives
3. Significantly enhances success criteria
4. Maintains comprehensive observability
5. Allows gradual rollout via feature flags

The success-criteria-optimizer subagent addresses the core requirement of making success measures actionable for both LLMs and technical systems, enabling automated validation and progress tracking throughout the Agento pipeline.
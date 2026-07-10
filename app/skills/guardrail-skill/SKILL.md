---
name: guardrail-skill
description: Enterprise security guardrails skill containing specialized system instructions for evaluating user inputs and AI outputs against prompt injection, safety, compliance, and PII disclosure.
---

# Guardrail Skill

This skill contains the instructions and resources required to perform enterprise-grade input/output security audits, detect prompt injections, and redact sensitive personal identifiable information (PII) using regex fast-passes and deep LLM evaluations.

## Resources
- `references/input_guardrail.txt`: System instructions for validating and sanitizing user-provided prompts.
- `references/output_guardrail.txt`: System instructions for validating and sanitizing AI-generated responses.

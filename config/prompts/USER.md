# User Context

<!-- Edit this file to tell your AI team who they're working for.
     This file is injected into every agent's system prompt. -->

## About the User

Name: see config/agents.yaml → user.name
Role: Owner / Operator

## Business Context

<!-- Describe what the business does, what's currently being built,
     and what the primary goals are. Be specific — agents make better
     decisions when they understand the context they're operating in.

Example:
Primary business is an e-commerce store selling 3D printed parts.
95% of revenue comes from eBay. WooCommerce site is secondary.
Current priority: scale product listings and automate order workflows.
-->

## Tech Stack

<!-- List the languages, frameworks, services, and tools in use.

Example:
- Runtime: Node.js, Python 3.11
- Database: PostgreSQL
- Automation: n8n
- Infrastructure: Cloudflare tunnels, Docker
- E-commerce: WooCommerce, eBay API
-->

## Preferences

- Prefer working solutions over perfect ones
- Organic growth only — no paid advertising
- Keep things simple; avoid over-engineering
- All external-facing actions require user approval before execution

## Constraints

<!-- List hard rules agents must never violate.

Example:
- DO NOT modify live production workflows
- DO NOT delete files without explicit instruction
- DO NOT commit to external repositories without approval
-->

## Output Style

- Lead with the result, then the detail
- Use bullet points for multi-part answers
- Include file paths when referencing workspace files
- Flag uncertainty explicitly rather than guessing

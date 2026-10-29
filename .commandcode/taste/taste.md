# Taste

- Verifies actual API response schemas before coding parsers; builds parsers defensively with `.get()` defaults and validates downloaded data rather than assuming field names. Confidence: 0.85
- Uses fallback matching (id → ref → fuzzy name/address → coords) and marks ambiguous comparisons as unresolved rather than relying on a single identifier. Confidence: 0.85
- Prefers the assistant to explain what it is about to do before acting, and to back up confirmations with concrete evidence or command output rather than bare assertions. Confidence: 0.8
- When doing QA/review sampling, wants diverse coverage that deliberately includes edge cases, and wants the assistant to proactively add any additional items worth inspecting rather than only what was asked. Confidence: 0.8

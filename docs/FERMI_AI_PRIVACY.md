# Fermi AI privacy boundary

Private questions use deterministic Fermi reporting with tenant-scoped queries.
Neither typed questions nor database records are sent to an AI provider.
Unsupported private questions return local guidance rather than a model fallback.
English and Bengali keywords are recognized; local report text currently uses English.

General advice calls Gemini 3.5 Flash-Lite using only a server-owned topic prompt and
one of two language values. Arbitrary text, attachments, history, user identifiers,
business identifiers and database results cannot enter this request. Selection of
a generic topic and language is visible to Google, along with normal service
connection metadata. Google receives no Fermi user identity. This does not mean
Google processes no data at all.

The previous Anthropic context-export path is removed in this branch. Production
continues to use its existing code until this PR is approved and released.

## Setup
Set GEMINI_API_KEY as a Secret for the Preview environment in Vercel project fermi.
Use a Google AI Studio key from a free-tier project; free access depends on the
project, model availability and quota. Do not paste the key in source or chat.
Redeploy the Preview to activate the new variable. No fallback paid service is
enabled by this application. Billing status is controlled in the Google project,
so this code cannot guarantee zero cost for a billing-enabled key.

The general-advice integration remains unavailable without the key; local
insights continue working. Never include records in live provider tests.

## Scope
Text-based education on six listed business topics only. No arbitrary external
questions, browsing, image/audio/video generation, files, tools or agents.
Enabling those requires a separate privacy and product design.
Free-tier prompts may be used by Google to improve products, so confidential
free text is intentionally not supported.

A worker-local 15-minute generic-answer cache and five-second request cooldown
reduce calls. These are not global multi-instance quotas; Google project quota
remains the authoritative usage limit.

## Verification
python -m unittest discover -s tests -p 'test_ai_privacy.py' -v
Tests cover the closed outbound payload, no private network path, tenant routing,
cache behavior, missing key, unsupported inputs and sanitized provider errors.

Live Preview verification: general marketing advice returned successfully after upgrading the unavailable 2.5 model to 3.5 Flash-Lite. Only the fixed public-topic prompt was transmitted.

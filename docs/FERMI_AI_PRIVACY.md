# Fermi AI: private records and public web questions

## Automatic routing
/api/chat first classifies the question locally using conservative rules.
Business markers, ownership words, sensitive patterns and mixed questions take
the local path. Clearly public question patterns take the public path only after
the user enables Allow public web questions. Unrecognized requests ask for
clarification locally. No external model is used to classify private questions.

This is a conservative rules-based router, not perfect semantic understanding.
It deliberately keeps many business-related public questions local. It cannot
reliably detect every confidential name or paraphrase a user types. Public
question consent is required and the interface tells users not to enter secrets.
Never promise that arbitrary user-supplied text is automatically anonymized.

## Private path
Existing tenant-scoped Fermi queries answer supported revenue, stock, best-seller
and business-summary intents. Today reports use the UTC record date. Unsupported
questions return local help rather than exporting data or guessing. No question
or report from this path is sent to Tavily, Gemini or Anthropic.

## Public path
Only the current explicitly approved public question goes to Tavily. Five or fewer
bounded search snippets and the question go to Gemini 3.5 Flash-Lite for synthesis.
No database connection, business/user identity or chat history is accepted by
public_web.py. Retrieved snippets are untrusted evidence, never instructions;
the model has no tools. Source URLs are returned from retrieval, not invented
by the model. Text is rendered using textContent. Links allow only HTTPS and
exclude obvious local/IP destinations; Fermi does not fetch those URLs itself.

Tavily search does not guarantee a page is up to date. Answers must state when
the evidence is insufficient. A retrieval timestamp is not a verification of
every claim. Missing keys, exhausted quotas and provider errors produce an
unavailable message, never an answer presented as current from model memory.

## Preview activation
Existing GEMINI_API_KEY remains server-side. Add TAVILY_API_KEY as a Secret in
Vercel Preview, scoped to feature/private-fermi-gemini-advice, then redeploy.
Tavily offers a free monthly allowance. Choose its free plan without enabling
pay-as-you-go or entering billing. Application code uses basic search only;
account settings determine whether quota overruns could incur costs.
No billing or Production settings are changed by this PR.

The fixed general-education topic endpoint is retained separately.
Public responses are not persisted or cached by Fermi. Worker cooldown and
signed-session cooldown reduce repeated usage but are not global multi-instance
usage quotas; provider account limits remain authoritative.

## Tests and status
Tests cover business/mixed/ambiguous routing, consent, missing key, no-result
failure, exact outbound payload fields and unsafe links. Provider requests in
unit tests are mocked; end-to-end public web retrieval requires TAVILY_API_KEY.

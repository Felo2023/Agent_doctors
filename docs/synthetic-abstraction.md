# Independent Synthetic Record Abstraction

The `abstractor-a` (Claude Opus 5.5) and `abstractor-b` (OpenAI Astra) OpenCode agents run in **separate TUI processes and saved sessions**. They see the same bounded set of source blocks from a *marked fictional fixture* but never each other's output. Their results are validated against block IDs, compared and displayed; **no proposal is automatically promoted to a case fact**.

## Try it

Use the installed extraction environment and existing provider credentials for **synthetic data only**. First create fictional PDF/Word/Excel/CSV sources that have **not** been reviewed into case facts:

```bash
.venv/bin/python -m scripts.synthetic_multiformat_demo
```

Then use the printed `Synthetic directory` path (not a private case or a real-record folder):

```bash
.venv/bin/python -m scripts.synthetic_dual_abstraction \
  --fixture-root '/path/from/demo' --demo-shared-auth --timeout 300
```

The launcher copies only its own restrictive agent prompts to separate private temporary workspaces; read/edit/bash/web/task tools are disabled. It briefly links the current user's OpenCode credentials into those **synthetic-only** profiles and removes the links when finished. Do not use this symlink approach for real patient data. Independent session IDs/model names and comparison groups are recorded in `dual_abstraction.json` inside the synthetic fixture; the source blocks themselves remain traceable to the original files.

If the [source-review app](synthetic-review.md) is running on the same fixture, open **Compare two independent AI abstractions** from its dashboard to view A/B statements alongside the exact source block. The page never offers a "publish both" shortcut; use the separate human proposal and independent review flow after checking the original.

## Boundaries and interpretation

- Inputs must be a marked synthetic snapshot, at most 100 blocks and 25,000 text characters per batch. Image placeholders and spreadsheet formulas with no saved value are omitted. A production pipeline needs page/section batching and completeness tracking instead of accepting truncated cases.
- Models return bounded JSON with category, statement, exact `block_id`, source attribution, and an event date. Exact dates require YYYY-MM-DD; an approximate year/month remains `YYYY` or `YYYY-MM`. Missing dates remain null, never an invented first-of-month date.
- Fabricated block IDs and malformed schema are rejected. **Valid JSON is not proof that a statement is entailed by the source.** Wording-level similarity is labelled `same_wording`; paraphrases appear as `different_or_multiple`, one-model omissions as `one_sided`, and blocks neither model cited as `omitted_by_both`. Every group requires human source review. Shared omissions must not be mistaken for absent symptoms or normal results.
- Results are stored as `unverified_independent_abstractions`, with `promoted_to_case: false`. Run them before human review in a normal synthetic intake flow. Running abstractors after a case version already exists does not silently change that version either.
- Model calls send the fictional source blocks to both providers. Case-specific consent, provider retention, credential isolation, and evaluation against solved/de-identified cases must be resolved before using this process on real health records.

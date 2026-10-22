# Custom defense code generation (experimental)

The detailed research protocol is [EXPERIMENT_DESIGN.md](../../EXPERIMENT_DESIGN.md),
with adversarial review decisions in [DESIGN_REVIEW.md](../../DESIGN_REVIEW.md).
Its planned selection, acceptance and evaluation gates extend beyond this prototype.

This prototype asks the **OpenAI Responses API** to write actual defensive C
statements and inserts them at the beginning of a selected function body or
nested compound statement. It is an external source transformation tool, **not
yet an API call inside Clang/LLVM**. No upstream compiler files are modified.

`custom` is a generation mode, not a sixth disjoint security property. Generated
code can implement one of the five fixed properties (memory access validity,
indirect-call validity, integer arithmetic validity, explicit information flow,
speculative information flow), or an application-specific invariant. Classify
the resulting property separately; do not count a generated bounds check as a
new protection orthogonal to ASan.

## Requirements and commands

Python 3.9+, Clang with JSON AST output; no Python dependencies or GPU required.
The initial version supports ordinary C functions in `.c` files. `--block 0`
selects the function body; later indices select nested `{ ... }` blocks in AST
traversal order. This does not yet select arbitrary instructions, C++ functions,
macros, helper definitions or runtime library additions.

From `llm-defense/custom`:

```sh
mkdir -p out
python3 custom_defense.py inspect examples/quota.c \
  --function accept_request \
  --objective 'Reject requests with length above 4096 by returning 0; preserve other behavior.' \
  --out out/request.json

# Offline demonstration: authored fixture, NOT an LLM-generated result.
python3 examples/make_fixture.py out/request.json out/fixture.json
python3 custom_defense.py generate out/request.json \
  --model offline-fixture --replay out/fixture.json --out out/plan.json
python3 custom_defense.py apply out/plan.json \
  --source examples/quota.c --out out/quota.custom.c
clang -O2 -iquote examples -c out/quota.custom.c -o out/quota.custom.o
```

For real generation, set `OPENAI_API_KEY` and `OPENAI_MODEL` in your environment,
then use a new plan filename:

```sh
python3 custom_defense.py generate out/request.json \
  --model "$OPENAI_MODEL" --out out/live-plan.json
python3 custom_defense.py apply out/live-plan.json \
  --source examples/quota.c --out out/quota.live.c
```

Live generation sends the **entire selected C source file**, the objective and
the selected region to `https://api.openai.com/v1/responses`. Includes are not
expanded into the prompt. Credentials are read only from `OPENAI_API_KEY` and
are not saved in artifacts. Requests use `store: false`, strict structured
output, an output-token cap, a timeout and no automatic retries. `store: false`
is not a guarantee of zero provider retention. Model ID is explicit; the tool
does not silently choose or upgrade a model.

OpenAI API references: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create).

## What is checked

1. Match the response's source hash and region identity to the request.
2. Recompute the AST region and verify the source and compiler version have not
   changed. Offsets are byte offsets, including for UTF-8 source files.
3. Insert returned statements; preserve all original source bytes. Reject empty
   output, directives, malformed schema, refusals and incomplete responses.
4. Parse the patched translation unit with Clang and verify the selected block
   still ends at its expected shifted boundary. Only then create a new file.

Original files and existing output artifacts are never overwritten. Failed
application does not produce a candidate file. Quote includes are resolved
against the original source directory during validation. When subsequently
compiling a generated file elsewhere, pass that same include directory.

These are structural and syntax checks, not semantic equivalence, attack
resistance, performance validation or a sandbox. A guard can still be wrong,
have side effects, reject legitimate inputs or allow attacks. Nothing in the
tool executes API-generated code. Authored tests execute only a known fixture.
Evaluate generated candidates in a controlled runner before accepting them as
experimental artifacts. Plans are trusted build inputs, including compiler
paths/arguments. Header dependencies and compile-time environment are not yet
fingerprinted; keep them fixed externally. `__FILE__` and similar path-sensitive
behavior can differ between the original and generated file.

## Validation

```sh
python3 -m unittest discover -s tests -v
```

Tests cover source/region mismatch, invalid C, block escape, refusals/incomplete
responses, no overwrites, nested blocks, local includes, UTF-8 offsets and a
mocked OpenAI HTTP contract. An authored quota guard is compiled with `-O2` and
executed to check valid/invalid boundaries and preservation of a sibling
function. No paid API request is needed. A passing fixture test is not evidence
that a live model produced an effective defense.

## Experiment placement and measurements

- **Local Mac:** editing, prompt/schema iteration, offline replay, syntax and
  trusted functional tests. An API-backed generator needs no local model GPU.
- **Fixed Linux x86-64 host:** the proposed common evaluation target for the
  five defenses, particularly x86 SLH. Pin compiler commit, CPU, OS, flags,
  libraries, inputs and CPU placement. Verify each defense's target support and
  composition before running a combined configuration.
- **Separate generation and execution:** save the raw API response, model ID,
  usage and generation latency in the plan; reuse that plan for runtime trials.
  Do not include API latency in protected-program runtime overhead.
- **Variants:** unprotected baseline, each fixed defense, rule-based selection,
  LLM selection, and LLM selection plus custom generation. Separately record
  generation rejection, syntax failure, valid-input regressions and security
  test outcomes. Classify custom guards by their actual protection condition.
- **Costs:** repeated wall-clock/CPU time, memory, code size, build time and API
  tokens. Compare each variant with the same baseline on the same machine;
  report distributions and uncertainty. Source-reported costs from different
  systems are not a unified overhead table.

No performance measurements or live-model security results are claimed yet.
Future native integration should retain the request/response artifacts, map
stable regions through optimization, and separate generated-code acceptance
from defense selection. The five fixed defense implementations are not part of
this initial custom prototype.

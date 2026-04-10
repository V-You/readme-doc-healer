---
name: validate-openapi
description: Validate an OpenAPI spec file (YAML or JSON) for ReadMe compatibility. Use when a customer reports import errors, rendering problems, or cryptic validation messages from ReadMe. Also use when a support engineer suspects structural issues in a spec that blocks upload or causes broken docs.
argument-hint: "path/to/spec.yaml or paste the error message"
---

# Validate OpenAPI for ReadMe

Investigate and resolve OpenAPI specification issues that prevent successful
import into ReadMe or cause broken documentation rendering.

## When to use

- Customer reports an error when importing an OpenAPI spec into ReadMe
  (e.g. "REQUIRED must have required property 'schema'", "validation failed",
  "unable to render")
- Customer shares a line number or error message from ReadMe that they cannot
  make sense of
- Support engineer suspects the YAML/JSON is structurally invalid or violates
  OpenAPI rules
- Customer's spec loads in Swagger Editor but fails in ReadMe (different
  validators catch different things)
- A previous AI agent or linter gave an inconclusive or incorrect diagnosis

Do NOT use for general YAML formatting questions, non-ReadMe platforms, or
questions about ReadMe pricing / account management.

## Background

ReadMe accepts OpenAPI 3.0.x, 3.1, and Swagger 2.0 (auto-upconverted via
`swagger2openapi`). Before rendering, ReadMe dereferences all `$ref` pointers
and validates the resulting document. This means:

- Error line numbers reported by ReadMe refer to an internal dereferenced
  document, NOT the customer's source YAML/JSON. That is why customers often
  find the reported line numbers useless.
- A spec that passes one validator (e.g. Swagger Editor, Redocly) may still
  fail in ReadMe because each tool validates against slightly different rules
  or versions of the spec.
- ReadMe does not support relative schemas (`$ref: "Pet.json"`), URL
  references (`$ref: "https://..."`), or recursive/circular refs in most
  contexts. Use `rdme` CLI to bundle external refs before upload.

Key ReadMe docs for reference:
- OpenAPI support overview: https://docs.readme.com/main/docs/openapi
- Compatibility chart: https://docs.readme.com/main/docs/openapi-compatibility-chart
- Upload & management: https://docs.readme.com/main/docs/openapi-upload-and-management
- OpenAPI extensions: https://docs.readme.com/main/docs/openapi-extensions

## Workflow

Follow these steps in order. Each step narrows the problem space.

### Step 0 -- gather context

Collect:
1. The spec file (YAML or JSON). Ask the customer if not provided.
2. The exact error message from ReadMe (screenshot or text).
3. The OpenAPI version (`openapi:` field) -- 3.0.x, 3.1.0, or swagger 2.0.
4. How the customer imported it (file upload, URL import, `rdme` CLI, API).

### Step 1 -- syntax check

Confirm the file is valid YAML (or JSON).

```python
import yaml, json, sys
from pathlib import Path

spec_path = Path("<SPEC_FILE>")
text = spec_path.read_text()

try:
    if spec_path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    print("Syntax: OK")
except Exception as e:
    print(f"Syntax: FAIL -- {e}")
    sys.exit(1)
```

If this fails, the file has a YAML/JSON syntax error. Report the exact line
and character to the customer.

### Step 2 -- OpenAPI structural validation

Run the spec through `openapi-spec-validator` (available in the project venv).

```python
from openapi_spec_validator import validate
import yaml
from pathlib import Path

spec = yaml.safe_load(Path("<SPEC_FILE>").read_text())
try:
    validate(spec)
    print("OpenAPI validation: PASS")
except Exception as e:
    print(f"OpenAPI validation: FAIL -- {e}")
```

If this crashes (e.g. `KeyError`) rather than raising a clean validation
error, that itself is a strong clue -- the validator hit a structurally
malformed object it did not expect.

### Step 3 -- targeted structural checks

These are the most common issues that cause ReadMe import failures. Scan for
all of them systematically.

#### 3a. Wrong-category `$ref` in parameter arrays

This is the single most common hard-to-find bug. Every entry in a `parameters`
array must be either:
- A Parameter Object (with `name`, `in`, and `schema` or `content`), or
- A `$ref` pointing to `#/components/parameters/...`

A `$ref` pointing to `#/components/schemas/...` inside a `parameters` array
is INVALID. ReadMe reports this as "REQUIRED must have required property
'schema'" because it tries to interpret the Schema Object as a Parameter Object
and finds no `schema` sub-field.

```python
import yaml
from pathlib import Path

spec = yaml.safe_load(Path("<SPEC_FILE>").read_text())
errors = []

def check_params(params, location):
    for i, p in enumerate(params):
        ref = p.get("$ref", "")
        if ref and "/schemas/" in ref:
            errors.append(
                f"{location}, parameter[{i}]: $ref points to a Schema "
                f"({ref}) but only Parameter refs are valid here"
            )
        elif not ref:
            if "name" not in p or "in" not in p:
                errors.append(
                    f"{location}, parameter[{i}]: inline Parameter Object "
                    f"missing 'name' or 'in'"
                )

for path, path_item in (spec.get("paths") or {}).items():
    # path-level parameters
    if "parameters" in path_item:
        check_params(path_item["parameters"], f"paths.{path}")
    # operation-level parameters
    for method in ("get","put","post","delete","patch","options","head","trace"):
        op = path_item.get(method)
        if op and "parameters" in op:
            check_params(
                op["parameters"],
                f"paths.{path}.{method} ({op.get('operationId','?')})"
            )

if errors:
    print(f"Found {len(errors)} wrong-category ref(s):")
    for e in errors:
        print(f"  - {e}")
else:
    print("No wrong-category $ref issues found.")
```

#### 3b. Missing required fields

Scan for common omissions:
- `info.title` and `info.version` are REQUIRED
- Every Parameter Object needs `name` and `in`
- Every Operation should have a unique `operationId` (ReadMe uses it to
  anchor pages -- duplicates cause silent overwrites)
- Response objects need at least a `description`

#### 3c. Unsupported features in ReadMe

Check for things ReadMe does not yet support:
- `content` on Parameter Objects (ReadMe marks it unsupported)
- `servers` at Path Item or Operation level (unsupported)
- `openIdConnect` or `mutualTLS` security types
- `Link Object` (unsupported)
- `XML Object` (unsupported)
- Relative or URL `$ref` targets (unsupported; must bundle first)
- Circular / deeply recursive `$ref` chains (limited support)

#### 3d. Duplicate `operationId` values

```python
from collections import Counter

ops = []
for path, item in (spec.get("paths") or {}).items():
    for method in ("get","put","post","delete","patch","options","head","trace"):
        op = item.get(method)
        if op and "operationId" in op:
            ops.append(op["operationId"])

dupes = [oid for oid, n in Counter(ops).items() if n > 1]
if dupes:
    print(f"Duplicate operationIds: {dupes}")
else:
    print("All operationIds are unique.")
```

#### 3e. Path template vs. parameter mismatch

Every `{variable}` in a path template must have a corresponding parameter
with `in: path` and matching `name`. And vice versa -- a path parameter
declared but not present in the template is suspicious.

### Step 4 -- simulate the fix

When you find an issue, validate that fixing it actually resolves the error.
Load the spec, apply the fix in memory, and re-run `validate()`:

```python
# example: remove a bad parameter entry
params = spec["paths"]["/some/path"]["delete"]["parameters"]
params.pop(BAD_INDEX)

from openapi_spec_validator import validate
try:
    validate(spec)
    print("After fix: VALID")
except Exception as e:
    print(f"After fix: still failing -- {e}")
```

If the spec still fails after one fix, there may be multiple issues. Iterate.

### Step 5 -- compose customer response

Write a response the SE can send to the customer. Include:

1. **What we found** -- describe the issue in plain language, referencing
   the exact location (path, operation, line number in their source file).
2. **Why ReadMe rejects it** -- briefly explain the OAS rule being violated
   and why ReadMe's error message was hard to interpret.
3. **How to fix it** -- give the exact edit (e.g. "remove line 1849" or
   "change `$ref` from `#/components/schemas/X` to
   `#/components/parameters/Y`").
4. **How to verify** -- suggest they re-upload after the fix, or run a
   local validator first (link to https://editor.swagger.io or
   `npx @readme/openapi-parser validate spec.yaml`).
5. **Links** -- include relevant ReadMe docs links from the background
   section above.

## Common error patterns (cheat sheet)

| ReadMe error message | Likely root cause |
|---|---|
| "REQUIRED must have required property 'schema'" | A `$ref` to `#/components/schemas/...` inside a `parameters` array |
| "validation failed" (generic) | Multiple possible causes -- run steps 1-3 |
| "must NOT have unevaluated properties" | Extra fields on an object that the spec version doesn't allow |
| "must be equal to one of the allowed values" | Invalid `in` value, invalid `style`, or enum mismatch |
| Line number doesn't match source | ReadMe validates a dereferenced doc -- line numbers don't map to source |
| Works in Swagger Editor, fails in ReadMe | ReadMe may enforce stricter rules or not support certain features (see 3c) |
| "Unable to render" or blank page | Circular refs, deeply nested allOf/oneOf, or missing response `description` |

## Pitfalls

- Do NOT trust the line numbers in ReadMe error messages -- they refer to
  an internally transformed document, not the customer's source file.
- Do NOT assume a single fix is enough. Always re-validate after each fix
  to check for additional issues.
- Do NOT rely on a single validator. Different tools catch different things.
  When `openapi-spec-validator` passes but ReadMe still rejects, check
  ReadMe's compatibility chart for unsupported features.
- Be careful with "fast" or "small" AI models for structural validation --
  they tend to hallucinate issues or miss the real one. When diagnosing,
  systematically parse and scan the spec rather than guessing from the
  error message alone.

## Example prompts

- "Customer gets 'REQUIRED must have required property schema' when importing their spec to ReadMe"
- "This YAML spec fails to import into ReadMe -- can you find out why?"
- "Validate md/hw/ticket1.yaml for ReadMe compatibility"
- "Customer says line 247 has an error but they can't see anything wrong there"
- "Spec works in Swagger Editor but ReadMe rejects it"

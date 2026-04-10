---
name: troubleshoot-bidirectional-sync
description: Troubleshoot ReadMe bi-directional sync issues with GitHub or GitLab repos. Use when a customer reports pages showing 404, blank nav entries, rendering failures, or content not syncing after repo edits. Also use when a support engineer suspects broken _order.yaml files, missing frontmatter, or misplaced folders.
argument-hint: "path/to/docs-folder or description of the sync problem"
---

# Troubleshoot bi-directional sync

Investigate and resolve issues where pages in a ReadMe project that uses
bi-directional sync (GitHub or GitLab) are broken -- 404s, blank nav entries,
pages that fail to render, or content that does not sync.

## When to use

- Customer reports a page shows 404 after someone edited the Git repo
- A page appears in the sidebar but is blank or fails to load
- Content changes in Git are not reflected in ReadMe (or vice versa)
- Pages appear in the wrong category or are missing from the sidebar
- Customer sees "page not found" for a page that clearly exists in the repo
- An editor reorganized folders or renamed files and things broke

Do NOT use for general Git questions, non-ReadMe platforms, or questions
about ReadMe pricing / account management. Do NOT use for OpenAPI spec
validation -- use the `validate-openapi` skill instead.

## Background

ReadMe's bi-directional sync creates a two-way connection between a ReadMe
project and a GitHub/GitLab repository. The `docs/` folder in the repo maps
to the Guides section in ReadMe.

Key structural rules:
- **Categories** are top-level folders inside `docs/`.
- **Page groups** (pages with children) are subfolders with an `index.md`
  and child pages inside.
- **Leaf pages** are `.md` files.
- **Navigation order** is controlled by `_order.yaml` files at each level.
- **Every `.md` file** must have YAML frontmatter delimited by `---` on both
  ends, with at least `title` (required by ReadMe).
- **Every page-group folder** must contain an `index.md` for the parent page.
- **File/folder names** become URL slugs -- they must be URL-safe (avoid
  special characters, spaces are converted but can cause issues).
- `_order.yaml` entries must match actual file/folder names (without the
  `.md` extension for files).

Key ReadMe docs for reference:
- Bi-directional sync overview: https://docs.readme.com/main/docs/bi-directional-sync
- Documentation structure: https://docs.readme.com/main/docs/documentation-structure
- Sync with GitHub: https://docs.readme.com/main/docs/sync-with-github
- Sync with GitLab: https://docs.readme.com/main/docs/sync-with-gitlab

## Workflow

Follow these steps in order. Each step narrows the problem space.

### Step 0 -- gather context

Collect:
1. The `docs/` folder from their repo (or a zip/listing of it). Ask the
   customer if not provided.
2. The exact symptoms -- 404, blank page, rendering error, wrong nav order.
3. Which specific pages are affected (names, categories).
4. What recent changes were made in Git (renames, moves, new pages).
5. Whether they use GitHub or GitLab, and what ReadMe plan they are on.

### Step 1 -- map the folder structure

List the full tree of the `docs/` folder. Pay attention to:
- Folder nesting (categories > page groups > subpage groups)
- Presence of `_order.yaml` at each level
- Presence of `index.md` in every page-group folder
- File/folder naming conventions (special characters, spaces)

```bash
find docs/ -type f -o -type d | sort
```

### Step 2 -- validate _order.yaml files

For every `_order.yaml`, check that:
1. Every entry matches an actual file or folder at the same level.
2. Every file/folder at that level is listed (unlisted items won't appear
   in the sidebar).
3. Entries are not duplicated.
4. The YAML is syntactically valid.
5. Entries don't reference items that exist at a different level (e.g., a
   page group listed in the wrong category's `_order.yaml`).

```python
import yaml
from pathlib import Path

def validate_order_files(docs_root):
    docs = Path(docs_root)
    errors = []

    for order_file in docs.rglob("_order.yaml"):
        parent = order_file.parent
        entries = yaml.safe_load(order_file.read_text()) or []

        if not isinstance(entries, list):
            errors.append(f"{order_file}: not a valid list")
            continue

        # Flatten nested entries (page groups can nest)
        flat_entries = []
        for e in entries:
            if isinstance(e, str):
                flat_entries.append(e)
            elif isinstance(e, dict):
                flat_entries.extend(e.keys())

        # Check each entry has a matching file or folder
        siblings = {
            p.stem if p.is_file() else p.name
            for p in parent.iterdir()
            if p.name not in ("_order.yaml",) and not p.name.startswith(".")
        }

        for entry in flat_entries:
            if entry not in siblings:
                # Check if the entry exists elsewhere in the tree
                found_elsewhere = list(docs.rglob(entry))
                if found_elsewhere:
                    errors.append(
                        f"{order_file}: '{entry}' not found here but "
                        f"exists at {found_elsewhere[0].relative_to(docs)} "
                        f"-- likely listed in the wrong _order.yaml"
                    )
                else:
                    errors.append(
                        f"{order_file}: '{entry}' not found as a "
                        f"file or folder in {parent}"
                    )

        # Check for items present on disk but missing from _order.yaml
        for s in siblings:
            if s not in flat_entries and not s.endswith(":Zone.Identifier"):
                errors.append(
                    f"{order_file}: '{s}' exists on disk but is "
                    f"not listed -- it won't appear in the sidebar"
                )

    return errors
```

### Step 3 -- validate frontmatter

Every `.md` file must have valid YAML frontmatter (opening `---`, valid YAML,
closing `---`). Common issues:

- Missing closing `---` delimiter (content bleeds into frontmatter)
- Missing `title` field (ReadMe requires it)
- Invalid YAML syntax in frontmatter
- Stray characters before the opening `---`

```python
import yaml
from pathlib import Path

def validate_frontmatter(docs_root):
    docs = Path(docs_root)
    errors = []

    for md_file in docs.rglob("*.md"):
        text = md_file.read_text()

        # Must start with ---
        if not text.startswith("---"):
            errors.append(f"{md_file}: does not start with frontmatter '---'")
            continue

        # Find closing ---
        second_marker = text.find("---", 3)
        if second_marker == -1:
            errors.append(
                f"{md_file}: missing closing '---' in frontmatter "
                f"-- ReadMe cannot parse this page"
            )
            continue

        fm_text = text[3:second_marker]
        try:
            fm = yaml.safe_load(fm_text)
        except yaml.YAMLError as e:
            errors.append(f"{md_file}: invalid YAML in frontmatter -- {e}")
            continue

        if not isinstance(fm, dict):
            errors.append(f"{md_file}: frontmatter is not a YAML mapping")
            continue

        if "title" not in fm:
            errors.append(f"{md_file}: missing required 'title' field")

    return errors
```

### Step 4 -- check page-group folders

Every folder that contains child pages (subfolders or `.md` files other than
`index.md`) must itself contain an `index.md`. Without it, the page group
has no parent page and ReadMe will show a 404 or blank entry.

```python
from pathlib import Path

def check_page_groups(docs_root):
    docs = Path(docs_root)
    errors = []

    for folder in docs.rglob("*"):
        if not folder.is_dir():
            continue
        # Skip hidden and macOS resource fork folders
        if any(part.startswith(".") or part == "__MACOSX"
               for part in folder.parts):
            continue

        md_children = [
            f for f in folder.iterdir()
            if f.suffix == ".md" and f.name != "index.md"
        ]
        subfolder_children = [
            f for f in folder.iterdir()
            if f.is_dir() and not f.name.startswith(".")
               and f.name != "__MACOSX"
        ]

        # If this folder has content children, it needs an index.md
        # (unless it's a category root -- top-level folders under docs/)
        is_category_root = folder.parent == docs
        if not is_category_root and (md_children or subfolder_children):
            if not (folder / "index.md").exists():
                errors.append(
                    f"{folder}: page-group folder has children but "
                    f"no index.md -- this page will 404 or show blank"
                )

    return errors
```

### Step 5 -- check file/folder naming

File and folder names become URL slugs. Check for:
- Special characters (apostrophes, quotes, ampersands, etc.)
- Non-ASCII characters that may not URL-encode cleanly
- Spaces (ReadMe converts them but they can cause edge-case issues)
- Names that collide after slug normalization

```python
import re
from pathlib import Path

def check_naming(docs_root):
    docs = Path(docs_root)
    warnings = []

    problematic = re.compile(r"[^a-zA-Z0-9._\-/ ]")

    for item in docs.rglob("*"):
        if any(part.startswith(".") or part == "__MACOSX"
               for part in item.parts):
            continue
        if item.name in ("_order.yaml",):
            continue
        if item.suffix == ".md":
            name = item.stem
        elif item.is_dir():
            name = item.name
        else:
            continue

        if problematic.search(name):
            warnings.append(
                f"{item}: name '{name}' contains special characters "
                f"that may cause URL routing issues"
            )

    return warnings
```

### Step 6 -- compose customer response

Write a single, compact response the SE can send to the customer. Keep it
concise -- the SE can always expand on details if needed.

Structure:
1. **Summary table** -- one row per issue: file path, what is wrong, fix.
2. **Fix steps** -- a short numbered list of the exact edits (files to
   create/change, lines to add/remove). No code fences around YAML
   one-liners; use inline `backticks` instead. Use diffs only for
   multi-line frontmatter fixes where context helps.
3. **Verification** -- one sentence: push the changes, wait a few minutes
   for sync, and check the ReadMe dashboard for errors.
4. **Links** -- list relevant ReadMe docs links from the background section.

Do NOT repeat verification instructions per issue -- put them once at the
end. Do NOT wrap the entire response in a blockquote. Avoid deeply nested
formatting (blockquote + code fence + YAML) -- it renders poorly.

## Common issue patterns (cheat sheet)

| Symptom | Likely root cause |
|---|---|
| Page shows 404 | Page group folder missing `index.md`, or `_order.yaml` references an item that doesn't exist at that level |
| Blank entry in sidebar nav | `_order.yaml` lists a name that doesn't match any file/folder (misspelling, wrong level) |
| Page fails to load (no 404) | Broken frontmatter -- usually a missing closing `---` or invalid YAML |
| Page exists in repo but not in sidebar | Page/folder not listed in its parent's `_order.yaml` |
| Page appears in wrong category | Page listed in the wrong `_order.yaml` -- or folder physically placed under wrong parent |
| Content not syncing from Git | Branch name doesn't match ReadMe version name exactly |
| Sync error after rename | Old name still in `_order.yaml`, or `index.md` not updated |
| Special characters in page name cause issues | Folder/file names with apostrophes, ampersands, etc. don't URL-encode cleanly |

## Pitfalls

- Do NOT assume the customer's folder structure matches what ReadMe
  expects. Always map the full tree first.
- Do NOT overlook `_order.yaml` -- it controls sidebar visibility. A page
  can exist on disk but be invisible if not listed.
- Do NOT forget `index.md` for page groups. This is the most common cause
  of 404s for pages that "clearly exist."
- Frontmatter issues (missing closing `---`) are invisible to casual
  inspection -- the file looks fine in a text editor but ReadMe cannot
  parse it.
- Folder and file names with special characters (apostrophes, etc.) may
  work in some contexts but break in others. Recommend URL-safe names.
- The `__MACOSX` and `.DS_Store` files from macOS zips are noise -- ignore
  them during analysis.
- Branch names must match ReadMe version names exactly for sync to work.

## Example prompts

- "Customer's page shows 404 after they reorganized their repo folders"
- "A page appears in the sidebar but is blank -- they're using bi-directional sync"
- "Customer renamed a page in Git and now it's missing from ReadMe"
- "Check this docs/ folder for bi-directional sync issues"
- "Page works in some categories but not others after a folder move"

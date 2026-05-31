# Metadata Statistics

An extension for **AUTOMATIC1111 Stable Diffusion WebUI** / **WebUI Forge** that
indexes your generated PNGs, mines their embedded generation metadata, and
turns it into ranked statistics, a tag-aware image search, and a per-tag and
per-LoRA category system.

It adds a single tab to the WebUI called **Metadata Statistics**. Indexing is
manual — nothing happens until you click *Scan*. All extension data lives in
its own SQLite database under `data/`; **your image files are never modified**.

---

## Table of contents

- [Features](#features)
- [Installation](#installation)
- [Quick start](#quick-start)
- [The UI in detail](#the-ui-in-detail)
  - [Scan tab](#scan-tab)
  - [Statistics tab](#statistics-tab)
  - [Search tab](#search-tab)
  - [Categories tab](#categories-tab)
- [How metadata is parsed](#how-metadata-is-parsed)
- [Database layout](#database-layout)
- [Safety and privacy](#safety-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Requirements](#requirements)
- [Development notes](#development-notes)

---

## Features

- **Manual recursive scan** of every PNG under the outputs folder(s) configured
  in your WebUI settings (`outdir_samples`, `outdir_txt2img_samples`,
  `outdir_img2img_samples`, etc.). Subfolders are walked.
- **Incremental indexing** — files already seen and unchanged (same path,
  size, and mtime) are skipped. There is also a *Force full rescan* checkbox.
- **Ranked statistics** with separate top-N lists for:
  - positive prompt tags
  - negative prompt tags
  - LoRAs (by name; weights and other modifiers are ignored)
  - checkpoint models
- **Search** by tag with two modes:
  - **Simple**: three boxes for `AND`, `OR`, `NOT` lists of comma-separated tags.
  - **Expert**: free-form query supporting `AND`, `OR`, `NOT`, parentheses,
    quoted phrases, and implicit `AND` (`red_hair (blue_eyes OR green_eyes) NOT armor`).
  - **Partial match** (default ON) — `ellie` matches `ellietlou1`.
  - **Category filter** with three modes: *Any*, *In category*, and
    *Missing category* (great for finding images you haven't yet labelled).
  - Paginated thumbnail gallery with a detail panel showing full prompt, model,
    LoRAs, categories, and the raw infotext chunk.
  - Untagged images appear in search results when no query is provided, so you
    never lose track of them.
- **Categories** that group related tags **and/or** LoRAs:
  - Create as many categories as you like (e.g. *Characters*, *Scene*,
    *Lighting*, *NSFW*, *Style*).
  - A tag or LoRA can belong to many categories.
  - Image↔category assignment is **materialized**: whenever you assign or
    unassign a tag or LoRA, the database is updated immediately so search and
    filtering are fast.
  - **No metadata is ever written back into the image files.** All category
    data lives in this extension's database.
- **On-disk thumbnail cache** (256 px JPEGs) for fast gallery rendering.
- **Robust prompt parsing** that handles common A1111 quirks: bracketed
  weights (`(blue_eyes:1.2)`), grouped weights spanning commas
  (`(deformed, distorted, disfigured:1.3)`), `<lora:name:weight>`,
  `<lyco:name:weight>`, and other angle-bracketed extension syntax such as
  `<hypernet:…>`.

---

## Installation

### Recommended: via WebUI's *Extensions → Install from URL*

1. Open your WebUI.
2. Go to **Extensions → Install from URL**.
3. Paste the repository URL and click **Install**.
4. Reload the WebUI.

### Manual install

```bash
cd <webui-root>/extensions
git clone <repository-url> sd-webui-metadata-statistics
```

Then restart the WebUI. A new **Metadata Statistics** tab appears at the top.

No extra Python dependencies are required — the extension uses Pillow,
`gradio`, and stdlib `sqlite3`, all of which ship with WebUI.

---

## Quick start

1. Open the **Metadata Statistics** tab.
2. Go to **Scan** and click **Scan** (leave *Force full rescan* unchecked).
   The first scan parses every PNG under your outputs folder and generates a
   thumbnail per image. This may take a while for large collections.
3. Click **Refresh status** to see progress; the scan runs in a background
   thread so the WebUI stays responsive.
4. Open **Statistics** and click **Refresh statistics** to see your top tags,
   LoRAs, and models.
5. Open **Search**, type tags into the *Must include (AND)* box, and click
   **Search**.
6. Optionally open **Categories**, create *Characters*, *Scene*, etc., and
   start assigning tags or LoRAs to them.

---

## The UI in detail

### Scan tab

| Control                | What it does |
|------------------------|--------------|
| **Scan**               | Starts a background scan. The button returns immediately. |
| **Force full rescan**  | Re-parses every PNG even if its size/mtime is unchanged. Use this after upgrading the extension to repair any rows written by the old parser. |
| **Refresh status**     | Re-reads the in-process progress dictionary and updates the status display. |
| **Scan roots**         | The folders that will be walked, read from `shared.opts.outdir_*`. |

Each scan visits every `*.png` file under the configured roots and:
- skips it if it's already indexed with the same size + mtime (unless
  *Force full rescan* is on),
- otherwise parses the PNG `parameters` text chunk, normalizes tags and LoRAs,
  records the model name,
- materializes that image's category memberships,
- generates a 256 px JPEG thumbnail under `data/thumbs/<shard>/<id>.jpg`.

### Statistics tab

Pick a *Top N* limit (20–1000) and click **Refresh statistics**. Four
sub-tabs show ranked frequency tables (most → least):

- **Positive tags** — every comma-separated, normalized token from the
  positive prompt.
- **Negative tags** — same, for the negative prompt.
- **LoRAs** — names only; weights are not part of the count.
- **Models** — checkpoint name from the `Model:` infotext field. Images that
  failed to parse contribute to `(unknown)`.

A tag that appears multiple times in the same prompt only counts once for
that image.

### Search tab

| Control                            | What it does |
|------------------------------------|--------------|
| **Expert mode**                    | Replaces the three simple boxes with a single query box. |
| **Partial match (substring)**      | On by default. `ellie` matches `ellietlou1`. Off = exact tag match. |
| **Must include (AND)**             | Comma-separated tags that must all be present. |
| **Any of (OR)**                    | Comma-separated tags — at least one must be present. |
| **Must exclude (NOT)**             | Comma-separated tags that must not appear. |
| **Expert query**                   | Supports `AND`, `OR`, `NOT`, parens, quoted phrases, and implicit `AND` between adjacent terms. Examples below. |
| **Category filter / mode**         | Restrict to images **In** a category, **Missing** a category, or **Any** (no category filter). |
| **Refresh categories**             | Reloads the dropdown after you add/rename/delete a category. |
| **Search / Prev / Next**           | Paginate 60 results per page (most recent first). |
| **Gallery**                        | Click a thumbnail to load its details. |
| **Raw infotext**                   | Expandable accordion showing the unmodified `parameters` chunk read from the file on demand. |

#### Expert query examples

```
red_hair AND smile
red_hair (blue_eyes OR green_eyes) NOT armor
"long hair" AND red_eyes
NOT 1girl
```

Notes:
- Operators are case-insensitive (`AND`, `and`, `And` all work).
- Adjacent terms without an explicit operator are AND'd:
  `red_hair smile` is the same as `red_hair AND smile`.
- All terms are normalized exactly like indexed tags (lowercased, underscores
  → spaces, weights/parens stripped).

#### Category filter modes

- **Any** — no category constraint.
- **In category X** — only images that are members of X.
- **Missing category X** — only images that are *not* members of X. This is
  the labelling workflow: pick *Characters*, set *Missing category*, and the
  gallery shows everything you haven't yet labelled. Open each, identify the
  character, add the corresponding tag or LoRA to *Characters*, and the image
  vanishes from the list.

### Categories tab

A single column with category-management controls:

| Control                | What it does |
|------------------------|--------------|
| **New category**       | Create a category by name. |
| **Category**           | Dropdown of existing categories. |
| **Rename to**          | Rename the selected category. |
| **Delete category**    | Delete the selected category and all its assignments. |
| **Refresh**            | Reload the dropdown. |

Below that, two side-by-side sections show what the selected category contains:

- **Tags in this category** — assign by normalized tag text (e.g. `blue eyes`).
- **LoRAs in this category** — assign by LoRA name (case-insensitive match).

An image becomes a member of a category if **any** of its tags **or** LoRAs is
assigned to that category. Re-materialization happens immediately on
assign/unassign — no rescan needed.

---

## How metadata is parsed

The parser is in [`ms_lib/parser.py`](ms_lib/parser.py).

1. **Read** — the PNG is opened with Pillow and its text chunks are scanned
   for `parameters`, `Parameters`, `prompt`, `Comment`, or `Description` (in
   that order). If none are present the file is recorded with empty
   tag/LoRA/model fields and still appears in search.
2. **Split into sections** — WebUI's own
   `modules.infotext_utils.parse_generation_parameters` is reused to split the
   text into a `Prompt`, `Negative prompt`, and a dict of key/value parameters.
   A small fallback parser handles cases where that import isn't available.
3. **Extract LoRAs** — two passes:
   - strict `<lora:name:…>` and `<lyco:name:…>` regexes,
   - a catch-all `<…>` regex that handles whitespace inside the brackets and
     other extension syntax such as `<hypernet:foo:0.5>`. Each matched segment
     is replaced with `, ` so it can never glue adjacent tags together.
   - The `Lora hashes` / `TI hashes` / `Lora` infotext fields also contribute
     LoRA names.
4. **Flatten paren groups** — A1111 syntax allows grouped weights like
   `(a, b, c:1.3)`. The parser repeatedly strips innermost `(…)` / `[…]` /
   `{…}` groups (dropping any trailing `:weight`) before comma-splitting, so
   `(deformed, distorted, disfigured:1.3)` becomes three separate tags.
5. **Split and normalize** — each prompt is split on commas and each token is:
   - trimmed,
   - stripped of any wrapping brackets that remain,
   - stripped of a trailing `:weight`,
   - lower-cased,
   - underscores → spaces,
   - internal whitespace collapsed.
   - The literals `break` and `and` (case-insensitive) are dropped as control
     keywords.
6. **Deduplicate** — a tag that appears more than once in the same prompt
   counts once for that image.
7. **Model name** is read from the `Model:` infotext field (string,
   stripped). LoRA weights and trigger-word styling are intentionally
   discarded.

---

## Database layout

The database is a single SQLite file at
`<extension>/data/metadata.db` (WAL mode). The schema lives in
[`ms_lib/db.py`](ms_lib/db.py).

| Table              | Purpose |
|--------------------|---------|
| `images`           | One row per indexed PNG. Stores `path`, `mtime`, `size`, `model`, `parse_ok`, `indexed_at`. |
| `tags`             | Distinct normalized tag strings. |
| `image_tags`       | (image_id, tag_id, kind). `kind = 0` for positive prompt, `1` for negative. |
| `loras`            | Distinct LoRA names. |
| `image_loras`      | (image_id, lora_id). |
| `categories`       | User-defined categories. |
| `tag_categories`   | (tag_id, category_id) — tag→category assignments. |
| `lora_categories`  | (lora_id, category_id) — LoRA→category assignments. |
| `image_categories` | Materialized (image_id, category_id) rows = `image_tags ⋈ tag_categories ∪ image_loras ⋈ lora_categories`. Rebuilt incrementally on assign/unassign and per-image during scan. |
| `meta`             | Simple key/value table for future use. |

Thumbnails live under `data/thumbs/<aa>/<bb>/<imageId>.jpg`, sharded by the
first four digits of the zero-padded image id.

---

## Safety and privacy

- **Your image files are never modified.** The extension only reads PNG text
  chunks; nothing is written back. All metadata, categories, and thumbnails
  live in `data/` inside the extension folder.
- **Only configured outputs are scanned.** The scanner walks the paths
  reported by `shared.opts.outdir_*` (or `outputs/` fallback). Nothing under
  `models/`, `embeddings/`, `repositories/`, or your home directory is
  touched.
- **Everything is offline.** No network calls, no telemetry, no third-party
  services.
- The SQLite WAL files (`metadata.db-wal`, `metadata.db-shm`) and the
  thumbnail folder are git-ignored by default.

---

## Troubleshooting

### "I edited a prompt / fixed the parser but search still returns old results"

Click **Force full rescan** on the **Scan** tab. The incremental scanner skips
files whose size + mtime is unchanged, which means old tag rows persist after
a parser change. A force-full pass rewrites them.

### "A tag appears merged with surrounding text, e.g. `blue eyes <lora:foo:0> trigger`"

This is the signature of an image that was indexed by an older version of the
parser. Restart the WebUI to pick up the latest code, then **Force full
rescan**.

### "My category isn't matching an image even though the right tag is in the prompt"

Two common causes:
1. The image was indexed before the tag's category was assigned **and** the
   tag is actually stored under a different normalization. Look at the
   *Statistics → Positive tags* table to find how the tag is normalized, then
   assign that exact form.
2. The image's tag is still merged from an old parse. Force full rescan.

### "I moved my outputs folder. What now?"

Update `outdir_samples` (or the per-mode variants) in WebUI's settings to the
new path, restart, and click **Scan** again. The old rows still point to the
old paths — there is currently no "prune missing files" command, but they're
harmless other than showing as broken thumbnails.

### "Statistics show LoRA weights as part of the LoRA name"

That would be a parsing bug. Please open an issue with the PNG's raw
`parameters` chunk (visible in the Search tab's *Raw infotext* accordion).

---

## Project layout

```
sd-webui-metadata-statistics/
├── README.md
├── .gitignore
├── scripts/
│   └── metadata_statistics.py   # entry point, registers the UI tab
├── ms_lib/                      # library code imported by the entry point
│   ├── __init__.py
│   ├── paths.py                 # resolves outputs roots + cache locations
│   ├── db.py                    # SQLite schema and per-table helpers
│   ├── parser.py                # PNG text-chunk → tags / LoRAs / model
│   ├── scanner.py               # background recursive walker + thumbnailer
│   ├── search.py                # AND/OR/NOT, expert-query parser, ranking
│   ├── categories.py            # category CRUD + tag/LoRA assignment
│   └── ui.py                    # all Gradio Blocks for the tab
└── data/                        # created at runtime, git-ignored
    ├── metadata.db
    └── thumbs/
```

---

## Requirements

- AUTOMATIC1111 WebUI **or** WebUI Forge (any version with Gradio 4.x).
- Python 3.10+ (whatever the WebUI itself uses).
- Pillow (already bundled with the WebUI).
- No extra `pip install` is required.

---

## Development notes

- The extension intentionally avoids adding `Script` classes so it doesn't
  appear in the txt2img/img2img script dropdowns.
- The scanner runs in a single background thread to avoid SQLite write
  contention. Read queries from the UI happen on the request thread.
- DB writes use a process-level mutex (`db._lock`) around each
  `_connect()`/`close()` cycle. Connections are short-lived; the on-disk file
  is in WAL mode so readers don't block the writer.
- The expert-query parser is hand-written (no `pyparsing` or similar);
  see [`ms_lib/search.py`](ms_lib/search.py) for the grammar in a docstring.
- Tag normalization is centralized in `parser.normalize_tag` so that indexing,
  search input, and category assignment all use the same canonical form.
- Schema migrations are handled by `CREATE TABLE IF NOT EXISTS`; new tables
  appear automatically on the next `init_db()` call. There are currently no
  destructive migrations.

Contributions and bug reports are welcome.

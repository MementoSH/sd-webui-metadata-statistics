# Metadata Statistics

A WebUI / WebUI Forge extension that indexes your generated PNGs, extracts
prompt tags, LoRAs and model names from PNG metadata, and gives you ranked
statistics, a tag search and a category system. Adds one tab called
**Metadata Statistics**. Image files are never modified. For local thumbnails were chosen for performance reasons while the WebUI is running. Each full image from the `outputs` folder is resized to a height of 256 pixels, with the width adjusted proportionally. Each thumbnail takes about 13 KB. In the future, there will be an option to choose between local thumbnails, which are faster but use disk space, and thumbnails generated on run, which are slower but do not require extra disk space.

## Features

- Manual recursive scan of the outputs folder(s) configured in WebUI.
- Incremental indexing with a _Force full rescan_ option.
- Ranked Top N lists for positive tags, negative tags, LoRAs and models.
- Tag search with `AND` / `OR` / `NOT`, expert query mode, and optional
  substring matching (e.g. `ellie` finds `ellietlou1`).
- Categories that group tags and/or LoRAs. An image belongs to a category if
  any of its tags or LoRAs is assigned to it. Filter search by _In category_,
  _Missing category_ or _Any_.
- Paginated thumbnail gallery with a detail panel.
- Local SQLite database and on-disk thumbnail cache inside `data/`.

## Install

In WebUI: _Extensions, Install from URL_, paste the repo URL.

Or manually:

```bash
cd <webui-root>/extensions
git clone https://github.com/MementoSH/sd-webui-metadata-statistics.git
```

Restart the WebUI. No extra Python packages are required.

## Quick start

1. Open **Metadata Statistics**, go to **Scan**, click **Scan**.
2. Click **Refresh status** while it runs.
3. In **Statistics**, click **Refresh statistics**.
4. In **Search**, type tags into the AND box and click **Search**.
5. In **Categories**, create a category and assign tags or LoRAs to it.

## Tabs

### Scan

Walks every PNG under your WebUI outputs paths. Skips files already indexed
with the same size and mtime unless _Force full rescan_ is ticked.

### Statistics

Four ranked tables: positive tags, negative tags, LoRAs, models. A tag that
appears multiple times in one prompt counts once for that image.

### Search

- Simple mode: AND, OR, NOT boxes (comma-separated tags).
- Expert mode: free-form query with `AND`, `OR`, `NOT`, parens, quoted
  phrases, and implicit `AND` between adjacent terms.
- _Partial match_ on by default for substring search.
- Category filter with _Any_, _In category_, _Missing category_. Use _Missing_
  to find images you have not yet labelled.
- Click a thumbnail to see full prompt, model, LoRAs, categories and the raw
  infotext.

### Categories

Create / rename / delete categories. Assign tags or LoRAs to them. Image
membership is recomputed immediately, no rescan needed.

## How parsing works

PNG `parameters` text chunk is read with Pillow and split using WebUI's own
`parse_generation_parameters`. Tag normalization: trim, lowercase, underscores
to spaces, strip wrapping `()` / `[]` / `{}` and trailing `:weight`. Grouped
weights like `(a, b, c:1.3)` are flattened so each tag separates. LoRA tokens
like `<lora:name:weight>` and `<lyco:name:weight>` are extracted by name and
replaced with `, ` so trigger words after them stay separate. Other angle
bracket extension syntax (e.g. `<hypernet:...>`) is also stripped.

## Safety

- Image files are never written to. All data lives in `data/` inside the
  extension folder.
- Only the WebUI outputs paths are scanned. `models/`, `embeddings/`, etc. are
  never touched.
- No network calls, no telemetry.

## Project layout

```
sd-webui-metadata-statistics/
├── scripts/metadata_statistics.py   # entry point, registers the tab
├── ms_lib/
│   ├── paths.py        # outputs roots, cache paths
│   ├── db.py           # SQLite schema and helpers
│   ├── parser.py       # PNG metadata to tags / LoRAs / model
│   ├── scanner.py      # background walker + thumbnailer
│   ├── search.py       # query parser, ranking
│   ├── categories.py   # category CRUD and assignment
│   └── ui.py           # all Gradio Blocks
└── data/               # created at runtime, git-ignored
```

## Requirements

WebUI or WebUI Forge with Gradio 4.x. Pillow ships with WebUI; no extra
dependencies.

## Troubleshooting

If old tags look wrong after upgrading the extension, click **Force full
rescan** on the **Scan** tab. The incremental scanner skips unchanged files,
so a force pass is needed to rewrite rows produced by an older parser.

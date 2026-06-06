"""Gradio UI for the Metadata Statistics extension.

Built as one tab with four sub-tabs: Scan, Statistics, Search, Categories.
"""

from __future__ import annotations

import html
import os
from typing import Any, List, Optional, Tuple

import gradio as gr

from . import categories as cats
from . import db, paths, scanner, search

_CAT_NONE = "—"  # dropdown sentinel for "no category filter"


PAGE_SIZE = 60


# ---------------------------------------------------------------------------
# Scan tab
# ---------------------------------------------------------------------------

# Static DOM for the progress bar. Rendered once; never re-rendered by Gradio.
# JS (registered in build_tab via block.load + js) mutates the inner nodes
# in-place, so CSS transitions on width can apply and the component never
# blinks the way a polled gr.HTML / gr.Markdown does.
_PROGRESS_STATIC_HTML = """
<div id="ms-progress-shell" style="
  width: 100%;
  background-color: rgba(127,127,127,0.25);
  border-radius: 6px;
  overflow: hidden;
  height: 22px;
  position: relative;
  font-family: sans-serif;
  display: none;
">
  <div id="ms-progress-bar-fill" style="
    width: 0%;
    height: 22px;
    background-color: #4caf50;
    transition: width 0.8s ease-out, background-color 0.3s ease;
  "></div>
  <div id="ms-progress-bar-label" style="
    position: absolute;
    inset: 0;
    line-height: 22px;
    text-align: center;
    font-weight: 600;
    color: #fff;
    text-shadow: 0 0 2px rgba(0,0,0,0.6);
    pointer-events: none;
  "></div>
</div>
<div id="ms-progress-status" style="margin-top: 8px; font-family: inherit;"></div>
"""


# Page-load script: defines window.msUpdateProgressBar(snap) which mutates the
# bar's DOM in place. Gradio just keeps a hidden gr.JSON in sync with
# scanner.get_progress(); its .change event runs this with the new snapshot.
_PROGRESSjs_INIT = r"""
() => {
  window.msUpdateProgressBar = function(snap) {
    if (!snap) return;
    var shell = document.getElementById('ms-progress-shell');
    var fill = document.getElementById('ms-progress-bar-fill');
    var label = document.getElementById('ms-progress-bar-label');
    var status = document.getElementById('ms-progress-status');
    if (!shell || !fill || !label || !status) return;

    var running = !!snap.running;
    var counting = !!snap.counting;
    var total = +snap.total_files || 0;
    var seen = +snap.total_seen || 0;
    var finished = !!snap.finished_at && !running;

    if (!running && !finished) {
      shell.style.display = 'none';
    } else {
      shell.style.display = 'block';
      if (running && (counting || total <= 0)) {
        fill.style.width = '100%';
        fill.style.backgroundColor = '#4caf50';
        fill.style.backgroundImage = 'linear-gradient(45deg,' +
          ' rgba(255,255,255,0.25) 25%, transparent 25%,' +
          ' transparent 50%, rgba(255,255,255,0.25) 50%,' +
          ' rgba(255,255,255,0.25) 75%, transparent 75%, transparent)';
        fill.style.backgroundSize = '24px 24px';
        label.textContent = 'Counting files…';
      } else {
        var pct = total > 0
          ? Math.max(0, Math.min(100, Math.round(100 * seen / total)))
          : (finished ? 100 : 0);
        fill.style.width = pct + '%';
        fill.style.backgroundImage = 'none';
        fill.style.backgroundColor = finished ? '#2e7d32' : '#4caf50';
        var suffix = finished ? 'done' : 'scanning';
        label.textContent = seen + ' / ' + total + '  (' + pct + '%) — ' + suffix;
      }
    }

    var esc = function(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    };
    var lines = [];
    var state = running
      ? (counting ? 'counting files' : 'running')
      : (snap.finished_at ? 'finished' : 'idle');
    lines.push('<div><strong>State:</strong> ' + state + '</div>');
    if (snap.started_at) {
      lines.push('<div>Started: ' +
        new Date(snap.started_at * 1000).toLocaleTimeString() + '</div>');
    }
    if (snap.finished_at) {
      var el = (snap.finished_at - snap.started_at).toFixed(1);
      lines.push('<div>Finished: ' +
        new Date(snap.finished_at * 1000).toLocaleTimeString() +
        ' (in ' + el + 's)</div>');
    }
    lines.push('<ul style="margin: 6px 0 0 18px;">');
    var seenStr = '<strong>' + seen + '</strong>' +
      (total ? ' / <strong>' + total + '</strong>' : '');
    lines.push('<li>Seen: ' + seenStr + '</li>');
    lines.push('<li>Inserted: <strong>' + (+snap.inserted || 0) + '</strong></li>');
    lines.push('<li>Updated: <strong>' + (+snap.updated || 0) + '</strong></li>');
    lines.push('<li>Skipped (unchanged): <strong>' + (+snap.skipped_unchanged || 0) + '</strong></li>');
    lines.push('<li>Parse failed: <strong>' + (+snap.parse_failed || 0) + '</strong></li>');
    lines.push('</ul>');
    if (snap.current_path) {
      lines.push('<div style="margin-top: 6px;">Current: <code>' +
        esc(snap.current_path) + '</code></div>');
    }
    if (snap.errors && snap.errors.length) {
      lines.push('<div style="margin-top: 6px;">Recent errors:' +
        '<ul style="margin-left: 18px;">');
      for (var i = 0; i < snap.errors.length; i++) {
        lines.push('<li>' + esc(snap.errors[i]) + '</li>');
      }
      lines.push('</ul></div>');
    }
    status.innerHTML = lines.join('');
  };
}
"""


def _scan_roots_markdown() -> str:
    roots = paths.outputs_roots()
    lines = ["**Scan roots (from WebUI options):**"]
    for r in roots:
        ok = "OK" if os.path.isdir(r) else "MISSING"
        lines.append(f"- `{r}` ({ok})")
    return "\n".join(lines)


def _build_scan_tab() -> Tuple[gr.JSON, gr.Markdown]:
    with gr.Column():
        roots_md = gr.Markdown(_scan_roots_markdown())
        with gr.Row():
            scan_btn = gr.Button("Scan", variant="primary")
            full_rescan = gr.Checkbox(label="Force full rescan", value=False)
            refresh_btn = gr.Button("Refresh status")
        # Static DOM (rendered once). build_tab() wires up periodic polling +
        # a JS DOM-mutation function — see _PROGRESSjs_INIT.
        gr.HTML(_PROGRESS_STATIC_HTML)
        progress_state = gr.JSON(
            value=scanner.get_progress(),
            visible=False,
        )

    def _do_scan(force: bool):
        scanner.start_scan(full_rescan=bool(force))
        return scanner.get_progress()

    def _do_refresh():
        return scanner.get_progress(), _scan_roots_markdown()

    scan_btn.click(_do_scan, inputs=full_rescan, outputs=progress_state)
    refresh_btn.click(_do_refresh, outputs=[progress_state, roots_md])

    return progress_state, roots_md


# ---------------------------------------------------------------------------
# Statistics tab
# ---------------------------------------------------------------------------

def _ranked_rows(kind: str, limit: int) -> List[List[Any]]:
    return [[i + 1, name, count] for i, (name, count) in enumerate(search.ranked(kind, limit))]


def _build_stats_tab() -> None:
    with gr.Column():
        with gr.Row():
            limit = gr.Slider(20, 1000, value=200, step=20, label="Top N")
            refresh = gr.Button("Refresh statistics", variant="primary")
        with gr.Tabs():
            with gr.TabItem("Positive tags"):
                pos_df = gr.Dataframe(
                    headers=["#", "Tag", "Count"],
                    datatype=["number", "str", "number"],
                    interactive=False,
                    wrap=True,
                )
            with gr.TabItem("Negative tags"):
                neg_df = gr.Dataframe(
                    headers=["#", "Tag", "Count"],
                    datatype=["number", "str", "number"],
                    interactive=False,
                    wrap=True,
                )
            with gr.TabItem("LoRAs"):
                lora_df = gr.Dataframe(
                    headers=["#", "LoRA", "Count"],
                    datatype=["number", "str", "number"],
                    interactive=False,
                    wrap=True,
                )
            with gr.TabItem("Models"):
                model_df = gr.Dataframe(
                    headers=["#", "Model", "Count"],
                    datatype=["number", "str", "number"],
                    interactive=False,
                    wrap=True,
                )

    def _refresh(n: float):
        n = int(n)
        return (
            _ranked_rows("pos", n),
            _ranked_rows("neg", n),
            _ranked_rows("lora", n),
            _ranked_rows("model", n),
        )

    refresh.click(_refresh, inputs=limit, outputs=[pos_df, neg_df, lora_df, model_df])


# ---------------------------------------------------------------------------
# Search tab
# ---------------------------------------------------------------------------

def _gallery_items(image_ids: List[int]) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    for image_id in image_ids:
        thumb = paths.thumb_path_for(image_id)
        if not os.path.isfile(thumb):
            # Fallback: try to point at the original file
            with db.connect() as con:
                row = con.execute("SELECT path FROM images WHERE id=?", (image_id,)).fetchone()
            if row and os.path.isfile(row["path"]):
                items.append((row["path"], str(image_id)))
            continue
        items.append((thumb, str(image_id)))
    return items


def _format_details_md(details: Optional[dict]) -> str:
    if not details:
        return "_Select an image to see its details._"
    lines = []
    lines.append(f"**File:** `{details['path']}`")
    lines.append(f"**Model:** {details.get('model') or '_unknown_'}")
    if details.get("loras"):
        lines.append("**LoRAs:** " + ", ".join(details["loras"]))
    if details.get("categories"):
        lines.append("**Categories:** " + ", ".join(details["categories"]))
    lines.append("")
    lines.append("**Positive tags:**")
    lines.append(", ".join(details["positive"]) if details["positive"] else "_(none)_")
    lines.append("")
    lines.append("**Negative tags:**")
    lines.append(", ".join(details["negative"]) if details["negative"] else "_(none)_")
    return "\n".join(lines)


def _build_search_tab() -> None:
    state_ids: List[int] = []  # current page ids (kept in gr.State)

    with gr.Column():
        with gr.Row():
            expert_toggle = gr.Checkbox(label="Expert mode (single query box)", value=False)
            partial_toggle = gr.Checkbox(
                label="Partial match (substring)",
                value=True,
                info="When on, 'ellie' matches tags like 'ellietlou1'. Off = exact tag match.",
            )

        with gr.Group(visible=True) as simple_group:
            with gr.Row():
                and_box = gr.Textbox(label="Must include (AND)", placeholder="red hair, smile")
                or_box = gr.Textbox(label="Any of (OR)", placeholder="blue eyes, green eyes")
                not_box = gr.Textbox(label="Must exclude (NOT)", placeholder="armor, helmet")
        with gr.Group(visible=False) as expert_group:
            expert_box = gr.Textbox(
                label="Expert query",
                placeholder='red_hair AND (blue_eyes OR green_eyes) NOT armor',
                lines=2,
            )

        with gr.Row():
            cat_filter = gr.Dropdown(
                label="Category filter",
                choices=[_CAT_NONE] + _cat_choices(),
                value=_CAT_NONE,
                interactive=True,
            )
            cat_mode = gr.Radio(
                label="Category mode",
                choices=[("Any", "any"), ("In category", "in"), ("Missing category", "missing")],
                value="any",
                interactive=True,
                info="'Missing' = images that don't yet belong to the chosen category.",
            )
            cat_filter_refresh = gr.Button("Refresh categories")

        with gr.Row():
            run_btn = gr.Button("Search", variant="primary")
            prev_btn = gr.Button("Prev")
            next_btn = gr.Button("Next")
            page_md = gr.Markdown("_No search yet._")

        gallery = gr.Gallery(
            label="Results",
            columns=6,
            height=560,
            preview=False,
            allow_preview=True,
            object_fit="cover",
        )

        details_md = gr.Markdown("_Select an image to see its details._")
        with gr.Accordion("Raw infotext", open=False):
            raw_md = gr.Markdown("")

        # state
        page_state = gr.State(1)
        ids_state = gr.State([])
        # last query state, so prev/next reuse the same query
        last_query = gr.State({
            "mode": "simple", "and": "", "or": "", "not": "", "expert": "",
            "partial": True, "cat_id": None, "cat_mode": "any",
        })

    def _toggle_mode(expert: bool):
        return gr.update(visible=not expert), gr.update(visible=expert)

    expert_toggle.change(_toggle_mode, inputs=expert_toggle, outputs=[simple_group, expert_group])

    def _do_search(expert: bool, andt: str, ort: str, nott: str, expert_q: str,
                   partial: bool, cat_choice: str, mode: str):
        cat_id = _cat_id_from_choice(cat_choice) if cat_choice and cat_choice != _CAT_NONE else None
        cat_mode_val = mode if cat_id is not None else "any"
        try:
            if expert:
                res = search.expert_search(
                    expert_q, page=1, page_size=PAGE_SIZE,
                    partial=bool(partial), category_id=cat_id, category_mode=cat_mode_val,
                )
            else:
                res = search.simple_search(
                    andt, ort, nott, page=1, page_size=PAGE_SIZE,
                    partial=bool(partial), category_id=cat_id, category_mode=cat_mode_val,
                )
        except Exception as e:  # noqa: BLE001
            return (
                gr.update(value=[]),
                f"**Query error:** {html.escape(str(e))}",
                1,
                [],
                {"mode": "expert" if expert else "simple", "and": andt, "or": ort, "not": nott,
                 "expert": expert_q, "partial": bool(partial),
                 "cat_id": cat_id, "cat_mode": cat_mode_val},
            )
        page_text = _format_page_text(res.page, res.total, res.page_size)
        return (
            gr.update(value=_gallery_items(res.image_ids)),
            page_text,
            res.page,
            res.image_ids,
            {"mode": "expert" if expert else "simple", "and": andt, "or": ort, "not": nott,
             "expert": expert_q, "partial": bool(partial),
             "cat_id": cat_id, "cat_mode": cat_mode_val},
        )

    def _change_page(delta: int, current_page: int, q: dict):
        new_page = max(1, int(current_page) + int(delta))
        partial = bool(q.get("partial", True))
        cat_id = q.get("cat_id")
        cat_mode_val = q.get("cat_mode", "any") if cat_id is not None else "any"
        try:
            if q.get("mode") == "expert":
                res = search.expert_search(
                    q.get("expert", ""), page=new_page, page_size=PAGE_SIZE,
                    partial=partial, category_id=cat_id, category_mode=cat_mode_val,
                )
            else:
                res = search.simple_search(
                    q.get("and", ""), q.get("or", ""), q.get("not", ""),
                    page=new_page, page_size=PAGE_SIZE,
                    partial=partial, category_id=cat_id, category_mode=cat_mode_val,
                )
        except Exception as e:  # noqa: BLE001
            return gr.update(), f"**Query error:** {html.escape(str(e))}", current_page, []
        # Clamp to the last available page if we went past the end
        if not res.image_ids and new_page > 1:
            return gr.update(), _format_page_text(int(current_page), res.total, res.page_size), current_page, []
        return (
            gr.update(value=_gallery_items(res.image_ids)),
            _format_page_text(res.page, res.total, res.page_size),
            res.page,
            res.image_ids,
        )

    def _refresh_cat_filter():
        return gr.update(choices=[_CAT_NONE] + _cat_choices(), value=_CAT_NONE)

    cat_filter_refresh.click(_refresh_cat_filter, outputs=cat_filter)

    run_btn.click(
        _do_search,
        inputs=[expert_toggle, and_box, or_box, not_box, expert_box,
                partial_toggle, cat_filter, cat_mode],
        outputs=[gallery, page_md, page_state, ids_state, last_query],
    )
    prev_btn.click(
        lambda p, q: _change_page(-1, p, q),
        inputs=[page_state, last_query],
        outputs=[gallery, page_md, page_state, ids_state],
    )
    next_btn.click(
        lambda p, q: _change_page(1, p, q),
        inputs=[page_state, last_query],
        outputs=[gallery, page_md, page_state, ids_state],
    )

    def _on_select(ids: List[int], evt: gr.SelectData):
        if not ids:
            return _format_details_md(None), ""
        idx = evt.index if isinstance(evt.index, int) else (evt.index[0] if evt.index else 0)
        if idx is None or idx < 0 or idx >= len(ids):
            return _format_details_md(None), ""
        details = search.get_image_details(int(ids[idx]))
        if not details:
            return _format_details_md(None), ""
        return _format_details_md(details), _raw_infotext_md(details)

    gallery.select(_on_select, inputs=ids_state, outputs=[details_md, raw_md])


def _format_page_text(page: int, total: int, page_size: int) -> str:
    if total == 0:
        return "_No matches._"
    last_page = max(1, (total + page_size - 1) // page_size)
    return f"Page **{page}** / {last_page} — {total} matches"


def _raw_infotext_md(details: dict) -> str:
    try:
        with db.connect() as con:
            row = con.execute(
                "SELECT path FROM images WHERE id=?", (details["id"],)
            ).fetchone()
        if not row:
            return ""
    except Exception:
        return ""
    # Re-read raw text chunk on demand; we don't cache it in DB.
    from .parser import _read_png_text  # local import to keep API tight
    raw = _read_png_text(row["path"]) or ""
    if not raw:
        return "_(no infotext chunk)_"
    return "```\n" + raw + "\n```"


# ---------------------------------------------------------------------------
# Categories tab
# ---------------------------------------------------------------------------

def _cat_choices() -> List[str]:
    return [f"{cid}: {name}" for cid, name in cats.list_categories()]


def _cat_id_from_choice(choice: str) -> Optional[int]:
    if not choice:
        return None
    head = choice.split(":", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def _build_categories_tab() -> None:
    with gr.Column():
        gr.Markdown(
            "Create categories like *Characters*, *Scene*, or *Lighting*. "
            "Then assign tags to them. A tag can belong to many categories. "
            "Image-category links are kept in this extension's database and "
            "are not written back into your image files."
        )
        with gr.Row():
            new_cat_name = gr.Textbox(label="New category", placeholder="Characters")
            new_cat_btn = gr.Button("Create category", variant="primary")
        with gr.Row():
            cat_select = gr.Dropdown(label="Category", choices=_cat_choices(), value=None, interactive=True)
            cat_refresh = gr.Button("Refresh")
            cat_rename = gr.Textbox(label="Rename to")
            cat_rename_btn = gr.Button("Rename")
            cat_delete_btn = gr.Button("Delete category", variant="stop")
        cat_status = gr.Markdown("")

        with gr.Row():
            with gr.Column():
                gr.Markdown("**Tags in this category**")
                with gr.Row():
                    tag_to_assign = gr.Textbox(label="Tag", placeholder="blue eyes")
                    assign_btn = gr.Button("Assign tag", variant="primary")
                    unassign_btn = gr.Button("Remove tag")
                tags_in_cat = gr.Dataframe(
                    headers=["Tag"],
                    datatype=["str"],
                    interactive=False,
                    wrap=True,
                )
            with gr.Column():
                gr.Markdown("**LoRAs in this category**")
                with gr.Row():
                    lora_to_assign = gr.Textbox(label="LoRA name", placeholder="myCharacterLora_v2")
                    assign_lora_btn = gr.Button("Assign LoRA", variant="primary")
                    unassign_lora_btn = gr.Button("Remove LoRA")
                loras_in_cat = gr.Dataframe(
                    headers=["LoRA"],
                    datatype=["str"],
                    interactive=False,
                    wrap=True,
                )

    def _refresh_choices():
        return gr.update(choices=_cat_choices())

    cat_refresh.click(_refresh_choices, outputs=cat_select)

    def _create(name: str):
        if not (name or "").strip():
            return gr.update(), "Category name cannot be empty."
        cid = cats.create_category(name.strip())
        return gr.update(choices=_cat_choices(), value=f"{cid}: {name.strip()}"), f"Created **{name.strip()}**."

    new_cat_btn.click(_create, inputs=new_cat_name, outputs=[cat_select, cat_status])

    def _rename(choice: str, new_name: str):
        cid = _cat_id_from_choice(choice)
        if cid is None:
            return gr.update(), "Pick a category first."
        try:
            cats.rename_category(cid, new_name)
        except Exception as e:  # noqa: BLE001
            return gr.update(), f"Error: {e}"
        return gr.update(choices=_cat_choices(), value=f"{cid}: {new_name.strip()}"), f"Renamed to **{new_name.strip()}**."

    cat_rename_btn.click(_rename, inputs=[cat_select, cat_rename], outputs=[cat_select, cat_status])

    def _delete(choice: str):
        cid = _cat_id_from_choice(choice)
        if cid is None:
            return gr.update(), "Pick a category first.", [], []
        cats.delete_category(cid)
        return gr.update(choices=_cat_choices(), value=None), "Deleted.", [], []

    cat_delete_btn.click(_delete, inputs=cat_select,
                         outputs=[cat_select, cat_status, tags_in_cat, loras_in_cat])

    def _load_cat(choice: str):
        cid = _cat_id_from_choice(choice)
        if cid is None:
            return [], [], ""
        trows = [[t] for t in cats.tags_in_category(cid)]
        lrows = [[l] for l in cats.loras_in_category(cid)]
        return trows, lrows, f"{len(trows)} tag(s), {len(lrows)} LoRA(s) in category."

    cat_select.change(_load_cat, inputs=cat_select,
                      outputs=[tags_in_cat, loras_in_cat, cat_status])

    def _assign(choice: str, tag_text: str):
        cid = _cat_id_from_choice(choice)
        if cid is None:
            return [], "Pick a category first."
        changed, n = cats.assign_tag_to_category(tag_text, cid)
        if not n:
            return [[t] for t in cats.tags_in_category(cid)], "Empty tag."
        msg = f"Assigned **{n}**." if changed else f"**{n}** was already assigned."
        return [[t] for t in cats.tags_in_category(cid)], msg

    assign_btn.click(_assign, inputs=[cat_select, tag_to_assign],
                     outputs=[tags_in_cat, cat_status])

    def _unassign(choice: str, tag_text: str):
        cid = _cat_id_from_choice(choice)
        if cid is None:
            return [], "Pick a category first."
        changed = cats.unassign_tag_from_category(tag_text, cid)
        msg = "Removed." if changed else "Tag was not assigned."
        return [[t] for t in cats.tags_in_category(cid)], msg

    unassign_btn.click(_unassign, inputs=[cat_select, tag_to_assign],
                       outputs=[tags_in_cat, cat_status])

    def _assign_lora(choice: str, lora_text: str):
        cid = _cat_id_from_choice(choice)
        if cid is None:
            return [], "Pick a category first."
        changed, n = cats.assign_lora_to_category(lora_text, cid)
        if not n:
            return [[l] for l in cats.loras_in_category(cid)], "Empty LoRA name."
        msg = f"Assigned LoRA **{n}**." if changed else f"LoRA **{n}** was already assigned."
        return [[l] for l in cats.loras_in_category(cid)], msg

    assign_lora_btn.click(_assign_lora, inputs=[cat_select, lora_to_assign],
                          outputs=[loras_in_cat, cat_status])

    def _unassign_lora(choice: str, lora_text: str):
        cid = _cat_id_from_choice(choice)
        if cid is None:
            return [], "Pick a category first."
        changed = cats.unassign_lora_from_category(lora_text, cid)
        msg = "Removed." if changed else "LoRA was not assigned."
        return [[l] for l in cats.loras_in_category(cid)], msg

    unassign_lora_btn.click(_unassign_lora, inputs=[cat_select, lora_to_assign],
                            outputs=[loras_in_cat, cat_status])


# ---------------------------------------------------------------------------
# Top-level tab
# ---------------------------------------------------------------------------

def build_tab() -> gr.Blocks:
    db.init_db()
    with gr.Blocks(analytics_enabled=False) as block:
        gr.Markdown("## Metadata Statistics")
        with gr.Tabs():
            with gr.TabItem("Scan"):
                scan_progress_state, _ = _build_scan_tab()
            with gr.TabItem("Statistics"):
                _build_stats_tab()
            with gr.TabItem("Search"):
                _build_search_tab()
            with gr.TabItem("Categories"):
                _build_categories_tab()

        # Register the in-place DOM updater once on page load.
        block.load(fn=None, inputs=None, outputs=None, js=_PROGRESSjs_INIT)
        # Poll the snapshot once a second; the JSON state update is invisible.
        block.load(
            fn=scanner.get_progress,
            outputs=scan_progress_state,
            every=1.0,
        )
        # Whenever the snapshot changes, run JS to mutate the bar in-place.
        # No Python re-render of the bar/status, so no flicker.
        scan_progress_state.change(
            fn=None,
            inputs=scan_progress_state,
            outputs=None,
            js="(snap) => { try { window.msUpdateProgressBar(snap); } catch(e) {} }",
        )
    return block

import {PRESETS, displayValue, downloadName, isObject, parsePreview, presetText} from "./preview.mjs";

const byId = (id) => document.getElementById(id);
const editor = byId("manifest-editor");
const buttons = [...document.querySelectorAll("[data-preset]")];
let selectedPreset = "artifact";
let updateTimer;
let current;

function setText(id, value) { byId(id).textContent = displayValue(value); }
function listItems(id, values, empty, argumentMode = false) {
  const list = byId(id);
  const items = Array.isArray(values) ? values : [];
  list.replaceChildren();
  for (const value of items.slice(0, 128)) {
    const item = document.createElement("li");
    if (argumentMode) {
      const code = document.createElement("code");
      code.textContent = displayValue(value);
      item.append(code);
    } else item.textContent = displayValue(value);
    list.append(item);
  }
  if (!items.length) {
    const item = document.createElement("li");
    item.textContent = empty;
    list.append(item);
  } else if (items.length > 128) {
    const item = document.createElement("li");
    item.textContent = "Preview limited to the first 128 entries.";
    list.append(item);
  }
}

function update() {
  clearTimeout(updateTimer);
  current = parsePreview(editor.value);
  const parsed = current.status === "parsed";
  const object = parsed && isObject(current.value);
  byId("byte-count").textContent = `${current.bytes.toLocaleString()} bytes`;
  byId("json-status").textContent = parsed ? "JSON parsed · not validated" : (current.status === "too-large" ? "Preview size limit reached" : "JSON needs attention");
  byId("json-status").dataset.tone = parsed ? "neutral" : "error";
  editor.setAttribute("aria-invalid", String(!parsed));
  byId("download").disabled = !parsed;
  byId("format").disabled = !parsed;
  byId("preview-content").hidden = !object;
  byId("parse-empty").hidden = Boolean(object);
  byId("review-strip").dataset.tone = !parsed || current.warnings.length ? "warning" : "neutral";
  byId("review-title").textContent = !parsed ? "The preview needs readable JSON" : current.warnings.length ? "Fields need a closer look" : "Parsing is the first step";
  byId("review-description").textContent = !parsed ? current.error : "This view checks JSON syntax and points out missing or unknown fields. It does not validate security, resource policy, image identity, or engine compatibility.";
  const warningList = byId("field-warnings");
  warningList.replaceChildren();
  warningList.hidden = !current.warnings.length;
  for (const warning of current.warnings) {
    const item = document.createElement("li");
    item.textContent = warning;
    warningList.append(item);
  }
  if (!object) return;
  const value = current.value;
  const limits = isObject(value.limits) ? value.limits : {};
  for (const [id, field] of [["run-id", "id"], ["task-id", "taskId"], ["owner-id", "ownerAgentId"], ["image-ref", "image"], ["network-value", "network"]]) setText(id, value[field]);
  setText("schema-tag", `Schema ${displayValue(value.schemaVersion, "unspecified")}`);
  for (const [id, field] of [["cpu-value", "cpus"], ["memory-value", "memoryMiB"], ["pids-value", "pids"], ["wall-value", "wallSeconds"]]) setText(id, limits[field]);
  byId("placeholder-note").hidden = !(typeof value.image === "string" && value.image.startsWith("example.invalid/"));
  listItems("argv-list", value.argv, "No argument entries to preview.", true);
  listItems("output-list", value.outputs, "No output paths declared.");
  setText("output-count", Array.isArray(value.outputs) ? value.outputs.length : 0);
}

function loadPreset(name) {
  if (!Object.hasOwn(PRESETS, name)) return;
  selectedPreset = name;
  editor.value = presetText(name);
  for (const button of buttons) {
    const active = button.dataset.preset === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  update();
}

for (const button of buttons) button.addEventListener("click", () => loadPreset(button.dataset.preset));
editor.addEventListener("input", () => { clearTimeout(updateTimer); updateTimer = setTimeout(update, 160); });
byId("reset").addEventListener("click", () => loadPreset(selectedPreset));
byId("format").addEventListener("click", () => {
  update();
  if (current.status !== "parsed") return;
  editor.value = JSON.stringify(current.value, null, 2);
  update();
});
byId("download").addEventListener("click", () => {
  update();
  if (current.status !== "parsed") return;
  // Preserve the user's exact text. Parsing here is not security validation.
  const blob = new Blob([editor.value], {type: "application/json;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = downloadName(current.value);
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
loadPreset(selectedPreset);

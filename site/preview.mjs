// Presentation helpers only. Python's runtime validator is authoritative.
export const VERSION = "0.1.0a1";
export const PREVIEW_BYTE_LIMIT = 64 * 1024;
const FIELDS = ["schemaVersion", "id", "taskId", "ownerAgentId", "image", "argv", "limits", "network", "outputs"];
const LIMIT_FIELDS = ["cpus", "memoryMiB", "pids", "wallSeconds"];
const digest = "a".repeat(64);

export const PRESETS = {
  artifact: {
    schemaVersion: 1, id: "write-artifact-01", taskId: "task-write-artifact", ownerAgentId: "agent-writer",
    image: `example.invalid/agentrooms/python@sha256:${digest}`,
    argv: ["python3", "-c", "from pathlib import Path; Path('result.txt').write_text('Hello from this task!\\n')"],
    limits: {cpus: 1, memoryMiB: 256, pids: 64, wallSeconds: 30}, network: "none", outputs: ["result.txt"],
  },
  analysis: {
    schemaVersion: 1, id: "batch-analysis-01", taskId: "task-batch-summary", ownerAgentId: "agent-analyst",
    image: `example.invalid/agentrooms/analysis@sha256:${"b".repeat(64)}`,
    argv: ["python3", "-c", "import json; from pathlib import Path; values=[3,5,8,13]; Path('summary.json').write_text(json.dumps({'count':len(values),'total':sum(values)}))"],
    limits: {cpus: 2, memoryMiB: 512, pids: 64, wallSeconds: 60}, network: "none", outputs: ["summary.json"],
  },
  tests: {
    schemaVersion: 1, id: "test-task-01", taskId: "task-sample-check", ownerAgentId: "agent-tester",
    image: `example.invalid/agentrooms/tests@sha256:${"c".repeat(64)}`,
    argv: ["python3", "-c", "from pathlib import Path; assert sum([1,2,3]) == 6; Path('check.txt').write_text('Sample assertion passed\\n')"],
    limits: {cpus: 1, memoryMiB: 128, pids: 32, wallSeconds: 15}, network: "none", outputs: ["check.txt"],
  },
};

export function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function fieldWarnings(value) {
  if (!isObject(value)) return ["A task manifest should be a JSON object, not an array or scalar."];
  const warnings = [];
  const missing = FIELDS.filter((key) => !Object.hasOwn(value, key));
  const unknown = Object.keys(value).filter((key) => !FIELDS.includes(key));
  if (missing.length) warnings.push(`Missing fields: ${missing.join(", ")}.`);
  if (unknown.length) warnings.push(`Unknown fields: ${unknown.join(", ")}.`);
  if (Object.hasOwn(value, "limits")) {
    if (!isObject(value.limits)) warnings.push("The limits field should be a JSON object.");
    else {
      const missingLimits = LIMIT_FIELDS.filter((key) => !Object.hasOwn(value.limits, key));
      const unknownLimits = Object.keys(value.limits).filter((key) => !LIMIT_FIELDS.includes(key));
      if (missingLimits.length) warnings.push(`Missing limit fields: ${missingLimits.join(", ")}.`);
      if (unknownLimits.length) warnings.push(`Unknown limit fields: ${unknownLimits.join(", ")}.`);
    }
  }
  for (const key of ["argv", "outputs"]) {
    if (Object.hasOwn(value, key) && !Array.isArray(value[key])) warnings.push(`The ${key} field should be an array.`);
  }
  return warnings;
}

export function parsePreview(text) {
  const bytes = new TextEncoder().encode(text).byteLength;
  if (bytes > PREVIEW_BYTE_LIMIT) return {status: "too-large", bytes, warnings: [], error: "Preview limited to 64 KiB. Your text has not been changed."};
  try {
    const value = JSON.parse(text);
    return {status: "parsed", bytes, value, warnings: fieldWarnings(value)};
  } catch (error) {
    return {status: "invalid-json", bytes, warnings: [], error: `JSON syntax: ${error.message}`};
  }
}

export function displayValue(value, fallback = "Not provided") {
  if (value === undefined) return fallback;
  if (typeof value === "string") return value === "" ? '"" (empty string)' : value;
  return JSON.stringify(value);
}

export function presetText(name) {
  if (!Object.hasOwn(PRESETS, name)) throw new Error("Unknown example");
  return JSON.stringify(PRESETS[name], null, 2);
}

export function downloadName(value) {
  // The filename is fixed; manifest IDs cannot influence paths or file suffixes.
  return "task.json";
}

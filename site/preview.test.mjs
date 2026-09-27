import test from "node:test";
import assert from "node:assert/strict";
import {PRESETS, PREVIEW_BYTE_LIMIT, VERSION, displayValue, downloadName, fieldWarnings, parsePreview, presetText} from "./preview.mjs";

test("published example contracts have all known fields and explicit placeholder images", () => {
  assert.equal(VERSION, "0.1.0a1");
  for (const name of Object.keys(PRESETS)) {
    const result = parsePreview(presetText(name));
    assert.equal(result.status, "parsed");
    assert.deepEqual(result.warnings, []);
    assert.equal(result.value.schemaVersion, 1);
    assert.equal(result.value.network, "none");
    assert.match(result.value.image, /^example\.invalid\/.+@sha256:[a-f0-9]{64}$/);
    assert.ok(result.value.argv.length > 0);
    assert.ok(result.value.outputs.length > 0);
  }
});

test("invalid JSON is reported without treating it as a contract", () => {
  const result = parsePreview('{"id":');
  assert.equal(result.status, "invalid-json");
  assert.equal(result.value, undefined);
  assert.match(result.error, /^JSON syntax:/);
});

test("warning list names missing and unknown fields at both known levels", () => {
  const value = structuredClone(PRESETS.artifact);
  delete value.ownerAgentId;
  delete value.limits.pids;
  value.secrets = {password: "example-only"};
  value.limits.extra = 123;
  const warnings = fieldWarnings(value).join("\n");
  assert.match(warnings, /Missing fields: ownerAgentId/);
  assert.match(warnings, /Unknown fields: secrets/);
  assert.match(warnings, /Missing limit fields: pids/);
  assert.match(warnings, /Unknown limit fields: extra/);
});

test("JSON arrays, scalars and unusual field shapes remain safe to describe", () => {
  for (const input of ["null", "[]", "42", '"hello"']) {
    const result = parsePreview(input);
    assert.equal(result.status, "parsed");
    assert.match(result.warnings[0], /JSON object/);
  }
  const value = {...PRESETS.artifact, argv: "shell text", outputs: null, limits: []};
  assert.equal(fieldWarnings(value).length, 3);
  assert.equal(displayValue(null), "null");
  assert.equal(displayValue(""), '"" (empty string)');
  assert.equal(displayValue(undefined), "Not provided");
});

test("HTML-like values and prototype names remain literal data", () => {
  const malicious = '<img src=x onerror="alert(1)">';
  const result = parsePreview(JSON.stringify({...PRESETS.artifact, id: malicious}));
  assert.equal(displayValue(result.value.id), malicious);
  assert.equal(Object.prototype.polluted, undefined);
  assert.match(fieldWarnings(JSON.parse('{"__proto__":{"polluted":true}}')).join(" "), /__proto__/);
  assert.equal(Object.prototype.polluted, undefined);
  assert.equal(downloadName({id: "../../bad.html"}), "task.json");
});

test("preview limit counts UTF-8 bytes and does not try to parse oversized text", () => {
  const result = parsePreview("é".repeat(PREVIEW_BYTE_LIMIT));
  assert.equal(result.status, "too-large");
  assert.equal(result.bytes, PREVIEW_BYTE_LIMIT * 2);
  assert.equal(result.value, undefined);
});

test("preview is intentionally not a duplicate runtime security validator", () => {
  const value = {...PRESETS.artifact, network: "host", image: "not-pinned", limits: {cpus: -1, memoryMiB: 0, pids: 0, wallSeconds: 0}};
  const result = parsePreview(JSON.stringify(value));
  assert.equal(result.status, "parsed");
  assert.equal(Object.hasOwn(result, "valid"), false);
  assert.equal(Object.hasOwn(result, "safe"), false);
  assert.deepEqual(result.warnings, []);
});

test("presets are deterministic and unknown preset names are rejected", () => {
  const original = presetText("artifact");
  const edited = JSON.parse(original);
  edited.id = "edited";
  assert.equal(presetText("artifact"), original);
  assert.throws(() => presetText("__proto__"), /Unknown example/);
});

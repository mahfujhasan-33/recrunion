const { test } = require("node:test");
const assert = require("node:assert/strict");
const { elapsedLabel } = require("../../app/static/js/interview_copilot.js");

test("interview timer clamps future times and formats elapsed minutes", () => {
  assert.equal(elapsedLabel(-50), "00:00");
  assert.equal(elapsedLabel(61.9), "01:01");
  assert.equal(elapsedLabel(3600), "60:00");
});

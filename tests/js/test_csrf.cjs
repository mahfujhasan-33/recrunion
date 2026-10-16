const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../app/static/js/csrf.js"), "utf8");

function browser({ token = "synthetic-csrf", status = 200 } = {}) {
  const requests = [];
  const notices = [];
  const nativeFetch = async (...args) => {
    requests.push(args);
    return new Response("", { status });
  };
  const document = {
    querySelector(selector) {
      if (selector === 'meta[name="csrf-token"]') return token ? { content: token } : null;
      if (selector === "#auth-expired") return notices[0] || null;
      return null;
    },
    createElement() {
      return { children: [], setAttribute() {}, append(...items) { this.children.push(...items); } };
    },
    body: { prepend(notice) { notices.push(notice); } },
  };
  const window = {
    fetch: nativeFetch,
    location: { origin: "https://recrunion.test", href: "https://recrunion.test/jobs" },
  };
  vm.runInNewContext(source, { window, document, Request, URL, Headers });
  return { window, requests, notices, nativeFetch };
}

test("same-origin writes preserve form bodies and headers while adding CSRF", async () => {
  const { window, requests } = browser();
  const body = new FormData();
  body.append("files", new Blob(["synthetic PDF"]), "sample.pdf");
  await window.fetch("/api/v1/jobs/id/applications/upload", {
    method: "POST", body, headers: { "X-Request-ID": "synthetic-request" },
  });
  assert.equal(requests[0][1].body, body);
  assert.equal(requests[0][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
  assert.equal(requests[0][1].headers.get("X-Request-ID"), "synthetic-request");
  assert.equal(requests[0][1].headers.has("Content-Type"), false);
});

test("Request objects inherit method and headers correctly", async () => {
  const { window, requests } = browser();
  const request = new Request("https://recrunion.test/api/v1/jobs/id", {
    method: "DELETE", headers: { Accept: "application/json" },
  });
  await window.fetch(request);
  assert.equal(requests[0][0], request);
  assert.equal(requests[0][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
  assert.equal(requests[0][1].headers.get("Accept"), "application/json");
});

test("CSRF is never added to another origin or a safe GET", async () => {
  const { window, requests } = browser();
  await window.fetch("https://external.test/upload", { method: "POST" });
  await window.fetch("/api/v1/jobs");
  for (const [, options] of requests) {
    assert.equal(new Headers(options.headers).has("X-CSRF-Token"), false);
  }
});

test("candidate pages without recruiter CSRF keep their native fetch", () => {
  const { window, nativeFetch } = browser({ token: null });
  assert.equal(window.fetch, nativeFetch);
});

test("expired recruiter session shows one notice and returns the original HTTP response", async () => {
  const { window, notices } = browser({ status: 401 });
  assert.equal((await window.fetch("/api/v1/jobs")).status, 401);
  await window.fetch("/api/v1/jobs");
  assert.equal(notices.length, 1);
  const link = notices[0].children.find((item) => typeof item === "object");
  assert.equal(link.href, "/login");
  assert.equal(link.target, "_blank");
});

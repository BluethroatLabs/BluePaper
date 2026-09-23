import assert from "node:assert/strict";
import { File } from "node:buffer";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const app = readFileSync(resolve(root, "bluepaper/web/app.js"), "utf8");

const EMPTY_TITLE = "Drop an untrusted document";
const EMPTY_META = "PDF, Office, ODF, EPUB, HWP, or images. Typically 32 MiB max.";

const calls = [];
const elements = new Map();

function createElement(id = "") {
  const listeners = {};
  const el = {
    id,
    hidden: id === "submit-error",
    disabled: id === "convert-btn",
    textContent: "",
    className: "",
    title: "",
    files: [],
    dataset: {},
    style: {},
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() {
        return false;
      },
    },
    setAttribute(name, value) {
      el[name] = value;
    },
    getAttribute() {
      return null;
    },
    addEventListener(type, fn) {
      (listeners[type] ||= []).push(fn);
    },
    appendChild(child) {
      return child;
    },
    replaceChildren() {},
    querySelectorAll() {
      return [];
    },
    contains() {
      return false;
    },
    dispatch(type, event) {
      for (const fn of listeners[type] || []) {
        fn(event);
      }
    },
  };
  let value = "";
  Object.defineProperty(el, "value", {
    get() {
      return value;
    },
    set(next) {
      value = String(next);
      if (id === "file" && value === "") {
        el.files = [];
      }
    },
  });
  return el;
}

function element(id) {
  if (!elements.has(id)) {
    const el = createElement(id);
    if (id === "drop-title") {
      el.textContent = EMPTY_TITLE;
    }
    if (id === "drop-meta") {
      el.textContent = EMPTY_META;
    }
    elements.set(id, el);
  }
  return elements.get(id);
}

globalThis.window = globalThis;
globalThis.sessionStorage = { removeItem() {} };
globalThis.location = { hostname: "127.0.0.1" };
globalThis.document = {
  getElementById: element,
  createElement,
  body: createElement("body"),
};
globalThis.addEventListener = () => {};
globalThis.fetch = async (path, options = {}) => {
  calls.push({
    path,
    method: options.method || "GET",
    body: options.body || null,
  });
  return {
    ok: false,
    status: 404,
    statusText: "Not Found",
    json: async () => ({}),
    headers: { get: () => null },
  };
};
globalThis.turnstile = {
  render(_widget, options) {
    options.callback("test-token");
    return "widget";
  },
  reset() {},
};

vm.runInThisContext(app, { filename: "bluepaper/web/app.js" });
window.bluepaperTurnstile();

const input = element("file");
const drop = element("drop");
const button = element("convert-btn");
const title = element("drop-title");
const meta = element("drop-meta");
const error = element("submit-error");
const form = element("convert-form");

function pdf(name, bytes = "%PDF-1.4 sensitive") {
  return new File([bytes], name, { type: "application/pdf" });
}

function choose(file) {
  input.files = [file];
  input.dispatch("change", {});
}

function dropFile(file) {
  drop.dispatch("drop", {
    preventDefault() {},
    dataTransfer: { files: [file] },
  });
}

function conversionPosts() {
  return calls.filter(
    (call) => call.path === "/v1/conversions" && call.method === "POST",
  );
}

function assertReady(name) {
  assert.equal(title.textContent, name);
  assert.match(meta.textContent, /ready to queue/);
  assert.equal(error.hidden, true);
  assert.equal(button.disabled, false);
}

function assertCleared(ext) {
  assert.equal(title.textContent, EMPTY_TITLE);
  assert.equal(meta.textContent, EMPTY_META);
  assert.equal(error.hidden, false);
  assert.equal(error.textContent, `Unsupported file type (${ext})`);
  assert.equal(button.disabled, true);
  assert.equal(input.value, "");
  assert.equal(input.files.length, 0);
  assert.equal(conversionPosts().length, 0);
}

assert.equal(button.disabled, true, "Queue starts disabled");

const first = pdf("encoded-action.pdf");
choose(first);
assertReady("encoded-action.pdf");
assert.equal(input.files[0], first);

choose(new File(["notes"], "unsupported.txt", { type: "text/plain" }));
assertCleared(".txt");
form.dispatch("submit", { preventDefault() {} });
assert.equal(conversionPosts().length, 0);
assert.equal(button.disabled, true);

const second = pdf("other.pdf", "%PDF-1.4 other");
choose(second);
assertReady("other.pdf");
assert.notEqual(title.textContent, "encoded-action.pdf");

dropFile(new File(["nope"], "notes.TXT", { type: "text/plain" }));
assertCleared(".txt");
assert.equal(input.files.length, 0);

dropFile(pdf("dropped.pdf"));
assertReady("dropped.pdf");
dropFile(new File(["bin"], "payload.bin"));
assertCleared(".bin");

choose(pdf("again.pdf"));
assertReady("again.pdf");
dropFile(new File(["plain"], "readme"));
assertCleared(".readme");

const { createHash } = require("node:crypto");
const fs = require("node:fs");
const http2 = require("node:http2");
const path = require("node:path");

const name = "torch-2.12.1+cu126-cp312-cp312-win_amd64.whl";
const relative = "/whl/cu126/" + name.replace("+", "%2B");
const size = 2_620_768_231;
const publisherHash = "71ee0eca3bde3a4067dfbc28c705b914d0773edd29ac4a634ad5e7a07f336aa1";
const chunkSize = 4 * 1024 ** 2;
const poolSize = 28;
const hostNames = ["download.pytorch.org", "download-r2.pytorch.org"];
const etags = ['"624db78e464fe224567b65e9ef22c60a"', '"2f61aa8aeefbe4be4c8ccf0be92c3e97-13"'];
const env = path.join(process.env.LOCALAPPDATA, "FieldFixAI", "ml", "envs", "cv-m12-v1");
const root = path.join(env, "wheelhouse-v4");
const partial = path.join(root, name + ".part");
const finished = path.join(root, name);
const journal = path.join(root, "ranges.jsonl");

const blockSizes = new Map();
const sessions = [];
let done = 0;
let lastReport = 0;
let dataFd;
let journalFd;
let fatal;

function sha(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function checkBoundary(offset, count) {
  const start = Math.floor(offset / chunkSize) * chunkSize;
  const expectedOffset = start + (blockSizes.get(start) ?? 0);
  if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(count) ||
      offset !== expectedOffset || count <= 0 || offset + count > Math.min(start + chunkSize, size)) {
    throw new Error("Wheel receipt is inconsistent at " + offset);
  }
  return start;
}

function remember(offset, count) {
  const start = checkBoundary(offset, count);
  blockSizes.set(start, offset - start + count);
  done += count;
  if (done - lastReport >= 32 * 1024 ** 2 || done === size) {
    console.log("Saved official wheel ranges: " + done.toLocaleString("en-US") + "/" + size.toLocaleString("en-US"));
    lastReport = done;
  }
}

function record(offset, bytes, source) {
  checkBoundary(offset, bytes.length);
  if (fs.writeSync(dataFd, bytes, 0, bytes.length, offset) !== bytes.length) {
    throw new Error("Partial write while staging a wheel range");
  }
  fs.fsyncSync(dataFd);
  const info = { offset, count: bytes.length, sha256: sha(bytes), source };
  fs.writeSync(journalFd, JSON.stringify(info) + "\n");
  fs.fsyncSync(journalFd);
  remember(offset, bytes.length);
}

function restore(folder, copy) {
  const index = fs.readFileSync(path.join(folder, "ranges.jsonl"), "utf8");
  const sourceData = fs.openSync(path.join(folder, name + ".part"), "r");
  try {
    if (fs.fstatSync(sourceData).size !== size) throw new Error("Resume wheel has an unexpected size");
    for (const line of index.trim().split("\n")) {
      if (!line) continue;
      const row = JSON.parse(line);
      checkBoundary(row.offset, row.count);
      const bytes = Buffer.allocUnsafe(row.count);
      if (fs.readSync(sourceData, bytes, 0, bytes.length, row.offset) !== row.count || sha(bytes) !== row.sha256) {
        throw new Error("Resume receipt SHA-256 differs at " + row.offset);
      }
      if (copy) record(row.offset, bytes, "rehashed receipt from wheelhouse-v3");
      else remember(row.offset, row.count);
    }
  } finally {
    fs.closeSync(sourceData);
  }
}

function connection(originIndex) {
  const saved = sessions[originIndex];
  if (saved && !saved.destroyed && !saved.closed) return saved;
  const host = hostNames[originIndex];
  const session = http2.connect("https://" + host, {
    settings: { initialWindowSize: 4 * 1024 ** 2 },
  });
  session.on("error", () => {});
  session.once("connect", () => session.setLocalWindowSize(32 * 1024 ** 2));
  sessions[originIndex] = session;
  return session;
}

function requestRange(originIndex, offset, last) {
  const requested = last - offset + 1;
  return new Promise((resolve) => {
    let stream;
    let accepted = false;
    let status;
    let length = 0;
    let finished = false;
    const buffers = [];
    const timer = setTimeout(() => end("range transfer timed out"), 55_000);
    function end(reason) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (stream && !stream.closed) stream.close(http2.constants.NGHTTP2_CANCEL);
      const result = accepted && length > 0 && length <= requested ? Buffer.concat(buffers, length) : null;
      resolve({ result, reason, status });
    }
    try {
      stream = connection(originIndex).request({
        ":method": "GET",
        ":path": relative,
        range: "bytes=" + offset + "-" + last,
        "accept-encoding": "identity",
      });
      stream.on("response", (headers) => {
        status = headers[":status"];
        accepted = status === 206 &&
          headers["content-range"] === "bytes " + offset + "-" + last + "/" + size &&
          headers.etag === etags[originIndex];
        if (!accepted) end("unexpected HTTP status, content range or ETag");
      });
      stream.on("data", (bytes) => {
        if (!accepted) return;
        length += bytes.length;
        if (length > requested) return end("range sent too many bytes");
        buffers.push(bytes);
      });
      stream.on("error", (error) => end(error.code ?? error.message));
      stream.on("aborted", () => end("range stream aborted"));
      stream.on("end", () => end("range stream ended"));
      stream.end();
    } catch (error) {
      end(error.code ?? error.message);
    }
  });
}

async function fetch(start) {
  let offset = start + (blockSizes.get(start) ?? 0);
  const last = Math.min(start + chunkSize, size) - 1;
  let noProgress = 0;
  while (offset <= last && !fatal) {
    const selectedHost = (start / chunkSize + noProgress) % hostNames.length;
    const response = await requestRange(selectedHost, offset, last);
    if (response.result) {
      record(offset, response.result, hostNames[selectedHost]);
      offset += response.result.length;
      noProgress = 0;
      continue;
    }
    if ([401, 403, 404, 429].includes(response.status)) {
      fatal = new Error("Official package host returned HTTP " + response.status + " at offset " + offset);
      return "publisher refused access";
    }
    if (++noProgress >= 5) return "offset " + offset + ": " + response.reason;
    await new Promise((resolve) => setTimeout(resolve, 1000 * noProgress));
  }
  return null;
}

async function verifyWhole(filename) {
  if (fs.statSync(filename).size !== size) throw new Error("Wrong assembled wheel size");
  const hash = createHash("sha256");
  for await (const bytes of fs.createReadStream(filename)) hash.update(bytes);
  if (hash.digest("hex") !== publisherHash) throw new Error("Wheel SHA-256 does not match the official PyTorch index");
}

async function run() {
  try {
    fs.mkdirSync(root);
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  }
  if (fs.existsSync(finished)) {
    await verifyWhole(finished);
    console.log("VERIFIED_WHEEL=" + finished);
    return;
  }
  if (fs.existsSync(partial) !== fs.existsSync(journal)) {
    throw new Error("Incomplete setup. Preserve this wheelhouse and inspect its receipts.");
  }
  const existing = fs.existsSync(partial);
  dataFd = fs.openSync(partial, existing ? "r+" : "wx+");
  journalFd = fs.openSync(journal, existing ? "a" : "ax");
  try {
    if (existing) restore(root, false);
    else {
      fs.ftruncateSync(dataFd, size);
      restore(path.join(env, "wheelhouse-v3"), true);
    }
    console.log("Resumed from " + done.toLocaleString("en-US") + " SHA-256-checked range bytes.");
    const offsets = [];
    for (let start = 0; start < size; start += chunkSize) {
      if ((blockSizes.get(start) ?? 0) < Math.min(chunkSize, size - start)) offsets.push(start);
    }
    let next = 0;
    const errors = [];
    async function worker() {
      while (!fatal && next < offsets.length) {
        const error = await fetch(offsets[next++]);
        if (error) errors.push(error);
      }
    }
    await Promise.all(Array.from({ length: poolSize }, () => worker()));
    if (fatal) throw fatal;
    if (done !== size) throw new Error("Incomplete wheel. Retained all verified ranges. " + errors.slice(0, 8).join("; "));
  } finally {
    for (const session of sessions) session?.destroy();
    fs.closeSync(dataFd);
    fs.closeSync(journalFd);
  }
  await verifyWhole(partial);
  fs.linkSync(partial, finished);
  console.log("VERIFIED_WHEEL=" + finished);
}

run().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});

import process from "node:process";

import { parser } from "@grafana/lezer-logql";

const MAX_INPUT_BYTES = 128 * 1024;
const MAX_QUERY_BYTES = 100 * 1024;
const PARSER_VERSION = "@grafana/lezer-logql@0.4.1";

function fail(message) {
  process.stdout.write(JSON.stringify({ ok: false, error: message }));
  process.exitCode = 1;
}

function decodeString(source) {
  if (source.startsWith("`") && source.endsWith("`")) {
    return { encoding: "raw", value: null };
  }
  if (!source.startsWith('"') || !source.endsWith('"')) {
    return { encoding: "invalid", value: null };
  }
  try {
    const value = JSON.parse(source);
    return typeof value === "string"
      ? { encoding: "json", value }
      : { encoding: "invalid", value: null };
  } catch {
    return { encoding: "unsupported_escape", value: null };
  }
}

function descendants(node) {
  const result = [];
  const visit = (current) => {
    result.push(current);
    for (let child = current.firstChild; child; child = child.nextSibling) {
      visit(child);
    }
  };
  visit(node);
  return result;
}

function firstNamed(node, names) {
  return descendants(node).find((item) => names.has(item.name));
}

function inspectQuery(query) {
  const tree = parser.parse(query);
  const nodeCounts = {};
  const errors = [];
  const strings = [];
  const terminals = [];
  const selectors = [];
  const matchers = [];
  const lineFilters = [];
  const pipelineStages = [];

  const visit = (node, parents) => {
    nodeCounts[node.name] = (nodeCounts[node.name] || 0) + 1;
    const text = query.slice(node.from, node.to);
    if (node.type.isError) {
      errors.push({ from: node.from, to: node.to });
    }
    if (node.name === "String") {
      strings.push({
        from: node.from,
        to: node.to,
        ...decodeString(text),
        parents: parents.slice(-8).reverse(),
      });
    }
    if (["Identifier", "Number", "Duration", "Bytes"].includes(node.name)) {
      terminals.push({ name: node.name, from: node.from, to: node.to, text });
    }
    if (node.name === "Selector") {
      selectors.push({ from: node.from, to: node.to, text });
    }
    if (node.name === "Matcher") {
      const children = descendants(node);
      const nameNode = children.find((item) => item.name === "Identifier");
      const operatorNode = children.find((item) =>
        ["Eq", "Neq", "Re", "Nre"].includes(item.name),
      );
      const valueNode = children.find((item) => item.name === "String");
      matchers.push({
        from: node.from,
        to: node.to,
        name: nameNode ? query.slice(nameNode.from, nameNode.to) : null,
        operator: operatorNode ? operatorNode.name : null,
        value: valueNode ? decodeString(query.slice(valueNode.from, valueNode.to)) : null,
      });
    }
    if (node.name === "LineFilter") {
      const operatorNode = firstNamed(
        node,
        new Set(["PipeExact", "PipeMatch", "PipePattern", "PipeNotEqual", "PipeNotMatch", "PipeNotPattern"]),
      );
      const valueNode = firstNamed(node, new Set(["String"]));
      lineFilters.push({
        from: node.from,
        to: node.to,
        operator: operatorNode ? operatorNode.name : null,
        value: valueNode ? decodeString(query.slice(valueNode.from, valueNode.to)) : null,
      });
    }
    if (node.name === "PipelineStage") {
      const childNames = descendants(node).slice(1).map((item) => item.name);
      pipelineStages.push({ from: node.from, to: node.to, nodes: childNames });
    }
    for (let child = node.firstChild; child; child = child.nextSibling) {
      visit(child, [...parents, node.name]);
    }
  };
  visit(tree.topNode, []);

  return {
    ok: true,
    parser_version: PARSER_VERSION,
    query_kind: nodeCounts.MetricExpr ? "metric" : "log",
    structural_tree: tree.toString(),
    node_counts: nodeCounts,
    errors,
    strings,
    terminals,
    selectors,
    matchers,
    line_filters: lineFilters,
    pipeline_stages: pipelineStages,
  };
}

const chunks = [];
let size = 0;
for await (const chunk of process.stdin) {
  size += chunk.length;
  if (size > MAX_INPUT_BYTES) {
    fail("parser request exceeds byte limit");
    break;
  }
  chunks.push(chunk);
}

if (!process.exitCode) {
  try {
    const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (
      !request ||
      typeof request !== "object" ||
      Array.isArray(request) ||
      Object.keys(request).length !== 1 ||
      typeof request.query !== "string"
    ) {
      fail("parser request must contain only a query string");
    } else if (Buffer.byteLength(request.query, "utf8") > MAX_QUERY_BYTES) {
      fail("query exceeds byte limit");
    } else {
      process.stdout.write(JSON.stringify(inspectQuery(request.query)));
    }
  } catch (error) {
    fail(error instanceof Error ? error.name : "invalid parser request");
  }
}

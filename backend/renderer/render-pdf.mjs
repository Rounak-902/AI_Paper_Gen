import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import katex from "katex";
import { chromium } from "playwright";

const root = path.dirname(fileURLToPath(import.meta.url));

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) {
    throw new Error(`missing ${name}`);
  }
  return process.argv[index + 1];
}

function escapeAttribute(value) {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;");
}

async function embeddedKatexCss() {
  const cssPath = path.join(root, "node_modules", "katex", "dist", "katex.min.css");
  let css = await fs.readFile(cssPath, "utf8");
  const urls = [...css.matchAll(/url\(([^)]+)\)/g)].map((match) => match[1].replaceAll(/["']/g, ""));
  for (const relative of new Set(urls)) {
    const fontPath = path.resolve(path.dirname(cssPath), relative);
    const bytes = await fs.readFile(fontPath);
    const extension = path.extname(fontPath).slice(1);
    const mime = extension === "woff2" ? "font/woff2" : extension === "woff" ? "font/woff" : "application/octet-stream";
    css = css.replaceAll(relative, `data:${mime};base64,${bytes.toString("base64")}`);
  }
  return css;
}

function renderMath(html) {
  return html.replace(
    /<span class="math-placeholder" data-katex-b64="([^"]+)" data-display="(true|false)"><\/span>/g,
    (_whole, encoded, display) => {
      const expression = Buffer.from(encoded, "base64url").toString("utf8");
      try {
        return katex.renderToString(expression, {
          displayMode: display === "true",
          output: "htmlAndMathml",
          strict: "warn",
          throwOnError: true,
          trust: false,
        });
      } catch (error) {
        throw new Error(`KaTeX failed for ${escapeAttribute(expression)}: ${error.message}`);
      }
    },
  );
}

const inputPath = argument("--input");
const outputPath = argument("--output");
const htmlOutputPath = argument("--html-output");
const spec = JSON.parse(await fs.readFile(inputPath, "utf8"));
const css = await embeddedKatexCss();
const finalHtml = renderMath(spec.html.replace("{{KATEX_CSS}}", css));
if (finalHtml.includes("math-placeholder") || finalHtml.includes("<script")) {
  throw new Error("final render HTML must contain static KaTeX markup and no scripts");
}
await fs.writeFile(htmlOutputPath, finalHtml, "utf8");

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ javaScriptEnabled: false });
  await context.route("**/*", (route) => {
    const url = route.request().url();
    if (url.startsWith("data:") || url.startsWith("about:")) route.continue();
    else route.abort("blockedbyclient");
  });
  const page = await context.newPage();
  await page.setContent(finalHtml, { waitUntil: "load" });
  await page.emulateMedia({ media: "print" });
  await page.pdf({
    path: outputPath,
    format: spec.pageSize,
    landscape: Boolean(spec.landscape),
    printBackground: true,
    preferCSSPageSize: true,
    displayHeaderFooter: true,
    headerTemplate: "<div></div>",
    footerTemplate: spec.footerHtml,
    tagged: true,
    outline: true,
  });
  await context.close();
} finally {
  await browser.close();
}

# PDF renderer runtime

P6-T2 uses this isolated Node runtime for official KaTeX server-side rendering and
Playwright's matched Chromium build. From this directory, install it with:

```powershell
npm ci
npx playwright install --with-deps chromium
```

The Python worker invokes `render-pdf.mjs`; it does not serve this directory or run a
long-lived Node service. Production worker images therefore need Node 20+ and the
Playwright Chromium/system libraries installed during the image build. Browser-side
JavaScript stays disabled during PDF creation.

# Resume Capture Runbook

## Goal

Resume a partially captured ebook from page `N` through the last page without breaking the live viewer session.

## Preconditions

1. Chromium is already running with `--remote-debugging-port=9222`.
2. The user is already logged in and the target ebook tab is open.
3. The tab must remain open during the whole run.

## Resume Procedure

1. Inspect the live viewer through CDP and confirm that `apryse-webviewer.instance.Core.documentViewer` exists.
2. Read `getPageCount()` from the viewer API.
3. Compare the raw folder against the intended page range and identify the first missing or corrupted page.
4. For the normal "book id is already open in the browser, make it a PDF" flow, prefer:

```bash
node <WORKSPACE_SCRIPTS>/make_pdf.js <bookId> <startPage>
```

5. If you need the low-level capture step only, start the hijack scraper with:

```bash
node <WORKSPACE_SCRIPTS>/hijack_scraper.js <startPage> last <rawDir> <bookId>
```

6. Wait for completion. Do not interact with the viewer tab during capture.
7. Verify the raw folder contains a continuous sequence from `page_0001.png` to the final page.
8. Rebuild cropped output from the full raw folder:

```bash
python3 <SKILL_ROOT>/scripts/crop_pages.py --input <rawDir> --output <croppedDir>
```

9. Rebuild the final PDF from the full cropped folder:

```bash
python3 <SKILL_ROOT>/scripts/create_pdf.py --input <croppedDir> --name <pdfPath>
```

10. Verify:
   raw image count
   cropped image count
   PDF page count

## Hard Rules

- Never call `page.reload()` on the viewer.
- Never rely on generic text input selectors to find the page field.
- If safe page navigation is unavailable, abort instead of capturing from an unknown position.
- If render checks fail, stop and inspect the viewer state before retrying.

## Notes For Book 1237

- Verified total pages: `368`
- Successful recovery run:
  from page `158`
  to page `368`
  using the viewer API

# Ebook Extraction Knowledge v1.2

## What Failed Before

1. `page.reload()` broke the live Apryse session.
2. Broad selectors such as `input.textarea` and `input[type="text"]` could target the zoom field instead of a page field.
3. When `gotoPage()` failed, the old flow still kept capturing, which allowed loading screens or wrong pages to be saved.
4. DOM-only checks missed canvases inside shadow DOM, so the viewer could be ready while the script thought it was blank.

## What Is Stable Now

1. Keep the current browser session intact. Do not reload the viewer tab.
2. Use the Apryse viewer API as the primary control surface:
   `document.querySelector('apryse-webviewer').instance.Core.documentViewer`
3. When the user gives a book id and says the book is already open, use the one-shot wrapper:
   `node <WORKSPACE_SCRIPTS>/make_pdf.js <bookId> <startPage>`
4. Navigate with `setCurrentPage(pageNum)` first. Treat keyboard navigation as fallback only.
5. Log `getCurrentPage()`, `getPageCount()`, and `getZoomLevel()` around capture boundaries.
6. Fail closed:
   If the script cannot safely jump to the requested start page, stop instead of saving anything.
7. Wait generously:
   20-30 second waits are acceptable when the document is heavy.
8. Render checks must search through shadow DOM and accessible iframes, not only the top document.

## Known Good Fact Pattern

- Book ID `1237` reports `368` pages through the viewer API.
- A stable zoom observed during successful capture was approximately `1.062415196743555` with the UI showing `107%`.
- White or near-blank pages can be much smaller in file size while still being valid.

## Operational Rules

- Do not trust page-input selectors unless you have confirmed the page field is actually visible in the current viewer layout.
- Do not assume the presence of a page input at all; some layouts expose only zoom controls while page changes are still possible through the API.
- Treat small file size as a warning, not an automatic failure, especially for the last page or blank pages.
- Always verify:
  raw image count
  cropped image count
  final PDF page count

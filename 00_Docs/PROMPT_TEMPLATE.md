# Ebook Extraction Prompt Template

Use this prompt when Antigravity needs to resume an extraction safely.

```text
Use the Ebook to PDF skill.

Task:
Resume ebook capture from page <START_PAGE> through the last page, then rebuild cropped images and the final PDF.

Constraints:
- Do not reload the browser tab.
- Keep the existing login session intact.
- Use the Apryse viewer API as the primary navigation method.
- If the script cannot safely confirm the target start page, stop instead of capturing.
- Wait generously for heavy pages to render.

Execution checklist:
1. Confirm the viewer tab is present through CDP.
2. Read total page count from `document.querySelector('apryse-webviewer').instance.Core.documentViewer.getPageCount()`.
3. Prefer the one-shot entry point `node <WORKSPACE_SCRIPTS>/make_pdf.js <BOOK_ID> <START_PAGE>`.
4. If the one-shot entry point cannot be used, resume raw capture with `hijack_scraper.js` from page <START_PAGE> to `last` using the book id.
5. Verify raw image continuity and total count.
6. Rebuild cropped output from the full raw set.
7. Rebuild the PDF from the full cropped set.
8. Report raw count, cropped count, and PDF page count.

Output targets:
- Raw dir: <RAW_DIR>
- Cropped dir: <CROPPED_DIR>
- PDF path: <PDF_PATH>
```

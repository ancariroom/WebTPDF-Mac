/**
 * Viewer adapter for WebTPDF.
 * Handles interactions with the Apryse WebViewer used by the target site.
 */
class ViewerAdapter {
    constructor(bookId = null) {
        this.bookId = bookId;
        this.name = 'viewer';
    }

    async findViewerPage(browserEngine) {
        const hasViewer = async (page) => {
            try {
                return await page.evaluate(() => Boolean(document.querySelector('apryse-webviewer')?.instance?.Core?.documentViewer));
            } catch (error) {
                return false;
            }
        };

        let page = null;
        if (this.bookId !== null && this.bookId !== undefined && this.bookId !== '') {
            const bookId = String(this.bookId);
            page = await browserEngine.findPage((candidate) => candidate.url().includes(bookId));
        }

        if (!page) {
            page = await browserEngine.findPage(hasViewer);
        }
        return page;
    }

    async getInfo(page) {
        return await page.evaluate(() => {
            const viewer = document.querySelector('apryse-webviewer')?.instance;
            const documentViewer = viewer?.Core?.documentViewer;
            const title = document.title.replace('ビューア: ', '').trim();
            return {
                title,
                currentPage: documentViewer?.getCurrentPage?.() ?? null,
                pageCount: documentViewer?.getPageCount?.() ?? null,
                zoomLevel: documentViewer?.getZoomLevel?.() ?? null,
            };
        });
    }

    async gotoPage(page, pageNum) {
        const result = await page.evaluate(async (targetPage) => {
            const viewer = document.querySelector('apryse-webviewer')?.instance;
            const documentViewer = viewer?.Core?.documentViewer;
            if (!documentViewer) return { ok: false, reason: 'Viewer API unavailable' };

            try {
                documentViewer.setCurrentPage(targetPage);
                // Viewer update is usually async, we wait a bit in the caller
                return { ok: true };
            } catch (error) {
                return { ok: false, reason: error.message };
            }
        }, pageNum);

        return result.ok;
    }

    async waitForReady(page) {
        // Standard v1.2: Wait for spinner to hide and canvas to render
        await page.waitForFunction(() => {
            function collectDeep(root, selector) {
                const results = [];
                function walk(node) {
                    if (!node || !node.querySelectorAll) return;
                    results.push(...node.querySelectorAll(selector));
                    for (const host of Array.from(node.querySelectorAll('*'))) {
                        if (host.shadowRoot) walk(host.shadowRoot);
                    }
                }
                walk(root);
                return results;
            }

            const spinnerVisible = collectDeep(document, '.ProgressModal, .loading, .spinner')
                .some(el => {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                });

            const hasCanvas = collectDeep(document, 'canvas')
                .some(canvas => canvas.width > 0 && canvas.height > 0);

            return !spinnerVisible && hasCanvas;
        }, { timeout: 45000 });
    }

    async ensureFocus(page) {
        const viewport = page.viewportSize();
        const x = viewport ? viewport.width / 2 : 500;
        await page.mouse.click(x, 100);
    }
}

module.exports = ViewerAdapter;

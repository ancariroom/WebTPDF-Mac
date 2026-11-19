const fs = require('fs');
const path = require('path');
const BrowserEngine = require('../core/browser_engine');

/**
 * Main orchestrator for EbookConverter.
 * Site-agnostic logic for scraping pages using an adapter.
 */
class EbookConvertOrchestrator {
    constructor() {
        this.engine = new BrowserEngine();
    }

    async runScrape(adapter, options = {}) {
        const { startPage = 1, endPage = null, outputDir = 'raw', delayMs = 20000 } = options;
        
        try {
            await this.engine.connect();
            const page = await adapter.findViewerPage(this.engine);
            if (!page) {
                throw new Error(`Viewer page for ${adapter.name} not found.`);
            }

            console.log(`Connected to: ${page.url()}`);
            const info = await adapter.getInfo(page);
            console.log(`Title: ${info.title}`);
            console.log(`Total Pages: ${info.pageCount}`);

            const resolvedEnd = endPage || info.pageCount;
            if (!resolvedEnd) throw new Error('Could not determine end page.');

            if (!fs.existsSync(outputDir)) {
                fs.mkdirSync(outputDir, { recursive: true });
            }

            await adapter.ensureFocus(page);

            // Jump to start page if not already there
            if (startPage > 1) {
                console.log(`Jumping to page ${startPage}...`);
                await adapter.gotoPage(page, startPage);
                await page.waitForTimeout(delayMs);
                await adapter.waitForReady(page);
            }

            for (let i = startPage; i <= resolvedEnd; i++) {
                console.log(`Processing page ${i}/${resolvedEnd}...`);
                const filename = path.join(outputDir, `page_${i.toString().padStart(4, '0')}.png`);
                
                await page.waitForTimeout(delayMs);
                await adapter.waitForReady(page);
                
                const size = await this.engine.capturePage(page, filename);
                console.log(`  [OK] Saved ${filename} (${size} bytes)`);

                if (i < resolvedEnd) {
                    const nextSucceeded = await adapter.gotoPage(page, i + 1);
                    if (!nextSucceeded) {
                        console.log('  Viewer API jump failed, falling back to PageDown...');
                        await adapter.ensureFocus(page);
                        await page.keyboard.press('PageDown');
                    }
                }
            }

            console.log('Capture complete!');
            return { title: info.title };
        } finally {
            await this.engine.disconnect();
        }
    }
}

module.exports = EbookConvertOrchestrator;

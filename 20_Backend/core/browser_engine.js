const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

/**
 * Core browser engine for WebTPDF.
 * Handles CDP connection and basic page interactions.
 */
class BrowserEngine {
    constructor(cdpEndpoint = 'http://127.0.0.1:9222') {
        this.cdpEndpoint = cdpEndpoint;
        this.browser = null;
    }

    async connect() {
        console.log(`Connecting to browser via CDP: ${this.cdpEndpoint}...`);
        this.browser = await chromium.connectOverCDP(this.cdpEndpoint);
        return this.browser;
    }

    async disconnect() {
        if (this.browser) {
            await this.browser.close();
            this.browser = null;
            console.log('Browser disconnected.');
        }
    }

    async findPage(matcher) {
        if (!this.browser) await this.connect();
        const predicate = typeof matcher === 'function'
            ? matcher
            : (page) => {
                if (matcher instanceof RegExp) {
                    return matcher.test(page.url());
                }
                return page.url().includes(matcher);
            };
        const contexts = this.browser.contexts();
        for (const context of contexts) {
            const pages = context.pages();
            for (const page of pages) {
                try {
                    if (await predicate(page)) {
                        return page;
                    }
                } catch (error) {
                    // Ignore pages that are not ready or cannot be inspected yet.
                }
            }
        }
        return null;
    }

    async capturePage(page, outputPath) {
        await page.screenshot({ path: outputPath });
        return fs.statSync(outputPath).size;
    }

    /**
     * Utility to find elements inside shadow roots or iframes.
     */
    async findDeepElement(page, selector) {
        return await page.evaluateHandle((sel) => {
            function findRecursive(r, s) {
                if (!r || !r.querySelector) return null;
                const el = r.querySelector(s);
                if (el) return el;
                const all = r.querySelectorAll('*');
                for (const node of all) {
                    if (node.shadowRoot) {
                        const found = findRecursive(node.shadowRoot, s);
                        if (found) return found;
                    }
                }
                const iframes = r.querySelectorAll('iframe');
                for (const iframe of iframes) {
                    try {
                        const found = findRecursive(iframe.contentDocument, s);
                        if (found) return found;
                    } catch (e) {}
                }
                return null;
            }
            return findRecursive(document, sel);
        }, selector);
    }
}

module.exports = BrowserEngine;

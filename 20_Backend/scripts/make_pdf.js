#!/usr/bin/env node
/**
 * Wrapper for the ebook extraction flow.
 */
const { spawnSync } = require('child_process');
const path = require('path');

const [bookId, startPage = '1'] = process.argv.slice(2);

if (!bookId) {
    console.log(`Usage: node ${path.basename(__filename)} <bookId> [startPage]`);
    process.exit(1);
}

console.log(`[Wrapper] Forwarding request to EbookConverter for book ID: ${bookId}...`);

const ebookConvertPath = path.join(__dirname, '../bin/ebook-convert.js');

const result = spawnSync('node', [
    ebookConvertPath,
    '--id', bookId,
    '--start', startPage
], { stdio: 'inherit' });

process.exit(result.status);

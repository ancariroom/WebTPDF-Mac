#!/usr/bin/env node
const argparse = require('argparse');
const path = require('path');
const { spawnSync } = require('child_process');
const EbookConvertOrchestrator = require('../core/orchestrator');
const ViewerAdapter = require('../adapters/viewer/adapter');

const AUDIOBOOK_WRAPPER = path.join(__dirname, '../scripts/run_ebook2audiobook.py');
const LOCAL_MARGIN_PDF = path.join(__dirname, '../scripts/local_margin_pdf.py');

async function main() {
    const argv = process.argv.slice(2);
    if (argv[0] === 'audiobook') {
        const completed = spawnSync('python3', [AUDIOBOOK_WRAPPER, ...argv.slice(1)], {
            stdio: 'inherit'
        });

        if (completed.error) {
            throw completed.error;
        }

        process.exit(typeof completed.status === 'number' ? completed.status : 1);
    }

    if (argv[0] === 'local-pdf') {
        const completed = spawnSync('python3', [LOCAL_MARGIN_PDF, ...argv.slice(1)], {
            stdio: 'inherit'
        });

        if (completed.error) {
            throw completed.error;
        }

        process.exit(typeof completed.status === 'number' ? completed.status : 1);
    }

    const parser = new argparse.ArgumentParser({
        description: 'WebTPDF - Ebook to PDF conversion tool. Use `local-pdf` for local EPUB/image conversion or `audiobook` for ebook2audiobook.'
    });
    parser.add_argument('--id', { help: 'Book ID (site-specific)' });
    parser.add_argument('--start', { type: 'int', default: 1, help: 'Start page' });
    parser.add_argument('--end', { type: 'int', help: 'End page (optional)' });
    parser.add_argument('--output', { help: 'Output PDF name (optional)' });
    
    const args = parser.parse_args(argv);

    const adapter = new ViewerAdapter(args.id);

    const orchestrator = new EbookConvertOrchestrator();
    const runKey = args.id || 'export';
    const workingDir = path.join(process.cwd(), `ebook_${runKey}_raw`);
    const croppedDir = path.join(process.cwd(), `ebook_${runKey}_cropped`);
    
    // 1. Scrape
    const result = await orchestrator.runScrape(adapter, {
        startPage: args.start,
        endPage: args.end,
        outputDir: workingDir
    });

    const pdfName = args.output || `${result.title.replace(/\s+/g, '_')}${args.id ? `_${args.id}` : ''}.pdf`;
    
    // 2. Crop
    console.log('Running image processor...');
    spawnSync('python3', [
        path.join(__dirname, '../core/image_processor.py'),
        '--input', workingDir,
        '--output', croppedDir
    ], { stdio: 'inherit' });

    // 3. Assemble
    console.log('Running PDF assembler...');
    spawnSync('python3', [
        path.join(__dirname, '../core/pdf_assembler.py'),
        '--input', croppedDir,
        '--name', pdfName
    ], { stdio: 'inherit' });

    console.log(`\nSuccess! PDF created at: ${pdfName}`);
}

main().catch(err => {
    console.error(`Error: ${err.message}`);
    process.exit(1);
});

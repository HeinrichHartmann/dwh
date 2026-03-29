# Document Warehouse

**A personal document archival system for long-term storage and effective retrieval of financial and tax documents.**

## Overview

Document Warehouse is a cold storage system designed for archiving, organizing, and retrieving personal financial documents over extended periods (10+ years). It emphasizes reliable storage and human-supervised organization over speed or full automation.

## Design Goals

### Primary Goals

- **Document Archival**: Secure, long-term storage of original documents with integrity preservation
- **Effective Retrieval**: Organized structure enabling quick manual lookup when needed
- **Cold Storage**: Optimized for infrequent access and bulk operations, not real-time queries
- **Triage Workflow**: Human-in-the-loop document classification assisted by language models
- **Semi-Automated ETL**: Guided import process with manual verification and deduplication
- **CSV Support**: Special handling for transactional data (bank statements, trades) with potential indexing

### Target Use Cases

1. **Tax Return Preparation**: Annual bulk export of relevant documents for specific tax years
2. **Financial Auditing**: Historical transaction analysis across multiple sources
3. **Document Retention**: Legal compliance with German tax law (10-year retention)
4. **Cross-Year Analysis**: Tracking financial trends, salary progression, investment performance

## Non-Goals

- ❌ **Full-text indexing**: No search engine; manual browsing and known-location retrieval
- ❌ **Fast retrieval**: Cold storage optimized for bulk operations, not sub-second lookups
- ❌ **Full automation**: Human oversight required for classification and quality control
- ❌ **Real-time updates**: Batch processing model, not continuous ingestion
- ❌ **Email archival**: Document-focused; emails handled separately

## Architecture

```
Document-Warehouse/
├── README.md              # This file
├── adr/                   # Architecture Decision Records
├── staging/               # Temporary landing zone for new documents
└── data/                  # Organized document archive
    ├── Lohnsteuer/
    ├── Comdirect-PostBox/
    ├── Comdirect-Umsaetze/
    └── ...
```

### Storage Strategy

**Current Implementation:** Git-based versioning
- Provides: Cloning, snapshotting, change tracking, backup
- Limitation: Not optimized for binary files, but "good enough for now"
- Future: May migrate to dedicated document management system

### Data Organization

Documents organized by **source** (not by tax year):
- Enables cross-year analysis
- Accumulates historical data
- Simplifies data ingestion from recurring sources

## Workflow

### 1. Ingestion (Staging)

All new downloads land in `staging/`:

```bash
# Human action: Download from source systems
# Example: Comdirect → PostBox → Download ZIP
# Example: Zalando Workday → Pay Documents → Download PDF
```

Documents remain in staging until processed (not permanent storage).

### 2. Triage (Human + LLM)

Semi-automated classification and organization:

```bash
# LLM-assisted workflow:
# 1. Identify document type (inspect with pdftotext, file headers)
# 2. Check for duplicates (MD5 hash comparison)
# 3. Determine target directory
# 4. Apply transformations if needed (split, rename, extract)
# 5. Human approval at each step
```

**Key Principle:** Always verify, never trust filenames or metadata alone.

### 3. ETL (Extract, Transform, Load)

Semi-automated processing pipeline:

**Extract:**
- Unzip archives
- Split concatenated PDFs
- Export from proprietary formats

**Transform:**
- Rename for consistency (YYYY-MM_DocumentType.pdf)
- Split multi-document files into individual records
- Deduplicate across import batches
- Validate integrity (checksums, page counts)

**Load:**
- Move to appropriate data/ subdirectory
- Preserve raw originals in raw/ folders
- Create processed versions in type-specific folders
- Clean staging/ when complete

### 4. Archival (Data)

Organized storage in `data/`:

**Pattern 1: Simple Sources** (files as-is)
```
data/Bitpanda/
└── bitpanda-trades-YYYY-MM-DD.csv
```

**Pattern 2: Raw + Processed** (transformation required)
```
data/Lohnsteuer/
├── raw/                           # Original imports (NEVER modify)
├── Lohnsteuerbescheinigungen/     # Annual tax certificates
├── Lohnabrechnungen/              # Monthly pay slips
└── Sozialversicherung/            # Social security certs
```

## Data Types

### 1. PDFs (Primary Format)
- Banking statements (Finanzreports)
- Tax certificates (Lohnsteuerbescheinigung, Jahressteuerbescheinigung)
- Investment confirmations (Wertpapierabrechnung)
- Pay slips (Lohnabrechnungen)

### 2. CSV Files (Transactional Data)
- Bank account transactions (Comdirect-Umsaetze)
- Cryptocurrency trades (Bitpanda)
- Investment tax details (Comdirect-Steuerdetails)

**Future Enhancement:** Consolidated indexes across CSV files
- Example: Unified banking history across all accounts/years
- Example: Complete crypto transaction ledger
- Implementation: DuckDB, SQLite, or Pandas-based consolidation

### 3. Calendar Exports
- Business travel verification (.gax, .ics formats)
- Used for Dienstreisen (business trip) deductions

## Security & Retention

### Security
- **Private repository**: Never commit to public Git repos
- **Encryption**: Store in encrypted storage (full-disk encryption)
- **Access control**: Local-only, no cloud sync
- **Sensitive data**: SSNs, tax IDs, full financial history

### Retention Policy (German Tax Law)
- **Tax documents**: 10 years minimum
- **Investment documents**: Until sale + 10 years
- **Pay slips**: Permanent (pension verification)
- **Bank statements**: 10 years recommended

### Data Integrity
- **Raw preservation**: Original files never modified
- **Version control**: Git tracks all changes
- **Checksums**: MD5 verification for deduplication
- **Validation**: Manual verification of critical transformations

## Triage Best Practices

### Always Inspect, Never Trust

```bash
# DON'T trust filenames
# Filename: "LSt-AN-Bescheinigungen 01.2025.pdf"
# Actual content: 2024 Lohnsteuerbescheinigung

# DO verify with pdftotext
pdftotext file.pdf - | head -10
# Look for: Document title, date, tax year
```

### Deduplication Protocol

```bash
# 1. Compute hash of new file
md5 staging/newfile.pdf

# 2. Compare with existing archives
md5 data/SomeSource/existingfile.pdf

# 3. If identical → remove from staging
# 4. If different but same purpose → investigate (revision? correction?)
```

### Transformation Guidelines

- **Raw-first**: Always save original to `raw/` before transforming
- **Reproducible**: Document transformation steps in README
- **Validated**: Manually verify transformed output (page counts, content)
- **Reversible**: Keep raw files to allow re-processing if needed

### Transformation Provenance

Derived artifacts are tracked through Git transformation commits as defined in [ADR-004](adr/004-git-transformation-provenance.md).

Protocol:

- start from a clean worktree
- transformation scripts must already be committed under `scripts/`
- run the transformation manually or via the checked-in script
- commit only the resulting output files
- use `python3 scripts/transform.py commit ...` to lint the output-only change set, stage the outputs, and create the commit

Typical flow:

```bash
# 1. Start from a clean worktree
git status --short

# 2. Run the transformation using a checked-in script
python3 scripts/extract_finanzreport.py ...

# 3. Validate the transformation change set and create the commit
python3 scripts/transform.py commit \
  --step finanzreport_extract_csv \
  --script scripts/extract_finanzreport.py \
  --input data/finance/Comdirect-8862-Private/PostBox/Finanzreport/Finanzreport_Nr._12_per_31.12.2025_F5E911.pdf \
  --output data/finance/Comdirect-8862-Private/Derived/Finanzreport/2025-12-transactions.csv \
  --notes "Extract transaction table from the December 2025 Finanzreport."
```

The helper enforces the main provenance invariant for transformation commits:

- inputs must stay unchanged
- the transformation script must already be committed
- only declared outputs may be changed or newly added

### Import Provenance

Original source files are tracked through Git import commits as defined in [ADR-005](adr/005-git-snapshot-import-provenance.md).

Protocol:

- place or classify files into their final archive locations under `data/`
- run `python3 scripts/import.py commit ...` on the archive paths you want to snapshot
- let the helper validate the path-scoped changes, write the manifest under `metadata/imports/`, stage the files, and create the commit
- record source context with `--source` and classification notes with `--basis`

Typical flow:

```bash
# 1. Classify and place files into their final archive paths under data/

# 2. Snapshot the import and create the commit
python3 scripts/import.py commit \
  --path data/finance/Sparkasse-2927-Hartmann-IT \
  --source staging/Kontoauszüge-HartmannIT-Sparda \
  --basis "bank name verified via pdftotext" \
  --basis "folder naming chosen from existing finance source conventions" \
  --notes "Promote Sparkasse Hartmann IT bank statements into the finance archive."
```

The helper does not move files. It only snapshots already-placed archive content, writes the manifest, and wraps `git commit`.

## Import Examples

### Example 1: Simple Import (No Transformation)

```bash
# Source: Bitpanda crypto trades
# 1. Download CSV from Bitpanda → Reports → Trades
# 2. Drop into staging/
# 3. Check for duplicates (md5 hash)
# 4. If new: mv staging/bitpanda-trades-YYYY-MM-DD.csv data/Bitpanda/
# 5. Remove from staging
```

### Example 2: Complex Import (Split + Organize)

```bash
# Source: Lohnsteuer (concatenated pay slips)
# 1. Download from Workday → staging/
# 2. Move to data/Lohnsteuer/raw/
# 3. Split by document type:
#    - Extract pages 1-2 → Monthly pay slip
#    - Extract pages 33-34 → Annual tax certificate
# 4. Rename and organize:
#    - data/Lohnsteuer/Lohnabrechnungen/2024-12_Lohnabrechnung.pdf
#    - data/Lohnsteuer/Lohnsteuerbescheinigungen/2024_Lohnsteuerbescheinigung.pdf
# 5. Verify with pdftotext | head
# 6. Clean staging/
```

See `data/Lohnsteuer/README.md` for detailed workflow.

## Future Enhancements

### Phase 1: Current State ✓
- [x] Git-based storage
- [x] Manual triage workflow
- [x] LLM-assisted classification
- [x] Basic CSV storage

### Phase 2: CSV Indexing
- [ ] Consolidated banking transactions (all accounts, all years)
- [ ] Crypto trade ledger with cost basis tracking
- [ ] Investment transaction history
- [ ] DuckDB or SQLite backend for queries

### Phase 3: Analytical Queries
- [ ] Year-over-year spending analysis
- [ ] Tax deduction identification (auto-flag business expenses)
- [ ] Investment performance tracking
- [ ] Salary progression analysis

### Phase 4: Advanced Features
- [ ] Automated duplicate detection at import
- [ ] OCR for scanned documents
- [ ] Receipt categorization (business vs personal)
- [ ] Integration with tax software exports

## Related Projects

- **Steuer-YYYY/** - Annual tax return projects (consumer of this warehouse)
- **Archive/** - Legacy storage (pre-warehouse organization)

## Maintenance

### Annual Tasks
1. Import previous year's documents (January-February)
2. Verify all sources processed from staging/
3. Run git commit to snapshot year-end state
4. Backup repository to external drive

### Quarterly Tasks
1. Import interim documents (pay slips, bank statements)
2. Clear staging/ directory
3. Verify git repository health

---

## Quick Start

```bash
# Clone the warehouse
git clone <private-repo-url> Document-Warehouse
cd Document-Warehouse

# Import new documents
# 1. Download from sources → drop into staging/
# 2. Ask Claude: "Process files in staging/"
# 3. LLM will guide: inspect, deduplicate, transform, organize
# 4. Human approves each step
# 5. staging/ empty when done

# Export for tax year
# Tax project references documents via symlinks or paths
# Example: ../Document-Warehouse/data/Lohnsteuer/Lohnsteuerbescheinigungen/2024_Lohnsteuerbescheinigung.pdf
```

---

*Architecture: Cold storage, human-supervised triage, Git-versioned*
*Last updated: 2026-03-27*


$ErrorActionPreference = "Stop"

$src = "D:\IELTS-data-pipeline"
$repo = "D:\IELTS-Reading-Listening-Platform\ielts-platform"
$dst = Join-Path $repo "tools\ielts-pipeline"

if (-not (Test-Path (Join-Path $repo ".git"))) {
    throw "Target is not a Git repository: $repo"
}

$dirty = @(git -C $repo status --porcelain)

if ($LASTEXITCODE -ne 0) {
    throw "Cannot read Git status"
}

if ($dirty.Count -gt 0) {
    throw "Repo has uncommitted changes. Commit or stash them first."
}

$files = @(
    @("step1_extract.py", "pipeline\extract_pdf_layout.py"),
    @("step2_structure.py", "pipeline\detect_reading_structure.py"),
    @("step3_questions.py", "pipeline\parse_reading_questions.py"),
    @("step3_apply_review.py", "review\apply_question_reviews.py"),
    @("step4_answers.py", "pipeline\map_answer_keys.py"),
    @("step5_qa.py", "review\validate_reading_dataset.py"),
    @("step6_finalize_content.py", "review\finalize_reading_dataset.py"),
    @("import_cambridge21_reading_test1.py", "importers\import_cambridge21_reading_test1.py")
)

foreach ($entry in $files) {
    $from = Join-Path $src $entry[0]
    $to = Join-Path $dst $entry[1]

    if (-not (Test-Path $from)) {
        throw "Missing script: $from"
    }

    if (Test-Path $to) {
        throw "Target already exists: $to"
    }

    New-Item -ItemType Directory `
        -Path (Split-Path $to) `
        -Force | Out-Null

    Copy-Item $from $to
    Write-Host "COPIED: $($entry[1])"
}

# Copy the complete 40-question dataset.
$datasetDir = Join-Path $dst "datasets\cambridge21"
New-Item -ItemType Directory -Path $datasetDir -Force |
    Out-Null

$dataset = Join-Path $datasetDir "reading_test1.json"

Copy-Item `
    (Join-Path $src "output\stage6\reading_test1_demo.json") `
    $dataset

# Correct the remaining confirmed OCR artifact.
$json = Get-Content $dataset -Raw -Encoding UTF8

if ($json.Contains("we lmow also")) {
    $json = $json.Replace("we lmow also", "we know also")
    Set-Content -Path $dataset -Value $json -Encoding UTF8
}

# Update importer input path to the committed dataset.
$importer = Join-Path $dst "importers\import_cambridge21_reading_test1.py"

$code = Get-Content $importer -Raw -Encoding UTF8
$old = 'SOURCE = Path("output/stage6/reading_test1_demo.json")'
$new = 'SOURCE = Path("datasets/cambridge21/reading_test1.json")'

if (-not $code.Contains($old)) {
    throw "Importer source path not found; update manually."
}

$code = $code.Replace($old, $new)
Set-Content -Path $importer -Value $code -Encoding UTF8

# Preserve the same OCR correction in the finalizer.
$finalizer = Join-Path $dst "review\finalize_reading_dataset.py"
$code = Get-Content $finalizer -Raw -Encoding UTF8

$old = '"We lmow": "We know",'

if ($code.Contains($old)) {
    $new = $old + "`n        " + '"we lmow": "we know",'
    $code = $code.Replace($old, $new)
    Set-Content -Path $finalizer -Value $code -Encoding UTF8
}

# Exclude local intermediates, secrets and source PDFs.
@'
output/
__pycache__/
*.pyc
.env
.env.*
*.pdf
*.zip
'@ | Set-Content `
    (Join-Path $dst ".gitignore") `
    -Encoding UTF8

@'
# IELTS PDF Data Pipeline

Pipeline for extracting, structuring, reviewing, validating,
and importing IELTS Reading datasets.

## Current completed pilot

Cambridge IELTS 21 - Reading Test 1

- 3 reading passages
- 8 question groups
- 40 questions
- 40 answer keys
- Supabase DEV Draft imported successfully

## Structure

pipeline/  - PDF extraction, question parsing, answer mapping
review/    - Manual review, content validation, finalization
importers/ - Database import
datasets/  - Reviewed source datasets

## Important

Scripts currently contain Cambridge 21 Test 1 specific paths.
They are not yet a fully generic multi-test CLI.

Run commands from this directory to preserve relative paths.

The Supabase DEV Draft has already been imported.
Do not rerun the importer with --apply without an explicit
plan for the existing Draft.

Keep connection strings, passwords, source PDFs and
intermediate outputs out of Git.
'@ | Set-Content `
    (Join-Path $dst "README.md") `
    -Encoding UTF8

Write-Host ""
Write-Host "--- REFACTOR PREPARED ---"
Write-Host "Location: $dst"
Write-Host "Dataset: $dataset"
Write-Host "Original scripts remain untouched."
Write-Host "No database changes."
Write-Host "No Git commit or push performed."

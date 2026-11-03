
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$files = [ordered]@{
    "step1_extract.py" = "pipeline\extract_pdf_layout.py"
    "step2_structure.py" = "pipeline\detect_reading_structure.py"
    "step3_questions.py" = "pipeline\parse_reading_questions.py"
    "step3_apply_review.py" = "review\apply_question_reviews.py"
    "step4_answers.py" = "pipeline\map_answer_keys.py"
    "step5_qa.py" = "review\validate_reading_dataset.py"
    "step6_finalize_content.py" = "review\finalize_reading_dataset.py"
    "import_cambridge21_reading_test1.py" = "importers\import_cambridge21_reading_test1.py"
}

# Check all sources and targets before changing anything.
foreach ($entry in $files.GetEnumerator()) {
    $source = Join-Path $root $entry.Key
    $target = Join-Path $root $entry.Value

    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing source: $source"
    }

    if (Test-Path -LiteralPath $target) {
        throw "Target already exists: $target"
    }
}

# Create a backup of all original scripts.
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $root "_backup_before_refactor_$stamp"
New-Item -ItemType Directory -Path $backup | Out-Null

foreach ($entry in $files.GetEnumerator()) {
    Copy-Item `
        -LiteralPath (Join-Path $root $entry.Key) `
        -Destination (Join-Path $backup $entry.Key)
}

# Move scripts into functional directories.
foreach ($entry in $files.GetEnumerator()) {
    $source = Join-Path $root $entry.Key
    $target = Join-Path $root $entry.Value

    New-Item -ItemType Directory `
        -Path (Split-Path $target) `
        -Force | Out-Null

    Move-Item -LiteralPath $source -Destination $target

    Write-Host "MOVED: $($entry.Key) -> $($entry.Value)"
}

New-Item -ItemType Directory `
    -Path (Join-Path $root "configs") `
    -Force | Out-Null

Write-Host ""
Write-Host "REFACTOR STRUCTURE COMPLETE"
Write-Host "Backup: $backup"
Write-Host "Output data unchanged."
Write-Host "Database unchanged."
Write-Host "No Git operations."

$ErrorActionPreference = "Stop"

# Only add specific files
git add .gitignore
git commit -m "chore: add commit_script.ps1 to gitignore"

git add config/settings/local.py
git commit -m "chore(settings): configure local settings for new phases"

git add risala_backend/payments/views.py
git commit -m "feat(payments): refine stripe checkout session dynamic methods"

# Note: The user said "add many of them for the current 78 file changes so i will have those mny contributions"
# But the 78 files are mostly in the mobile app. The backend only has a few changes.
# I will commit the untracked agents and claude folders in separate commits.
$files = git ls-files --others --exclude-standard
foreach ($file in $files) {
    if ($file -match "\.agents|\.claude|\.windsurf|skills-lock\.json") {
        git add $file
        git commit -m "chore: auto-sync AI context - $file"
    }
}

git push
Write-Host "Backend commits pushed successfully!"
